"""Session lifecycle, search, items pagination, mode/model, and workspace HTTP routes."""

from typing import Any, Callable, Dict, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Query

from ..schemas import (
    ArchiveSessionsRequest,
    ModeRequest,
    ModelRequest,
    NewSessionRequest,
    RemoveSessionRequest,
    RemoveWorkdirRequest,
    SwitchSessionRequest,
    SyncSessionRequest,
)


def build_sessions_router(
    require_http_auth: Callable[..., Any],
    get_manager: Callable[[], Any],
) -> Tuple[APIRouter, Dict[str, Any]]:
    router = APIRouter()

    @router.get("/api/sessions")
    async def get_sessions(
        profile: Optional[str] = None,
        session_id: Optional[str] = None,
        refresh: int = 0,
        _token: str = Depends(require_http_auth),
    ):
        manager = get_manager()
        try:
            mgr = manager.get_manager(profile)
        except Exception:
            mgr = manager.active
        cur_sid = str(session_id or "").strip() or mgr.session_id
        all_sessions = await mgr.list_sessions(
            active_session_id=cur_sid,
            include_archived=True,
            force_refresh=bool(refresh),
        )
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

    @router.get("/api/sessions/search")
    async def search_sessions_api(
        q: str = Query(default="", max_length=200),
        profile: Optional[str] = None,
        session_id: Optional[str] = None,
        scope: str = Query(default="active"),
        limit: int = Query(default=50, ge=1, le=200),
        _token: str = Depends(require_http_auth),
    ):
        manager = get_manager()
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

    @router.get("/api/sessions/{session_id}/items")
    async def get_session_items_page(
        session_id: str,
        before_id: Optional[str] = None,
        before_offset: Optional[int] = None,
        limit: int = 200,
        profile: Optional[str] = None,
        _token: str = Depends(require_http_auth),
    ):
        manager = get_manager()
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

    @router.get("/api/sessions/{session_id}/items/{item_id}")
    async def get_session_item_detail(
        session_id: str,
        item_id: str,
        profile: Optional[str] = None,
        _token: str = Depends(require_http_auth),
    ):
        manager = get_manager()
        try:
            mgr = manager.get_manager(profile)
        except Exception:
            mgr = manager.active
        item = mgr.get_item_detail(session_id=session_id, item_id=item_id)
        if not item:
            raise HTTPException(status_code=404, detail="未找到该记录详情")
        return {"item": item}

    @router.post("/api/session/new")
    async def create_new_session_api(
        body: Optional[NewSessionRequest] = None,
        _token: str = Depends(require_http_auth),
    ):
        try:
            mgr = get_manager().get_manager(body.profile if body else None)
            workdir = body.workdir if body else None
            new_sid = await mgr.reset_session(workdir=workdir)
            return {"ok": True, "session_id": new_sid or mgr.session_id, "workdir": mgr.workdir}
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @router.post("/api/session/switch")
    async def switch_session_api(
        body: SwitchSessionRequest,
        _token: str = Depends(require_http_auth),
    ):
        try:
            mgr = get_manager().get_manager(body.profile)
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

    @router.post("/api/session/sync")
    async def sync_session_api(
        body: Optional[SyncSessionRequest] = None,
        _token: str = Depends(require_http_auth),
    ):
        try:
            mgr = get_manager().get_manager(body.profile if body else None)
            sid = body.session_id if body else None
            force = body.force if body is not None else True
            return await mgr.sync_session_history(session_id=sid, force=force)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @router.post("/api/session/remove")
    async def remove_session_api(
        body: RemoveSessionRequest,
        _token: str = Depends(require_http_auth),
    ):
        try:
            mgr = get_manager().get_manager(body.profile)
            return await mgr.remove_session(body.session_id)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @router.post("/api/sessions/archive")
    async def archive_sessions_api(
        body: ArchiveSessionsRequest,
        _token: str = Depends(require_http_auth),
    ):
        try:
            mgr = get_manager().get_manager(body.profile)
            return await mgr.archive_sessions(body.session_ids)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @router.post("/api/sessions/unarchive")
    async def unarchive_sessions_api(
        body: ArchiveSessionsRequest,
        _token: str = Depends(require_http_auth),
    ):
        try:
            mgr = get_manager().get_manager(body.profile)
            return await mgr.unarchive_sessions(body.session_ids)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @router.post("/api/workspace/remove")
    async def remove_workspace_api(
        body: RemoveWorkdirRequest,
        _token: str = Depends(require_http_auth),
    ):
        try:
            mgr = get_manager().get_manager(body.profile)
            return await mgr.remove_workdir(body.workdir)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @router.post("/api/session/reset")
    async def reset_session(_token: str = Depends(require_http_auth)):
        manager = get_manager()
        await manager.reset_session()
        return {"ok": True, "session_id": manager.session_id, "workdir": manager.workdir}

    @router.post("/api/session/mode")
    async def change_mode(body: ModeRequest, _token: str = Depends(require_http_auth)):
        try:
            mgr = get_manager().get_manager(body.profile)
            await mgr.set_mode(body.mode_id, sid=body.session_id)
            return {"ok": True, "current_mode": body.mode_id}
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @router.post("/api/session/model")
    async def change_model(body: ModelRequest, _token: str = Depends(require_http_auth)):
        try:
            mgr = get_manager().get_manager(body.profile)
            await mgr.set_model(body.model_id, sid=body.session_id)
            return {"ok": True, "current_model": body.model_id}
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    return router, {
        "get_sessions": get_sessions,
        "search_sessions_api": search_sessions_api,
        "get_session_items_page": get_session_items_page,
        "get_session_item_detail": get_session_item_detail,
        "create_new_session_api": create_new_session_api,
        "switch_session_api": switch_session_api,
        "sync_session_api": sync_session_api,
        "remove_session_api": remove_session_api,
        "archive_sessions_api": archive_sessions_api,
        "unarchive_sessions_api": unarchive_sessions_api,
        "remove_workspace_api": remove_workspace_api,
        "reset_session": reset_session,
        "change_mode": change_mode,
        "change_model": change_model,
    }
