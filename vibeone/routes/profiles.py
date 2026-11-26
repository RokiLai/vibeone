"""Profile management, OAuth login flow, quota, and Web Push HTTP routes."""

import asyncio
import time
from typing import Any, Callable, Dict, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from .. import config, quota
from ..schemas import (
    PushSubscribeRequest,
    PushUnsubscribeRequest,
    ReloadProfileRequest,
    StartProfileLoginRequest,
    SubmitProfileLoginCodeRequest,
    SwitchProfileRequest,
)


def build_profiles_router(
    require_http_auth: Callable[..., Any],
    get_manager: Callable[[], Any],
    get_push_manager: Callable[[], Any],
    get_auth_flow_manager: Callable[[], Any],
) -> Tuple[APIRouter, Dict[str, Any]]:
    router = APIRouter()

    @router.get("/api/push/config")
    async def push_config(_token: str = Depends(require_http_auth)):
        pm = get_push_manager()
        return {
            "supported": pm.supported,
            "public_key": pm.public_key,
            "subscriptions_count": len(pm.list_subscriptions()),
        }

    @router.post("/api/push/subscribe")
    async def push_subscribe(body: PushSubscribeRequest, _token: str = Depends(require_http_auth)):
        pm = get_push_manager()
        if not pm.supported:
            raise HTTPException(status_code=503, detail="后端未启用 Web Push 依赖 (pywebpush)")
        try:
            count = pm.subscribe({"endpoint": body.endpoint, "keys": body.keys})
            return {"ok": True, "subscriptions_count": count}
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @router.post("/api/push/unsubscribe")
    async def push_unsubscribe(body: PushUnsubscribeRequest, _token: str = Depends(require_http_auth)):
        count = get_push_manager().unsubscribe(body.endpoint)
        return {"ok": True, "subscriptions_count": count}

    @router.post("/api/push/test")
    async def push_test(_token: str = Depends(require_http_auth)):
        pm = get_push_manager()
        if not pm.supported:
            raise HTTPException(status_code=503, detail="后端未启用 Web Push")
        res = await pm.send_notification(
            title="VibeOne · 推送测试成功",
            body="任务完成、需要权限审批或异常中断时，你将收到系统通知。",
            session_id=get_manager().session_id or "",
            tag="vibeone-test",
        )
        return {"ok": res["sent"] > 0, **res}

    @router.get("/api/profiles")
    async def get_profiles(
        profile: Optional[str] = None,
        _token: str = Depends(require_http_auth),
    ):
        manager = get_manager()
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

    @router.post("/api/profile/switch")
    async def switch_profile_api(body: SwitchProfileRequest, _token: str = Depends(require_http_auth)):
        try:
            res = await get_manager().switch_profile(
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

    @router.post("/api/profile/reload")
    async def reload_profile_api(body: ReloadProfileRequest, _token: str = Depends(require_http_auth)):
        try:
            res = await get_manager().reload_profile(body.profile, force=body.force)
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

    @router.post("/api/profile/login/start")
    async def start_profile_login_api(body: StartProfileLoginRequest, _token: str = Depends(require_http_auth)):
        try:
            return await get_auth_flow_manager().start_login(
                body.profile,
                backend=body.backend,
                create_if_missing=body.create_if_missing,
                logout_first=body.logout_first,
            )
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @router.get("/api/profile/login/{flow_id}")
    async def get_profile_login_api(flow_id: str, _token: str = Depends(require_http_auth)):
        flow = get_auth_flow_manager().get_flow(flow_id)
        if flow is None:
            raise HTTPException(status_code=404, detail="登录会话不存在或已过期")
        return flow

    @router.post("/api/profile/login/{flow_id}/submit")
    async def submit_profile_login_code_api(
        flow_id: str,
        body: SubmitProfileLoginCodeRequest,
        _token: str = Depends(require_http_auth),
    ):
        try:
            return await get_auth_flow_manager().submit_code(flow_id, body.code)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @router.post("/api/profile/login/{flow_id}/cancel")
    async def cancel_profile_login_api(flow_id: str, _token: str = Depends(require_http_auth)):
        try:
            return await get_auth_flow_manager().cancel_flow(flow_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @router.get("/api/quota")
    async def get_quota(
        refresh: int = 0,
        profile: Optional[str] = None,
        _token: str = Depends(require_http_auth),
    ):
        manager = get_manager()
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

    return router, {
        "push_config": push_config,
        "push_subscribe": push_subscribe,
        "push_unsubscribe": push_unsubscribe,
        "push_test": push_test,
        "get_profiles": get_profiles,
        "switch_profile_api": switch_profile_api,
        "reload_profile_api": reload_profile_api,
        "start_profile_login_api": start_profile_login_api,
        "get_profile_login_api": get_profile_login_api,
        "submit_profile_login_code_api": submit_profile_login_code_api,
        "cancel_profile_login_api": cancel_profile_login_api,
        "get_quota": get_quota,
    }
