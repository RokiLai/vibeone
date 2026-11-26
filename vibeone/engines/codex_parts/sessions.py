"""Codex thread listing, switching, removal, and forking mixin."""

import asyncio
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from fastapi import WebSocket

from ... import config
from ..base import SessionRuntime

logger = config.logger


class CodexSessionsMixin:
    """Thread listing cache, session switching, removal, activation, and forking."""

    async def _fetch_cached_threads(
        self,
        max_age: float = 15.0,
        *,
        stale_max_age: float = 120.0,
        force_refresh: bool = False,
    ) -> List[Dict[str, Any]]:
        now = time.time()
        age = now - self._thread_list_cache_time
        if not force_refresh and self._thread_list_cache is not None:
            if age < max_age:
                return self._thread_list_cache
            if age < stale_max_age:
                if (
                    self._thread_list_bg_refresh is None
                    or self._thread_list_bg_refresh.done()
                ):
                    async def _bg_refresh() -> None:
                        try:
                            await self._fetch_cached_threads(force_refresh=True)
                        except Exception:
                            pass

                    self._thread_list_bg_refresh = asyncio.create_task(
                        _bg_refresh(),
                        name=f"codex-thread-list-refresh-{self.current_profile}",
                    )
                return self._thread_list_cache

        if self._thread_list_in_flight is not None and not self._thread_list_in_flight.done():
            try:
                return await asyncio.shield(self._thread_list_in_flight)
            except Exception:
                pass

        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        self._thread_list_in_flight = fut
        try:
            res = await self._send_request("thread/list", {"archived": False, "limit": 100}, timeout=15.0)
            threads = [t for t in (res.get("data") or []) if isinstance(t, dict)]
            self._thread_list_cache = threads
            self._thread_list_cache_time = time.time()
            if not fut.done():
                fut.set_result(threads)
            return threads
        except Exception as exc:
            if not fut.done():
                fut.set_exception(exc)
            raise
        finally:
            if self._thread_list_in_flight is fut:
                self._thread_list_in_flight = None

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
                    self._remove_from_thread_cache(sid)
                    reclaimed_sids.add(sid)
                    continue
                self._unmark_session_empty(sid)
            if not include_archived and sid in archived_map:
                continue
            if sid not in viewed_sids and (sid in hidden or is_hidden_dir(cwd, cwd_to_inode.get(cwd))):
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
                self._remove_from_thread_cache(s)

        try:
            await self.start()
            threads = await self._fetch_cached_threads(max_age=15.0, force_refresh=force_refresh)
            for t in threads:
                if not isinstance(t, dict):
                    continue
                sid = str(t.get("id") or "").strip()
                if not sid or sid in self._btw_sessions:
                    continue
                raw_cwd = str(t.get("cwd") or "").strip() or self.workdir
                cwd = resolve_group_cwd(raw_cwd)
                if not include_archived and sid in archived_map:
                    merged.pop(sid, None)
                    continue
                if sid not in viewed_sids and (sid in hidden or is_hidden_dir(cwd, cwd_to_inode.get(cwd))):
                    merged.pop(sid, None)
                    continue
                title = str(t.get("name") or t.get("preview") or "").strip()
                if title:
                    title = " ".join(title.split())[:60]
                else:
                    title = "未命名会话"
                updated_ts = float(t.get("updatedAt") or t.get("createdAt") or 0)
                existing = merged.get(sid)
                if existing:
                    if title and existing["title"] in ("新会话", "Agy Remote", "VibeOne", "Codex", ""):
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
            pass
        except Exception as exc:
            logger.warning("Codex thread/list failed: %s", str(exc) or type(exc).__name__)

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
        return items

    async def _close_background_session(self, sid: str) -> None:
        self._remove_from_thread_cache(sid)
        if self.connected:
            try:
                await self._send_request("thread/unsubscribe", {"threadId": sid}, timeout=3.0)
            except Exception:
                pass

    async def remove_session(self, session_id: str) -> Dict[str, Any]:
        sid = str(session_id or "").strip()
        if not sid:
            raise ValueError("session_id 不能为空")
        async with self._session_lock:
            was_current = sid == self.session_id
            rt = self._runtimes.get(sid)
            if rt:
                self._cancel_unsolicited_push(rt)
                if rt.is_busy:
                    await self.cancel_current_turn(sid=sid)
            self._runtimes.pop(sid, None)
            self._unmark_session_empty(sid)
            self._delete_saved_usage(sid)
            self._delete_saved_queue(sid)
            self._unarchive_sessions({sid})
            self._add_hidden_sessions({sid})
            self.store.delete_session(sid)
            self._remove_from_thread_cache(sid)

            await self._close_background_session(sid)

            affected_clients = self._clients_for_session(sid) if self._client_views is not None else []
            if was_current or affected_clients:
                await self.start()
                if was_current:
                    self.session_id = None
                    remaining = [s for s in await self.list_sessions() if s["session_id"] != sid]
                    switched = False
                    for cand in remaining:
                        cand_cwd = cand["cwd"] if cand.get("cwd") and os.path.isdir(cand["cwd"]) else self.workdir
                        try:
                            await self._activate_existing_session_unlocked(
                                cand["session_id"],
                                cand_cwd,
                                cand.get("title"),
                                target_updated_at=cand.get("updated_at"),
                            )
                            switched = True
                            break
                        except Exception as exc:
                            logger.warning("Fallback switch to %s failed: %s", cand["session_id"], exc)
                    if not switched:
                        await self._create_new_session(clear_history=True)
                if self._client_views is not None:
                    for cws in affected_clients:
                        self._client_views[cws] = {"profile": self.current_profile, "session_id": self.session_id}
                    if affected_clients:
                        await self.broadcast(
                            {"type": "snapshot", "data": self.get_snapshot(sid=self.session_id)},
                            targets=affected_clients,
                        )
                elif was_current:
                    await self.broadcast({"type": "snapshot", "data": self.get_snapshot()})

            await self.broadcast({"type": "sessions_changed"})
            return {
                "ok": True,
                "removed_session_id": sid,
                "was_current": was_current,
                "current_session_id": self.session_id,
                "current_workdir": self.workdir,
            }

    async def switch_session(
        self,
        session_id: str,
        workdir: Optional[str] = None,
        title: Optional[str] = None,
        *,
        broadcast_all: bool = True,
        current_sid: Optional[str] = None,
        exclude_ws: Optional[WebSocket] = None,
    ) -> str:
        sid = str(session_id or "").strip()
        if not sid:
            raise ValueError("session_id 不能为空")
        async with self._session_lock:
            await self.start()
            prev_sid = current_sid or self.session_id
            prev_empty = bool(
                prev_sid
                and prev_sid != sid
                and self._is_session_empty(prev_sid)
                and not self._is_session_viewed_by_other(prev_sid, exclude_ws=exclude_ws)
            )
            existing_rt = self._runtimes.get(sid)
            if existing_rt and existing_rt.loaded_in_acp:
                target_cwd = self._normalize_workdir(workdir) if workdir else existing_rt.workdir
                existing_rt.workdir = target_cwd
                if title and existing_rt.title in ("会话", "新会话", "Agy Remote", "VibeOne", "Codex", ""):
                    existing_rt.title = title
                self.session_id = sid
                self.workdir = target_cwd
                self._sync_preferred_model(rt=existing_rt)
                if prev_empty and prev_sid and prev_sid != sid:
                    await self._reclaim_empty_session_unlocked(prev_sid)
                self._set_profile_session_id(sid)
                self._set_profile_workdir(self.workdir)
                self.store.set_meta("session_title", existing_rt.title)
                self._remember_workdir(self.workdir)
                if broadcast_all:
                    await self.broadcast({"type": "snapshot", "data": self.get_snapshot(sid=sid)})
                if prev_empty or broadcast_all:
                    await self.broadcast({"type": "sessions_changed"})
                return sid

            target_cwd = None
            target_title = title
            target_updated_at: Optional[float] = None
            local = self.store.get_session(sid)
            if local:
                if float(local.get("updated_at") or 0) > 0:
                    target_updated_at = float(local["updated_at"])
                if not workdir and os.path.isdir(local["workdir"]):
                    target_cwd = self._canonical_workdir(local["workdir"])
                if not target_title:
                    target_title = local["title"]
            if workdir:
                target_cwd = self._normalize_workdir(workdir)
            if not target_cwd or target_updated_at is None:
                for s in await self.list_sessions(include_hidden=True):
                    if s["session_id"] == sid:
                        if not target_cwd and s["cwd"] and os.path.isdir(s["cwd"]):
                            target_cwd = self._canonical_workdir(s["cwd"])
                        if not target_title:
                            target_title = s["title"]
                        if target_updated_at is None and float(s.get("updated_at") or 0) > 0:
                            target_updated_at = float(s["updated_at"])
                        break
            if not target_cwd:
                target_cwd = self.workdir

            self._unhide_session(sid)
            self._unhide_workdir(target_cwd)
            load_res, replayed_items = await self._load_or_resume_session(sid, target_cwd)
            loaded_sid = str((load_res.get("thread") or {}).get("id") or sid)
            rt = self._get_or_create_runtime(loaded_sid, workdir=target_cwd, title=target_title)
            rt.loaded_in_acp = True
            self.session_id = loaded_sid
            self.workdir = target_cwd
            self._apply_codex_session_meta(load_res, rt=rt)
            self._apply_loaded_items(loaded_sid, replayed_items)
            if prev_empty and prev_sid and prev_sid != loaded_sid:
                await self._reclaim_empty_session_unlocked(prev_sid)
            stored_sess = self.store.get_session(loaded_sid)
            rt.title = target_title or (stored_sess["title"] if stored_sess else None) or rt.title or "会话"
            rt.usage = self._get_saved_usage(loaded_sid)
            self._set_profile_session_id(loaded_sid)
            self._set_profile_workdir(self.workdir)
            self.store.set_meta("session_title", rt.title)
            self.store.upsert_session(
                loaded_sid, self.workdir, rt.title, updated_at=target_updated_at, touch=False, profile=self.current_profile
            )
            self._remember_workdir(self.workdir)
            if broadcast_all:
                await self.broadcast({"type": "snapshot", "data": self.get_snapshot(sid=loaded_sid)})
            if prev_empty or broadcast_all:
                await self.broadcast({"type": "sessions_changed"})
            return loaded_sid

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
            self._sync_preferred_model(rt=rt)
        else:
            load_res, replayed_items = await self._load_or_resume_session(sid, target_cwd)
            loaded_sid = str((load_res.get("thread") or {}).get("id") or sid)
            rt = self._get_or_create_runtime(loaded_sid, workdir=target_cwd, title=target_title)
            rt.loaded_in_acp = True
            self.session_id = loaded_sid
            self.workdir = target_cwd
            self._apply_codex_session_meta(load_res, rt=rt)
            self._apply_loaded_items(loaded_sid, replayed_items)
            rt.usage = self._get_saved_usage(loaded_sid)
        stored_sess = self.store.get_session(rt.session_id)
        rt.title = target_title or rt.title or (stored_sess["title"] if stored_sess else None) or "会话"
        self._set_profile_session_id(rt.session_id)
        self._set_profile_workdir(self.workdir)
        self.store.set_meta("session_title", rt.title)
        self.store.upsert_session(
            rt.session_id, self.workdir, rt.title, updated_at=target_updated_at, touch=False, profile=self.current_profile
        )
        self._remember_workdir(self.workdir)
