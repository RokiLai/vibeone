"""Static assets, PWA files, index template assembly, and health check routes."""

import hashlib
import re
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

from fastapi import APIRouter, HTTPException, Response
from fastapi.responses import FileResponse, HTMLResponse

from .. import config

_VENDOR_MEDIA_TYPES = {
    "tailwind.min.css": "text/css; charset=utf-8",
    "tailwindcss.min.js": "application/javascript",
    "purify.min.js": "application/javascript",
    "marked.min.js": "application/javascript",
    "highlight.min.js": "application/javascript",
    "github-dark.min.css": "text/css",
}

_APP_SCRIPT_FILES = (
    "app-i18n-dict.js",
    "app-i18n.js",
    "app-bootstrap.js",
    "app-telemetry.js",
    "app-sidebar-gestures.js",
    "app-sidebar-search.js",
    "app-sidebar-actions.js",
    "app-sidebar.js",
    "app-chat-markdown.js",
    "app-chat-helpers.js",
    "app-chat-render.js",
    "app-chat.js",
    "app-pickers.js",
    "app-websocket.js",
    "app-auth.js",
    "app-composer-cards.js",
    "app-composer-slash.js",
    "app-composer.js",
    "app-picker-events.js",
    "app-profiles.js",
    "app-profile-login.js",
    "app-settings.js",
    "app-image-viewer.js",
    "app-git-diff-format.js",
    "app-git-diff.js",
    "app-file-tools.js",
    "app.js",
)

_STATIC_MEDIA_TYPES = {
    "app.css": "text/css; charset=utf-8",
    **{name: "application/javascript; charset=utf-8" for name in _APP_SCRIPT_FILES},
}

_INCLUDE_RE = re.compile(r"<!--\s*INCLUDE:([A-Za-z0-9_.-]+\.html)\s*-->")

_BUNDLE_CACHE: Dict[str, Any] = {"sig": None, "bytes": b"", "hash": ""}
_FILE_HASH_CACHE: Dict[str, Tuple[Tuple[int, int], str]] = {}


def _file_version_hash(path: Path) -> str:
    try:
        st = path.stat()
    except OSError:
        return ""
    sig = (st.st_mtime_ns, st.st_size)
    key = str(path)
    cached = _FILE_HASH_CACHE.get(key)
    if cached and cached[0] == sig:
        return cached[1]
    digest = hashlib.sha256(path.read_bytes()).hexdigest()[:12]
    _FILE_HASH_CACHE[key] = (sig, digest)
    return digest


def get_app_js_bundle() -> Tuple[bytes, str]:
    static_dir = config.BASE_DIR / "static"
    paths = [static_dir / name for name in _APP_SCRIPT_FILES]
    sig = tuple((p.stat().st_mtime_ns, p.stat().st_size) for p in paths)
    if _BUNDLE_CACHE["sig"] == sig and _BUNDLE_CACHE["bytes"]:
        return _BUNDLE_CACHE["bytes"], _BUNDLE_CACHE["hash"]

    parts = []
    for name, path in zip(_APP_SCRIPT_FILES, paths):
        parts.append(f"/* === {name} === */\n")
        parts.append(path.read_text(encoding="utf-8"))
        parts.append("\n;\n")
    raw = "".join(parts).encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()[:12]
    _BUNDLE_CACHE["sig"] = sig
    _BUNDLE_CACHE["bytes"] = raw
    _BUNDLE_CACHE["hash"] = digest
    return raw, digest


def render_index_html() -> str:
    index_path = config.BASE_DIR / "index.html"
    content = index_path.read_text(encoding="utf-8")
    templates_dir = (config.BASE_DIR / "templates").resolve()

    def _replace(match: re.Match) -> str:
        name = match.group(1)
        part = (templates_dir / name).resolve()
        if part.parent != templates_dir or not part.is_file():
            return match.group(0)
        return part.read_text(encoding="utf-8")

    html = _INCLUDE_RE.sub(_replace, content)
    _, bundle_v = get_app_js_bundle()
    app_css_v = _file_version_hash(config.BASE_DIR / "static" / "app.css")
    tw_css_v = _file_version_hash(config.BASE_DIR / "vendor" / "tailwind.min.css")
    html = html.replace("__APP_BUNDLE_V__", bundle_v)
    html = html.replace("__APP_CSS_V__", app_css_v)
    html = html.replace("__TAILWIND_CSS_V__", tw_css_v)
    return html


