"""Snapshot serialization, item pagination/detail, status payload, and WS broadcast."""

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


class ACPSnapshotsMixin:
    """Snapshot serialization, item pagination/detail, status payload, and WS broadcast."""

    SNAPSHOT_ITEM_LIMIT = 200

    def _background_counts(self, exclude_sid: Optional[str] = None) -> Tuple[int, int]:
        bg_busy = 0
        bg_perm = 0
        active_sid = exclude_sid if exclude_sid is not None else self.session_id
        for sid, rt in self._runtimes.items():
            if sid == active_sid:
                continue
            if rt.is_busy:
                bg_busy += 1
            bg_perm += len(rt.pending_permissions)
        return bg_busy, bg_perm

    @staticmethod
    def _slim_item(item: Dict[str, Any], keep_full: bool = False) -> Dict[str, Any]:
        if keep_full:
            return item
        itype = item.get("type")
        if itype == "tool":
            has_detail = bool(item.get("output") or item.get("args"))
            slim = {
                "id": item.get("id"),
                "type": "tool",
                "name": item.get("name"),
                "kind": item.get("kind"),
                "title": item.get("title"),
                "status": item.get("status"),
                "ts": item.get("ts"),
            }
            if has_detail:
                slim["detail_deferred"] = True
            return slim
        if itype == "thought":
            full_text = str(item.get("text") or "")
            if len(full_text) <= 90:
                return item
            return {
                "id": item.get("id"),
                "type": "thought",
                "text": full_text[:90],
                "ts": item.get("ts"),
                "detail_deferred": True,
            }
        return item

    def get_item_detail(self, session_id: Optional[str], item_id: str) -> Optional[Dict[str, Any]]:
        iid = str(item_id or "").strip()
        if not iid:
            return None
        sid = str(session_id or self.session_id or "").strip()
        rt = self._runtimes.get(sid) if sid else None
        if rt and rt.items:
            for it in reversed(rt.items):
                if it.get("id") == iid:
                    return it
        return self.store.get_item(iid)

    def get_items_page(
        self,
        session_id: Optional[str] = None,
        before_id: Optional[str] = None,
        before_offset: Optional[int] = None,
        limit: int = 200,
    ) -> Dict[str, Any]:
        sid = str(session_id or self.session_id or "").strip()
        if not sid:
            return {
                "session_id": "",
                "items": [],
                "total_items": 0,
                "items_offset": 0,
                "has_more_items": False,
            }
        rt = self._runtimes.get(sid)
        if rt and rt.items:
            all_items = rt.items
        else:
            all_items = self.store.load_items(session_id=sid)

        total = len(all_items)
        end_idx = total
        if before_id:
            for idx, it in enumerate(all_items):
                if it.get("id") == before_id:
                    end_idx = idx
                    break
        elif before_offset is not None:
            end_idx = max(0, min(total, int(before_offset)))

        page_size = max(1, min(300, int(limit or 200)))
        start_idx = max(0, end_idx - page_size)
        return {
            "session_id": sid,
            "items": [self._slim_item(it, keep_full=False) for it in all_items[start_idx:end_idx]],
            "total_items": total,
            "items_offset": start_idx,
            "has_more_items": start_idx > 0,
        }

    def get_snapshot(
        self, item_limit: int = SNAPSHOT_ITEM_LIMIT, sid: Optional[str] = None
    ) -> Dict[str, Any]:
        target_sid = str(sid or "").strip() or self.session_id
        bg_busy, bg_perm = self._background_counts(exclude_sid=target_sid)
        rt = self._runtimes.get(target_sid) if target_sid else self._active_runtime()
        if rt:
            all_items = rt.items
            is_busy = rt.is_busy
            title = rt.title
            workdir = rt.workdir
            current_mode = rt.current_mode
            current_model = rt.current_model
            usage = rt.usage
            pending_perms = list(rt.pending_permissions.values())
            queued_prompts = list(rt.queued_prompts)
        else:
            all_items = self.store.load_items(session_id=target_sid) if target_sid else self._fallback_items
            is_busy = False
            sess_row = self.store.get_session(target_sid) if target_sid else None
            title = (sess_row.get("title") if sess_row else None) or self._fallback_title
            workdir = (sess_row.get("workdir") if sess_row else None) or self.workdir
            current_mode = self._fallback_mode
            current_model = self._fallback_model
            usage = self._get_saved_usage(target_sid) if target_sid else self._fallback_usage
            pending_perms = list(self._fallback_permissions.values()) if not target_sid else []
            queued_prompts = self._get_saved_queue(target_sid) if target_sid else []

        total_items = len(all_items)
        if item_limit > 0 and total_items > item_limit:
            items_offset = total_items - item_limit
            raw_page = all_items[items_offset:]
            has_more_items = True
        else:
            items_offset = 0
            raw_page = all_items
            has_more_items = False

        # Keep items in the currently running turn full; slim historical collapsed tool/thought items
        live_cutoff = len(raw_page)
        if is_busy:
            for idx in range(len(raw_page) - 1, -1, -1):
                if raw_page[idx].get("type") == "user":
                    live_cutoff = idx
                    break
        page_items = [
            self._slim_item(it, keep_full=(i >= live_cutoff))
            for i, it in enumerate(raw_page)
        ]
        return {
            "connected": self.connected,
            "is_busy": is_busy,
            "bg_busy_count": bg_busy,
            "bg_perm_count": bg_perm,
            "backend": getattr(self, "backend", "agy"),
            "current_profile": self.current_profile,
            "current_profile_email": self._current_profile_email(),
            "session_id": target_sid,
            "title": title,
            "workdir": workdir,
            "agent_info": self.agent_info,
            "current_mode": current_mode,
            "available_modes": self.available_modes,
            "current_model": current_model,
            "available_models": self.available_models,
            "available_commands": self.available_commands,
            "usage": usage,
            "last_error": self.last_error,
            "pending_permissions": pending_perms,
            "queued_prompts": queued_prompts,
            "items": page_items,
            "total_items": total_items,
            "items_offset": items_offset,
            "has_more_items": has_more_items,
        }

    def _build_status_payload(self, sid: Optional[str] = None) -> Dict[str, Any]:
        target_sid = str(sid or "").strip() or self.session_id
        bg_busy, bg_perm = self._background_counts(exclude_sid=target_sid)
        rt = self._runtimes.get(target_sid) if target_sid else self._active_runtime()
        if rt:
            is_busy = rt.is_busy
            title = rt.title
            workdir = rt.workdir
            current_mode = rt.current_mode
            current_model = rt.current_model
        else:
            is_busy = False
            sess_row = self.store.get_session(target_sid) if target_sid else None
            title = (sess_row.get("title") if sess_row else None) or self._fallback_title
            workdir = (sess_row.get("workdir") if sess_row else None) or self.workdir
            current_mode = self._fallback_mode
            current_model = self._fallback_model
        return {
            "type": "status",
            "data": {
                "connected": self.connected,
                "is_busy": is_busy,
                "bg_busy_count": bg_busy,
                "bg_perm_count": bg_perm,
                "backend": getattr(self, "backend", "agy"),
                "current_profile": self.current_profile,
                "current_profile_email": self._current_profile_email(),
                "session_id": target_sid,
                "title": title,
                "workdir": workdir,
                "agent_info": self.agent_info,
                "current_mode": current_mode,
                "available_modes": self.available_modes,
                "current_model": current_model,
                "available_models": self.available_models,
                "available_commands": self.available_commands,
                "last_error": self.last_error,
            },
        }

    async def broadcast_status(self) -> None:
        if self._client_views is None:
            await self.broadcast(self._build_status_payload(self.session_id))
            return
        groups: Dict[Optional[str], List[WebSocket]] = {}
        for ws in self._clients_for_profile():
            csid = (self._client_views.get(ws) or {}).get("session_id")
            groups.setdefault(csid, []).append(ws)
        for csid, targets in groups.items():
            await self.broadcast(self._build_status_payload(csid), targets=targets)

    async def broadcast(
        self,
        event: Dict[str, Any],
        *,
        sid: Optional[str] = None,
        targets: Optional[List[WebSocket]] = None,
    ) -> None:
        if targets is not None:
            recipients = list(targets)
        elif sid is not None:
            recipients = self._clients_for_session(sid)
        else:
            recipients = self._clients_for_profile()
        if not recipients:
            return
        payload = json.dumps(event, ensure_ascii=False)
        any_enqueued = False
        for ws in recipients:
            if self.enqueue_client_payload(ws, payload):
                any_enqueued = True
        if any_enqueued:
            await self._yield_for_fast_senders()
