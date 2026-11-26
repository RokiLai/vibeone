"""Filesystem directory browsing and file preview routes."""

import mimetypes
import os
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .. import config

_MAX_FILE_PREVIEW_BYTES = 512 * 1024  # 512 KB
_PREVIEW_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico", ".svg"}
_EXT_TO_HLJS_LANG = {
    ".py": "python",
    ".pyi": "python",
    ".js": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".json": "json",
    ".jsonl": "json",
    ".html": "xml",
    ".htm": "xml",
    ".xml": "xml",
    ".svg": "xml",
    ".css": "css",
    ".scss": "scss",
    ".less": "less",
    ".md": "markdown",
    ".markdown": "markdown",
    ".mdx": "markdown",
    ".sh": "bash",
    ".bash": "bash",
    ".zsh": "bash",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "ini",
    ".ini": "ini",
    ".cfg": "ini",
    ".conf": "ini",
    ".sql": "sql",
    ".rs": "rust",
    ".go": "go",
    ".java": "java",
    ".kt": "kotlin",
    ".swift": "swift",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".cs": "csharp",
    ".rb": "ruby",
    ".php": "php",
    ".lua": "lua",
    ".r": "r",
    ".dockerfile": "dockerfile",
    ".makefile": "makefile",
    ".diff": "diff",
    ".patch": "diff",
    ".graphql": "graphql",
    ".vue": "xml",
    ".svelte": "xml",
}


class MkdirRequest(BaseModel):
    parent_path: str
    name: str


def _guess_preview_language(file_path: Path) -> str:
    ext = file_path.suffix.lower()
    if ext in _EXT_TO_HLJS_LANG:
        return _EXT_TO_HLJS_LANG[ext]
    name_lower = file_path.name.lower()
    if name_lower == "dockerfile" or name_lower.startswith("dockerfile."):
        return "dockerfile"
    if name_lower == "makefile":
        return "makefile"
    if name_lower in {".gitignore", ".dockerignore", ".env", ".env.example"}:
        return "ini"
    return "plaintext"


