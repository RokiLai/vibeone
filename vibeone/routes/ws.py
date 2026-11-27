"""WebSocket endpoint for real-time chat, streaming, and session control."""

import json
import logging
import time
from pathlib import Path
from typing import Any, Callable, Dict, Tuple

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from .. import config
from .telemetry import sanitize_trace_token

logger = config.logger
telemetry_logger = logging.getLogger("vibeone.telemetry")


def build_ws_router(
    get_manager: Callable[[], Any],
    verify_ws_origin_fn: Callable[[WebSocket], bool],
    verify_ws_auth_fn: Callable[[WebSocket], bool],
) -> Tuple[APIRouter, Dict[str, Any]]:
    router = APIRouter()

    @router.websocket("/ws")
    async def websocket_endpoint(ws: WebSocket):
        if not verify_ws_origin_fn(ws):
            await ws.close(code=4403, reason="Forbidden origin")
            return
        if not verify_ws_auth_fn(ws):
            await ws.close(code=4401, reason="Unauthorized")
            return

        manager = get_manager()
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

        async def _send_json(payload_obj: Dict[str, Any]) -> bool:
            return await manager.send_ws_text(ws, json.dumps(payload_obj, ensure_ascii=False))

        try:
            init_profile = str(ws.query_params.get("profile") or "").strip()
            init_session = str(ws.query_params.get("session") or "").strip() or None
            client_cid = sanitize_trace_token(ws.query_params.get("cid")) or "-"
            client_page = sanitize_trace_token(ws.query_params.get("page_id")) or "-"
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

            if not await _send_json({"type": "snapshot", "data": mgr.get_snapshot(sid=view_sid)}):
                return

            while True:
                raw = await ws.receive_text()
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                action = msg.get("type")
                trace_id = sanitize_trace_token(msg.get("trace_id")) or "-"
                action_started = time.perf_counter()
                mgr, cur_sid = _current_view()
                if action == "ping":
                    manager.clients.add(ws)
                    if not await _send_json({"type": "pong"}):
                        break
                elif action == "client_visibility":
                    manager.clients.add(ws)
                    manager._client_visible[ws] = bool(msg.get("visible", True))
                elif action == "request_snapshot":
                    manager.clients.add(ws)
                    if not await _send_json({"type": "snapshot", "data": mgr.get_snapshot(sid=cur_sid)}):
                        break
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
                            await _send_json({"type": "snapshot", "data": target_mgr.get_snapshot(sid=target_sid)})
                        except Exception as exc:
                            telemetry_logger.warning(
                                "ws_action_error action=%s trace=%s cid=%s page=%s sid=%s err=%s",
                                action,
                                trace_id,
                                client_cid,
                                client_page,
                                cur_sid or "-",
                                exc,
                            )
                            await _send_json({"type": "toast", "data": {"level": "error", "message": str(exc)}})
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
                            await _send_json(
                                {
                                    "type": "toast",
                                    "data": {"level": "info", "message": f"已加入待发送队列（第 {res.get('queue_len', 1)} 条）"},
                                }
                            )
                    except Exception as exc:
                        await _send_json({"type": "toast", "data": {"level": "error", "message": str(exc)}})
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
                            await _send_json({"type": "toast", "data": {"level": "info", "message": "⚡ 正在插队立即发送..."}})
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
                        await _send_json({"type": "toast", "data": {"level": "error", "message": str(exc)}})
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
                        await _send_json({"type": "snapshot", "data": mgr.get_snapshot(sid=new_sid)})
                        await _send_json({"type": "toast", "data": {"level": "info", "message": f"已在 {dir_name} 创建新会话"}})
                    except Exception as exc:
                        await _send_json({"type": "toast", "data": {"level": "error", "message": str(exc)}})
                elif action == "switch_session":
                    sid = str(msg.get("session_id", "")).strip()
                    workdir = msg.get("workdir")
                    title = msg.get("title")
                    if sid:
                        try:
                            if hasattr(mgr, "can_fast_switch_local") and mgr.can_fast_switch_local(sid):
                                mgr._get_or_create_runtime(sid, workdir=workdir, title=title)
                                manager.set_client_view(ws, mgr.current_profile, sid)
                                await _send_json({"type": "snapshot", "data": mgr.get_snapshot(sid=sid)})
                                mgr.schedule_background_resume(
                                    sid,
                                    workdir=workdir,
                                    title=title,
                                    current_sid=cur_sid,
                                    exclude_ws=ws,
                                )
                            else:
                                loaded_sid = await mgr.switch_session(
                                    sid,
                                    workdir=workdir,
                                    title=title,
                                    broadcast_all=False,
                                    current_sid=cur_sid,
                                    exclude_ws=ws,
                                )
                                manager.set_client_view(ws, mgr.current_profile, loaded_sid)
                                await _send_json({"type": "snapshot", "data": mgr.get_snapshot(sid=loaded_sid)})
                        except Exception as exc:
                            telemetry_logger.warning(
                                "ws_action_error action=%s trace=%s cid=%s page=%s sid=%s err=%s",
                                action,
                                trace_id,
                                client_cid,
                                client_page,
                                cur_sid or "-",
                                exc,
                            )
                            await _send_json({"type": "toast", "data": {"level": "error", "message": str(exc)}})
                elif action == "remove_session":
                    sid = str(msg.get("session_id", "")).strip()
                    if sid:
                        try:
                            await mgr.remove_session(sid)
                            await _send_json({"type": "toast", "data": {"level": "info", "message": "已从侧边栏移除该会话"}})
                        except Exception as exc:
                            telemetry_logger.warning(
                                "ws_action_error action=%s trace=%s cid=%s page=%s sid=%s err=%s",
                                action,
                                trace_id,
                                client_cid,
                                client_page,
                                cur_sid or "-",
                                exc,
                            )
                            await _send_json({"type": "toast", "data": {"level": "error", "message": str(exc)}})
                elif action == "remove_workdir":
                    workdir = str(msg.get("workdir", "")).strip()
                    if workdir:
                        try:
                            res = await mgr.remove_workdir(workdir)
                            dir_name = Path(workdir).name or workdir
                            await _send_json(
                                {
                                    "type": "toast",
                                    "data": {"level": "info", "message": f"已移除目录「{dir_name}」（共 {res['removed_count']} 条会话）"},
                                }
                            )
                        except Exception as exc:
                            telemetry_logger.warning(
                                "ws_action_error action=%s trace=%s cid=%s page=%s sid=%s err=%s",
                                action,
                                trace_id,
                                client_cid,
                                client_page,
                                cur_sid or "-",
                                exc,
                            )
                            await _send_json({"type": "toast", "data": {"level": "error", "message": str(exc)}})
                elif action == "set_mode":
                    mode_id = str(msg.get("mode_id", "")).strip()
                    if mode_id:
                        try:
                            await mgr.set_mode(mode_id, sid=cur_sid)
                        except Exception as exc:
                            telemetry_logger.warning(
                                "ws_action_error action=%s trace=%s cid=%s page=%s sid=%s err=%s",
                                action,
                                trace_id,
                                client_cid,
                                client_page,
                                cur_sid or "-",
                                exc,
                            )
                            await _send_json({"type": "toast", "data": {"level": "error", "message": str(exc)}})
                elif action == "set_model":
                    model_id = str(msg.get("model_id", "")).strip()
                    if model_id:
                        try:
                            await mgr.set_model(model_id, sid=cur_sid)
                            label = mgr.get_model_display_name(model_id)
                            await _send_json({"type": "toast", "data": {"level": "info", "message": f"已切换模型: {label}"}})
                        except Exception as exc:
                            telemetry_logger.warning(
                                "ws_action_error action=%s trace=%s cid=%s page=%s sid=%s err=%s",
                                action,
                                trace_id,
                                client_cid,
                                client_page,
                                cur_sid or "-",
                                exc,
                            )
                            await _send_json({"type": "toast", "data": {"level": "error", "message": str(exc)}})
                if action != "ping":
                    dur_ms = (time.perf_counter() - action_started) * 1000.0
                    if dur_ms >= 400.0:
                        telemetry_logger.info(
                            "ws_action_slow action=%s trace=%s cid=%s page=%s sid=%s dur_ms=%.1f",
                            action,
                            trace_id,
                            client_cid,
                            client_page,
                            cur_sid or "-",
                            dur_ms,
                        )
        except WebSocketDisconnect:
            pass
        finally:
            manager.remove_client(ws)

    return router, {"websocket_endpoint": websocket_endpoint}
