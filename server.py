"""VibeOne FastAPI entrypoint and HTTP/WebSocket API layer."""

import asyncio
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, Request, WebSocket
from fastapi.middleware.gzip import GZipMiddleware

from vibeone import auth_http, config, passkey, quota, restart
from vibeone.auth_flow import ProfileAuthFlowManager
from vibeone.auth_http import (
    _AUTH_FAILURES,
    _AUTH_LOCKOUTS,
    _AUTH_LOCK_SECONDS,
    _AUTH_MAX_FAILURES,
    _AUTH_WINDOW_SECONDS,
    _TRUSTED_PROXY_HOSTS,
    _check_auth_rate_limit,
    _clear_auth_failures,
    _get_client_ip,
    _normalize_host_port,
    _record_auth_failure,
    _tokens_match,
    extract_token_from_http,
    make_http_auth_dependency,
    verify_ws_auth_with_checker,
    verify_ws_origin,
)
from vibeone.engines import ProfileManagerPool
from vibeone.push import WebPushManager
from vibeone.restart import _spawn_launchd_restart, make_deferred_restart_trigger
from vibeone.routes.auth import _PASSKEY_CHALLENGES, _PASSKEY_CHALLENGE_TTL, build_auth_router
from vibeone.routes.files import (
    MkdirRequest,
    _EXT_TO_HLJS_LANG,
    _MAX_FILE_PREVIEW_BYTES,
    _PREVIEW_IMAGE_EXTS,
    _guess_preview_language,
    build_files_router,
)
from vibeone.routes.git_diff import (
    _MAX_SINGLE_FILE_DIFF_CHARS,
    _MAX_UNTRACKED_DIFF_FILES,
    _MAX_UNTRACKED_FILE_BYTES,
    _MAX_UNTRACKED_TOTAL_BYTES,
    _build_single_untracked_diff_from_path,
    _build_untracked_diff,
    _list_untracked_files,
    _parse_git_name_status_z,
    _parse_git_numstat_z,
    _run_git,
    _validate_repo_rel_path,
    build_git_diff_router,
)
from vibeone.routes.profiles import build_profiles_router
from vibeone.routes.sessions import build_sessions_router
from vibeone.routes.static_assets import (
    _STATIC_MEDIA_TYPES,
    _VENDOR_MEDIA_TYPES,
    build_static_assets_router,
)
from vibeone.routes.uploads import (
    _DEFAULT_ORPHAN_UPLOAD_MIN_AGE_SECONDS,
    _UPLOAD_READ_CHUNK_BYTES,
    build_uploads_router,
)
from vibeone.routes.ws import build_ws_router
from vibeone.schemas import (
    ArchiveSessionsRequest,
    AuthRequest,
    ModeRequest,
    ModelRequest,
    NewSessionRequest,
    PasskeyLoginVerifyRequest,
    PasskeyRegisterVerifyRequest,
    PushSubscribeRequest,
    PushUnsubscribeRequest,
    ReloadProfileRequest,
    RemoveSessionRequest,
    RemoveWorkdirRequest,
    StartProfileLoginRequest,
    SubmitProfileLoginCodeRequest,
    SwitchProfileRequest,
    SwitchSessionRequest,
    SyncSessionRequest,
)
from vibeone.store import SessionStore, ensure_migrated_db_path

logger = config.logger


def _is_authorized_token(candidate: Optional[str]) -> bool:
    if not candidate:
        return False
    tok = candidate.strip()
    if _tokens_match(tok):
        return True
    st = globals().get("store")
    if st is not None and st.validate_auth_session(tok):
        return True
    return False


require_http_auth = make_http_auth_dependency(_is_authorized_token)


def verify_ws_auth(ws: WebSocket) -> bool:
    return verify_ws_auth_with_checker(ws, _is_authorized_token)


store = SessionStore(ensure_migrated_db_path())
push_manager = WebPushManager(store)
manager = ProfileManagerPool(config.VIBEONE_WORKDIR, store, push=push_manager)
auth_flow_manager = ProfileAuthFlowManager(manager)

maybe_trigger_deferred_restart = make_deferred_restart_trigger(lambda: manager)
config.register_deferred_restart_hook(maybe_trigger_deferred_restart)


@asynccontextmanager
async def lifespan(app: FastAPI):
    manager.schedule_prewarm()
    asyncio.create_task(asyncio.to_thread(cleanup_orphan_uploads))
    yield
    await auth_flow_manager.shutdown()
    await manager.stop()


app = FastAPI(title="VibeOne", lifespan=lifespan)
app.add_middleware(GZipMiddleware, minimum_size=1024)

_DEFAULT_CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; "
    "font-src 'self' data:; "
    "connect-src 'self' ws: wss:; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "frame-ancestors 'none'; "
    "form-action 'self'"
)


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    response.headers.setdefault("Content-Security-Policy", _DEFAULT_CSP)
    return response


_static_router, _static_handlers = build_static_assets_router(get_manager=lambda: manager)
app.include_router(_static_router)
globals().update(_static_handlers)

_profiles_router, _profiles_handlers = build_profiles_router(
    require_http_auth=require_http_auth,
    get_manager=lambda: manager,
    get_push_manager=lambda: push_manager,
    get_auth_flow_manager=lambda: auth_flow_manager,
)
app.include_router(_profiles_router)
globals().update(_profiles_handlers)

_auth_router, _auth_handlers = build_auth_router(
    require_http_auth=require_http_auth,
    get_store=lambda: store,
)
app.include_router(_auth_router)
globals().update(_auth_handlers)

_uploads_router, _uploads_handlers = build_uploads_router(
    require_http_auth=require_http_auth,
    get_store=lambda: store,
)
app.include_router(_uploads_router)
cleanup_orphan_uploads = _uploads_handlers["cleanup_orphan_uploads"]
upload_image = _uploads_handlers["upload_image"]
cleanup_uploads_api = _uploads_handlers["cleanup_uploads_api"]
serve_upload = _uploads_handlers["serve_upload"]

_git_diff_router, _git_diff_handlers = build_git_diff_router(
    require_http_auth=require_http_auth,
    get_manager=lambda: manager,
    get_store=lambda: store,
)
app.include_router(_git_diff_router)
globals().update(_git_diff_handlers)

_files_router, _files_handlers = build_files_router(
    require_http_auth=require_http_auth,
    get_manager=lambda: manager,
    get_store=lambda: store,
)
app.include_router(_files_router)
globals().update(_files_handlers)

_sessions_router, _sessions_handlers = build_sessions_router(
    require_http_auth=require_http_auth,
    get_manager=lambda: manager,
)
app.include_router(_sessions_router)
globals().update(_sessions_handlers)

_ws_router, _ws_handlers = build_ws_router(
    get_manager=lambda: manager,
    verify_ws_origin_fn=verify_ws_origin,
    verify_ws_auth_fn=verify_ws_auth,
)
app.include_router(_ws_router)
globals().update(_ws_handlers)
