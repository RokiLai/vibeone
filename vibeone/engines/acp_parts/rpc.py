"""JSON-RPC stdio transport, server requests, permissions, and session/update handling."""

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


class ACPRpcMixin:
    """JSON-RPC stdio transport, server requests, permissions, and session/update handling."""

    async def _write_json(self, obj: Dict[str, Any]) -> None:
        if not self.proc or not self.proc.stdin:
            raise RuntimeError("ACP process is not running")
        line = (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")
        async with self._write_lock:
            self.proc.stdin.write(line)
            await self.proc.stdin.drain()

    async def _send_request(
        self, method: str, params: Dict[str, Any], timeout: Optional[float] = None
    ) -> Dict[str, Any]:
        self._rpc_id += 1
        req_id = self._rpc_id
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        self._pending_rpc[req_id] = fut

        await self._write_json(
            {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}
        )
        try:
            if timeout is not None:
                return await asyncio.wait_for(fut, timeout=timeout)
            return await fut
        finally:
            self._pending_rpc.pop(req_id, None)

    async def _send_notification(self, method: str, params: Dict[str, Any]) -> None:
        await self._write_json({"jsonrpc": "2.0", "method": method, "params": params})

    async def _read_stderr_loop(self, proc: asyncio.subprocess.Process) -> None:
        assert proc and proc.stderr
        try:
            while True:
                raw = await proc.stderr.readline()
                if not raw:
                    break
                line = raw.decode("utf-8", errors="replace").rstrip()
                if any(k in line for k in ("ERROR", "E11", "Failed", "fatal", "panic")):
                    logger.warning("[agy stderr] %s", line)
                else:
                    logger.debug("[agy stderr] %s", line)
        except asyncio.CancelledError:
            pass

    async def _read_stdout_loop(self, proc: asyncio.subprocess.Process) -> None:
        assert proc and proc.stdout
        try:
            while True:
                raw = await proc.stdout.readline()
                if not raw:
                    break
                line = raw.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    logger.warning("Non-JSON line from agy stdout: %s", line[:200])
                    continue
                await self._handle_rpc_message(msg)
        except asyncio.CancelledError:
            return
        except Exception as exc:
            logger.exception("Error in ACP stdout loop: %s", exc)
        finally:
            self._flush_pending_items()
            if self.proc is proc:
                self.connected = False
                for rt in self._runtimes.values():
                    rt.is_busy = False
                    rt.loaded_in_acp = False
                    rt.active_assistant_id = None
                    rt.active_thought_id = None
                    rt.pending_permissions.clear()
                self._perm_to_sid.clear()
                for fut in list(self._pending_rpc.values()):
                    if not fut.done():
                        fut.set_exception(RuntimeError("ACP subprocess exited unexpectedly"))
                self._pending_rpc.clear()
                self.last_error = "Agy 后台进程已退出，发送消息时将自动重启。"
                await self.broadcast_status()
                await self.broadcast({"type": "sessions_changed"})

    async def _handle_rpc_message(self, msg: Dict[str, Any]) -> None:
        # 1. Response to a client->server request
        if "id" in msg and "method" not in msg:
            req_id = msg["id"]
            fut = self._pending_rpc.get(req_id)
            if fut and not fut.done():
                if "error" in msg and msg["error"] is not None:
                    err = msg["error"]
                    fut.set_exception(RuntimeError(err.get("message", str(err))))
                else:
                    fut.set_result(msg.get("result", {}))
            return

        method = msg.get("method")
        params = msg.get("params") or {}

        # 2. Server->Client request (has id & method) -> e.g. session/request_permission
        if "id" in msg and method:
            await self._handle_server_request(msg["id"], method, params)
            return

        # 3. Server->Client notification -> e.g. session/update
        if method == "session/update":
            await self._handle_session_update(params)

    async def _handle_server_request(
        self, req_id: Any, method: str, params: Dict[str, Any]
    ) -> None:
        if method == "session/request_permission":
            key = str(req_id)
            sid = str(params.get("sessionId") or "").strip() or (self.session_id or "")
            if sid and sid in self._btw_sessions:
                await self._write_json(
                    {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "result": {"outcome": {"outcome": "cancelled"}},
                    }
                )
                return
            tool_call = params.get("toolCall") or {}
            options = params.get("options") or [
                {"optionId": "allow", "name": "Allow", "kind": "allow_once"},
                {"optionId": "deny", "name": "Deny", "kind": "reject_once"},
            ]
            perm_record = {
                "key": key,
                "request_id": req_id,
                "session_id": sid,
                "tool_call": {
                    "toolCallId": tool_call.get("toolCallId", ""),
                    "name": tool_call.get("name", "tool"),
                    "title": tool_call.get("title", tool_call.get("name", "操作权限")),
                    "kind": tool_call.get("kind", "other"),
                    "args": tool_call.get("args") or tool_call.get("rawInput") or {},
                },
                "options": options,
                "ts": time.time(),
            }
            if sid:
                rt = self._get_or_create_runtime(sid)
                rt.pending_permissions[key] = perm_record
                self._perm_to_sid[key] = sid
            else:
                self._fallback_permissions[key] = perm_record
            await self.broadcast({"type": "permission_request", "data": perm_record}, sid=sid or None)
            await self.broadcast_status()
            await self.broadcast({"type": "sessions_changed"})
            rt_title = self._runtimes[sid].title if sid in self._runtimes else "会话"
            self._schedule_push_if_unseen(
                sid,
                title=f"需确认权限 · {rt_title}",
                body=str(perm_record["tool_call"]["title"] or "有操作等待审批"),
                tag=f"agy-perm-{sid}",
            )
            logger.info("Permission requested (id=%s, sid=%s): %s", key, sid[:8], perm_record["tool_call"]["title"])
        else:
            # Unsupported server->client request: return JSON-RPC method not found
            await self._write_json(
                {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32601, "message": f"Method not supported by client: {method}"},
                }
            )

    async def respond_permission(self, key: str, option_id: Optional[str], cancelled: bool = False) -> bool:
        skey = str(key)
        sid = self._perm_to_sid.pop(skey, None)
        perm = None
        if sid and sid in self._runtimes:
            perm = self._runtimes[sid].pending_permissions.pop(skey, None)
        if perm is None:
            for r in self._runtimes.values():
                if skey in r.pending_permissions:
                    perm = r.pending_permissions.pop(skey, None)
                    sid = r.session_id
                    break
        if perm is None:
            perm = self._fallback_permissions.pop(skey, None)
        if not perm:
            return False
        if cancelled or not option_id:
            result_payload = {"outcome": {"outcome": "cancelled"}}
        else:
            result_payload = {"outcome": {"outcome": "selected", "optionId": option_id}}

        await self._write_json(
            {
                "jsonrpc": "2.0",
                "id": perm["request_id"],
                "result": result_payload,
            }
        )
        await self.broadcast(
            {
                "type": "permission_resolved",
                "data": {"key": skey, "option_id": option_id, "cancelled": cancelled},
            },
            sid=sid or None,
        )
        await self.broadcast_status()
        await self.broadcast({"type": "sessions_changed"})
        return True

    async def _handle_session_update(self, params: Dict[str, Any]) -> None:
        update = params.get("update") or {}
        kind = update.get("sessionUpdate", "")
        sid = str(params.get("sessionId") or "").strip() or (self.session_id or "")

        # Ephemeral /btw side-question stream: isolate completely from main runtimes & SQLite
        btw_ctx = self._btw_sessions.get(sid) if sid else None
        if btw_ctx is not None:
            if kind == "agent_message_chunk" and not btw_ctx.get("cancelled"):
                text = self._extract_content_text(update.get("content"))
                if text:
                    btw_ctx["text"] += text
                    await self._emit_btw_event(
                        btw_ctx,
                        {"type": "btw_delta", "data": {"btw_id": btw_ctx["btw_id"], "delta": text}},
                    )
            return

        # Buffer historical items during `session/load` instead of streaming them one by one
        if (
            self._replaying_session_id
            and sid == self._replaying_session_id
            and kind in ("user_message_chunk", "agent_message_chunk", "agent_thought_chunk", "tool_call", "tool_call_update")
        ):
            self._append_replay_update(update)
            return

        if sid and (sid in self._get_hidden_sessions() or sid in self._get_archived_sessions()):
            now = time.time()
            if self.connected and (now - self._ignored_close_sent_at.get(sid, 0.0)) >= 30.0:
                self._ignored_close_sent_at[sid] = now
                asyncio.create_task(self._close_background_session(sid))
            return

        rt = self._get_or_create_runtime(sid) if sid else None
        target_sid = sid or None

        if kind == "agent_message_chunk":
            if not rt:
                return
            rt.active_thought_id = None
            text = self._extract_content_text(update.get("content"))
            if not text:
                return
            if not rt.active_assistant_id:
                item_id = f"asst_{uuid.uuid4().hex[:12]}"
                rt.active_assistant_id = item_id
                item = {"id": item_id, "type": "assistant", "text": text, "ts": time.time()}
                self._add_or_update_item(item, sid=sid, deferred=True)
            else:
                item = rt.items_by_id[rt.active_assistant_id]
                item["text"] += text
                self._add_or_update_item(item, sid=sid, deferred=True)
            await self.broadcast(
                {
                    "type": "text_delta",
                    "data": {"item_id": rt.active_assistant_id, "delta": text},
                },
                sid=target_sid,
            )
            if not rt.is_busy:
                self._schedule_unsolicited_push(rt)

        elif kind == "agent_thought_chunk":
            if not rt:
                return
            rt.active_assistant_id = None
            if not rt.is_busy:
                self._cancel_unsolicited_push(rt)
            text = self._extract_content_text(update.get("content"))
            if not text:
                return
            if not rt.active_thought_id:
                item_id = f"thought_{uuid.uuid4().hex[:12]}"
                rt.active_thought_id = item_id
                item = {"id": item_id, "type": "thought", "text": text, "ts": time.time()}
                self._add_or_update_item(item, sid=sid, deferred=True)
            else:
                item = rt.items_by_id[rt.active_thought_id]
                item["text"] += text
                self._add_or_update_item(item, sid=sid, deferred=True)
            await self.broadcast(
                {
                    "type": "thought_delta",
                    "data": {"item_id": rt.active_thought_id, "delta": text},
                },
                sid=target_sid,
            )

        elif kind in ("tool_call", "tool_call_update"):
            if not rt:
                return
            rt.active_assistant_id = None
            rt.active_thought_id = None
            if not rt.is_busy:
                self._cancel_unsolicited_push(rt)
            tc_id = update.get("toolCallId") or f"tc_{uuid.uuid4().hex[:10]}"
            item_id = f"tool_{tc_id}"
            existing = rt.items_by_id.get(item_id)

            output_text = self._extract_content_text(update.get("content"))
            if not output_text and update.get("rawOutput") is not None:
                raw_out = update.get("rawOutput")
                if isinstance(raw_out, dict) and "output" in raw_out:
                    output_text = str(raw_out["output"])
                elif isinstance(raw_out, str):
                    output_text = raw_out
                else:
                    output_text = json.dumps(raw_out, ensure_ascii=False, indent=2)

            tool_item = {
                "id": item_id,
                "type": "tool",
                "toolCallId": tc_id,
                "name": update.get("name") or (existing.get("name") if existing else "tool"),
                "title": update.get("title") or (existing.get("title") if existing else ""),
                "kind": update.get("kind") or (existing.get("kind") if existing else "other"),
                "status": update.get("status") or (existing.get("status") if existing else "in_progress"),
                "args": update.get("args") or update.get("rawInput") or (existing.get("args") if existing else {}),
                "output": output_text or (existing.get("output") if existing else ""),
                "ts": existing["ts"] if existing else time.time(),
            }
            self._add_or_update_item(tool_item, sid=sid)
            await self.broadcast({"type": "item_upsert", "data": tool_item}, sid=target_sid)

        elif kind == "session_info_update":
            title = update.get("title")
            if title and rt:
                rt.title = title
                self.store.upsert_session(sid, rt.workdir, title, profile=self.current_profile)
                if sid == self.session_id:
                    self.store.set_meta("session_title", title)
                await self.broadcast_status()
                await self.broadcast({"type": "sessions_changed"})

        elif kind == "usage_update":
            new_usage = {
                "used": int(update.get("used", 0) or 0),
                "size": int(update.get("size", 0) or 0),
            }
            if rt:
                rt.usage = new_usage
                self._save_usage(sid, new_usage)
            await self.broadcast({"type": "usage", "data": new_usage}, sid=target_sid)

        elif kind == "available_commands_update":
            cmds = update.get("availableCommands")
            if isinstance(cmds, list):
                self.available_commands = [
                    {
                        "name": str(c.get("name") or "").strip(),
                        "description": str(c.get("description") or "").strip(),
                    }
                    for c in cmds
                    if isinstance(c, dict) and c.get("name")
                ]
                await self.broadcast_status()

        elif kind == "current_mode_update":
            mode_id = update.get("currentModeId") or update.get("modeId")
            if mode_id and rt:
                rt.current_mode = mode_id
                await self.broadcast_status()

        elif kind == "current_model_update":
            model_id = update.get("currentModelId") or update.get("modelId")
            if model_id and rt and model_id != rt.current_model:
                rt.current_model = model_id
                await self.broadcast_status()

        elif kind == "config_option_update":
            if not rt:
                return
            config_options = update.get("configOptions") or []
            changed = False
            cfg_model = None
            cfg_thought = None
            for opt in config_options:
                if not isinstance(opt, dict):
                    continue
                opt_id = opt.get("id")
                val = opt.get("currentValue")
                if opt_id == "mode" and val and val != rt.current_mode:
                    rt.current_mode = str(val)
                    changed = True
                elif opt_id == "model" and val:
                    cfg_model = str(val)
                elif opt_id == "thought_level" and val:
                    cfg_thought = str(val)
            if cfg_model:
                valid_ids = self._get_available_model_ids()
                candidate = f"{cfg_model}-{cfg_thought}" if cfg_thought else cfg_model
                resolved = None
                if candidate in valid_ids:
                    resolved = candidate
                elif cfg_model in valid_ids:
                    resolved = cfg_model
                if resolved and resolved != rt.current_model:
                    rt.current_model = resolved
                    changed = True
            if changed:
                await self.broadcast_status()
