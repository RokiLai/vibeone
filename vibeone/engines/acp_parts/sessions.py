"""ACP session list caching, merging, activation, and full-text search."""

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


class ACPSessionsMixin:
    """ACP session list caching, merging, activation, and full-text search."""

    def _invalidate_acp_session_list_cache(self) -> None:
        self._acp_session_list_cache = None
        self._acp_session_list_cache_time = 0.0

    def _upsert_acp_session_list_cache(self, entry: Dict[str, Any]) -> None:
        if self._acp_session_list_cache is None or not isinstance(entry, dict):
            return
        sid = str(entry.get("sessionId") or "").strip()
        if not sid:
            return
        updated = [
            s for s in self._acp_session_list_cache
            if str(s.get("sessionId") or "").strip() != sid
        ]
        updated.insert(0, entry)
        self._acp_session_list_cache = updated

    def _remove_from_acp_session_list_cache(self, sid: str) -> None:
        if self._acp_session_list_cache is None or not sid:
            return
        self._acp_session_list_cache = [
            s for s in self._acp_session_list_cache
            if str(s.get("sessionId") or "").strip() != sid
        ]

    async def _fetch_cached_acp_sessions(
        self,
        *,
        max_age: float = 15.0,
        stale_max_age: float = 120.0,
        force_refresh: bool = False,
    ) -> List[Dict[str, Any]]:
        now = time.time()
        age = now - self._acp_session_list_cache_time
        if not force_refresh and self._acp_session_list_cache is not None:
            if age < max_age:
                return self._acp_session_list_cache
            if age < stale_max_age:
                if (
                    self._acp_session_list_bg_refresh is None
                    or self._acp_session_list_bg_refresh.done()
                ):
                    async def _bg_refresh() -> None:
                        try:
                            await self._fetch_cached_acp_sessions(force_refresh=True)
                        except Exception:
                            pass

                    self._acp_session_list_bg_refresh = asyncio.create_task(
                        _bg_refresh(),
                        name=f"acp-session-list-refresh-{self.current_profile}",
                    )
                return self._acp_session_list_cache

        if (
            self._acp_session_list_in_flight is not None
            and not self._acp_session_list_in_flight.done()
        ):
            try:
                return await asyncio.shield(self._acp_session_list_in_flight)
            except Exception:
                pass

        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        self._acp_session_list_in_flight = fut
        try:
            res = await self._send_request("session/list", {}, timeout=15.0)
            sessions = [s for s in (res.get("sessions") or []) if isinstance(s, dict)]
            self._acp_session_list_cache = sessions
            self._acp_session_list_cache_time = time.time()
            if not fut.done():
                fut.set_result(sessions)
            return sessions
        except Exception as exc:
            if not fut.done():
                fut.set_exception(exc)
            raise
        finally:
            if self._acp_session_list_in_flight is fut:
                self._acp_session_list_in_flight = None

    async def list_sessions(
        self,
        include_hidden: bool = False,
        active_session_id: Optional[str] = None,
        include_archived: bool = False,
        force_refresh: bool = False,
    ) -> List[Dict[str, Any]]:
        hidden = set() if include_hidden else self._get_hidden_sessions()
        hidden_dirs = [] if include_hidden else self._get_hidden_workdirs()
        is_hidden_dir = self._build_hidden_workdir_matcher(hidden_dirs)
        archived_map = self._get_archived_sessions()
        cur_sid = str(active_session_id or "").strip() or self.session_id
        viewed_sids = self._viewed_session_ids()
        if cur_sid:
            viewed_sids.add(cur_sid)
        merged: Dict[str, Dict[str, Any]] = {}
        inode_to_cwd: Dict[Tuple[int, int], str] = {}
        cwd_to_inode: Dict[str, Optional[Tuple[int, int]]] = {}

        def resolve_group_cwd(raw_cwd: str) -> str:
            canon = self._canonical_workdir(raw_cwd) or raw_cwd
            if canon in cwd_to_inode:
                existing_inode = cwd_to_inode[canon]
                if existing_inode is not None:
                    return inode_to_cwd.get(existing_inode, canon)
                return canon
            if canon and os.path.isdir(canon):
                try:
                    st = os.stat(canon)
                    key = (st.st_dev, st.st_ino)
                    cwd_to_inode[canon] = key
                    existing_cwd = inode_to_cwd.get(key)
                    if existing_cwd:
                        return existing_cwd
                    inode_to_cwd[key] = canon
                    return canon
                except OSError:
                    pass
            cwd_to_inode[canon] = None
            return canon

        if self.workdir:
            resolve_group_cwd(self.workdir)

        # 1. Local SQLite sessions (auto-reclaim any abandoned non-current empty sessions)
        empty_tracked = self._get_empty_sessions()
        reclaimed_sids: Set[str] = set()
        for row in self.store.list_sessions(limit=200, profile=self.current_profile):
            sid = row["session_id"]
            cwd = resolve_group_cwd(row["workdir"])
            item_count = int(row["item_count"] or 0)
            rt = self._runtimes.get(sid)
            if (
                sid not in viewed_sids
                and sid not in archived_map
                and not (rt and rt.is_busy)
                and (item_count == 0 or sid in empty_tracked)
            ):
                if item_count == 0:
                    self.store.delete_session(sid)
                    self._runtimes.pop(sid, None)
                    self._remove_from_acp_session_list_cache(sid)
                    reclaimed_sids.add(sid)
                    continue
                else:
                    self._unmark_session_empty(sid)
            if not include_archived and sid in archived_map:
                continue
            if sid not in viewed_sids and (
                sid in hidden or is_hidden_dir(cwd, cwd_to_inode.get(cwd))
            ):
                continue
            merged[sid] = {
                "session_id": sid,
                "cwd": cwd,
                "dir_name": Path(cwd).name or cwd,
                "title": row["title"] or "新会话",
                "updated_at": float(row["updated_at"] or 0),
                "has_local_history": item_count > 0,
                "is_current": sid == cur_sid,
            }

        # Also sweep any tracked empty sessions that weren't in local SQLite
        sessions_with_items = self.store.get_sessions_with_items()
        stale_empty = {
            s
            for s in empty_tracked
            if s not in viewed_sids and s not in sessions_with_items and s not in archived_map
        }
        reclaimed_sids.update(stale_empty)
        if reclaimed_sids:
            self._add_hidden_sessions(reclaimed_sids)
            hidden.update(reclaimed_sids)
            for s in reclaimed_sids:
                self._unmark_session_empty(s)
                self._remove_from_acp_session_list_cache(s)
            logger.info("Auto-reclaimed %d abandoned empty session(s)", len(reclaimed_sids))

        # 2. Live ACP session/list (cached with SWR; includes CLI & multi-directory sessions)
        try:
            await self.start()
            remote_sessions = await self._fetch_cached_acp_sessions(force_refresh=force_refresh)
            for s in remote_sessions:
                sid = str(s.get("sessionId") or "").strip()
                if not sid or sid in self._btw_sessions:
                    continue
                raw_acp_cwd = str(s.get("cwd") or "").strip() or self.workdir
                cwd = resolve_group_cwd(raw_acp_cwd)
                if not include_archived and sid in archived_map:
                    merged.pop(sid, None)
                    continue
                if sid not in viewed_sids and (
                    sid in hidden or is_hidden_dir(cwd, cwd_to_inode.get(cwd))
                ):
                    merged.pop(sid, None)
                    continue
                title = str(s.get("title") or "").strip() or "未命名会话"
                updated_ts = 0.0
                raw_ts = s.get("updatedAt")
                if isinstance(raw_ts, str) and raw_ts:
                    try:
                        updated_ts = datetime.fromisoformat(raw_ts.replace("Z", "+00:00")).timestamp()
                    except Exception:
                        updated_ts = 0.0
                elif isinstance(raw_ts, (int, float)):
                    updated_ts = float(raw_ts)

                if updated_ts > 0:
                    self._remote_updated_at[sid] = max(self._remote_updated_at.get(sid, 0.0), updated_ts)

                existing = merged.get(sid)
                if existing:
                    if title and (existing["title"] in ("新会话", "Agy Remote", "VibeOne", "")):
                        existing["title"] = title
                    if cwd and os.path.isdir(cwd):
                        existing["cwd"] = cwd
                        existing["dir_name"] = Path(cwd).name or cwd
                    if existing["has_local_history"]:
                        existing["updated_at"] = max(existing["updated_at"], updated_ts)
                    elif sid not in empty_tracked and updated_ts > 0 and existing["updated_at"] <= 0:
                        existing["updated_at"] = updated_ts
                else:
                    merged[sid] = {
                        "session_id": sid,
                        "cwd": cwd,
                        "dir_name": Path(cwd).name or cwd,
                        "title": title,
                        "updated_at": updated_ts,
                        "has_local_history": sid in sessions_with_items,
                        "is_current": sid == cur_sid,
                    }
        except asyncio.CancelledError:
            logger.warning("ACP session/list cancelled (returning local sessions only)")
        except Exception as exc:
            logger.warning(
                "ACP session/list failed (returning local sessions only): %s",
                str(exc) or type(exc).__name__,
            )

        items = list(merged.values())
        for it in items:
            sid = it["session_id"]
            rt = self._runtimes.get(sid)
            it["is_current"] = sid == cur_sid
            it["is_busy"] = bool(rt and rt.is_busy)
            it["pending_permissions"] = len(rt.pending_permissions) if rt else 0
            it["queued_count"] = len(rt.queued_prompts) if rt else 0
            it["is_archived"] = sid in archived_map
            it["archived_at"] = float(archived_map.get(sid, 0.0))
        items.sort(
            key=lambda x: (
                not (x["session_id"] in empty_tracked and (x["updated_at"] or 0) <= 0),
                -(x["updated_at"] or 0),
            )
        )
        # If the currently active idle session has newer activity on disk (e.g. continued
        # in the local `agy` CLI terminal), trigger a background lossless history sync.
        cur_sid = self.session_id
        if cur_sid and self._has_newer_remote_update(cur_sid):
            cur_rt = self._runtimes.get(cur_sid)
            if not (cur_rt and cur_rt.is_busy):
                self._schedule_session_sync(cur_sid)
        return items

    async def _activate_existing_session_unlocked(
        self,
        sid: str,
        target_cwd: str,
        target_title: Optional[str] = None,
        target_updated_at: Optional[float] = None,
    ) -> None:
        target_cwd = self._canonical_workdir(target_cwd) or target_cwd
        self._unhide_session(sid)
        self._unhide_workdir(target_cwd)

        existing_rt = self._runtimes.get(sid)
        if existing_rt and existing_rt.loaded_in_acp:
            rt = existing_rt
            rt.workdir = target_cwd
            self.session_id = sid
            self.workdir = target_cwd
            await self._apply_preferred_model(rt=rt)
        else:
            load_res, replayed_items = await self._load_or_resume_session(sid, target_cwd)
            loaded_sid = load_res.get("sessionId", sid)
            rt = self._get_or_create_runtime(loaded_sid, workdir=target_cwd, title=target_title)
            rt.loaded_in_acp = True
            self.session_id = loaded_sid
            self.workdir = target_cwd
            self._apply_session_meta(load_res, rt=rt)
            await self._apply_default_mode(rt=rt)
            await self._apply_preferred_model(rt=rt)
            self._apply_loaded_items(loaded_sid, replayed_items)
            rt.usage = self._get_saved_usage(loaded_sid)

        stored_sess = self.store.get_session(rt.session_id)
        rt.title = target_title or rt.title or (stored_sess["title"] if stored_sess else None) or "会话"

        self._set_profile_session_id(rt.session_id)
        self._set_profile_workdir(self.workdir)
        self.store.set_meta("session_title", rt.title)
        self.store.upsert_session(
            rt.session_id,
            self.workdir,
            rt.title,
            updated_at=target_updated_at,
            touch=False,
            profile=self.current_profile,
        )
        self._remember_workdir(self.workdir)

    async def _close_background_session(self, sid: str) -> None:
        if self.connected:
            try:
                await self._send_request("session/close", {"sessionId": sid}, timeout=3.0)
            except Exception:
                pass

    async def search_sessions(
        self,
        query: str,
        *,
        scope: str = "active",
        active_session_id: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """Full-text search sessions strictly scoped to this manager's current profile."""
        raw_q = str(query or "").strip()
        if not raw_q:
            return []

        scope_norm = str(scope or "active").strip().lower()
        if scope_norm not in ("active", "archived", "all"):
            scope_norm = "active"

        hidden = self._get_hidden_sessions()
        hidden_dirs = self._get_hidden_workdirs()
        archived_map = self._get_archived_sessions()
        cur_sid = str(active_session_id or "").strip() or self.session_id
        viewed_sids = self._viewed_session_ids()
        if cur_sid:
            viewed_sids.add(cur_sid)

        excluded_sids: Set[str] = set(hidden) - viewed_sids
        only_sids: Optional[Set[str]] = None
        if scope_norm == "active":
            excluded_sids.update(archived_map.keys())
        elif scope_norm == "archived":
            only_sids = set(archived_map.keys()) - excluded_sids
            if not only_sids:
                return []

        self._flush_pending_items()
        profile = self.current_profile
        raw_hits = await asyncio.to_thread(
            self.store.search_sessions,
            profile,
            raw_q,
            limit=max(1, min(int(limit or 50), 200)),
            excluded_session_ids=excluded_sids,
            only_session_ids=only_sids,
        )

        is_hidden_dir = self._build_hidden_workdir_matcher(hidden_dirs)
        results: List[Dict[str, Any]] = []
        for row in raw_hits:
            sid = row["session_id"]
            if sid in self._btw_sessions:
                continue
            cwd = self._canonical_workdir(row["workdir"]) or row["workdir"]
            if sid not in viewed_sids and is_hidden_dir(cwd):
                continue
            rt = self._runtimes.get(sid)
            archived_at = float(archived_map.get(sid, 0.0) or 0.0)
            results.append(
                {
                    "session_id": sid,
                    "cwd": cwd,
                    "dir_name": Path(cwd).name or cwd,
                    "title": row["title"] or "新会话",
                    "updated_at": float(row["updated_at"] or 0.0),
                    "has_local_history": True,
                    "is_current": sid == cur_sid,
                    "is_busy": bool(rt and rt.is_busy),
                    "pending_permissions": len(rt.pending_permissions) if rt else 0,
                    "is_archived": sid in archived_map,
                    "archived_at": archived_at,
                    "matched_in": row["matched_in"],
                    "match_count": int(row["match_count"] or 0),
                    "snippet": row["snippet"] or "",
                    "match_type": row["match_type"] or "",
                    "match_item_id": row["match_item_id"] or "",
                    "match_ts": float(row["match_ts"] or 0.0),
                }
            )
        return results