def build_files_router(
    *,
    require_http_auth: Callable[..., Any],
    get_manager: Callable[[], Any],
    get_store: Callable[[], Any],
) -> Tuple[APIRouter, Dict[str, Any]]:
    router = APIRouter()

    def _resolve_preview_file_path(
        raw_path: str,
        workdir: Optional[str] = None,
        profile: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> Tuple[str, str]:
        cleaned = str(raw_path or "").strip()
        if not cleaned:
            raise HTTPException(status_code=400, detail="缺少文件路径")

        base_workdir = ""
        if workdir and os.path.isdir(os.path.abspath(os.path.expanduser(workdir))):
            base_workdir = os.path.abspath(os.path.expanduser(workdir))
        if not base_workdir:
            pool = get_manager()
            try:
                mgr = pool.get_manager(profile)
            except Exception:
                mgr = pool.active
            if session_id:
                rt = mgr._runtimes.get(session_id)
                if rt and rt.workdir:
                    base_workdir = rt.workdir
                else:
                    row = get_store().get_session(session_id)
                    if row and row.get("workdir"):
                        base_workdir = row["workdir"]
            if not base_workdir:
                base_workdir = mgr.workdir

        expanded = os.path.expanduser(cleaned)
        if os.path.isabs(expanded):
            target = os.path.abspath(expanded)
        else:
            target = os.path.abspath(os.path.join(base_workdir or os.getcwd(), expanded))
        return target, base_workdir

    @router.get("/api/fs/dirs")
    async def list_directories(
        path: Optional[str] = None,
        show_hidden: bool = False,
        profile: Optional[str] = None,
        _token: str = Depends(require_http_auth),
    ):
        pool = get_manager()
        try:
            mgr = pool.get_manager(profile)
        except Exception:
            mgr = pool.active
        raw_target = (path or "").strip() or mgr.workdir
        target = os.path.abspath(os.path.expanduser(raw_target))
        if not os.path.exists(target):
            raise HTTPException(status_code=404, detail=f"目录不存在: {target}")
        if not os.path.isdir(target):
            raise HTTPException(status_code=400, detail=f"路径不是目录: {target}")

        entries: List[Dict[str, Any]] = []
        try:
            with os.scandir(target) as it:
                for entry in it:
                    name = entry.name
                    if not show_hidden and name.startswith("."):
                        continue
                    try:
                        if not entry.is_dir(follow_symlinks=True):
                            continue
                    except OSError:
                        continue
                    full_path = os.path.join(target, name)
                    is_git = False
                    try:
                        is_git = os.path.exists(os.path.join(full_path, ".git"))
                    except OSError:
                        pass
                    entries.append(
                        {
                            "name": name,
                            "path": full_path,
                            "is_git": is_git,
                        }
                    )
        except PermissionError:
            raise HTTPException(status_code=403, detail=f"没有权限读取目录: {target}")
        except OSError as exc:
            raise HTTPException(status_code=400, detail=f"读取目录失败: {exc}")

        entries.sort(key=lambda e: e["name"].lower())
        parent = os.path.dirname(target)
        parent_path = parent if parent and parent != target else None

        return {
            "current": target,
            "current_name": Path(target).name or target,
            "is_git": os.path.exists(os.path.join(target, ".git")),
            "parent": parent_path,
            "home": os.path.abspath(os.path.expanduser("~")),
            "default_workdir": config.VIBEONE_WORKDIR,
            "shortcuts": mgr.get_shortcut_dirs(),
            "entries": entries,
        }

    @router.post("/api/fs/mkdir")
    async def create_directory(body: MkdirRequest, _token: str = Depends(require_http_auth)):
        name = body.name.strip()
        if not name or name in (".", "..") or "/" in name or "\\" in name:
            raise HTTPException(status_code=400, detail="文件夹名称不合法")
        try:
            parent = get_manager()._normalize_workdir(body.parent_path)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        new_dir = os.path.join(parent, name)
        if os.path.exists(new_dir):
            raise HTTPException(status_code=409, detail=f"目录已存在: {name}")
        try:
            os.makedirs(new_dir, exist_ok=False)
        except OSError as exc:
            raise HTTPException(status_code=400, detail=f"创建目录失败: {exc}")
        return {"ok": True, "path": new_dir, "name": name}

    @router.get("/api/fs/file")
    async def read_file_preview(
        path: str = Query(..., min_length=1),
        workdir: Optional[str] = None,
        profile: Optional[str] = None,
        session_id: Optional[str] = None,
        _token: str = Depends(require_http_auth),
    ):
        target, base_workdir = _resolve_preview_file_path(
            path, workdir=workdir, profile=profile, session_id=session_id
        )
        if not os.path.exists(target):
            raise HTTPException(status_code=404, detail=f"文件不存在: {target}")
        if os.path.isdir(target):
            raise HTTPException(status_code=400, detail=f"目标路径是目录，暂不支持作为文件预览: {target}")
        if not os.path.isfile(target):
            raise HTTPException(status_code=400, detail=f"目标不是常规文件: {target}")

        fpath = Path(target)
        try:
            st = fpath.stat()
        except PermissionError:
            raise HTTPException(status_code=403, detail=f"没有权限读取文件: {target}")
        except OSError as exc:
            raise HTTPException(status_code=400, detail=f"无法读取文件信息: {exc}")

        size = int(st.st_size)
        ext = fpath.suffix.lower()
        mime_type = mimetypes.guess_type(str(fpath))[0] or "application/octet-stream"
        language = _guess_preview_language(fpath)

        display_path = target
        if base_workdir:
            try:
                rel = os.path.relpath(target, base_workdir)
                if rel and not rel.startswith(".."):
                    display_path = rel
            except ValueError:
                pass

        if ext in _PREVIEW_IMAGE_EXTS:
            return {
                "path": target,
                "display_path": display_path,
                "name": fpath.name,
                "ext": ext,
                "language": language,
                "kind": "image",
                "mime_type": mime_type if mime_type.startswith("image/") else "image/png",
                "size": size,
                "line_count": 0,
                "truncated": False,
                "content": "",
                "mtime": float(st.st_mtime),
            }

        try:
            with open(target, "rb") as fh:
                raw = fh.read(_MAX_FILE_PREVIEW_BYTES + 1)
        except PermissionError:
            raise HTTPException(status_code=403, detail=f"没有权限读取文件: {target}")
        except OSError as exc:
            raise HTTPException(status_code=400, detail=f"读取文件失败: {exc}")

        truncated = len(raw) > _MAX_FILE_PREVIEW_BYTES
        if truncated:
            raw = raw[:_MAX_FILE_PREVIEW_BYTES]

        if b"\x00" in raw[:8192]:
            return {
                "path": target,
                "display_path": display_path,
                "name": fpath.name,
                "ext": ext,
                "language": "plaintext",
                "kind": "binary",
                "mime_type": mime_type,
                "size": size,
                "line_count": 0,
                "truncated": False,
                "content": "",
                "mtime": float(st.st_mtime),
            }

        content = raw.decode("utf-8", errors="replace")
        line_count = len(content.splitlines()) if content else 0
        return {
            "path": target,
            "display_path": display_path,
            "name": fpath.name,
            "ext": ext,
            "language": language,
            "kind": "text",
            "mime_type": mime_type,
            "size": size,
            "shown_bytes": len(raw),
            "line_count": line_count,
            "truncated": truncated,
            "content": content,
            "mtime": float(st.st_mtime),
        }

    @router.get("/api/fs/file/raw")
    async def serve_preview_file_raw(
        path: str = Query(..., min_length=1),
        workdir: Optional[str] = None,
        profile: Optional[str] = None,
        session_id: Optional[str] = None,
        _token: str = Depends(require_http_auth),
    ):
        target, _ = _resolve_preview_file_path(
            path, workdir=workdir, profile=profile, session_id=session_id
        )
        if not os.path.isfile(target):
            raise HTTPException(status_code=404, detail="文件不存在")
        ext = Path(target).suffix.lower()
        if ext not in _PREVIEW_IMAGE_EXTS:
            raise HTTPException(status_code=400, detail="仅支持图片类文件的原始内容预览")
        mime_type = mimetypes.guess_type(target)[0] or "application/octet-stream"
        return FileResponse(
            target,
            media_type=mime_type,
            headers={
                "Cache-Control": "no-store",
                "Content-Security-Policy": "sandbox",
            },
        )

    return router, {
        "list_directories": list_directories,
        "create_directory": create_directory,
        "read_file_preview": read_file_preview,
        "serve_preview_file_raw": serve_preview_file_raw,
        "_resolve_preview_file_path": _resolve_preview_file_path,
    }
