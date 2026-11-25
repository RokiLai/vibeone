"""VibeOne FastAPI entrypoint and HTTP/WebSocket API layer."""

import asyncio
import hmac
import json
import mimetypes
import os
import secrets
import shutil
import subprocess
import sys
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import (
    Cookie,
    Depends,
    FastAPI,
    File,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel

from vibeone import config, passkey, quota
from vibeone.auth_flow import ProfileAuthFlowManager
from vibeone.engines import ProfileManagerPool
from vibeone.push import WebPushManager
from vibeone.store import SessionStore, ensure_migrated_db_path

logger = config.logger
_deferred_restart_scheduled = False

# ---------------------------------------------------------------------------
# Auth Helpers
# ---------------------------------------------------------------------------

_AUTH_FAILURES: Dict[str, List[float]] = {}
_AUTH_LOCKOUTS: Dict[str, float] = {}
_AUTH_MAX_FAILURES = 10
_AUTH_WINDOW_SECONDS = 300.0
_AUTH_LOCK_SECONDS = 60.0


_TRUSTED_PROXY_HOSTS = {"127.0.0.1", "::1", "localhost"}


def _get_client_ip(request: Request) -> str:
    peer_host = request.client.host if request.client else "unknown"
    if peer_host in _TRUSTED_PROXY_HOSTS:
        real_ip = (request.headers.get("x-real-ip") or "").strip()
        if real_ip:
            return real_ip
    return peer_host


def _check_auth_rate_limit(client_ip: str) -> None:
    now = time.time()
    locked_until = _AUTH_LOCKOUTS.get(client_ip, 0.0)
    if locked_until > now:
        retry_after = max(1, int(locked_until - now))
        raise HTTPException(
            status_code=429,
            detail=f"Too many failed login attempts. Try again in {retry_after}s.",
            headers={"Retry-After": str(retry_after)},
        )


def _record_auth_failure(client_ip: str) -> None:
    now = time.time()
    cutoff = now - _AUTH_WINDOW_SECONDS
    recent = [ts for ts in _AUTH_FAILURES.get(client_ip, []) if ts > cutoff]
    recent.append(now)
    _AUTH_FAILURES[client_ip] = recent
    if len(recent) >= _AUTH_MAX_FAILURES:
        _AUTH_LOCKOUTS[client_ip] = now + _AUTH_LOCK_SECONDS
        _AUTH_FAILURES.pop(client_ip, None)


def _clear_auth_failures(client_ip: str) -> None:
    _AUTH_FAILURES.pop(client_ip, None)
    _AUTH_LOCKOUTS.pop(client_ip, None)


def _tokens_match(candidate: Optional[str]) -> bool:
    if not candidate:
        return False
    return hmac.compare_digest(candidate.strip(), config.VIBEONE_WEB_TOKEN)


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


def extract_token_from_http(
    authorization: Optional[str] = None,
    x_agy_token: Optional[str] = None,
    cookie_token: Optional[str] = None,
) -> Optional[str]:
    if authorization:
        parts = authorization.split(" ", 1)
        if len(parts) == 2 and parts[0].lower() == "bearer":
            return parts[1].strip()
    if x_agy_token:
        return x_agy_token.strip()
    if cookie_token:
        return cookie_token.strip()
    return None


async def require_http_auth(
    authorization: Optional[str] = Header(default=None),
    x_vibeone_token: Optional[str] = Header(default=None, alias="X-VibeOne-Token"),
    x_agy_token: Optional[str] = Header(default=None, alias="X-Agy-Token"),
    vibeone_token: Optional[str] = Cookie(default=None, alias=config.AUTH_COOKIE_NAME),
    legacy_cookie_token: Optional[str] = Cookie(default=None, alias=config.LEGACY_AUTH_COOKIE_NAME),
) -> str:
    token = extract_token_from_http(
        authorization,
        x_vibeone_token or x_agy_token,
        vibeone_token or legacy_cookie_token,
    )
    if not _is_authorized_token(token):
        raise HTTPException(status_code=401, detail="Unauthorized: invalid or missing token")
    return token or ""


def verify_ws_auth(ws: WebSocket) -> bool:
    query_token = ws.query_params.get("token")
    if _is_authorized_token(query_token):
        return True
    auth_header = ws.headers.get("authorization")
    x_header = ws.headers.get("x-vibeone-token") or ws.headers.get("x-agy-token")
    cookie_token = ws.cookies.get(config.AUTH_COOKIE_NAME) or ws.cookies.get(config.LEGACY_AUTH_COOKIE_NAME)
    token = extract_token_from_http(auth_header, x_header, cookie_token)
    return _is_authorized_token(token)



store = SessionStore(ensure_migrated_db_path())
push_manager = WebPushManager(store)
manager = ProfileManagerPool(config.VIBEONE_WORKDIR, store, push=push_manager)
auth_flow_manager = ProfileAuthFlowManager(manager)


def _spawn_launchd_restart(delay_sec: float = 0.5) -> None:
    """Spawn a detached helper via posix_spawn to kickstart the macOS LaunchAgent."""
    if sys.platform != "darwin" or not os.path.isfile("/bin/launchctl"):
        logger.warning("Deferred LaunchAgent restart is only supported on macOS with /bin/launchctl")
        return
    target_uid = str(os.getuid())
    safe_delay = f"{max(0.1, float(delay_sec)):.2f}"
    subprocess.Popen(
        [
            "/bin/sh",
            "-c",
            'sleep "$1"; exec /bin/launchctl kickstart -k "gui/$2/$3"',
            "--",
            safe_delay,
            target_uid,
            config.VIBEONE_LAUNCHD_LABEL,
        ],
        start_new_session=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
    )


async def _execute_deferred_restart(push_task: Optional[asyncio.Task] = None) -> None:
    global _deferred_restart_scheduled
    try:
        if push_task is not None:
            try:
                await asyncio.wait_for(asyncio.shield(push_task), timeout=8.0)
            except Exception as exc:
                logger.warning("Deferred restart waited for push task with error: %s", exc)
        try:
            await manager.active.broadcast(
                {
                    "type": "toast",
                    "data": {
                        "level": "info",
                        "message": "服务正在自动重启以应用最新更新...",
                    },
                }
            )
        except Exception:
            pass
        await asyncio.sleep(1.0)
        logger.info(
            "Executing deferred LaunchAgent restart for gui/%d/%s",
            os.getuid(),
            config.VIBEONE_LAUNCHD_LABEL,
        )
        _spawn_launchd_restart(delay_sec=0.3)
    except Exception as exc:
        _deferred_restart_scheduled = False
        logger.error("Deferred restart failed: %s", exc)


def maybe_trigger_deferred_restart(push_task: Optional[asyncio.Task] = None) -> bool:
    """If .restart_after_turn exists and all turns are idle, restart after push + WS flush."""
    global _deferred_restart_scheduled
    if _deferred_restart_scheduled:
        return False
    if not config.RESTART_MARKER_FILE.exists():
        return False
    if manager.any_busy():
        logger.info("Deferred restart marker present, waiting for other busy sessions to finish")
        return False
    try:
        config.RESTART_MARKER_FILE.unlink(missing_ok=True)
    except Exception as exc:
        logger.warning("Failed to remove restart marker %s: %s", config.RESTART_MARKER_FILE, exc)
    _deferred_restart_scheduled = True
    asyncio.create_task(_execute_deferred_restart(push_task=push_task))
    return True


config.register_deferred_restart_hook(maybe_trigger_deferred_restart)


@asynccontextmanager
async def lifespan(app: FastAPI):
    manager.schedule_prewarm()
    yield
    await auth_flow_manager.shutdown()
    await manager.stop()


app = FastAPI(title="VibeOne", lifespan=lifespan)


class SwitchProfileRequest(BaseModel):
    profile: str
    force: bool = False
    scope: str = "client"


class ReloadProfileRequest(BaseModel):
    profile: str
    force: bool = False


class StartProfileLoginRequest(BaseModel):
    profile: str
    backend: Optional[str] = None
    create_if_missing: bool = False
    logout_first: bool = False


class SubmitProfileLoginCodeRequest(BaseModel):
    code: str


class AuthRequest(BaseModel):
    token: str


class PasskeyRegisterVerifyRequest(BaseModel):
    id: str
    attestationObject: str
    clientDataJSON: str
    label: str = ""


class PasskeyLoginVerifyRequest(BaseModel):
    id: str
    authenticatorData: str
    clientDataJSON: str
    signature: str


class ModeRequest(BaseModel):
    mode_id: str
    profile: Optional[str] = None
    session_id: Optional[str] = None


class ModelRequest(BaseModel):
    model_id: str
    profile: Optional[str] = None
    session_id: Optional[str] = None


class NewSessionRequest(BaseModel):
    workdir: Optional[str] = None
    profile: Optional[str] = None


class SwitchSessionRequest(BaseModel):
    session_id: str
    workdir: Optional[str] = None
    title: Optional[str] = None
    profile: Optional[str] = None


class SyncSessionRequest(BaseModel):
    session_id: Optional[str] = None
    force: bool = True
    profile: Optional[str] = None


class MkdirRequest(BaseModel):
    parent_path: str
    name: str


class RemoveSessionRequest(BaseModel):
    session_id: str
    profile: Optional[str] = None


class ArchiveSessionsRequest(BaseModel):
    session_ids: List[str]
    profile: Optional[str] = None


class RemoveWorkdirRequest(BaseModel):
    workdir: str
    profile: Optional[str] = None


class PushSubscribeRequest(BaseModel):
    endpoint: str
    keys: Dict[str, str]


class PushUnsubscribeRequest(BaseModel):
    endpoint: str


@app.get("/", response_class=HTMLResponse)
async def serve_index():
    index_path = config.BASE_DIR / "index.html"
    return HTMLResponse(content=index_path.read_text(encoding="utf-8"))


@app.get("/sw.js")
async def serve_service_worker():
    return FileResponse(
        config.BASE_DIR / "sw.js",
        media_type="application/javascript",
        headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"},
    )


@app.get("/manifest.json")
async def serve_manifest():
    return FileResponse(config.BASE_DIR / "manifest.json", media_type="application/manifest+json")


@app.get("/apple-touch-icon.png")
async def serve_apple_touch_icon():
    return FileResponse(config.BASE_DIR / "icons" / "apple-touch-icon.png", media_type="image/png")


@app.get("/icon-192.png")
async def serve_icon_192():
    return FileResponse(config.BASE_DIR / "icons" / "icon-192.png", media_type="image/png")


@app.get("/icon-512.png")
async def serve_icon_512():
    return FileResponse(config.BASE_DIR / "icons" / "icon-512.png", media_type="image/png")


@app.get("/favicon.png")
@app.get("/favicon.ico")
async def serve_favicon():
    return FileResponse(config.BASE_DIR / "icons" / "favicon.png", media_type="image/png")


@app.get("/icon.svg")
async def serve_icon():
    svg_path = config.BASE_DIR / "icons" / "icon.svg"
    if svg_path.is_file():
        return FileResponse(svg_path, media_type="image/svg+xml")
    return FileResponse(config.BASE_DIR / "icons" / "icon-512.png", media_type="image/png")


_VENDOR_MEDIA_TYPES = {
    "tailwindcss.min.js": "application/javascript",
    "purify.min.js": "application/javascript",
    "marked.min.js": "application/javascript",
    "highlight.min.js": "application/javascript",
    "github-dark.min.css": "text/css",
}


@app.get("/vendor/{filename}")
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


_STATIC_MEDIA_TYPES = {
    "app.css": "text/css; charset=utf-8",
    "app-bootstrap.js": "application/javascript; charset=utf-8",
    "app-sidebar-gestures.js": "application/javascript; charset=utf-8",
    "app-sidebar.js": "application/javascript; charset=utf-8",
    "app-chat.js": "application/javascript; charset=utf-8",
    "app-pickers.js": "application/javascript; charset=utf-8",
    "app-websocket.js": "application/javascript; charset=utf-8",
    "app-auth.js": "application/javascript; charset=utf-8",
    "app-composer.js": "application/javascript; charset=utf-8",
    "app-picker-events.js": "application/javascript; charset=utf-8",
    "app-profiles.js": "application/javascript; charset=utf-8",
    "app-settings.js": "application/javascript; charset=utf-8",
    "app-image-viewer.js": "application/javascript; charset=utf-8",
    "app-file-tools.js": "application/javascript; charset=utf-8",
    "app.js": "application/javascript; charset=utf-8",
}


@app.get("/static/{filename}")
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


@app.get("/api/health")
async def health():
    return {"ok": True, "acp_connected": manager.connected}


@app.get("/api/push/config")
async def push_config(_token: str = Depends(require_http_auth)):
    return {
        "supported": push_manager.supported,
        "public_key": push_manager.public_key,
        "subscriptions_count": len(push_manager.list_subscriptions()),
    }


@app.post("/api/push/subscribe")
async def push_subscribe(body: PushSubscribeRequest, _token: str = Depends(require_http_auth)):
    if not push_manager.supported:
        raise HTTPException(status_code=503, detail="后端未启用 Web Push 依赖 (pywebpush)")
    try:
        count = push_manager.subscribe({"endpoint": body.endpoint, "keys": body.keys})
        return {"ok": True, "subscriptions_count": count}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/push/unsubscribe")
async def push_unsubscribe(body: PushUnsubscribeRequest, _token: str = Depends(require_http_auth)):
    count = push_manager.unsubscribe(body.endpoint)
    return {"ok": True, "subscriptions_count": count}


@app.get("/api/profiles")
async def get_profiles(
    profile: Optional[str] = None,
    _token: str = Depends(require_http_auth),
):
    active_prof = str(profile or "").strip() or manager.current_profile
    try:
        mgr = manager.get_manager(active_prof)
    except Exception:
        active_prof = manager.current_profile
        mgr = manager.active
    busy_count = sum(1 for rt in mgr._runtimes.values() if rt.is_busy)
    return {
        "current_profile": active_prof,
        "busy_count": busy_count,
        "profiles": manager.get_profiles_info(active_profile=active_prof),
    }


@app.post("/api/profile/switch")
async def switch_profile_api(body: SwitchProfileRequest, _token: str = Depends(require_http_auth)):
    try:
        res = await manager.switch_profile(
            body.profile,
            force=body.force,
            broadcast_all=(body.scope == "global"),
        )
        if not res.get("ok") and res.get("needs_confirm"):
            return JSONResponse(
                status_code=409,
                content={
                    "detail": f"当前账号有 {res['busy_count']} 个任务正在运行，切换账号将中断它们",
                    **res,
                },
            )
        return res
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/profile/reload")
async def reload_profile_api(body: ReloadProfileRequest, _token: str = Depends(require_http_auth)):
    try:
        res = await manager.reload_profile(body.profile, force=body.force)
        if not res.get("ok") and res.get("needs_confirm"):
            return JSONResponse(
                status_code=409,
                content={
                    "detail": f"账号 {body.profile} 当前有 {res['busy_count']} 个任务正在运行，重载将中断它们",
                    **res,
                },
            )
        return res
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/profile/login/start")
async def start_profile_login_api(body: StartProfileLoginRequest, _token: str = Depends(require_http_auth)):
    try:
        return await auth_flow_manager.start_login(
            body.profile,
            backend=body.backend,
            create_if_missing=body.create_if_missing,
            logout_first=body.logout_first,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/profile/login/{flow_id}")
async def get_profile_login_api(flow_id: str, _token: str = Depends(require_http_auth)):
    flow = auth_flow_manager.get_flow(flow_id)
    if flow is None:
        raise HTTPException(status_code=404, detail="登录会话不存在或已过期")
    return flow


@app.post("/api/profile/login/{flow_id}/submit")
async def submit_profile_login_code_api(
    flow_id: str,
    body: SubmitProfileLoginCodeRequest,
    _token: str = Depends(require_http_auth),
):
    try:
        return await auth_flow_manager.submit_code(flow_id, body.code)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/profile/login/{flow_id}/cancel")
async def cancel_profile_login_api(flow_id: str, _token: str = Depends(require_http_auth)):
    try:
        return await auth_flow_manager.cancel_flow(flow_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/quota")
async def get_quota(
    refresh: int = 0,
    profile: Optional[str] = None,
    _token: str = Depends(require_http_auth),
):
    target_profile = str(profile or "").strip() or manager.current_profile
    try:
        target_mgr = manager.get_manager(target_profile)
    except Exception:
        target_profile = manager.current_profile
        target_mgr = manager.active
    now = time.time()
    cached = quota._quota_cache.get("data")
    if (
        cached
        and not refresh
        and cached.get("profile") == target_profile
        and now - quota._quota_cache["ts"] < quota._QUOTA_CACHE_TTL
    ):
        return {**cached, "cached": True}

    # Query only the active engine's own quota (Codex vs Antigravity)
    if config.is_codex_profile(target_profile):
        codex_mgr = target_mgr
        try:
            await codex_mgr.start()
            q = await codex_mgr.fetch_quota()
            cur = {
                "profile": target_profile,
                "email": codex_mgr._current_profile_email(),
                "is_current": True,
                "endpoint": q.get("endpoint", "codex-app-server"),
                "summary": q.get("summary", []),
                "groups": q.get("groups", []),
            }
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Codex 额度查询失败: {str(exc) or type(exc).__name__}")
        data = {
            "profile": target_profile,
            "endpoint": cur.get("endpoint", ""),
            "summary": cur.get("summary", []),
            "groups": cur.get("groups", []),
            "accounts": [cur],
            "fetched_at": now,
        }
        quota._quota_cache.update(ts=now, data=data)
        return {**data, "cached": False}

    profiles = [
        p for p in config.list_agy_profiles(target_profile)
        if p.get("logged_in") and not config.is_codex_profile(p["name"])
    ]
    if not profiles:
        profiles = [{"name": target_profile, "email": target_mgr._current_profile_email(), "is_current": True}]
    # Put current profile first
    profiles.sort(key=lambda p: (not p.get("is_current"), p["name"]))

    async def _query_one(p: Dict[str, Any]) -> Dict[str, Any]:
        pname = p["name"]
        try:
            q = await asyncio.to_thread(quota._fetch_quota_sync, pname)
            return {
                "profile": pname,
                "email": p.get("email"),
                "is_current": pname == target_profile,
                "endpoint": q.get("endpoint"),
                "summary": q.get("summary", []),
                "groups": q.get("groups", []),
            }
        except Exception as exc:
            return {
                "profile": pname,
                "email": p.get("email"),
                "is_current": pname == target_profile,
                "error": str(exc),
                "summary": [],
                "groups": [],
            }

    accounts = await asyncio.gather(*[_query_one(p) for p in profiles])
    cur = next((a for a in accounts if a.get("is_current")), accounts[0])
    if cur.get("error") and all(a.get("error") for a in accounts):
        raise HTTPException(status_code=502, detail=f"余量查询失败: {cur['error']}")

    data = {
        "profile": target_profile,
        "endpoint": cur.get("endpoint", ""),
        "summary": cur.get("summary", []),
        "groups": cur.get("groups", []),
        "accounts": accounts,
        "fetched_at": now,
    }
    quota._quota_cache.update(ts=now, data=data)
    return {**data, "cached": False}


@app.post("/api/push/test")
async def push_test(_token: str = Depends(require_http_auth)):
    if not push_manager.supported:
        raise HTTPException(status_code=503, detail="后端未启用 Web Push")
    res = await push_manager.send_notification(
        title="VibeOne · 推送测试成功",
        body="任务完成、需要权限审批或异常中断时，你将收到系统通知。",
        session_id=manager.session_id or "",
        tag="vibeone-test",
    )
    return {"ok": res["sent"] > 0, **res}


_PASSKEY_CHALLENGES: Dict[str, Dict[str, Any]] = {}
_PASSKEY_CHALLENGE_TTL = 300.0


def _issue_auth_cookie(request: Request, response: Response) -> None:
    is_https = (
        request.url.scheme == "https"
        or request.headers.get("x-forwarded-proto", "").lower() == "https"
    )
    session_token = store.create_auth_session(ttl_seconds=60 * 60 * 24 * 30)
    response.set_cookie(
        key=config.AUTH_COOKIE_NAME,
        value=session_token,
        httponly=True,
        samesite="lax",
        secure=is_https,
        max_age=60 * 60 * 24 * 30,
        path="/",
    )


def _create_passkey_challenge(kind: str, request: Request) -> Tuple[str, str, str]:
    now = time.time()
    expired = [k for k, v in _PASSKEY_CHALLENGES.items() if v["expires_at"] <= now]
    for k in expired:
        _PASSKEY_CHALLENGES.pop(k, None)
    challenge = passkey.b64url_encode(secrets.token_bytes(32))
    rp_id, origin = passkey.resolve_rp_and_origin(request)
    _PASSKEY_CHALLENGES[challenge] = {
        "kind": kind,
        "rp_id": rp_id,
        "origin": origin,
        "expires_at": now + _PASSKEY_CHALLENGE_TTL,
    }
    return challenge, rp_id, origin


def _pop_passkey_challenge(client_data_json_b64: str, expected_kind: str) -> Tuple[str, str, str]:
    try:
        client_data = json.loads(passkey.b64url_decode(client_data_json_b64).decode("utf-8"))
        challenge = str(client_data.get("challenge", ""))
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid clientDataJSON") from exc
    entry = _PASSKEY_CHALLENGES.pop(challenge, None)
    if not entry or entry["expires_at"] <= time.time() or entry["kind"] != expected_kind:
        raise HTTPException(status_code=400, detail="Passkey challenge expired or invalid")
    return challenge, entry["rp_id"], entry["origin"]


@app.post("/api/auth")
async def login(body: AuthRequest, request: Request, response: Response):
    client_ip = _get_client_ip(request)
    _check_auth_rate_limit(client_ip)
    if not _tokens_match(body.token):
        _record_auth_failure(client_ip)
        raise HTTPException(status_code=401, detail="Invalid token")
    _clear_auth_failures(client_ip)
    _issue_auth_cookie(request, response)
    return {"ok": True}


@app.get("/api/auth/passkey/status")
async def passkey_status():
    return {"enabled": store.count_passkeys() > 0, "count": store.count_passkeys()}


@app.post("/api/auth/passkey/login/options")
async def passkey_login_options(request: Request):
    client_ip = _get_client_ip(request)
    _check_auth_rate_limit(client_ip)
    creds = store.list_passkeys()
    if not creds:
        raise HTTPException(status_code=404, detail="No passkeys registered")
    challenge, rp_id, _ = _create_passkey_challenge("login", request)
    return {
        "challenge": challenge,
        "rpId": rp_id,
        "timeout": 60000,
        "userVerification": "preferred",
        "allowCredentials": [{"type": "public-key", "id": c["credential_id"]} for c in creds],
    }


@app.post("/api/auth/passkey/login/verify")
async def passkey_login_verify(body: PasskeyLoginVerifyRequest, request: Request, response: Response):
    client_ip = _get_client_ip(request)
    _check_auth_rate_limit(client_ip)
    challenge, rp_id, origin = _pop_passkey_challenge(body.clientDataJSON, "login")
    cred = store.get_passkey(body.id)
    if not cred:
        _record_auth_failure(client_ip)
        raise HTTPException(status_code=401, detail="Unknown passkey")
    try:
        new_count = passkey.verify_authentication(
            authenticator_data_b64=body.authenticatorData,
            client_data_json_b64=body.clientDataJSON,
            signature_b64=body.signature,
            expected_challenge=challenge,
            expected_rp_id=rp_id,
            expected_origin=origin,
            public_key_pem=cred["public_key_pem"],
            alg=cred["alg"],
            stored_sign_count=cred["sign_count"],
        )
    except Exception as exc:
        _record_auth_failure(client_ip)
        raise HTTPException(status_code=401, detail=f"Passkey verification failed: {exc}") from exc
    _clear_auth_failures(client_ip)
    store.update_passkey_usage(body.id, new_count)
    _issue_auth_cookie(request, response)
    return {"ok": True}


@app.post("/api/auth/passkey/register/options")
async def passkey_register_options(request: Request, _token: str = Depends(require_http_auth)):
    challenge, rp_id, _ = _create_passkey_challenge("register", request)
    creds = store.list_passkeys()
    return {
        "challenge": challenge,
        "rp": {"name": "VibeOne", "id": rp_id},
        "user": {
            "id": passkey.b64url_encode(b"vibeone-owner"),
            "name": "vibeone",
            "displayName": "VibeOne",
        },
        "pubKeyCredParams": [
            {"type": "public-key", "alg": -7},
            {"type": "public-key", "alg": -257},
        ],
        "timeout": 60000,
        "attestation": "none",
        "authenticatorSelection": {
            "residentKey": "preferred",
            "userVerification": "preferred",
        },
        "excludeCredentials": [{"type": "public-key", "id": c["credential_id"]} for c in creds],
    }


@app.post("/api/auth/passkey/register/verify")
async def passkey_register_verify(
    body: PasskeyRegisterVerifyRequest,
    request: Request,
    _token: str = Depends(require_http_auth),
):
    challenge, rp_id, origin = _pop_passkey_challenge(body.clientDataJSON, "register")
    try:
        verified = passkey.verify_registration(
            attestation_object_b64=body.attestationObject,
            client_data_json_b64=body.clientDataJSON,
            expected_challenge=challenge,
            expected_rp_id=rp_id,
            expected_origin=origin,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Passkey registration failed: {exc}") from exc
    store.add_passkey(
        credential_id=verified["credential_id"],
        public_key_pem=verified["public_key_pem"],
        alg=verified["alg"],
        sign_count=verified["sign_count"],
        label=body.label or "iCloud / Device Passkey",
    )
    return {"ok": True, "count": store.count_passkeys()}


@app.get("/api/auth/passkeys")
async def list_passkeys_api(_token: str = Depends(require_http_auth)):
    return {"passkeys": store.list_passkeys()}


@app.delete("/api/auth/passkeys/{credential_id}")
async def delete_passkey_api(credential_id: str, _token: str = Depends(require_http_auth)):
    deleted = store.delete_passkey(credential_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Passkey not found")
    return {"ok": True, "count": store.count_passkeys()}


@app.get("/api/auth/check")
async def auth_check(_token: str = Depends(require_http_auth)):
    return {"ok": True}


@app.get("/api/auth/token")
async def get_auth_token(_token: str = Depends(require_http_auth)):
    return JSONResponse(
        content={"token": config.VIBEONE_WEB_TOKEN},
        headers={"Cache-Control": "no-store"},
    )


@app.post("/api/logout")
async def logout(request: Request, response: Response):
    cookie_token = request.cookies.get(config.AUTH_COOKIE_NAME) or request.cookies.get(config.LEGACY_AUTH_COOKIE_NAME)
    if cookie_token:
        store.revoke_auth_session(cookie_token)
    response.delete_cookie(config.AUTH_COOKIE_NAME, path="/")
    response.delete_cookie(config.LEGACY_AUTH_COOKIE_NAME, path="/")
    return {"ok": True}


@app.post("/api/upload")
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

    content = await file.read()
    if len(content) > config.MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Image exceeds maximum upload size")
    if not content:
        raise HTTPException(status_code=400, detail="Empty file")

    safe_name = f"{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}{ext}"
    dest = (config.UPLOADS_DIR / safe_name).resolve()
    dest.write_bytes(content)

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
                content = dest.read_bytes()
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
        "size": len(content),
    }


@app.get("/uploads/{filename}")
async def serve_upload(filename: str, _token: str = Depends(require_http_auth)):
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")
    path = (config.UPLOADS_DIR / filename).resolve()
    if not path.exists() or path.parent != config.UPLOADS_DIR.resolve():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(path)


async def _run_git(args: List[str], cwd: str) -> Tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        "git",
        *args,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    return (
        proc.returncode or 0,
        out.decode("utf-8", errors="replace"),
        err.decode("utf-8", errors="replace"),
    )


_MAX_UNTRACKED_DIFF_FILES = 50
_MAX_UNTRACKED_FILE_BYTES = 256 * 1024
_MAX_UNTRACKED_TOTAL_BYTES = 1024 * 1024


async def _build_untracked_diff(repo_root: str) -> str:
    code, out, _ = await _run_git(["ls-files", "--others", "--exclude-standard", "-z"], repo_root)
    if code != 0 or not out:
        return ""
    rel_paths = [p for p in out.split("\x00") if p]
    if not rel_paths:
        return ""

    root_path = Path(repo_root).resolve()
    chunks: List[str] = []
    total_bytes = 0

    for rel in rel_paths[:_MAX_UNTRACKED_DIFF_FILES]:
        fpath = (root_path / rel).resolve()
        try:
            fpath.relative_to(root_path)
        except ValueError:
            continue
        if not fpath.is_file():
            continue

        try:
            st = fpath.stat()
            mode = "100755" if (st.st_mode & 0o111) else "100644"
            size = st.st_size
        except OSError:
            continue

        header = [
            f"diff --git a/{rel} b/{rel}",
            f"new file mode {mode}",
        ]

        if size > _MAX_UNTRACKED_FILE_BYTES or total_bytes + size > _MAX_UNTRACKED_TOTAL_BYTES:
            header.append(f"Binary files /dev/null and b/{rel} differ")
            chunks.append("\n".join(header))
            continue

        try:
            raw = fpath.read_bytes()
        except OSError:
            continue

        if b"\x00" in raw[:8192]:
            header.append(f"Binary files /dev/null and b/{rel} differ")
            chunks.append("\n".join(header))
            continue

        total_bytes += len(raw)
        header.append("--- /dev/null")
        header.append(f"+++ b/{rel}")
        if raw:
            text = raw.decode("utf-8", errors="replace")
            lines = text.splitlines()
            if lines:
                header.append(f"@@ -0,0 +1,{len(lines)} @@")
                header.extend(f"+{line}" for line in lines)
                if not text.endswith("\n"):
                    header.append("\\ No newline at end of file")
        chunks.append("\n".join(header))

    return "\n".join(chunks)


@app.get("/api/diff")
async def get_git_diff(
    workdir: Optional[str] = None,
    profile: Optional[str] = None,
    session_id: Optional[str] = None,
    _token: str = Depends(require_http_auth),
):
    target_workdir = ""
    if workdir and os.path.isdir(os.path.abspath(os.path.expanduser(workdir))):
        target_workdir = os.path.abspath(os.path.expanduser(workdir))
    if not target_workdir:
        try:
            mgr = manager.get_manager(profile)
        except Exception:
            mgr = manager.active
        if session_id:
            rt = mgr._runtimes.get(session_id)
            if rt and rt.workdir:
                target_workdir = rt.workdir
            else:
                row = store.get_session(session_id)
                if row and row.get("workdir"):
                    target_workdir = row["workdir"]
        if not target_workdir:
            target_workdir = mgr.workdir
    code, top_out, _ = await _run_git(["rev-parse", "--show-toplevel"], target_workdir)
    if code != 0:
        return JSONResponse(
            {
                "is_git_repo": False,
                "workdir": target_workdir,
                "branch": "",
                "status": "",
                "stat": "",
                "diff": "",
                "staged_diff": "",
                "message": "当前工作目录不是 Git 仓库",
            }
        )

    repo_root = top_out.strip()
    (
        (_, branch, _),
        (_, status_txt, _),
        (_, stat_txt, _),
        (_, diff_txt, _),
        (_, staged_txt, _),
        untracked_diff,
    ) = await asyncio.gather(
        _run_git(["rev-parse", "--abbrev-ref", "HEAD"], repo_root),
        _run_git(["status", "--short"], repo_root),
        _run_git(["diff", "--stat"], repo_root),
        _run_git(["diff"], repo_root),
        _run_git(["diff", "--cached"], repo_root),
        _build_untracked_diff(repo_root),
    )
    combined_diff = diff_txt
    if untracked_diff:
        combined_diff = (
            diff_txt.rstrip("\n") + "\n" + untracked_diff
            if diff_txt.strip()
            else untracked_diff
        )
    return {
        "is_git_repo": True,
        "workdir": repo_root,
        "branch": branch.strip(),
        "status": status_txt,
        "stat": stat_txt,
        "diff": combined_diff,
        "staged_diff": staged_txt,
    }


@app.get("/api/fs/dirs")
async def list_directories(
    path: Optional[str] = None,
    show_hidden: bool = False,
    profile: Optional[str] = None,
    _token: str = Depends(require_http_auth),
):
    try:
        mgr = manager.get_manager(profile)
    except Exception:
        mgr = manager.active
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


@app.post("/api/fs/mkdir")
async def create_directory(body: MkdirRequest, _token: str = Depends(require_http_auth)):
    name = body.name.strip()
    if not name or name in (".", "..") or "/" in name or "\\" in name:
        raise HTTPException(status_code=400, detail="文件夹名称不合法")
    try:
        parent = manager._normalize_workdir(body.parent_path)
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


def _resolve_preview_file_path(
    raw_path: str,
    workdir: Optional[str] = None,
    profile: Optional[str] = None,
    session_id: Optional[str] = None,
) -> Tuple[str, str]:
    """Resolve an absolute, ~, or workdir-relative file path for preview."""
    cleaned = str(raw_path or "").strip()
    if not cleaned:
        raise HTTPException(status_code=400, detail="缺少文件路径")

    base_workdir = ""
    if workdir and os.path.isdir(os.path.abspath(os.path.expanduser(workdir))):
        base_workdir = os.path.abspath(os.path.expanduser(workdir))
    if not base_workdir:
        try:
            mgr = manager.get_manager(profile)
        except Exception:
            mgr = manager.active
        if session_id:
            rt = mgr._runtimes.get(session_id)
            if rt and rt.workdir:
                base_workdir = rt.workdir
            else:
                row = store.get_session(session_id)
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


@app.get("/api/fs/file")
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


@app.get("/api/fs/file/raw")
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


@app.get("/api/sessions")
async def get_sessions(
    profile: Optional[str] = None,
    session_id: Optional[str] = None,
    _token: str = Depends(require_http_auth),
):
    try:
        mgr = manager.get_manager(profile)
    except Exception:
        mgr = manager.active
    cur_sid = str(session_id or "").strip() or mgr.session_id
    all_sessions = await mgr.list_sessions(active_session_id=cur_sid, include_archived=True)
    sessions = [s for s in all_sessions if not s.get("is_archived")]
    archived_sessions = [s for s in all_sessions if s.get("is_archived")]
    archived_sessions.sort(
        key=lambda x: (-(float(x.get("archived_at") or 0.0)), -(float(x.get("updated_at") or 0.0)))
    )
    cur_rt = mgr._runtimes.get(cur_sid) if cur_sid else None
    cur_workdir = cur_rt.workdir if cur_rt else mgr.workdir
    return {
        "current_profile": mgr.current_profile,
        "current_session_id": cur_sid,
        "current_workdir": cur_workdir,
        "sessions": sessions,
        "archived_sessions": archived_sessions,
        "shortcuts": mgr.get_shortcut_dirs(),
    }


@app.get("/api/sessions/search")
async def search_sessions_api(
    q: str = Query(default="", max_length=200),
    profile: Optional[str] = None,
    session_id: Optional[str] = None,
    scope: str = Query(default="active"),
    limit: int = Query(default=50, ge=1, le=200),
    _token: str = Depends(require_http_auth),
):
    requested_profile = str(profile or "").strip()
    if requested_profile:
        try:
            mgr = manager.get_manager(requested_profile)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc))
    else:
        mgr = manager.active

    cur_sid = str(session_id or "").strip() or mgr.session_id
    query_str = str(q or "").strip()
    if not query_str:
        return {
            "current_profile": mgr.current_profile,
            "query": "",
            "scope": scope,
            "results": [],
        }

    results = await mgr.search_sessions(
        query_str,
        scope=scope,
        active_session_id=cur_sid,
        limit=limit,
    )
    return {
        "current_profile": mgr.current_profile,
        "query": query_str,
        "scope": scope,
        "results": results,
    }


@app.get("/api/sessions/{session_id}/items")
async def get_session_items_page(
    session_id: str,
    before_id: Optional[str] = None,
    before_offset: Optional[int] = None,
    limit: int = 200,
    profile: Optional[str] = None,
    _token: str = Depends(require_http_auth),
):
    try:
        mgr = manager.get_manager(profile)
    except Exception:
        mgr = manager.active
    return mgr.get_items_page(
        session_id=session_id,
        before_id=before_id,
        before_offset=before_offset,
        limit=limit,
    )


@app.get("/api/sessions/{session_id}/items/{item_id}")
async def get_session_item_detail(
    session_id: str,
    item_id: str,
    profile: Optional[str] = None,
    _token: str = Depends(require_http_auth),
):
    try:
        mgr = manager.get_manager(profile)
    except Exception:
        mgr = manager.active
    item = mgr.get_item_detail(session_id=session_id, item_id=item_id)
    if not item:
        raise HTTPException(status_code=404, detail="未找到该记录详情")
    return {"item": item}


@app.post("/api/session/new")
async def create_new_session_api(
    body: Optional[NewSessionRequest] = None,
    _token: str = Depends(require_http_auth),
):
    try:
        mgr = manager.get_manager(body.profile if body else None)
        workdir = body.workdir if body else None
        new_sid = await mgr.reset_session(workdir=workdir)
        return {"ok": True, "session_id": new_sid or mgr.session_id, "workdir": mgr.workdir}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/session/switch")
async def switch_session_api(
    body: SwitchSessionRequest,
    _token: str = Depends(require_http_auth),
):
    try:
        mgr = manager.get_manager(body.profile)
        loaded_sid = await mgr.switch_session(body.session_id, workdir=body.workdir, title=body.title)
        rt = mgr._runtimes.get(loaded_sid)
        return {
            "ok": True,
            "session_id": loaded_sid,
            "workdir": rt.workdir if rt else mgr.workdir,
            "title": rt.title if rt else mgr.session_title,
        }
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/session/sync")
async def sync_session_api(
    body: Optional[SyncSessionRequest] = None,
    _token: str = Depends(require_http_auth),
):
    try:
        mgr = manager.get_manager(body.profile if body else None)
        sid = body.session_id if body else None
        force = body.force if body is not None else True
        return await mgr.sync_session_history(session_id=sid, force=force)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/session/remove")
async def remove_session_api(
    body: RemoveSessionRequest,
    _token: str = Depends(require_http_auth),
):
    try:
        mgr = manager.get_manager(body.profile)
        return await mgr.remove_session(body.session_id)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/sessions/archive")
async def archive_sessions_api(
    body: ArchiveSessionsRequest,
    _token: str = Depends(require_http_auth),
):
    try:
        mgr = manager.get_manager(body.profile)
        return await mgr.archive_sessions(body.session_ids)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/sessions/unarchive")
async def unarchive_sessions_api(
    body: ArchiveSessionsRequest,
    _token: str = Depends(require_http_auth),
):
    try:
        mgr = manager.get_manager(body.profile)
        return await mgr.unarchive_sessions(body.session_ids)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/workspace/remove")
async def remove_workspace_api(
    body: RemoveWorkdirRequest,
    _token: str = Depends(require_http_auth),
):
    try:
        mgr = manager.get_manager(body.profile)
        return await mgr.remove_workdir(body.workdir)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/session/reset")
async def reset_session(_token: str = Depends(require_http_auth)):
    await manager.reset_session()
    return {"ok": True, "session_id": manager.session_id, "workdir": manager.workdir}


@app.post("/api/session/mode")
async def change_mode(body: ModeRequest, _token: str = Depends(require_http_auth)):
    try:
        mgr = manager.get_manager(body.profile)
        await mgr.set_mode(body.mode_id, sid=body.session_id)
        return {"ok": True, "current_mode": body.mode_id}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/session/model")
async def change_model(body: ModelRequest, _token: str = Depends(require_http_auth)):
    try:
        mgr = manager.get_manager(body.profile)
        await mgr.set_model(body.model_id, sid=body.session_id)
        return {"ok": True, "current_model": body.model_id}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    if not verify_ws_auth(ws):
        await ws.close(code=4401, reason="Unauthorized")
        return

    await ws.accept()
    manager.clients.add(ws)
    manager._client_visible[ws] = True

    def _current_view():
        prof, sid = manager.get_client_view(ws)
        try:
            mgr = manager.get_manager(prof)
        except Exception:
            mgr = manager.active
        return mgr, (sid or mgr.session_id)

    try:
        # Resolve this tab's initial (profile, session_id) without disturbing other connected clients
        init_profile = str(ws.query_params.get("profile") or "").strip()
        init_session = str(ws.query_params.get("session") or "").strip() or None
        mgr = manager.active
        if init_profile and init_profile != mgr.current_profile:
            try:
                mgr, _ = await manager.ensure_profile_ready(init_profile)
            except Exception as exc:
                logger.warning("WS init profile %s failed (%s), falling back to %s", init_profile, exc, manager.current_profile)
                mgr = manager.active
        view_sid = mgr.session_id
        if init_session:
            if init_session == mgr.session_id:
                view_sid = init_session
            else:
                try:
                    view_sid = await mgr.switch_session(
                        init_session,
                        broadcast_all=False,
                        current_sid=None,
                        exclude_ws=ws,
                    )
                except Exception as exc:
                    logger.warning("WS init session %s failed (%s), falling back to %s", init_session, exc, mgr.session_id)
                    view_sid = mgr.session_id
        manager.set_client_view(ws, mgr.current_profile, view_sid)

        # Immediately push full snapshot for THIS client's view
        await ws.send_text(
            json.dumps({"type": "snapshot", "data": mgr.get_snapshot(sid=view_sid)}, ensure_ascii=False)
        )

        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            action = msg.get("type")
            mgr, cur_sid = _current_view()
            if action == "ping":
                manager.clients.add(ws)
                await ws.send_text(json.dumps({"type": "pong"}))
            elif action == "client_visibility":
                manager.clients.add(ws)
                manager._client_visible[ws] = bool(msg.get("visible", True))
            elif action == "request_snapshot":
                manager.clients.add(ws)
                await ws.send_text(
                    json.dumps({"type": "snapshot", "data": mgr.get_snapshot(sid=cur_sid)}, ensure_ascii=False)
                )
            elif action == "switch_profile":
                target_profile = str(msg.get("profile") or "").strip()
                target_sid = str(msg.get("session_id") or "").strip() or None
                if target_profile:
                    try:
                        target_mgr, _ = await manager.ensure_profile_ready(target_profile)
                        if target_sid:
                            try:
                                target_sid = await target_mgr.switch_session(
                                    target_sid,
                                    broadcast_all=False,
                                    current_sid=None,
                                    exclude_ws=ws,
                                )
                            except Exception:
                                target_sid = target_mgr.session_id
                        else:
                            target_sid = target_mgr.session_id
                        manager.set_client_view(ws, target_mgr.current_profile, target_sid)
                        await ws.send_text(
                            json.dumps(
                                {"type": "snapshot", "data": target_mgr.get_snapshot(sid=target_sid)},
                                ensure_ascii=False,
                            )
                        )
                        await ws.send_text(json.dumps({"type": "sessions_changed"}))
                    except Exception as exc:
                        await ws.send_text(
                            json.dumps({"type": "toast", "data": {"level": "error", "message": str(exc)}}, ensure_ascii=False)
                        )
            elif action == "prompt":
                prompt_text = str(msg.get("text", "")).strip()
                display_text = msg.get("display_text")
                images = msg.get("images") or []
                if not prompt_text and not images:
                    continue
                try:
                    if config.is_codex_profile(mgr.current_profile):
                        res = await mgr.send_prompt(
                            text=prompt_text,
                            display_text=display_text,
                            images=images,
                            sid=cur_sid,
                            origin_ws=ws,
                        )
                    else:
                        res = await mgr.send_prompt(
                            text=prompt_text,
                            display_text=display_text,
                            images=images,
                            sid=cur_sid,
                        )
                    if res.get("queued"):
                        await ws.send_text(
                            json.dumps(
                                {
                                    "type": "toast",
                                    "data": {"level": "info", "message": f"已加入待发送队列（第 {res.get('queue_len', 1)} 条）"},
                                },
                                ensure_ascii=False,
                            )
                        )
                except Exception as exc:
                    await ws.send_text(
                        json.dumps({"type": "toast", "data": {"level": "error", "message": str(exc)}}, ensure_ascii=False)
                    )
            elif action == "queue_remove":
                qid = str(msg.get("queue_id", "")).strip()
                if qid:
                    await mgr.remove_queued_prompt(qid, sid=cur_sid)
            elif action == "queue_clear":
                await mgr.clear_queued_prompts(sid=cur_sid)
            elif action == "queue_run_next":
                await mgr.run_next_queued_prompt(sid=cur_sid)
            elif action == "queue_steer":
                qid = str(msg.get("queue_id", "")).strip()
                if qid:
                    ok = await mgr.steer_queued_prompt(qid, sid=cur_sid)
                    if ok:
                        await ws.send_text(
                            json.dumps(
                                {"type": "toast", "data": {"level": "info", "message": "⚡ 正在插队立即发送..."}},
                                ensure_ascii=False,
                            )
                        )
            elif action == "permission_response":
                key = str(msg.get("key", ""))
                option_id = msg.get("option_id")
                cancelled = bool(msg.get("cancelled", False))
                await mgr.respond_permission(key, option_id, cancelled=cancelled)
            elif action == "cancel":
                await mgr.cancel_current_turn(sid=cur_sid)
            elif action == "btw_prompt":
                question = str(msg.get("question", "")).strip()
                btw_id = str(msg.get("btw_id", "")).strip() or None
                if not question:
                    continue
                try:
                    await mgr.send_btw_prompt(
                        question=question,
                        btw_id=btw_id,
                        sid=cur_sid,
                        origin_ws=ws,
                    )
                except Exception as exc:
                    await ws.send_text(
                        json.dumps({"type": "toast", "data": {"level": "error", "message": str(exc)}}, ensure_ascii=False)
                    )
            elif action == "btw_cancel":
                btw_id = str(msg.get("btw_id", "")).strip()
                if btw_id:
                    await mgr.cancel_btw(btw_id)
            elif action in ("reset", "new_session"):
                workdir = msg.get("workdir")
                try:
                    new_sid = await mgr.reset_session(
                        workdir=workdir,
                        broadcast_all=False,
                        current_sid=cur_sid,
                        exclude_ws=ws,
                    )
                    manager.set_client_view(ws, mgr.current_profile, new_sid)
                    rt = mgr._runtimes.get(new_sid) if new_sid else None
                    dir_path = rt.workdir if rt else mgr.workdir
                    dir_name = Path(dir_path).name or dir_path
                    await ws.send_text(
                        json.dumps({"type": "snapshot", "data": mgr.get_snapshot(sid=new_sid)}, ensure_ascii=False)
                    )
                    await ws.send_text(
                        json.dumps(
                            {"type": "toast", "data": {"level": "info", "message": f"已在 {dir_name} 创建新会话"}},
                            ensure_ascii=False,
                        )
                    )
                except Exception as exc:
                    await ws.send_text(
                        json.dumps({"type": "toast", "data": {"level": "error", "message": str(exc)}}, ensure_ascii=False)
                    )
            elif action == "switch_session":
                sid = str(msg.get("session_id", "")).strip()
                workdir = msg.get("workdir")
                title = msg.get("title")
                if sid:
                    try:
                        loaded_sid = await mgr.switch_session(
                            sid,
                            workdir=workdir,
                            title=title,
                            broadcast_all=False,
                            current_sid=cur_sid,
                            exclude_ws=ws,
                        )
                        manager.set_client_view(ws, mgr.current_profile, loaded_sid)
                        rt = mgr._runtimes.get(loaded_sid)
                        dir_path = rt.workdir if rt else mgr.workdir
                        dir_name = Path(dir_path).name or dir_path
                        sess_title = rt.title if rt else mgr.session_title
                        await ws.send_text(
                            json.dumps({"type": "snapshot", "data": mgr.get_snapshot(sid=loaded_sid)}, ensure_ascii=False)
                        )
                        await ws.send_text(
                            json.dumps(
                                {"type": "toast", "data": {"level": "info", "message": f"已切换到 {dir_name} · {sess_title}"}},
                                ensure_ascii=False,
                            )
                        )
                    except Exception as exc:
                        await ws.send_text(
                            json.dumps({"type": "toast", "data": {"level": "error", "message": str(exc)}}, ensure_ascii=False)
                        )
            elif action == "remove_session":
                sid = str(msg.get("session_id", "")).strip()
                if sid:
                    try:
                        await mgr.remove_session(sid)
                        await ws.send_text(
                            json.dumps(
                                {"type": "toast", "data": {"level": "info", "message": "已从侧边栏移除该会话"}},
                                ensure_ascii=False,
                            )
                        )
                    except Exception as exc:
                        await ws.send_text(
                            json.dumps({"type": "toast", "data": {"level": "error", "message": str(exc)}}, ensure_ascii=False)
                        )
            elif action == "remove_workdir":
                workdir = str(msg.get("workdir", "")).strip()
                if workdir:
                    try:
                        res = await mgr.remove_workdir(workdir)
                        dir_name = Path(workdir).name or workdir
                        await ws.send_text(
                            json.dumps(
                                {"type": "toast", "data": {"level": "info", "message": f"已移除目录「{dir_name}」（共 {res['removed_count']} 条会话）"}},
                                ensure_ascii=False,
                            )
                        )
                    except Exception as exc:
                        await ws.send_text(
                            json.dumps({"type": "toast", "data": {"level": "error", "message": str(exc)}}, ensure_ascii=False)
                        )
            elif action == "set_mode":
                mode_id = str(msg.get("mode_id", "")).strip()
                if mode_id:
                    try:
                        await mgr.set_mode(mode_id, sid=cur_sid)
                    except Exception as exc:
                        await ws.send_text(
                            json.dumps({"type": "toast", "data": {"level": "error", "message": str(exc)}}, ensure_ascii=False)
                        )
            elif action == "set_model":
                model_id = str(msg.get("model_id", "")).strip()
                if model_id:
                    try:
                        await mgr.set_model(model_id, sid=cur_sid)
                        label = mgr.get_model_display_name(model_id)
                        await ws.send_text(
                            json.dumps(
                                {"type": "toast", "data": {"level": "info", "message": f"已切换模型: {label}"}},
                                ensure_ascii=False,
                            )
                        )
                    except Exception as exc:
                        await ws.send_text(
                            json.dumps({"type": "toast", "data": {"level": "error", "message": str(exc)}}, ensure_ascii=False)
                        )
    except WebSocketDisconnect:
        pass
    finally:
        manager.remove_client(ws)
