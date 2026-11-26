"""Prompt block construction, prompt queue management, turn execution, and cancel."""

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


class ACPTurnsMixin:
    """Prompt block construction, prompt queue management, turn execution, and cancel."""

    @staticmethod
    def _resolve_upload_file(im: Dict[str, Any]) -> Optional[Path]:
        if not isinstance(im, dict):
            return None
        uploads_root = config.UPLOADS_DIR.resolve()
        fname = str(im.get("filename") or "").strip()
        if fname and "/" not in fname and "\\" not in fname and ".." not in fname:
            cand = (uploads_root / fname).resolve()
            if cand.exists() and cand.is_file() and cand.parent == uploads_root:
                return cand
        abs_p = str(im.get("abs_path") or "").strip()
        if abs_p:
            cand = Path(abs_p).resolve()
            if cand.exists() and cand.is_file() and cand.parent == uploads_root:
                return cand
        return None

    @staticmethod
    async def _prepare_image_for_acp(path: Path) -> Tuple[bytes, str]:
        ext = path.suffix.lower()
        mime_map = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".webp": "image/webp",
            ".gif": "image/gif",
        }
        raw = path.read_bytes()
        needs_convert = ext not in mime_map
        # Downscale very large images (>4MB) so JSON-RPC base64 payload stays fast & reliable
        needs_resize = len(raw) > 4 * 1024 * 1024 and ext != ".gif"
        if (needs_convert or needs_resize) and shutil.which("sips"):
            with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tf:
                tmp_out = Path(tf.name)
            try:
                cmd = ["sips", "-s", "format", "jpeg", "-s", "formatOptions", "85"]
                if needs_resize:
                    cmd.extend(["-Z", "2048"])
                cmd.extend([str(path), "--out", str(tmp_out)])
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await asyncio.wait_for(proc.wait(), timeout=15.0)
                if proc.returncode == 0 and tmp_out.exists() and tmp_out.stat().st_size > 0:
                    return tmp_out.read_bytes(), "image/jpeg"
            except Exception as exc:
                logger.warning("Image conversion via sips failed for %s: %s", path.name, exc)
            finally:
                tmp_out.unlink(missing_ok=True)
        mime = mime_map.get(ext) or mimetypes.guess_type(str(path))[0] or "image/png"
        return raw, mime

    async def _build_prompt_blocks(
        self, prompt_text: str, images: Optional[List[Dict[str, Any]]] = None
    ) -> List[Dict[str, Any]]:
        blocks: List[Dict[str, Any]] = []
        fallback_paths: List[str] = []

        for im in images or []:
            path = self._resolve_upload_file(im)
            if not path:
                continue
            if self.supports_image_prompt:
                try:
                    data_bytes, mime_type = await self._prepare_image_for_acp(path)
                    blocks.append(
                        {
                            "type": "image",
                            "mimeType": mime_type,
                            "data": base64.b64encode(data_bytes).decode("ascii"),
                        }
                    )
                    continue
                except Exception as exc:
                    logger.warning("Failed encoding image %s for ACP: %s", path.name, exc)
            fallback_paths.append(str(path))

        final_text = prompt_text.strip()
        if fallback_paths:
            prefix = "[用户上传的图片本地路径]\n" + "\n".join(fallback_paths) + "\n\n"
            final_text = prefix + (final_text or "请查看以上图片并回复。")
        elif not final_text and blocks:
            final_text = "请查看以上图片并回复。"

        if final_text:
            blocks.append({"type": "text", "text": final_text})
        return blocks

    async def _broadcast_queue(self, sid: str) -> None:
        rt = self._runtimes.get(sid)
        q = list(rt.queued_prompts) if rt else []
        await self.broadcast({"type": "queue_update", "data": q}, sid=sid)

    async def remove_queued_prompt(self, queue_id: str, sid: Optional[str] = None) -> bool:
        target_sid = sid or self.session_id
        if not target_sid or not queue_id:
            return False
        rt = self._runtimes.get(target_sid)
        if not rt:
            return False
        before = len(rt.queued_prompts)
        rt.queued_prompts = [q for q in rt.queued_prompts if q.get("id") != queue_id]
        if len(rt.queued_prompts) != before:
            self._save_queue(target_sid, rt.queued_prompts)
            await self._broadcast_queue(target_sid)
            await self.broadcast({"type": "sessions_changed"})
            return True
        return False

    async def clear_queued_prompts(self, sid: Optional[str] = None) -> int:
        target_sid = sid or self.session_id
        if not target_sid:
            return 0
        rt = self._runtimes.get(target_sid)
        if not rt:
            return 0
        count = len(rt.queued_prompts)
        if count:
            rt.queued_prompts.clear()
            self._save_queue(target_sid, rt.queued_prompts)
            await self._broadcast_queue(target_sid)
            await self.broadcast({"type": "sessions_changed"})
        return count

    async def run_next_queued_prompt(self, sid: Optional[str] = None) -> bool:
        target_sid = sid or self.session_id
        if not target_sid:
            return False
        rt = self._runtimes.get(target_sid)
        if not rt or rt.is_busy or not rt.queued_prompts:
            return False
        nxt = rt.queued_prompts.pop(0)
        self._save_queue(target_sid, rt.queued_prompts)
        await self._broadcast_queue(target_sid)
        await self._start_prompt_turn(
            target_sid,
            nxt.get("text", ""),
            display_text=nxt.get("display_text"),
            images=nxt.get("images") or [],
        )
        return True

    async def steer_queued_prompt(self, queue_id: str, sid: Optional[str] = None) -> bool:
        """Interrupt the current busy turn (if any) and immediately send the chosen queued prompt."""
        target_sid = sid or self.session_id
        if not target_sid or not queue_id:
            return False
        rt = self._runtimes.get(target_sid)
        if not rt:
            return False
        idx = next((i for i, q in enumerate(rt.queued_prompts) if q.get("id") == queue_id), -1)
        if idx < 0:
            return False
        entry = rt.queued_prompts.pop(idx)
        self._save_queue(target_sid, rt.queued_prompts)
        await self._broadcast_queue(target_sid)
        await self.broadcast({"type": "sessions_changed"})

        if not rt.is_busy:
            await self._start_prompt_turn(
                target_sid,
                entry.get("text", ""),
                display_text=entry.get("display_text"),
                images=entry.get("images") or [],
            )
            return True

        rt.steer_next_prompt = entry
        await self.cancel_current_turn(sid=target_sid)
        logger.info("Steering busy session %s with queued prompt %s", target_sid[:8], queue_id)
        return True

    async def _start_prompt_turn(
        self,
        sid: str,
        clean_text: str,
        display_text: Optional[str] = None,
        images: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        rt = self._get_or_create_runtime(sid, workdir=self.workdir, title=self.session_title)
        img_list = list(images or [])
        user_item = {
            "id": f"user_{uuid.uuid4().hex[:12]}",
            "type": "user",
            "text": display_text if display_text is not None else clean_text,
            "images": img_list,
            "ts": time.time(),
        }
        self._unmark_session_empty(sid)
        self._add_or_update_item(user_item, sid=sid)
        self.store.upsert_session(sid, rt.workdir, rt.title, touch=True, profile=self.current_profile)
        await self.broadcast({"type": "item_upsert", "data": user_item}, sid=sid)

        self._cancel_unsolicited_push(rt)
        rt.is_busy = True
        rt.cancel_requested = False
        rt.active_assistant_id = None
        rt.active_thought_id = None
        await self.broadcast_status()
        await self.broadcast({"type": "sessions_changed"})

        # Run prompt in a background asyncio task tied to `sid` so WS disconnect OR switching sessions never cancels it!
        rt.prompt_task = asyncio.create_task(self._run_prompt_turn(sid, clean_text, img_list))

    async def send_prompt(
        self,
        text: str,
        display_text: Optional[str] = None,
        images: Optional[List[Dict[str, Any]]] = None,
        sid: Optional[str] = None,
    ) -> Dict[str, Any]:
        await self.start()
        target_sid = str(sid or "").strip() or self.session_id
        if not target_sid:
            raise RuntimeError("ACP 会话未就绪")
        rt = self._get_or_create_runtime(target_sid, workdir=self.workdir, title=self.session_title)

        img_list = list(images or [])
        clean_text = (text or "").strip()
        if not clean_text and not img_list:
            return {"queued": False}

        # If this session is currently busy, enqueue the message instead of rejecting!
        if rt.is_busy:
            entry = {
                "id": f"q_{uuid.uuid4().hex[:10]}",
                "text": clean_text,
                "display_text": display_text if display_text is not None else clean_text,
                "images": img_list,
                "ts": time.time(),
            }
            rt.queued_prompts.append(entry)
            self._save_queue(target_sid, rt.queued_prompts)
            await self._broadcast_queue(target_sid)
            await self.broadcast({"type": "sessions_changed"})
            logger.info("Queued prompt for busy session %s (queue_len=%d)", target_sid[:8], len(rt.queued_prompts))
            return {"queued": True, "queue_id": entry["id"], "queue_len": len(rt.queued_prompts)}

        await self._start_prompt_turn(target_sid, clean_text, display_text=display_text, images=img_list)
        return {"queued": False}

    async def _run_prompt_turn(
        self, sid: str, prompt_text: str, images: Optional[List[Dict[str, Any]]] = None
    ) -> None:
        rt = self._get_or_create_runtime(sid)
        start_item_count = len(rt.items)
        failed = False
        abnormal_stop = False
        stop_reason = "end_turn"
        early_stop_msg = ""
        try:
            prompt_blocks = await self._build_prompt_blocks(prompt_text, images)
            res = await self._send_request(
                "session/prompt",
                {
                    "sessionId": sid,
                    "prompt": prompt_blocks,
                },
                timeout=None,
            )
            stop_reason = str(res.get("stopReason", "end_turn") or "end_turn")
            sr_low = stop_reason.lower()
            turn_new_items = rt.items[start_item_count:]
            has_assistant_reply = any(it.get("type") == "assistant" and str(it.get("text") or "").strip() for it in turn_new_items)
            was_slash_cmd = prompt_text.lstrip().startswith("/")

            if sr_low not in ("end_turn", "cancelled"):
                abnormal_stop = True
                reason_map = {
                    "max_tokens": "达到单次输出长度上限 (max_tokens)",
                    "max_turn_requests": "达到单回合最大工具步数上限 (max_turn_requests)",
                    "refusal": "模型拒绝了本次请求 (refusal)",
                }
                reason_zh = reason_map.get(sr_low, f"非正常结束 ({stop_reason})")
                early_stop_msg = f"响应提前中断：{reason_zh}，可点击「继续」接续执行。"
            elif sr_low == "end_turn" and not rt.cancel_requested and not has_assistant_reply and not was_slash_cmd:
                abnormal_stop = True
                early_stop_msg = "模型上游响应异常中止（未生成最终回复），可点击「继续」接续执行。"

            if abnormal_stop:
                logger.warning(
                    "Abnormal early stop (sid=%s, stopReason=%s, new_items=%d): %s",
                    sid[:8],
                    stop_reason,
                    len(turn_new_items),
                    early_stop_msg,
                )
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
            else:
                logger.info("Prompt turn finished (sid=%s, stopReason=%s)", sid[:8], stop_reason)

            await self.broadcast(
                {
                    "type": "turn_end",
                    "data": {
                        "stopReason": stop_reason,
                        "usage": res.get("usage"),
                        "early_stop_message": early_stop_msg or None,
                    },
                },
                sid=sid,
            )
        except Exception as exc:
            failed = True
            logger.error("Prompt turn failed (sid=%s): %s", sid[:8], exc)
            err_item = {
                "id": f"err_{uuid.uuid4().hex[:12]}",
                "type": "error",
                "text": str(exc),
                "ts": time.time(),
            }
            self._add_or_update_item(err_item, sid=sid)
            await self.broadcast({"type": "item_upsert", "data": err_item}, sid=sid)
        finally:
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

            # Priority 1: explicit user steer ("⚡ 插队") immediately fires after cancelling the current turn!
            # Priority 2: auto-dequeue next queued prompt ONLY when turn finished cleanly.
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
                label = rt.title or "VibeOne"
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
        # Mark cancel_requested so queued follow-ups pause instead of firing unexpectedly after Stop
        if rt:
            rt.cancel_requested = True
            for key in list(rt.pending_permissions.keys()):
                await self.respond_permission(key, None, cancelled=True)
        try:
            await self._send_notification(
                "session/cancel",
                {"sessionId": target_sid},
            )
        except Exception as exc:
            logger.warning("Failed sending session/cancel for %s: %s", target_sid[:8], exc)
