"""Empty-session reclamation, hidden/archived tracking, and workdir visibility."""

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


class ACPVisibilityMixin:
    """Empty-session reclamation, hidden/archived tracking, and workdir visibility."""

    def _get_empty_sessions(self) -> Set[str]:
        raw = self.store.get_meta("empty_sessions", [])
        if not isinstance(raw, list):
            return set()
        return {str(x) for x in raw if x}

    def _mark_session_empty(self, sid: str) -> None:
        if not sid:
            return
        cur = self._get_empty_sessions()
        if sid not in cur:
            cur.add(sid)
            self.store.set_meta("empty_sessions", sorted(cur))

    def _unmark_session_empty(self, sid: str) -> None:
        if not sid:
            return
        cur = self._get_empty_sessions()
        if sid in cur:
            cur.discard(sid)
            self.store.set_meta("empty_sessions", sorted(cur))

    def _is_session_empty(self, sid: Optional[str]) -> bool:
        if not sid:
            return False
        rt = self._runtimes.get(sid)
        if rt and rt.is_busy:
            return False
        if self.store.count_items(sid) > 0 or (rt and len(rt.items) > 0):
            self._unmark_session_empty(sid)
            return False
        return True

    def _is_current_session_empty(self) -> bool:
        return self._is_session_empty(self.session_id)

    def _is_session_viewed_by_other(
        self, sid: Optional[str], exclude_ws: Optional[WebSocket] = None
    ) -> bool:
        if not sid or self._client_views is None:
            return False
        for ws in list(self.clients):
            if ws is exclude_ws:
                continue
            view = self._client_views.get(ws)
            if view and view.get("profile") == self.current_profile and view.get("session_id") == sid:
                return True
        return False

    async def _close_session_bg(self, sid: str) -> None:
        if not self.connected or not sid:
            return
        try:
            await self._send_request("session/close", {"sessionId": sid}, timeout=2.5)
        except Exception:
            pass

    async def _reclaim_empty_session_unlocked(self, sid: str) -> bool:
        if not sid:
            return False
        rt = self._runtimes.get(sid)
        if rt and rt.is_busy:
            return False
        if self.store.count_items(sid) > 0:
            self._unmark_session_empty(sid)
            return False
        self._unmark_session_empty(sid)
        self._delete_saved_usage(sid)
        self._delete_saved_queue(sid)
        self._runtimes.pop(sid, None)
        self.store.delete_session(sid)
        self._add_hidden_sessions({sid})
        self._remove_from_acp_session_list_cache(sid)
        if self.connected:
            asyncio.create_task(self._close_session_bg(sid))
        logger.info("Auto-reclaimed empty session: %s", sid)
        return True

    async def reset_session(
        self,
        workdir: Optional[str] = None,
        *,
        broadcast_all: bool = True,
        current_sid: Optional[str] = None,
        exclude_ws: Optional[WebSocket] = None,
    ) -> Optional[str]:
        async with self._session_lock:
            target_workdir = self._normalize_workdir(workdir) if workdir is not None else None
            # Do NOT cancel background running sessions — let them keep running concurrently!
            await self.start()

            # Reuse current session if it's already empty in the same directory;
            # otherwise auto-reclaim the abandoned empty session before creating a new one.
            prev_sid = current_sid or self.session_id
            prev_rt = self._runtimes.get(prev_sid) if prev_sid else None
            prev_workdir = prev_rt.workdir if prev_rt else self.workdir
            prev_empty = self._is_session_empty(prev_sid)
            other_targets = (
                [c for c in self._clients_for_profile() if c is not exclude_ws]
                if exclude_ws is not None
                else None
            )
            if prev_empty and (target_workdir is None or self._same_dir(prev_workdir, target_workdir)):
                if broadcast_all:
                    await self.broadcast({"type": "snapshot", "data": self.get_snapshot(sid=prev_sid)})
                await self.broadcast({"type": "sessions_changed"}, targets=other_targets)
                return prev_sid

            await self._create_new_session(clear_history=True, workdir=target_workdir)
            if (
                prev_empty
                and prev_sid
                and prev_sid != self.session_id
                and not self._is_session_viewed_by_other(prev_sid, exclude_ws=exclude_ws)
            ):
                await self._reclaim_empty_session_unlocked(prev_sid)
            if broadcast_all:
                await self.broadcast({"type": "snapshot", "data": self.get_snapshot()})
            await self.broadcast({"type": "sessions_changed"}, targets=other_targets)
            return self.session_id

    def _get_hidden_sessions(self) -> Set[str]:
        raw = self.store.get_meta("hidden_sessions", [])
        if not isinstance(raw, list):
            return set()
        return {str(x) for x in raw if x}

    def _add_hidden_sessions(self, sids: Set[str]) -> None:
        if not sids:
            return
        cur = self._get_hidden_sessions()
        cur.update(str(s) for s in sids if s)
        self.store.set_meta("hidden_sessions", sorted(cur))

    def _unhide_session(self, sid: str) -> None:
        if not sid:
            return
        cur = self._get_hidden_sessions()
        if sid in cur:
            cur.discard(sid)
            self.store.set_meta("hidden_sessions", sorted(cur))

    def _get_archived_sessions(self) -> Dict[str, float]:
        raw = self.store.get_meta("archived_sessions", {})
        if isinstance(raw, list):
            return {str(x): 0.0 for x in raw if x}
        if not isinstance(raw, dict):
            return {}
        out: Dict[str, float] = {}
        for k, v in raw.items():
            sid = str(k or "").strip()
            if not sid:
                continue
            try:
                out[sid] = float(v or 0.0)
            except Exception:
                out[sid] = 0.0
        return out

    def _add_archived_sessions(self, sids: Set[str]) -> int:
        if not sids:
            return 0
        cur = self._get_archived_sessions()
        now = time.time()
        added = 0
        for raw in sids:
            sid = str(raw or "").strip()
            if not sid:
                continue
            if sid not in cur:
                added += 1
            cur[sid] = cur.get(sid) or now
        self.store.set_meta("archived_sessions", cur)
        return added

    def _unarchive_sessions(self, sids: Set[str]) -> int:
        if not sids:
            return 0
        cur = self._get_archived_sessions()
        removed = 0
        for raw in sids:
            sid = str(raw or "").strip()
            if sid in cur:
                cur.pop(sid, None)
                removed += 1
        if removed:
            self.store.set_meta("archived_sessions", cur)
        return removed

    def _get_hidden_workdirs(self) -> List[str]:
        raw = self.store.get_meta("hidden_workdirs", [])
        if not isinstance(raw, list):
            return []
        return [str(x) for x in raw if isinstance(x, str) and x.strip()]

    def _is_workdir_hidden(
        self, path: str, hidden_workdirs: Optional[List[str]] = None
    ) -> bool:
        if not path:
            return False
        dirs = hidden_workdirs if hidden_workdirs is not None else self._get_hidden_workdirs()
        return any(self._same_dir(path, d) for d in dirs)

    def _build_hidden_workdir_matcher(
        self, hidden_workdirs: Optional[List[str]] = None
    ):
        """Precompute canonical paths and (st_dev, st_ino) for O(1) hidden-workdir checks."""
        dirs = hidden_workdirs if hidden_workdirs is not None else self._get_hidden_workdirs()
        if not dirs:
            return lambda _cwd, _inode=None: False
        canon_set: Set[str] = set()
        inode_set: Set[Tuple[int, int]] = set()
        for d in dirs:
            c = self._canonical_workdir(d)
            if not c:
                continue
            canon_set.add(c)
            try:
                if os.path.exists(c):
                    st = os.stat(c)
                    inode_set.add((st.st_dev, st.st_ino))
            except OSError:
                pass
        stat_cache: Dict[str, Optional[Tuple[int, int]]] = {}

        def _is_hidden(cwd: str, inode: Optional[Tuple[int, int]] = None) -> bool:
            if not cwd:
                return False
            if cwd in canon_set:
                return True
            if not inode_set:
                return False
            if inode is not None:
                return inode in inode_set
            if cwd in stat_cache:
                cached = stat_cache[cwd]
                return cached in inode_set if cached is not None else False
            canon = self._canonical_workdir(cwd)
            if canon in canon_set:
                return True
            key: Optional[Tuple[int, int]] = None
            try:
                if canon and os.path.exists(canon):
                    st = os.stat(canon)
                    key = (st.st_dev, st.st_ino)
            except OSError:
                key = None
            stat_cache[cwd] = key
            return key in inode_set if key is not None else False

        return _is_hidden

    def _add_hidden_workdir(self, path: str) -> None:
        raw = str(path or "").strip()
        if not raw:
            return
        norm = os.path.abspath(os.path.expanduser(raw))
        cur = self._get_hidden_workdirs()
        if any(self._same_dir(norm, existing) for existing in cur):
            return
        cur.append(norm)
        self.store.set_meta("hidden_workdirs", cur)

    def _unhide_workdir(self, path: str) -> bool:
        raw = str(path or "").strip()
        if not raw:
            return False
        cur = self._get_hidden_workdirs()
        kept = [d for d in cur if not self._same_dir(raw, d)]
        if len(kept) != len(cur):
            self.store.set_meta("hidden_workdirs", kept)
            return True
        return False

    def _forget_workdir(self, path: str) -> None:
        try:
            target_abs = os.path.abspath(os.path.expanduser(path))
            target_real = os.path.realpath(target_abs)
            recent = self.store.get_meta("recent_workdirs", [])
            if not isinstance(recent, list):
                return
            kept = []
            for item in recent:
                if not isinstance(item, str):
                    continue
                item_abs = os.path.abspath(item)
                if item_abs == target_abs or os.path.realpath(item_abs) == target_real:
                    continue
                kept.append(item)
            self.store.set_meta("recent_workdirs", kept)
        except Exception:
            pass

    @classmethod
    def _same_dir(cls, p1: str, p2: str) -> bool:
        c1 = cls._canonical_workdir(p1)
        c2 = cls._canonical_workdir(p2)
        if not c1 or not c2:
            return False
        if c1 == c2:
            return True
        try:
            if os.path.exists(c1) and os.path.exists(c2):
                return os.path.samefile(c1, c2)
        except OSError:
            pass
        return False
