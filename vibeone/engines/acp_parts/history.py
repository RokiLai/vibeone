"""History replay merging, external CLI sync, and item upsert helpers."""

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


class ACPHistoryMixin:
    """History replay merging, external CLI sync, and item upsert helpers."""

    @staticmethod
    def _item_match_key(item: Dict[str, Any]) -> Tuple[str, str]:
        kind = str(item.get("type") or "")
        if kind == "tool":
            tc_id = str(item.get("toolCallId") or "").strip()
            if tc_id:
                return ("tool", tc_id)
            name = str(item.get("name") or "").strip()
            title = str(item.get("title") or "").strip()
            return ("tool", f"{name}:{title}")
        text = " ".join(str(item.get("text") or "").split())
        return (kind, text)

    @classmethod
    def _extract_new_replayed_tail(
        cls, local_items: List[Dict[str, Any]], replayed_items: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Find genuinely new tail items from ACP replay without ever dropping local items."""
        if not local_items:
            return list(replayed_items)
        if not replayed_items:
            return []

        local_keys = [cls._item_match_key(it) for it in local_items]
        replay_keys = [cls._item_match_key(it) for it in replayed_items]
        matcher = difflib.SequenceMatcher(None, local_keys, replay_keys, autojunk=False)
        blocks = [b for b in matcher.get_matching_blocks() if b.size > 0]
        if not blocks:
            return []

        last_block = blocks[-1]
        replay_end = last_block.b + last_block.size
        if replay_end >= len(replayed_items):
            return []

        local_ids = {str(it.get("id") or "") for it in local_items}
        local_tool_ids = {
            str(it.get("toolCallId") or "")
            for it in local_items
            if it.get("type") == "tool" and it.get("toolCallId")
        }
        tail: List[Dict[str, Any]] = []
        for it in replayed_items[replay_end:]:
            it_id = str(it.get("id") or "")
            tc_id = str(it.get("toolCallId") or "") if it.get("type") == "tool" else ""
            if (it_id and it_id in local_ids) or (tc_id and tc_id in local_tool_ids):
                continue
            tail.append(it)
        return tail

    def _apply_loaded_items(
        self, session_id: Optional[str], replayed_items: Optional[List[Dict[str, Any]]]
    ) -> int:
        if not session_id:
            return 0
        rt = self._get_or_create_runtime(session_id)
        local_items = self.store.load_items(session_id)
        if replayed_items:
            if not local_items:
                rt.items = list(replayed_items)
                rt.items_by_id = {it["id"]: it for it in rt.items}
                self.store.replace_session_items(session_id, rt.items)
                return len(rt.items)

            # Lossless suffix merge: keep all local SQLite items (including web-sent user
            # messages & uploaded image previews) untouched, and append only newer tail items
            # added externally (e.g. via local `agy` CLI).
            new_tail = self._extract_new_replayed_tail(local_items, replayed_items)
            if not new_tail:
                rt.items = local_items
                rt.items_by_id = {it["id"]: it for it in rt.items}
                return 0

            base_ts = max(
                (float(it.get("ts") or 0.0) for it in local_items),
                default=0.0,
            )
            now = time.time()
            merged = list(local_items)
            for idx, item in enumerate(new_tail):
                item_copy = dict(item)
                ts_val = float(item_copy.get("ts") or now)
                if ts_val <= base_ts:
                    ts_val = base_ts + 0.001 * (idx + 1)
                item_copy["ts"] = ts_val
                merged.append(item_copy)
                self.store.upsert_item(session_id, item_copy)

            rt.items = merged
            rt.items_by_id = {it["id"]: it for it in rt.items}
            logger.info(
                "Merged %d new replayed item(s) into session %s (total=%d)",
                len(new_tail),
                session_id,
                len(rt.items),
            )
            return len(new_tail)
        else:
            rt.items = local_items
            rt.items_by_id = {it["id"]: it for it in rt.items}
            return 0

    def _schedule_session_sync(self, sid: str) -> None:
        if not sid or sid in self._syncing_sessions:
            return
        self._syncing_sessions.add(sid)

        async def _runner() -> None:
            try:
                await self.sync_session_history(sid, force=False)
            except Exception as exc:
                logger.warning("Background session sync failed for %s: %s", sid, exc)
            finally:
                self._syncing_sessions.discard(sid)

        asyncio.create_task(_runner(), name=f"sync-session-{sid[:8]}")

    async def sync_session_history(
        self, session_id: Optional[str] = None, force: bool = True
    ) -> Dict[str, Any]:
        """Reload session history from ACP/engine and losslessly append any newer external turns."""
        sid = str(session_id or self.session_id or "").strip()
        if not sid:
            return {"ok": False, "synced": False, "added": 0, "reason": "no_session"}

        async with self._session_lock:
            await self.start()
            rt = self._runtimes.get(sid)
            if rt and rt.is_busy:
                return {"ok": True, "synced": False, "added": 0, "session_id": sid, "reason": "busy"}

            if not force and not self._has_newer_remote_update(sid):
                return {"ok": True, "synced": False, "added": 0, "session_id": sid, "reason": "up_to_date"}

            local = self.store.get_session(sid)
            target_cwd = (
                (rt.workdir if rt and rt.workdir else None)
                or (self._canonical_workdir(local["workdir"]) if local and local.get("workdir") and os.path.isdir(local["workdir"]) else None)
                or self.workdir
            )
            load_res, replayed_items = await self._load_or_resume_session(sid, target_cwd, force_load=True)
            loaded_sid = str(
                load_res.get("sessionId")
                or (load_res.get("thread") or {}).get("id")
                or sid
            )
            rt = self._get_or_create_runtime(loaded_sid, workdir=target_cwd)
            rt.loaded_in_acp = True
            added = self._apply_loaded_items(loaded_sid, replayed_items)

            remote_ts = float(self._remote_updated_at.get(loaded_sid) or self._remote_updated_at.get(sid) or 0.0)
            local_ts = float(local.get("updated_at") or 0.0) if local else 0.0
            new_ts = max(local_ts, remote_ts)
            if added > 0 and new_ts <= local_ts:
                new_ts = time.time()
            if new_ts > local_ts:
                self.store.touch_session(loaded_sid, ts=new_ts)

            if added > 0:
                await self.broadcast(
                    {"type": "snapshot", "data": self.get_snapshot(sid=loaded_sid)},
                    sid=loaded_sid,
                )
                await self.broadcast({"type": "sessions_changed"})

            return {
                "ok": True,
                "synced": True,
                "added": added,
                "session_id": loaded_sid,
            }

    def _add_or_update_item(self, item: Dict[str, Any], sid: Optional[str] = None) -> None:
        target_sid = sid or self.session_id
        item_id = item["id"]
        if target_sid:
            rt = self._get_or_create_runtime(target_sid)
            if item_id in rt.items_by_id:
                rt.items_by_id[item_id].update(item)
            else:
                rt.items.append(item)
                rt.items_by_id[item_id] = item
            self.store.upsert_item(target_sid, rt.items_by_id[item_id])
        else:
            if item_id in self._fallback_items_by_id:
                self._fallback_items_by_id[item_id].update(item)
            else:
                self._fallback_items.append(item)
                self._fallback_items_by_id[item_id] = item

    @staticmethod
    def _extract_content_text(content_obj: Any) -> str:
        if not content_obj:
            return ""
        if isinstance(content_obj, str):
            return content_obj
        if isinstance(content_obj, dict):
            if content_obj.get("type") == "text":
                return str(content_obj.get("text", ""))
            if content_obj.get("type") == "content" and isinstance(content_obj.get("content"), dict):
                return ACPHistoryMixin._extract_content_text(content_obj["content"])
            if "text" in content_obj:
                return str(content_obj["text"])
        if isinstance(content_obj, list):
            parts = [ACPHistoryMixin._extract_content_text(c) for c in content_obj]
            return "\n".join(p for p in parts if p)
        return ""

    def _append_replay_update(self, update: Dict[str, Any]) -> None:
        kind = update.get("sessionUpdate", "")
        now = time.time()

        if kind == "user_message_chunk":
            self._replay_assistant_id = None
            self._replay_thought_id = None
            text = self._extract_content_text(update.get("content"))
            if not text:
                return
            if not self._replay_user_id:
                item_id = f"user_{uuid.uuid4().hex[:12]}"
                self._replay_user_id = item_id
                item = {"id": item_id, "type": "user", "text": text, "images": [], "ts": now}
                self._replay_items.append(item)
                self._replay_by_id[item_id] = item
            else:
                self._replay_by_id[self._replay_user_id]["text"] += text

        elif kind == "agent_message_chunk":
            self._replay_user_id = None
            self._replay_thought_id = None
            text = self._extract_content_text(update.get("content"))
            if not text:
                return
            if not self._replay_assistant_id:
                item_id = f"asst_{uuid.uuid4().hex[:12]}"
                self._replay_assistant_id = item_id
                item = {"id": item_id, "type": "assistant", "text": text, "ts": now}
                self._replay_items.append(item)
                self._replay_by_id[item_id] = item
            else:
                self._replay_by_id[self._replay_assistant_id]["text"] += text

        elif kind == "agent_thought_chunk":
            self._replay_user_id = None
            self._replay_assistant_id = None
            text = self._extract_content_text(update.get("content"))
            if not text:
                return
            if not self._replay_thought_id:
                item_id = f"thought_{uuid.uuid4().hex[:12]}"
                self._replay_thought_id = item_id
                item = {"id": item_id, "type": "thought", "text": text, "ts": now}
                self._replay_items.append(item)
                self._replay_by_id[item_id] = item
            else:
                self._replay_by_id[self._replay_thought_id]["text"] += text

        elif kind in ("tool_call", "tool_call_update"):
            self._replay_user_id = None
            self._replay_assistant_id = None
            self._replay_thought_id = None
            tc_id = update.get("toolCallId") or f"tc_{uuid.uuid4().hex[:10]}"
            item_id = f"tool_{tc_id}"
            existing = self._replay_by_id.get(item_id)

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
                "status": update.get("status") or (existing.get("status") if existing else "completed"),
                "args": update.get("args") or update.get("rawInput") or (existing.get("args") if existing else {}),
                "output": output_text or (existing.get("output") if existing else ""),
                "ts": existing["ts"] if existing else now,
            }
            if existing:
                existing.update(tool_item)
            else:
                self._replay_items.append(tool_item)
                self._replay_by_id[item_id] = tool_item
