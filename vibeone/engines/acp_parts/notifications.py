"""Client view filtering, Web Push notifications, and /btw side-question sessions."""

import asyncio
import base64
import difflib
import json
import mimetypes
import os
import shutil
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import unquote, urlparse

from fastapi import WebSocket

from ... import config, quota
from ..base import SessionRuntime

logger = config.logger


class ACPNotificationsMixin:
    """Client view filtering, Web Push notifications, and /btw side-question sessions."""

    def _is_active(self) -> bool:
        if self._is_active_fn is not None:
            return bool(self._is_active_fn())
        return True

    def _clients_for_profile(self) -> List[WebSocket]:
        if self._client_views is None:
            return list(self.clients) if self._is_active() else []
        return [
            ws
            for ws in list(self.clients)
            if (self._client_views.get(ws) or {}).get("profile") == self.current_profile
        ]

    def _clients_for_session(self, sid: Optional[str]) -> List[WebSocket]:
        if self._client_views is None:
            if not self._is_active():
                return []
            if sid and sid != self.session_id:
                return []
            return list(self.clients)
        target_sid = sid or self.session_id
        return [
            ws
            for ws in list(self.clients)
            if (self._client_views.get(ws) or {}).get("profile") == self.current_profile
            and (not target_sid or (self._client_views.get(ws) or {}).get("session_id") == target_sid)
        ]

    def _clients_not_viewing_session(self, sid: str) -> List[WebSocket]:
        if self._client_views is None:
            if not self._is_active() or sid == self.session_id:
                return []
            return list(self.clients)
        return [
            ws
            for ws in list(self.clients)
            if (self._client_views.get(ws) or {}).get("profile") != self.current_profile
            or (self._client_views.get(ws) or {}).get("session_id") != sid
        ]

    def _viewed_session_ids(self) -> Set[str]:
        sids: Set[str] = set()
        if self.session_id:
            sids.add(self.session_id)
        if self._client_views is not None:
            for ws in list(self.clients):
                view = self._client_views.get(ws)
                if view and view.get("profile") == self.current_profile and view.get("session_id"):
                    sids.add(str(view["session_id"]))
        return sids

    def _has_visible_viewer_for(self, sid: str) -> bool:
        viewers = self._clients_for_session(sid)
        if not viewers:
            return False
        return any(self._client_visible.get(ws, True) for ws in viewers)

    def _schedule_push_if_unseen(self, sid: str, title: str, body: str, tag: str = "") -> Optional[asyncio.Task]:
        if not self.push or not self.push.supported:
            return None
        if self._has_visible_viewer_for(sid):
            logger.debug(
                "Skipped WebPush (visible viewer active, profile=%s, sid=%s)",
                self.current_profile,
                sid[:8] if sid else "-",
            )
            return None
        task = asyncio.create_task(
            self.push.send_notification(
                title=title,
                body=body,
                session_id=sid,
                tag=tag,
                profile=self.current_profile,
            )
        )
        self._push_tasks.add(task)

        def _on_push_done(t: asyncio.Task) -> None:
            self._push_tasks.discard(t)
            if t.cancelled():
                return
            exc = t.exception()
            if exc is not None:
                logger.warning(
                    "WebPush background task failed (profile=%s, sid=%s): %s",
                    self.current_profile,
                    sid[:8] if sid else "-",
                    exc,
                )

        task.add_done_callback(_on_push_done)
        return task

    def _cancel_unsolicited_push(self, rt: SessionRuntime) -> None:
        task = rt.unsolicited_push_task
        rt.unsolicited_push_task = None
        if task and not task.done():
            task.cancel()

    def _schedule_unsolicited_push(self, rt: SessionRuntime, delay: float = 4.0) -> None:
        """Debounce WebPush notifications for unsolicited background updates (e.g. /schedule)."""
        self._cancel_unsolicited_push(rt)
        sid = rt.session_id

        async def _delayed_notify() -> None:
            try:
                await asyncio.sleep(delay)
                if (
                    rt.is_busy
                    or self._runtimes.get(sid) is not rt
                    or sid in self._get_hidden_sessions()
                    or sid in self._get_archived_sessions()
                ):
                    return
                rt.active_assistant_id = None
                self.store.upsert_session(sid, rt.workdir, rt.title, touch=True, profile=self.current_profile)
                await self.broadcast({"type": "sessions_changed"})
                label = rt.title or sid[:8]
                last_asst = ""
                for it in reversed(rt.items):
                    if it.get("type") == "assistant" and str(it.get("text") or "").strip():
                        last_asst = " ".join(str(it["text"]).split())[:110]
                        break
                non_viewers = self._non_viewers_for_session(sid)
                if non_viewers:
                    await self.broadcast(
                        {
                            "type": "toast",
                            "data": {
                                "level": "info",
                                "message": f"后台会话「{label}」有新消息",
                            },
                        },
                        targets=non_viewers,
                    )
                self._schedule_push_if_unseen(
                    sid,
                    title=f"后台任务更新 · {label}",
                    body=last_asst or "收到新的后台消息，点击查看",
                    tag=f"agy-bg-{sid}",
                )
            except asyncio.CancelledError:
                return
            except Exception as exc:
                logger.warning("Unsolicited background push failed (sid=%s): %s", sid[:8], exc)
            finally:
                if rt.unsolicited_push_task is asyncio.current_task():
                    rt.unsolicited_push_task = None

        rt.unsolicited_push_task = asyncio.create_task(_delayed_notify())

    async def _emit_btw_event(self, btw_ctx: Dict[str, Any], payload: Dict[str, Any]) -> None:
        origin_ws = btw_ctx.get("origin_ws")
        if origin_ws is not None:
            await self.broadcast(payload, targets=[origin_ws])
        else:
            await self.broadcast(payload, sid=btw_ctx.get("source_sid") or None)

    def _build_btw_context(self, source_sid: Optional[str]) -> str:
        if not source_sid:
            return ""
        rt = self._runtimes.get(source_sid)
        items = list(rt.items) if (rt and rt.items) else self.store.load_items(session_id=source_sid)
        if not items:
            return ""
        snippets: List[str] = []
        total_chars = 0
        for it in reversed(items):
            itype = it.get("type")
            if itype not in ("user", "assistant"):
                continue
            txt = str(it.get("text") or "").strip()
            if not txt:
                continue
            if len(txt) > 1200:
                txt = txt[:1200] + "…"
            role = "用户" if itype == "user" else "助手"
            entry = f"[{role}]\n{txt}"
            if total_chars + len(entry) > 12000 and snippets:
                break
            snippets.append(entry)
            total_chars += len(entry)
            if len(snippets) >= 16:
                break
        snippets.reverse()
        return "\n\n".join(snippets)

    def _build_btw_fallback_prompt(self, question: str, source_sid: Optional[str]) -> str:
        ctx_text = self._build_btw_context(source_sid)
        header = (
            "[临时旁白提问 /btw]\n"
            "请仅基于下方当前会话上下文简明直接地回答用户的旁白提问。"
            "不要调用任何工具，不要修改文件或执行命令。"
        )
        if ctx_text:
            return f"{header}\n\n<当前会话上下文>\n{ctx_text}\n</当前会话上下文>\n\n用户旁白提问：\n{question}"
        return f"{header}\n\n用户旁白提问：\n{question}"

    async def send_btw_prompt(
        self,
        question: str,
        btw_id: Optional[str] = None,
        sid: Optional[str] = None,
        origin_ws: Optional[WebSocket] = None,
    ) -> Dict[str, Any]:
        clean_q = (question or "").strip()
        if not clean_q:
            raise RuntimeError("旁白提问不能为空")
        bid = str(btw_id or "").strip() or f"btw_{uuid.uuid4().hex[:10]}"
        source_sid = str(sid or "").strip() or self.session_id or ""
        btw_ctx: Dict[str, Any] = {
            "btw_id": bid,
            "source_sid": source_sid,
            "origin_ws": origin_ws,
            "question": clean_q,
            "text": "",
            "temp_sid": "",
            "cancelled": False,
        }
        task = asyncio.create_task(self._run_btw_task(btw_ctx))
        btw_ctx["task"] = task
        return {"btw_id": bid}

    async def _run_btw_task(self, btw_ctx: Dict[str, Any]) -> None:
        bid = btw_ctx["btw_id"]
        source_sid = btw_ctx["source_sid"]
        temp_sid = ""
        error_msg = ""
        try:
            await self.start()
            rt = self._runtimes.get(source_sid) if source_sid else None
            workdir = rt.workdir if rt else self.workdir
            model_id = (rt.current_model if rt else "") or self._fallback_model

            new_res = await self._send_request(
                "session/new",
                {"cwd": workdir, "mcpServers": []},
                timeout=30.0,
            )
            temp_sid = str(new_res.get("sessionId") or "").strip()
            if not temp_sid:
                raise RuntimeError("无法创建临时旁白会话")

            btw_ctx["temp_sid"] = temp_sid
            self._btw_sessions[temp_sid] = btw_ctx
            self._btw_id_to_sid[bid] = temp_sid
            self._add_hidden_sessions([temp_sid])

            if model_id:
                try:
                    await self._send_request(
                        "session/set_model",
                        {"sessionId": temp_sid, "modelId": model_id},
                        timeout=8.0,
                    )
                except Exception:
                    pass

            if btw_ctx.get("cancelled"):
                return

            full_prompt = self._build_btw_fallback_prompt(btw_ctx["question"], source_sid)
            await self._send_request(
                "session/prompt",
                {"sessionId": temp_sid, "prompt": [{"type": "text", "text": full_prompt}]},
                timeout=180.0,
            )
        except asyncio.CancelledError:
            btw_ctx["cancelled"] = True
        except Exception as exc:
            if not btw_ctx.get("cancelled"):
                error_msg = str(exc) or type(exc).__name__
                logger.warning("ACP /btw failed (%s): %s", bid, error_msg)
        finally:
            self._btw_id_to_sid.pop(bid, None)
            if temp_sid:
                self._btw_sessions.pop(temp_sid, None)
                try:
                    await self._send_request("session/close", {"sessionId": temp_sid}, timeout=2.5)
                except Exception:
                    pass
            if not btw_ctx.get("cancelled"):
                await self._emit_btw_event(
                    btw_ctx,
                    {
                        "type": "btw_done",
                        "data": {
                            "btw_id": bid,
                            "text": btw_ctx.get("text", ""),
                            "error": error_msg,
                        },
                    },
                )

    async def cancel_btw(self, btw_id: str) -> bool:
        bid = str(btw_id or "").strip()
        if not bid:
            return False
        temp_sid = self._btw_id_to_sid.get(bid)
        if not temp_sid:
            return False
        btw_ctx = self._btw_sessions.get(temp_sid)
        if not btw_ctx:
            return False
        btw_ctx["cancelled"] = True
        try:
            await self._send_notification("session/cancel", {"sessionId": temp_sid})
        except Exception:
            pass
        return True
