"""HTTP/WebSocket authentication, rate limiting, and origin validation helpers."""

import hmac
import time
from typing import Callable, Dict, List, Optional
from urllib.parse import urlparse

from fastapi import Cookie, Header, HTTPException, Request, WebSocket

from . import config

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


def _normalize_host_port(host: str, default_scheme: str = "http") -> str:
    cleaned = (host or "").split(",")[0].strip().lower()
    if not cleaned:
        return ""
    parsed = urlparse(f"{default_scheme}://{cleaned}")
    hostname = (parsed.hostname or "").lower()
    if not hostname:
        return ""
    try:
        port = parsed.port
    except ValueError:
        return ""
    if (default_scheme == "http" and port == 80) or (default_scheme == "https" and port == 443):
        port = None
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"
    return f"{hostname}:{port}" if port is not None else hostname


def verify_ws_origin(ws: WebSocket) -> bool:
    """Validate browser WebSocket Origin header against the target host (CSWSH defense)."""
    raw_origin = (ws.headers.get("origin") or "").strip()
    if not raw_origin:
        return True
    if raw_origin.lower() == "null":
        return False
    parsed = urlparse(raw_origin)
    scheme = (parsed.scheme or "").lower()
    if scheme not in ("http", "https") or not parsed.hostname:
        return False
    origin_host = _normalize_host_port(parsed.netloc, default_scheme=scheme)
    if not origin_host:
        return False

    allowed_hosts = set()
    raw_host = (ws.headers.get("host") or "").strip()
    if raw_host:
        norm = _normalize_host_port(raw_host, default_scheme=scheme)
        if norm:
            allowed_hosts.add(norm)

    peer_host = ws.client.host if ws.client else ""
    if peer_host in _TRUSTED_PROXY_HOSTS:
        fwd_host = (ws.headers.get("x-forwarded-host") or "").strip()
        if fwd_host:
            norm = _normalize_host_port(fwd_host, default_scheme=scheme)
            if norm:
                allowed_hosts.add(norm)

    if not allowed_hosts:
        return False
    return origin_host in allowed_hosts


def make_http_auth_dependency(is_authorized_fn: Callable[[Optional[str]], bool]):
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
        if not is_authorized_fn(token):
            raise HTTPException(status_code=401, detail="Unauthorized: invalid or missing token")
        return token or ""

    return require_http_auth


def verify_ws_auth_with_checker(ws: WebSocket, is_authorized_fn: Callable[[Optional[str]], bool]) -> bool:
    query_token = ws.query_params.get("token")
    if is_authorized_fn(query_token):
        return True
    auth_header = ws.headers.get("authorization")
    x_header = ws.headers.get("x-vibeone-token") or ws.headers.get("x-agy-token")
    cookie_token = ws.cookies.get(config.AUTH_COOKIE_NAME) or ws.cookies.get(config.LEGACY_AUTH_COOKIE_NAME)
    token = extract_token_from_http(auth_header, x_header, cookie_token)
    return is_authorized_fn(token)
