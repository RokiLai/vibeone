"""Codex prompt execution, turn steering, cancellation, and /btw side-question mixin."""

import asyncio
import time
import uuid
from typing import Any, Dict, List, Optional

from fastapi import WebSocket

from ... import config

logger = config.logger


class CodexTurnsMixin:
    """Handles prompt turns, /compact, /review, /fork dispatch, steering, cancel, and /btw."""

    @staticmethod
    def _parse_review_target(arg: str) -> Dict[str, Any]:
        clean = str(arg or "").strip()
        if not clean:
            return {"type": "uncommittedChanges"}
        parts = clean.split(None, 1)
        head = parts[0].lower()
        rest = parts[1].strip() if len(parts) > 1 else ""
        if head in ("branch", "base") and rest:
            return {"type": "baseBranch", "branch": rest}
        if head == "commit" and rest:
            return {"type": "commit", "sha": rest}
        return {"type": "custom", "instructions": clean}

    async def send_prompt(
        self,
        text: str,
        display_text: Optional[str] = None,
        images: Optional[List[Dict[str, Any]]] = None,
        sid: Optional[str] = None,
        origin_ws: Optional[WebSocket] = None,
    ) -> Dict[str, Any]:
        clean = (text or "").strip()
        if clean.lower() == "/fork" or clean.lower().startswith("/fork "):
            await self.start()
            target_sid = str(sid or "").strip() or self.session_id
            if not target_sid:
                raise RuntimeError("Codex 会话未就绪")
            return await self._fork_current_session(target_sid, origin_ws=origin_ws)
        return await super().send_prompt(text, display_text=display_text, images=images, sid=sid)

    async def _build_codex_input(
        self, prompt_text: str, images: Optional[List[Dict[str, Any]]] = None
    ) -> List[Dict[str, Any]]:
        blocks: List[Dict[str, Any]] = []
        for im in images or []:
            path = self._resolve_upload_file(im)
            if path:
                blocks.append({"type": "localImage", "path": str(path)})
        final_text = prompt_text.strip()
        if final_text.startswith("/"):
            parts = final_text[1:].split(None, 1)
            cmd_name = parts[0].strip().lower() if parts else ""
            rest = parts[1].strip() if len(parts) > 1 else ""
            skill = self._codex_skills.get(cmd_name)
            if skill and skill.get("path"):
                blocks.append({"type": "skill", "name": skill["name"], "path": skill["path"]})
                final_text = rest or f"请使用 {skill['name']} 技能处理当前任务。"
        if not final_text and blocks:
            final_text = "请查看以上图片并回复。"
        if final_text:
            blocks.append({"type": "text", "text": final_text})
        return blocks

    async def steer_queued_prompt(self, queue_id: str, sid: Optional[str] = None) -> bool:
        target_sid = sid or self.session_id
        if not target_sid or not queue_id:
            return False
        rt = self._runtimes.get(target_sid)
        if not rt:
            return False
        turn_id = self._thread_turn_ids.get(target_sid)
        if rt.is_busy and turn_id and self.connected:
            idx = next((i for i, q in enumerate(rt.queued_prompts) if q.get("id") == queue_id), -1)
            if idx < 0:
                return False
            entry = rt.queued_prompts.pop(idx)
            self._save_queue(target_sid, rt.queued_prompts)
            await self._broadcast_queue(target_sid)
            await self.broadcast({"type": "sessions_changed"})
            try:
                clean_text = entry.get("text", "")
                display_text = entry.get("display_text")
                images = entry.get("images") or []
                input_blocks = await self._build_codex_input(clean_text, images)
                await self._send_request(
                    "turn/steer",
                    {"threadId": target_sid, "expectedTurnId": turn_id, "input": input_blocks},
                    timeout=10.0,
                )
                user_item = {
                    "id": f"user_{uuid.uuid4().hex[:12]}",
                    "type": "user",
                    "text": display_text if display_text is not None else clean_text,
                    "images": list(images),
                    "ts": time.time(),
                }
                self._add_or_update_item(user_item, sid=target_sid)
                await self.broadcast({"type": "item_upsert", "data": user_item}, sid=target_sid)
                return True
            except Exception as exc:
                logger.warning("Codex turn/steer failed (%s), falling back to interrupt+start", exc)
                rt.queued_prompts.insert(0, entry)
        return await super().steer_queued_prompt(queue_id, sid=target_sid)

    async def _run_prompt_turn(
        self, sid: str, prompt_text: str, images: Optional[List[Dict[str, Any]]] = None
    ) -> None:
        rt = self._get_or_create_runtime(sid)
        start_item_count = len(rt.items)
        failed = False
        abnormal_stop = False
        stop_reason = "end_turn"
        early_stop_msg = ""
        was_slash_cmd = prompt_text.lstrip().startswith("/")
        try:
            clean_prompt = prompt_text.strip()
            low_prompt = clean_prompt.lower()
            is_compact = low_prompt == "/compact" or low_prompt.startswith("/compact ")
            is_review = low_prompt == "/review" or low_prompt.startswith("/review ")

            loop = asyncio.get_running_loop()
            turn_fut: asyncio.Future = loop.create_future()
            self._turn_futures[sid] = turn_fut

            if is_compact:
                self._compact_sids.add(sid)
                await self._send_request("thread/compact/start", {"threadId": sid}, timeout=30.0)
                try:
                    completed_turn = await asyncio.wait_for(turn_fut, timeout=90.0)
                except asyncio.TimeoutError:
                    completed_turn = {"status": "completed"}
                if not any(
                    it.get("type") == "assistant" and str(it.get("text") or "").strip()
                    for it in rt.items[start_item_count:]
                ):
                    done_item = {
                        "id": f"asst_{uuid.uuid4().hex[:12]}",
                        "type": "assistant",
                        "text": "已完成当前 Codex 会话的上下文压缩。",
                        "ts": time.time(),
                    }
                    self._add_or_update_item(done_item, sid=sid)
                    await self.broadcast({"type": "item_upsert", "data": done_item}, sid=sid)
            elif is_review:
                review_arg = clean_prompt[len("/review"):].strip()
                target = self._parse_review_target(review_arg)
                start_res = await self._send_request(
                    "review/start",
                    {"threadId": sid, "delivery": "inline", "target": target},
                    timeout=30.0,
                )
                turn_obj = start_res.get("turn") or {}
                turn_id = str(turn_obj.get("id") or "")
                if turn_id:
                    self._thread_turn_ids[sid] = turn_id
                completed_turn = await turn_fut
            else:
                input_blocks = await self._build_codex_input(prompt_text, images)
                approval_policy, _, sandbox_policy = self._mode_to_codex_policy(rt.current_mode)
                base_model, effort = self._split_model_and_effort(rt.current_model)
                params: Dict[str, Any] = {
                    "threadId": sid,
                    "input": input_blocks,
                    "cwd": rt.workdir,
                    "approvalPolicy": approval_policy,
                    "sandboxPolicy": sandbox_policy,
                }
                if base_model:
                    params["model"] = base_model
                if effort:
                    params["effort"] = effort

                start_res = await self._send_request("turn/start", params, timeout=30.0)
                turn_obj = start_res.get("turn") or {}
                turn_id = str(turn_obj.get("id") or "")
                if turn_id:
                    self._thread_turn_ids[sid] = turn_id

                completed_turn = await turn_fut
            t_status = str((completed_turn or {}).get("status") or "completed").lower()
            if t_status == "interrupted":
                stop_reason = "cancelled"
            elif t_status == "failed":
                err_obj = (completed_turn or {}).get("error") or {}
                raise RuntimeError(str(err_obj.get("message") or "Codex 回合执行失败"))
            else:
                stop_reason = "end_turn"

            turn_new_items = rt.items[start_item_count:]
            has_assistant_reply = any(
                it.get("type") == "assistant" and str(it.get("text") or "").strip()
                for it in turn_new_items
            )
            if stop_reason == "end_turn" and not rt.cancel_requested and not has_assistant_reply and not was_slash_cmd:
                abnormal_stop = True
                early_stop_msg = "模型未生成最终回复，可点击「继续」接续执行。"
                notice_item = {
                    "id": f"warn_{uuid.uuid4().hex[:12]}",
                    "type": "error",
                    "kind": "early_stop",
                    "stop_reason": stop_reason,
                    "text": early_stop_msg,
                    "ts": time.time(),
                }
                self._add_or_update_item(notice_item, sid=sid)
                await self.broadcast({"type": "item_upsert", "data": notice_item}, sid=sid)

            await self.broadcast(
                {
                    "type": "turn_end",
                    "data": {
                        "stopReason": stop_reason,
                        "usage": rt.usage,
                        "early_stop_message": early_stop_msg or None,
                    },
                },
                sid=sid,
            )
            logger.info("Codex prompt turn finished (sid=%s, stopReason=%s)", sid[:8], stop_reason)
        except Exception as exc:
            failed = True
            logger.error("Codex prompt turn failed (sid=%s): %s", sid[:8], exc)
            err_item = {
                "id": f"err_{uuid.uuid4().hex[:12]}",
                "type": "error",
                "text": str(exc),
                "ts": time.time(),
            }
            self._add_or_update_item(err_item, sid=sid)
            await self.broadcast({"type": "item_upsert", "data": err_item}, sid=sid)
        finally:
            self._flush_pending_items(sid)
            self._compact_sids.discard(sid)
            self._turn_futures.pop(sid, None)
            self._thread_turn_ids.pop(sid, None)
            if self.store.count_items(sid) > 0:
                self.store.touch_session(sid)
            was_cancelled = rt.cancel_requested or stop_reason.lower() == "cancelled"
            steer_entry = rt.steer_next_prompt
            rt.steer_next_prompt = None
            rt.cancel_requested = False
            rt.is_busy = False
            rt.active_assistant_id = None
            rt.active_thought_id = None
            rt.prompt_task = None

            next_queued = None
            if steer_entry is not None and self.connected:
                next_queued = steer_entry
            elif not failed and not was_cancelled and not abnormal_stop and rt.queued_prompts and self.connected:
                next_queued = rt.queued_prompts.pop(0)
                self._save_queue(sid, rt.queued_prompts)
                await self._broadcast_queue(sid)

            if next_queued is not None:
                await self._start_prompt_turn(
                    sid,
                    next_queued.get("text", ""),
                    display_text=next_queued.get("display_text"),
                    images=next_queued.get("images") or [],
                )
            else:
                await self.broadcast_status()
                await self.broadcast({"type": "sessions_changed"})
                label = rt.title or "Codex"
                non_viewers = self._clients_not_viewing_session(sid)
                if non_viewers:
                    status_msg = (
                        "执行出错"
                        if failed
                        else ("响应提前中断" if abnormal_stop else "已完成")
                    )
                    await self.broadcast(
                        {
                            "type": "toast",
                            "data": {
                                "level": "error" if (failed or abnormal_stop) else "info",
                                "message": f"后台会话「{label}」{status_msg}",
                            },
                        },
                        targets=non_viewers,
                    )
                push_task: Optional[asyncio.Task] = None
                if not was_cancelled:
                    if failed:
                        push_title = f"执行出错 · {label}"
                        push_body = str(rt.items[-1].get("text") or "任务执行失败")[:110] if rt.items else "任务执行失败"
                        push_tag = f"agy-err-{sid}"
                    elif abnormal_stop:
                        push_title = f"响应提前中断 · {label}"
                        push_body = early_stop_msg[:110]
                        push_tag = f"agy-warn-{sid}"
                    else:
                        last_asst = ""
                        for it in reversed(rt.items):
                            if it.get("type") == "assistant" and str(it.get("text") or "").strip():
                                last_asst = " ".join(str(it["text"]).split())[:110]
                                break
                        push_title = f"任务已完成 · {label}"
                        push_body = last_asst or "回复已生成，点击查看"
                        push_tag = f"agy-done-{sid}"
                    push_task = self._schedule_push_if_unseen(sid, title=push_title, body=push_body, tag=push_tag)
                config.trigger_deferred_restart(push_task=push_task)

    async def cancel_current_turn(self, sid: Optional[str] = None) -> None:
        target_sid = sid or self.session_id
        if not target_sid:
            return
        rt = self._runtimes.get(target_sid)
        if rt:
            rt.cancel_requested = True
            for key in list(rt.pending_permissions.keys()):
                await self.respond_permission(key, None, cancelled=True)
        turn_id = self._thread_turn_ids.get(target_sid)
        if turn_id:
            try:
                await self._send_request("turn/interrupt", {"threadId": target_sid, "turnId": turn_id}, timeout=5.0)
            except Exception as exc:
                logger.warning("Failed sending turn/interrupt for %s: %s", target_sid[:8], exc)

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
            base_model, effort = self._split_model_and_effort(model_id)

            forked = False
            if source_sid:
                fork_params: Dict[str, Any] = {
                    "threadId": source_sid,
                    "cwd": workdir,
                    "approvalPolicy": "never",
                    "sandbox": "read-only",
                }
                if base_model:
                    fork_params["model"] = base_model
                try:
                    fork_res = await self._send_request("thread/fork", fork_params, timeout=25.0)
                    temp_sid = str((fork_res.get("thread") or {}).get("id") or "").strip()
                    forked = bool(temp_sid)
                except Exception as exc:
                    logger.warning("Codex /btw thread/fork failed for %s (%s); falling back to thread/start", source_sid[:8], exc)

            if not temp_sid:
                start_params: Dict[str, Any] = {
                    "cwd": workdir,
                    "approvalPolicy": "never",
                    "sandbox": "read-only",
                }
                if base_model:
                    start_params["model"] = base_model
                start_res = await self._send_request("thread/start", start_params, timeout=25.0)
                temp_sid = str((start_res.get("thread") or {}).get("id") or "").strip()

            if not temp_sid:
                raise RuntimeError("无法创建临时旁白线程")

            btw_ctx["temp_sid"] = temp_sid
            self._btw_sessions[temp_sid] = btw_ctx
            self._btw_id_to_sid[bid] = temp_sid

            if btw_ctx.get("cancelled"):
                return

            if forked:
                prompt_text = (
                    "[临时旁白提问 /btw]\n"
                    "请直接基于当前对话上下文简明回答以下问题，不要修改文件或执行写操作：\n\n"
                    f"{btw_ctx['question']}"
                )
            else:
                prompt_text = self._build_btw_fallback_prompt(btw_ctx["question"], source_sid)

            loop = asyncio.get_running_loop()
            turn_fut: asyncio.Future = loop.create_future()
            self._turn_futures[temp_sid] = turn_fut

            turn_params: Dict[str, Any] = {
                "threadId": temp_sid,
                "input": [{"type": "text", "text": prompt_text}],
                "cwd": workdir,
                "approvalPolicy": "never",
                "sandboxPolicy": {"type": "readOnly"},
            }
            if base_model:
                turn_params["model"] = base_model
            if effort:
                turn_params["effort"] = effort

            t_res = await self._send_request("turn/start", turn_params, timeout=30.0)
            turn_id = str((t_res.get("turn") or {}).get("id") or "")
            if turn_id:
                self._thread_turn_ids[temp_sid] = turn_id

            if btw_ctx.get("cancelled"):
                if turn_id:
                    try:
                        await self._send_request("turn/interrupt", {"threadId": temp_sid, "turnId": turn_id}, timeout=5.0)
                    except Exception:
                        pass
                return

            await asyncio.wait_for(turn_fut, timeout=180.0)
        except asyncio.CancelledError:
            btw_ctx["cancelled"] = True
        except Exception as exc:
            if not btw_ctx.get("cancelled"):
                error_msg = str(exc) or type(exc).__name__
                logger.warning("Codex /btw failed (%s): %s", bid, error_msg)
        finally:
            self._btw_id_to_sid.pop(bid, None)
            if temp_sid:
                self._btw_sessions.pop(temp_sid, None)
                self._turn_futures.pop(temp_sid, None)
                self._thread_turn_ids.pop(temp_sid, None)
                try:
                    await self._send_request("thread/archive", {"threadId": temp_sid}, timeout=2.5)
                except Exception:
                    pass
                try:
                    await self._send_request("thread/unsubscribe", {"threadId": temp_sid}, timeout=2.5)
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
        turn_id = self._thread_turn_ids.get(temp_sid)
        if turn_id:
            try:
                await self._send_request("turn/interrupt", {"threadId": temp_sid, "turnId": turn_id}, timeout=5.0)
            except Exception:
                pass
        fut = self._turn_futures.get(temp_sid)
        if fut and not fut.done():
            fut.cancel()
        return True