def build_static_assets_router(
    get_manager: Callable[[], Any],
) -> Tuple[APIRouter, Dict[str, Any]]:
    router = APIRouter()

    @router.get("/", response_class=HTMLResponse)
    async def serve_index():
        return HTMLResponse(content=render_index_html())

    @router.get("/sw.js")
    async def serve_service_worker():
        return FileResponse(
            config.BASE_DIR / "sw.js",
            media_type="application/javascript",
            headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"},
        )

    @router.get("/manifest.json")
    async def serve_manifest():
        return FileResponse(config.BASE_DIR / "manifest.json", media_type="application/manifest+json")

    @router.get("/apple-touch-icon.png")
    async def serve_apple_touch_icon():
        return FileResponse(config.BASE_DIR / "icons" / "apple-touch-icon.png", media_type="image/png")

    @router.get("/icon-192.png")
    async def serve_icon_192():
        return FileResponse(config.BASE_DIR / "icons" / "icon-192.png", media_type="image/png")

    @router.get("/icon-512.png")
    async def serve_icon_512():
        return FileResponse(config.BASE_DIR / "icons" / "icon-512.png", media_type="image/png")

    @router.get("/favicon.png")
    @router.get("/favicon.ico")
    async def serve_favicon():
        return FileResponse(config.BASE_DIR / "icons" / "favicon.png", media_type="image/png")

    @router.get("/icon.svg")
    async def serve_icon():
        svg_path = config.BASE_DIR / "icons" / "icon.svg"
        if svg_path.is_file():
            return FileResponse(svg_path, media_type="image/svg+xml")
        return FileResponse(config.BASE_DIR / "icons" / "icon-512.png", media_type="image/png")

    @router.get("/vendor/{filename}")
    async def serve_vendor_asset(filename: str, v: Optional[str] = None):
        media_type = _VENDOR_MEDIA_TYPES.get(filename)
        if not media_type:
            raise HTTPException(status_code=404, detail="Vendor asset not found")
        path = (config.BASE_DIR / "vendor" / filename).resolve()
        if not path.is_file() or path.parent != (config.BASE_DIR / "vendor").resolve():
            raise HTTPException(status_code=404, detail="Vendor asset not found")
        cache_ctrl = (
            "public, max-age=31536000, immutable"
            if v and v == _file_version_hash(path)
            else "public, max-age=604800"
        )
        return FileResponse(
            path,
            media_type=media_type,
            headers={"Cache-Control": cache_ctrl},
        )

    @router.get("/static/app.bundle.js")
    async def serve_app_bundle(v: Optional[str] = None):
        raw, digest = get_app_js_bundle()
        cache_ctrl = (
            "public, max-age=31536000, immutable"
            if v and v == digest
            else "no-cache"
        )
        return Response(
            content=raw,
            media_type="application/javascript; charset=utf-8",
            headers={"Cache-Control": cache_ctrl, "ETag": f'"{digest}"'},
        )

    @router.get("/static/{filename}")
    async def serve_static_asset(filename: str, v: Optional[str] = None):
        media_type = _STATIC_MEDIA_TYPES.get(filename)
        if not media_type:
            raise HTTPException(status_code=404, detail="Static asset not found")
        path = (config.BASE_DIR / "static" / filename).resolve()
        if not path.is_file() or path.parent != (config.BASE_DIR / "static").resolve():
            raise HTTPException(status_code=404, detail="Static asset not found")
        cache_ctrl = (
            "public, max-age=31536000, immutable"
            if v and v == _file_version_hash(path)
            else "no-cache"
        )
        return FileResponse(
            path,
            media_type=media_type,
            headers={"Cache-Control": cache_ctrl},
        )

    @router.get("/api/health")
    async def health():
        return {"ok": True, "acp_connected": get_manager().connected}

    return router, {
        "serve_index": serve_index,
        "serve_service_worker": serve_service_worker,
        "serve_manifest": serve_manifest,
        "serve_apple_touch_icon": serve_apple_touch_icon,
        "serve_icon_192": serve_icon_192,
        "serve_icon_512": serve_icon_512,
        "serve_favicon": serve_favicon,
        "serve_icon": serve_icon,
        "serve_vendor_asset": serve_vendor_asset,
        "serve_app_bundle": serve_app_bundle,
        "serve_static_asset": serve_static_asset,
        "health": health,
    }
