"""Image upload, serving, and orphan upload cleanup routes."""

import asyncio
import mimetypes
import shutil
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse

from .. import config

logger = config.logger

_UPLOAD_READ_CHUNK_BYTES = 64 * 1024
_UPLOAD_CHUNK_TIMEOUT_SECONDS = 30.0
_DEFAULT_ORPHAN_UPLOAD_MIN_AGE_SECONDS = 3600.0


def build_uploads_router(
    *,
    require_http_auth: Callable[..., Any],
    get_store: Callable[[], Any],
) -> Tuple[APIRouter, Dict[str, Any]]:
    router = APIRouter()

    def cleanup_orphan_uploads(
        uploads_dir: Optional[Path] = None,
        *,
        min_age_seconds: float = _DEFAULT_ORPHAN_UPLOAD_MIN_AGE_SECONDS,
    ) -> Dict[str, Any]:
        """Delete unreferenced files in uploads_dir older than min_age_seconds."""
        target_dir = Path(uploads_dir) if uploads_dir is not None else config.UPLOADS_DIR
        if not target_dir.is_dir():
            return {"deleted": 0, "freed_bytes": 0, "kept": 0}
        referenced = get_store().get_referenced_upload_filenames()
        cutoff = time.time() - max(0.0, float(min_age_seconds))
        deleted = 0
        freed_bytes = 0
        kept = 0
        for entry in target_dir.iterdir():
            try:
                if entry.is_symlink() or not entry.is_file():
                    continue
                if entry.name in referenced:
                    kept += 1
                    continue
                st = entry.stat()
                if st.st_mtime > cutoff:
                    kept += 1
                    continue
                size = int(st.st_size)
                entry.unlink(missing_ok=True)
                deleted += 1
                freed_bytes += size
            except OSError as exc:
                logger.warning("Failed inspecting/deleting orphan upload %s: %s", entry.name, exc)
        if deleted > 0:
            logger.info(
                "Cleaned up %d orphan upload(s) (freed %.1f KB, kept %d)",
                deleted,
                freed_bytes / 1024.0,
                kept,
            )
        return {"deleted": deleted, "freed_bytes": freed_bytes, "kept": kept}

    @router.post("/api/upload")
    async def upload_image(
        file: UploadFile = File(...),
        _token: str = Depends(require_http_auth),
    ):
        orig_name = file.filename or "upload.png"
        ext = Path(orig_name).suffix.lower()
        if not ext:
            guessed = mimetypes.guess_extension(file.content_type or "") or ".png"
            ext = ".jpg" if guessed == ".jpe" else guessed
        if ext not in config.ALLOWED_IMAGE_EXTS:
            raise HTTPException(status_code=400, detail=f"Unsupported image type: {ext}")

        safe_name = f"{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}{ext}"
        dest = (config.UPLOADS_DIR / safe_name).resolve()
        total_size = 0
        try:
            with open(dest, "wb") as out_f:
                while True:
                    try:
                        chunk = await asyncio.wait_for(
                            file.read(_UPLOAD_READ_CHUNK_BYTES),
                            timeout=_UPLOAD_CHUNK_TIMEOUT_SECONDS,
                        )
                    except asyncio.TimeoutError as exc:
                        raise HTTPException(
                            status_code=408,
                            detail="Upload timed out while receiving file data",
                        ) from exc
                    if not chunk:
                        break
                    total_size += len(chunk)
                    if total_size > config.MAX_UPLOAD_BYTES:
                        raise HTTPException(status_code=413, detail="Image exceeds maximum upload size")
                    out_f.write(chunk)
            if total_size == 0:
                raise HTTPException(status_code=400, detail="Empty file")
        except Exception:
            dest.unlink(missing_ok=True)
            raise

        # Auto-convert HEIC/HEIF/BMP to JPEG for universal browser preview & native ACP multimodal support
        if ext in {".heic", ".heif", ".bmp"} and shutil.which("sips"):
            jpg_name = f"{dest.stem}.jpg"
            jpg_dest = (config.UPLOADS_DIR / jpg_name).resolve()
            try:
                proc = await asyncio.create_subprocess_exec(
                    "sips", "-s", "format", "jpeg", "-s", "formatOptions", "90",
                    str(dest), "--out", str(jpg_dest),
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await asyncio.wait_for(proc.wait(), timeout=15.0)
                if proc.returncode == 0 and jpg_dest.exists() and jpg_dest.stat().st_size > 0:
                    dest.unlink(missing_ok=True)
                    dest = jpg_dest
                    safe_name = jpg_name
                    ext = ".jpg"
                    total_size = int(dest.stat().st_size)
            except Exception as exc:
                logger.warning("Failed converting %s to jpeg: %s", safe_name, exc)

        mime_map = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".webp": "image/webp",
            ".gif": "image/gif",
        }
        mime_type = mime_map.get(ext) or mimetypes.guess_type(str(dest))[0] or "image/png"

        return {
            "filename": safe_name,
            "abs_path": str(dest),
            "url": f"/uploads/{safe_name}",
            "mime_type": mime_type,
            "size": total_size,
        }

    @router.post("/api/uploads/cleanup")
    async def cleanup_uploads_api(
        min_age_seconds: float = Query(default=_DEFAULT_ORPHAN_UPLOAD_MIN_AGE_SECONDS, ge=0.0),
        _token: str = Depends(require_http_auth),
    ):
        res = await asyncio.to_thread(cleanup_orphan_uploads, min_age_seconds=min_age_seconds)
        return {"ok": True, **res}

    @router.get("/uploads/{filename}")
    async def serve_upload(filename: str, _token: str = Depends(require_http_auth)):
        if "/" in filename or "\\" in filename or ".." in filename:
            raise HTTPException(status_code=400, detail="Invalid filename")
        path = (config.UPLOADS_DIR / filename).resolve()
        if not path.exists() or path.parent != config.UPLOADS_DIR.resolve():
            raise HTTPException(status_code=404, detail="File not found")
        return FileResponse(path)

    return router, {
        "cleanup_orphan_uploads": cleanup_orphan_uploads,
        "upload_image": upload_image,
        "cleanup_uploads_api": cleanup_uploads_api,
        "serve_upload": serve_upload,
    }
