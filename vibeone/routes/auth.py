"""Authentication, login/logout, and WebAuthn passkey HTTP routes."""

import json
import secrets
import time
from typing import Any, Callable, Dict, Tuple

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from .. import auth_http, config, passkey
from ..schemas import (
    AuthRequest,
    PasskeyLoginVerifyRequest,
    PasskeyRegisterVerifyRequest,
)

_PASSKEY_CHALLENGES: Dict[str, Dict[str, Any]] = {}
_PASSKEY_CHALLENGE_TTL = 300.0


def build_auth_router(
    require_http_auth: Callable[..., Any],
    get_store: Callable[[], Any],
) -> Tuple[APIRouter, Dict[str, Any]]:
    router = APIRouter()

    def _issue_auth_cookie(request: Request, response: Response) -> None:
        is_https = (
            request.url.scheme == "https"
            or request.headers.get("x-forwarded-proto", "").lower() == "https"
        )
        session_token = get_store().create_auth_session(ttl_seconds=60 * 60 * 24 * 30)
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

    @router.post("/api/auth")
    async def login(body: AuthRequest, request: Request, response: Response):
        client_ip = auth_http._get_client_ip(request)
        auth_http._check_auth_rate_limit(client_ip)
        if not auth_http._tokens_match(body.token):
            auth_http._record_auth_failure(client_ip)
            raise HTTPException(status_code=401, detail="Invalid token")
        auth_http._clear_auth_failures(client_ip)
        _issue_auth_cookie(request, response)
        return {"ok": True}

    @router.get("/api/auth/passkey/status")
    async def passkey_status():
        st = get_store()
        return {"enabled": st.count_passkeys() > 0, "count": st.count_passkeys()}

    @router.post("/api/auth/passkey/login/options")
    async def passkey_login_options(request: Request):
        client_ip = auth_http._get_client_ip(request)
        auth_http._check_auth_rate_limit(client_ip)
        creds = get_store().list_passkeys()
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

    @router.post("/api/auth/passkey/login/verify")
    async def passkey_login_verify(body: PasskeyLoginVerifyRequest, request: Request, response: Response):
        client_ip = auth_http._get_client_ip(request)
        auth_http._check_auth_rate_limit(client_ip)
        challenge, rp_id, origin = _pop_passkey_challenge(body.clientDataJSON, "login")
        st = get_store()
        cred = st.get_passkey(body.id)
        if not cred:
            auth_http._record_auth_failure(client_ip)
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
            auth_http._record_auth_failure(client_ip)
            raise HTTPException(status_code=401, detail=f"Passkey verification failed: {exc}") from exc
        auth_http._clear_auth_failures(client_ip)
        st.update_passkey_usage(body.id, new_count)
        _issue_auth_cookie(request, response)
        return {"ok": True}

    @router.post("/api/auth/passkey/register/options")
    async def passkey_register_options(request: Request, _token: str = Depends(require_http_auth)):
        challenge, rp_id, _ = _create_passkey_challenge("register", request)
        creds = get_store().list_passkeys()
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

    @router.post("/api/auth/passkey/register/verify")
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
        st = get_store()
        st.add_passkey(
            credential_id=verified["credential_id"],
            public_key_pem=verified["public_key_pem"],
            alg=verified["alg"],
            sign_count=verified["sign_count"],
            label=body.label or "iCloud / Device Passkey",
        )
        return {"ok": True, "count": st.count_passkeys()}

    @router.get("/api/auth/passkeys")
    async def list_passkeys_api(_token: str = Depends(require_http_auth)):
        return {"passkeys": get_store().list_passkeys()}

    @router.delete("/api/auth/passkeys/{credential_id}")
    async def delete_passkey_api(credential_id: str, _token: str = Depends(require_http_auth)):
        st = get_store()
        deleted = st.delete_passkey(credential_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Passkey not found")
        return {"ok": True, "count": st.count_passkeys()}

    @router.get("/api/auth/check")
    async def auth_check(_token: str = Depends(require_http_auth)):
        return {"ok": True}

    @router.get("/api/auth/token")
    async def get_auth_token(_token: str = Depends(require_http_auth)):
        return JSONResponse(
            content={"token": config.VIBEONE_WEB_TOKEN},
            headers={"Cache-Control": "no-store"},
        )

    @router.post("/api/logout")
    async def logout(request: Request, response: Response):
        cookie_token = request.cookies.get(config.AUTH_COOKIE_NAME) or request.cookies.get(config.LEGACY_AUTH_COOKIE_NAME)
        if cookie_token:
            get_store().revoke_auth_session(cookie_token)
        response.delete_cookie(config.AUTH_COOKIE_NAME, path="/")
        response.delete_cookie(config.LEGACY_AUTH_COOKIE_NAME, path="/")
        return {"ok": True}

    return router, {
        "_issue_auth_cookie": _issue_auth_cookie,
        "_create_passkey_challenge": _create_passkey_challenge,
        "_pop_passkey_challenge": _pop_passkey_challenge,
        "login": login,
        "passkey_status": passkey_status,
        "passkey_login_options": passkey_login_options,
        "passkey_login_verify": passkey_login_verify,
        "passkey_register_options": passkey_register_options,
        "passkey_register_verify": passkey_register_verify,
        "list_passkeys_api": list_passkeys_api,
        "delete_passkey_api": delete_passkey_api,
        "auth_check": auth_check,
        "get_auth_token": get_auth_token,
        "logout": logout,
    }
