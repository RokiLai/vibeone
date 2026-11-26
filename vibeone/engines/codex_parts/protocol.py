"""Codex JSON-RPC dispatch, approval requests, notifications, and quota mixin."""

import asyncio
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


class CodexProtocolMixin:
    """Handles Codex JSON-RPC messages, approval requests, notifications, and quota."""

    async def _handle_rpc_message(self, msg: Dict[str, Any]) -> None:
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

        method = str(msg.get("method") or "")
        params = msg.get("params") or {}
        if "id" in msg and method:
            await self._handle_server_request(msg["id"], method, params)
            return
        await self._handle_codex_notification(method, params)

    async def _handle_server_request(self, req_id: Any, method: str, params: Dict[str, Any]) -> None:
        sid = str(params.get("threadId") or "").strip() or (self.session_id or "")
        key = str(req_id)
        if method in (
            "item/commandExecution/requestApproval",
            "item/fileChange/requestApproval",
            "item/permissions/requestApproval",
            "item/tool/requestUserInput",
        ):
            if sid and sid in self._btw_sessions:
                if method == "item/permissions/requestApproval":
                    res_payload: Dict[str, Any] = {"permissions": {}, "scope": "turn"}
                elif method == "item/tool/requestUserInput":
                    res_payload = {"answers": {}}
                else:
                    res_payload = {"decision": "decline"}
                await self._write_json({"jsonrpc": "2.0", "id": req_id, "result": res_payload})
                return
            if method == "item/commandExecution/requestApproval":
                title = str(params.get("command") or params.get("reason") or "执行命令审批")
                kind = "execute"
                args: Dict[str, Any] = {"command": params.get("command", ""), "cwd": params.get("cwd", ""), "reason": params.get("reason", "")}
                options = [
                    {"optionId": "accept", "name": "允许一次", "kind": "allow_once"},
                    {"optionId": "acceptForSession", "name": "本会话允许", "kind": "allow_always"},
                    {"optionId": "decline", "name": "拒绝", "kind": "reject_once"},
                ]
            elif method == "item/fileChange/requestApproval":
                title = str(params.get("reason") or "文件修改审批")
                kind = "edit"
                args = {"grantRoot": params.get("grantRoot", ""), "reason": params.get("reason", "")}
                options = [
                    {"optionId": "accept", "name": "允许一次", "kind": "allow_once"},
                    {"optionId": "acceptForSession", "name": "本会话允许", "kind": "allow_always"},
                    {"optionId": "decline", "name": "拒绝", "kind": "reject_once"},
                ]
            elif method == "item/permissions/requestApproval":
                title = str(params.get("reason") or "权限提升审批")
                kind = "other"
                args = {"permissions": params.get("permissions") or {}, "reason": params.get("reason", "")}
                options = [
                    {"optionId": "accept", "name": "允许本次", "kind": "allow_once"},
                    {"optionId": "acceptForSession", "name": "本会话允许", "kind": "allow_always"},
                    {"optionId": "decline", "name": "拒绝", "kind": "reject_once"},
                ]
            else:
                questions = params.get("questions") or []
                first_q = questions[0] if questions and isinstance(questions[0], dict) else {}
                title = str(first_q.get("question") or "Codex 请求输入")
                kind = "other"
                args = {"questions": questions}
                options = [
                    {"optionId": str(opt.get("label") or opt.get("value") or i), "name": str(opt.get("label") or opt.get("value") or i), "kind": "allow_once"}
                    for i, opt in enumerate(first_q.get("options") or [])
                    if isinstance(opt, dict)
                ] or [
                    {"optionId": "确认", "name": "确认", "kind": "allow_once"},
                    {"optionId": "取消", "name": "取消", "kind": "reject_once"},
                ]

            perm_record = {
                "key": key,
                "request_id": req_id,
                "session_id": sid,
                "codex_method": method,
                "codex_params": params,
                "tool_call": {
                    "toolCallId": str(params.get("itemId") or ""),
                    "name": method.split("/")[1] if "/" in method else method,
                    "title": title,
                    "kind": kind,
                    "args": args,
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
            rt_title = self._runtimes[sid].title if sid in self._runtimes else "Codex"
            self._schedule_push_if_unseen(
                sid,
                title=f"需确认权限 · {rt_title}",
                body=title,
                tag=f"agy-perm-{sid}",
            )
            return

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

        cmethod = perm.get("codex_method") or ""
        cparams = perm.get("codex_params") or {}
        if cmethod == "item/permissions/requestApproval":
            if cancelled or not option_id or option_id == "decline":
                result_payload = {"permissions": {}, "scope": "turn"}
            else:
                result_payload = {
                    "permissions": cparams.get("permissions") or {},
                    "scope": "session" if option_id == "acceptForSession" else "turn",
                }
        elif cmethod == "item/tool/requestUserInput":
            answers: Dict[str, Any] = {}
            if not cancelled and option_id:
                for q in cparams.get("questions") or []:
                    if isinstance(q, dict) and q.get("id"):
                        answers[str(q["id"])] = {"answers": [str(option_id)]}
            result_payload = {"answers": answers}
        else:
            decision = "decline" if (cancelled or not option_id) else str(option_id)
            result_payload = {"decision": decision}

        await self._write_json({"jsonrpc": "2.0", "id": perm["request_id"], "result": result_payload})
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

    async def _handle_codex_notification(self, method: str, params: Dict[str, Any]) -> None:
        sid = str(params.get("threadId") or "").strip() or (self.session_id or "")

        btw_ctx = self._btw_sessions.get(sid) if sid else None
        if btw_ctx is not None:
            if method == "item/agentMessage/delta" and not btw_ctx.get("cancelled"):
                delta = str(params.get("delta") or "")
                if delta:
                    btw_ctx["text"] += delta
                    await self._emit_btw_event(
                        btw_ctx,
                        {"type": "btw_delta", "data": {"btw_id": btw_ctx["btw_id"], "delta": delta}},
                    )
            elif method == "item/completed" and not btw_ctx.get("cancelled"):
                raw_item = params.get("item") or {}
                ui_item = self._convert_codex_item_to_ui(raw_item)
                if ui_item and ui_item.get("type") == "assistant" and ui_item.get("text") and not btw_ctx.get("text"):
                    text = str(ui_item["text"])
                    btw_ctx["text"] = text
                    await self._emit_btw_event(
                        btw_ctx,
                        {"type": "btw_delta", "data": {"btw_id": btw_ctx["btw_id"], "delta": text}},
                    )
            elif method == "turn/completed":
                fut = self._turn_futures.get(sid)
                if fut and not fut.done():
                    fut.set_result(params.get("turn") or {})
            elif method == "error" and not params.get("willRetry"):
                err_obj = params.get("error") or {}
                fut = self._turn_futures.get(sid)
                if fut and not fut.done():
                    fut.set_exception(RuntimeError(str(err_obj.get("message") or "Codex error")))
            return

        if sid and (sid in self._get_hidden_sessions() or sid in self._get_archived_sessions()):
            now = time.time()
            if self.connected and (now - self._ignored_close_sent_at.get(sid, 0.0)) >= 30.0:
                self._ignored_close_sent_at[sid] = now
                asyncio.create_task(self._close_background_session(sid))
            return

        rt = self._get_or_create_runtime(sid) if sid else None
        target_sid = sid or None

        if method == "item/agentMessage/delta":
            if not rt:
                return
            delta = str(params.get("delta") or "")
            if not delta:
                return
            rt.active_thought_id = None
            raw_iid = str(params.get("itemId") or "")
            item_id = f"asst_{raw_iid}" if raw_iid else (rt.active_assistant_id or f"asst_{uuid.uuid4().hex[:12]}")
            rt.active_assistant_id = item_id
            existing = rt.items_by_id.get(item_id)
            if existing:
                existing["text"] += delta
                self._add_or_update_item(existing, sid=sid)
            else:
                self._add_or_update_item({"id": item_id, "type": "assistant", "text": delta, "ts": time.time()}, sid=sid)
            await self.broadcast({"type": "text_delta", "data": {"item_id": item_id, "delta": delta}}, sid=target_sid)

        elif method in ("item/reasoning/summaryTextDelta", "item/reasoning/textDelta"):
            if not rt:
                return
            delta = str(params.get("delta") or "")
            if not delta:
                return
            rt.active_assistant_id = None
            raw_iid = str(params.get("itemId") or "")
            item_id = f"thought_{raw_iid}" if raw_iid else (rt.active_thought_id or f"thought_{uuid.uuid4().hex[:12]}")
            rt.active_thought_id = item_id
            existing = rt.items_by_id.get(item_id)
            if existing:
                existing["text"] += delta
                self._add_or_update_item(existing, sid=sid)
            else:
                self._add_or_update_item({"id": item_id, "type": "thought", "text": delta, "ts": time.time()}, sid=sid)
            await self.broadcast({"type": "thought_delta", "data": {"item_id": item_id, "delta": delta}}, sid=target_sid)

        elif method == "item/commandExecution/outputDelta":
            if not rt:
                return
            raw_iid = str(params.get("itemId") or "")
            delta = str(params.get("delta") or "")
            if not raw_iid or not delta:
                return
            item_id = f"tool_{raw_iid}"
            existing = rt.items_by_id.get(item_id)
            if existing:
                existing["output"] = str(existing.get("output") or "") + delta
                self._add_or_update_item(existing, sid=sid)
                await self.broadcast({"type": "item_upsert", "data": existing}, sid=target_sid)

        elif method in ("item/started", "item/completed"):
            if not rt:
                return
            raw_item = params.get("item") or {}
            ui_item = self._convert_codex_item_to_ui(raw_item)
            if not ui_item:
                return
            if ui_item["type"] == "tool":
                rt.active_assistant_id = None
                rt.active_thought_id = None
                existing = rt.items_by_id.get(ui_item["id"])
                if existing and not ui_item.get("output") and existing.get("output"):
                    ui_item["output"] = existing["output"]
                self._add_or_update_item(ui_item, sid=sid)
                await self.broadcast({"type": "item_upsert", "data": ui_item}, sid=target_sid)
            elif ui_item["type"] == "assistant" and method == "item/completed":
                if ui_item.get("text"):
                    existing = rt.items_by_id.get(ui_item["id"])
                    if not existing:
                        self._add_or_update_item(ui_item, sid=sid)
                        await self.broadcast({"type": "item_upsert", "data": ui_item}, sid=target_sid)
                rt.active_assistant_id = None
            elif ui_item["type"] == "thought" and method == "item/completed":
                if ui_item.get("text") and not rt.items_by_id.get(ui_item["id"]):
                    self._add_or_update_item(ui_item, sid=sid)
                    await self.broadcast({"type": "item_upsert", "data": ui_item}, sid=target_sid)
                rt.active_thought_id = None

        elif method == "thread/tokenUsage/updated":
            tu = params.get("tokenUsage") or {}
            last_tu = tu.get("last") or tu.get("total") or {}
            new_usage = {
                "used": int(last_tu.get("totalTokens") or 0),
                "size": int(tu.get("modelContextWindow") or 0),
            }
            if rt:
                rt.usage = new_usage
                self._save_usage(sid, new_usage)
            await self.broadcast({"type": "usage", "data": new_usage}, sid=target_sid)

        elif method == "thread/name/updated":
            title = str(params.get("threadName") or "").strip()
            if title and rt:
                rt.title = title
                self.store.upsert_session(sid, rt.workdir, title, profile=self.current_profile)
                if sid == self.session_id:
                    self.store.set_meta("session_title", title)
                await self.broadcast_status()
                await self.broadcast({"type": "sessions_changed"})

        elif method == "account/rateLimits/updated":
            self._codex_rate_limits = params.get("rateLimits")

        elif method == "thread/compacted":
            if sid in self._compact_sids:
                fut = self._turn_futures.get(sid)
                if fut and not fut.done():
                    fut.set_result({"status": "completed"})

        elif method == "turn/completed":
            self._invalidate_thread_cache()
            fut = self._turn_futures.get(sid)
            if fut and not fut.done():
                fut.set_result(params.get("turn") or {})

        elif method == "error":
            if not params.get("willRetry"):
                err_obj = params.get("error") or {}
                fut = self._turn_futures.get(sid)
                if fut and not fut.done():
                    fut.set_exception(RuntimeError(str(err_obj.get("message") or "Codex error")))

    async def fetch_quota(self) -> Dict[str, Any]:
        if not self.connected:
            raise RuntimeError("Codex 尚未连接")
        res = await self._send_request("account/rateLimits/read", {}, timeout=10.0)
        snapshots: List[Dict[str, Any]] = []
        by_id = res.get("rateLimitsByLimitId")
        if isinstance(by_id, dict) and by_id:
            snapshots = [v for v in by_id.values() if isinstance(v, dict)]
        elif isinstance(res.get("rateLimits"), dict):
            snapshots = [res["rateLimits"]]

        reset_credits = res.get("rateLimitResetCredits") or {}
        avail_resets = int(reset_credits.get("availableCount") or 0) if isinstance(reset_credits, dict) else 0

        summary: List[Dict[str, Any]] = []
        for idx, snap in enumerate(snapshots):
            buckets: List[Dict[str, Any]] = []
            for key in ("primary", "secondary"):
                win = snap.get(key)
                if not isinstance(win, dict):
                    continue
                mins = int(win.get("windowDurationMins") or 0)
                if mins and mins <= 360:
                    w_name = "5h"
                elif mins >= 10000:
                    w_name = "weekly"
                else:
                    w_name = f"{mins}m" if mins else key
                used_pct = float(win.get("usedPercent") or 0)
                rem = max(0.0, min(1.0, 1.0 - used_pct / 100.0))
                resets_at = win.get("resetsAt")
                reset_iso = ""
                if isinstance(resets_at, (int, float)) and resets_at > 0:
                    reset_iso = datetime.fromtimestamp(float(resets_at), tz=timezone.utc).isoformat()
                buckets.append({"window": w_name, "label": w_name, "remaining": rem, "reset_time": reset_iso})
            if buckets:
                plan = str(snap.get("planType") or self._codex_account.get("planType") or "").strip()
                raw_name = str(snap.get("limitName") or "").strip()
                if raw_name:
                    name = raw_name
                elif plan:
                    name = f"Codex {plan.capitalize()} 额度"
                else:
                    name = "Codex 额度"
                members: List[str] = []
                credits_info = snap.get("credits")
                if isinstance(credits_info, dict):
                    if credits_info.get("unlimited"):
                        members.append("无限额度")
                    elif credits_info.get("hasCredits") and credits_info.get("balance"):
                        members.append(f"Credits 余额: {credits_info['balance']}")
                if idx == 0 and avail_resets > 0:
                    members.append(f"免费额度重置次数: {avail_resets} 次")
                summary.append({"name": name, "members": members, "buckets": buckets})
        return {"profile": self.current_profile, "endpoint": "codex-app-server", "summary": summary, "groups": []}
