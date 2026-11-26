"""Static assets, PWA files, index template assembly, and health check routes."""

import re
from typing import Any, Callable, Dict, Tuple

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, HTMLResponse

from .. import config

_VENDOR_MEDIA_TYPES = {
    "tailwindcss.min.js": "application/javascript",
    "purify.min.js": "application/javascript",
    "marked.min.js": "application/javascript",
    "highlight.min.js": "application/javascript",
    "github-dark.min.css": "text/css",
}

_STATIC_MEDIA_TYPES = {
    "app.css": "text/css; charset=utf-8",
    "app-i18n-dict.js": "application/javascript; charset=utf-8",
    "app-i18n.js": "application/javascript; charset=utf-8",
    "app-bootstrap.js": "application/javascript; charset=utf-8",
    "app-sidebar-gestures.js": "application/javascript; charset=utf-8",
    "app-sidebar-search.js": "application/javascript; charset=utf-8",
    "app-sidebar-actions.js": "application/javascript; charset=utf-8",
    "app-sidebar.js": "application/javascript; charset=utf-8",
    "app-chat-markdown.js": "application/javascript; charset=utf-8",
    "app-chat-helpers.js": "application/javascript; charset=utf-8",
    "app-chat-render.js": "application/javascript; charset=utf-8",
    "app-chat.js": "application/javascript; charset=utf-8",
    "app-pickers.js": "application/javascript; charset=utf-8",
    "app-websocket.js": "application/javascript; charset=utf-8",
    "app-auth.js": "application/javascript; charset=utf-8",
    "app-composer-cards.js": "application/javascript; charset=utf-8",
    "app-composer-slash.js": "application/javascript; charset=utf-8",
    "app-composer.js": "application/javascript; charset=utf-8",
    "app-picker-events.js": "application/javascript; charset=utf-8",
    "app-profiles.js": "application/javascript; charset=utf-8",
    "app-profile-login.js": "application/javascript; charset=utf-8",
    "app-settings.js": "application/javascript; charset=utf-8",
    "app-image-viewer.js": "application/javascript; charset=utf-8",
    "app-git-diff-format.js": "application/javascript; charset=utf-8",
    "app-git-diff.js": "application/javascript; charset=utf-8",
    "app-file-tools.js": "application/javascript; charset=utf-8",
    "app.js": "application/javascript; charset=utf-8",
}

_INCLUDE_RE = re.compile(r"<!--\s*INCLUDE:([A-Za-z0-9_.-]+\.html)\s*-->")


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

    return _INCLUDE_RE.sub(_replace, content)


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
    async def serve_vendor_asset(filename: str):
        media_type = _VENDOR_MEDIA_TYPES.get(filename)
        if not media_type:
            raise HTTPException(status_code=404, detail="Vendor asset not found")
        path = (config.BASE_DIR / "vendor" / filename).resolve()
        if not path.is_file() or path.parent != (config.BASE_DIR / "vendor").resolve():
            raise HTTPException(status_code=404, detail="Vendor asset not found")
        return FileResponse(
            path,
            media_type=media_type,
            headers={"Cache-Control": "public, max-age=604800"},
        )

    @router.get("/static/{filename}")
    async def serve_static_asset(filename: str):
        media_type = _STATIC_MEDIA_TYPES.get(filename)
        if not media_type:
            raise HTTPException(status_code=404, detail="Static asset not found")
        path = (config.BASE_DIR / "static" / filename).resolve()
        if not path.is_file() or path.parent != (config.BASE_DIR / "static").resolve():
            raise HTTPException(status_code=404, detail="Static asset not found")
        return FileResponse(
            path,
            media_type=media_type,
            headers={"Cache-Control": "no-cache"},
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
        "serve_static_asset": serve_static_asset,
        "health": health,
    }
