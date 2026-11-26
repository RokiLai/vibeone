"""Archive/unarchive, remove session/workdir, switch session, and mode/model setters."""

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


class ACPSessionOpsMixin:
    """Archive/unarchive, remove session/workdir, switch session, and mode/model setters."""

    async def archive_sessions(self, session_ids: List[str]) -> Dict[str, Any]:
        target_sids = list(dict.fromkeys(str(s or "").strip() for s in (session_ids or []) if str(s or "").strip()))
        if not target_sids:
            raise ValueError("请选择至少一个会话")
        target_set = set(target_sids)

        async with self._session_lock:
            was_current = bool(self.session_id and self.session_id in target_set)
            prev_workdir = self.workdir

            for sid in target_sids:
                rt = self._runtimes.get(sid)
                if rt:
                    self._cancel_unsolicited_push(rt)
                    if rt.is_busy:
                        await self.cancel_current_turn(sid=sid)
                self._runtimes.pop(sid, None)
                await self._close_background_session(sid)

            self._add_archived_sessions(target_set)

            affected_clients: List[WebSocket] = []
            if self._client_views is not None:
                for cws in self._clients_for_profile():
                    csid = (self._client_views.get(cws) or {}).get("session_id")
                    if csid and csid in target_set:
                        affected_clients.append(cws)

            if was_current or affected_clients:
                await self.start()
                if was_current:
                    self.session_id = None
                    remaining = [
                        s for s in await self.list_sessions(include_archived=False)
                        if s["session_id"] not in target_set
                    ]
                    same_dir = [s for s in remaining if self._same_dir(s.get("cwd", ""), prev_workdir)]
                    other_dir = [s for s in remaining if not self._same_dir(s.get("cwd", ""), prev_workdir)]
                    switched = False
                    for cand in same_dir + other_dir:
                        cand_cwd = cand["cwd"] if cand.get("cwd") and os.path.isdir(cand["cwd"]) else prev_workdir
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
                            logger.warning("Fallback switch after archive to %s failed: %s", cand["session_id"], exc)
                    if not switched:
                        await self._create_new_session(clear_history=True, workdir=prev_workdir)
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
            logger.info("Archived %d session(s) (was_current=%s)", len(target_sids), was_current)
            return {
                "ok": True,
                "archived_session_ids": target_sids,
                "archived_count": len(target_sids),
                "was_current": was_current,
                "current_session_id": self.session_id,
                "current_workdir": self.workdir,
            }

    async def unarchive_sessions(self, session_ids: List[str]) -> Dict[str, Any]:
        target_sids = list(dict.fromkeys(str(s or "").strip() for s in (session_ids or []) if str(s or "").strip()))
        if not target_sids:
            raise ValueError("请选择至少一个会话")
        target_set = set(target_sids)

        async with self._session_lock:
            restored = self._unarchive_sessions(target_set)
            for sid in target_sids:
                local = self.store.get_session(sid)
                if local and local.get("workdir"):
                    self._unhide_workdir(local["workdir"])
            await self.broadcast({"type": "sessions_changed"})
            logger.info("Unarchived %d session(s)", restored)
            return {
                "ok": True,
                "unarchived_session_ids": target_sids,
                "unarchived_count": restored,
            }

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
            self._remove_from_acp_session_list_cache(sid)

            # Best-effort ACP close for both current and background sessions
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
            logger.info("Removed session from sidebar: %s (was_current=%s)", sid, was_current)
            return {
                "ok": True,
                "removed_session_id": sid,
                "was_current": was_current,
                "current_session_id": self.session_id,
                "current_workdir": self.workdir,
            }

    async def remove_workdir(self, workdir: str) -> Dict[str, Any]:
        raw = str(workdir or "").strip()
        if not raw:
            raise ValueError("目录路径不能为空")

        async with self._session_lock:
            all_sessions = await self.list_sessions(include_hidden=False)
            matching_sids = {
                s["session_id"]
                for s in all_sessions
                if self._same_dir(s.get("cwd", ""), raw)
            }
            was_current_dir = self._same_dir(self.workdir, raw) or (
                bool(self.session_id) and self.session_id in matching_sids
            )
            if self.session_id and was_current_dir:
                matching_sids.add(self.session_id)

            for msid in matching_sids:
                mrt = self._runtimes.get(msid)
                if mrt:
                    self._cancel_unsolicited_push(mrt)
                    if mrt.is_busy:
                        await self.cancel_current_turn(sid=msid)
                self._runtimes.pop(msid, None)

            self._add_hidden_workdir(raw)
            self._forget_workdir(raw)

            affected_clients: List[WebSocket] = []
            if self._client_views is not None:
                for cws in self._clients_for_profile():
                    csid = (self._client_views.get(cws) or {}).get("session_id")
                    if csid and csid in matching_sids:
                        affected_clients.append(cws)
            if was_current_dir or affected_clients:
                await self.start()
                if was_current_dir:
                    self.session_id = None
                    remaining = [
                        s for s in all_sessions
                        if s["session_id"] not in matching_sids and not self._same_dir(s.get("cwd", ""), raw)
                    ]
                    switched = False
                    for cand in remaining:
                        cand_cwd = cand["cwd"]
                        if not cand_cwd or not os.path.isdir(cand_cwd):
                            continue
                        try:
                            await self._activate_existing_session_unlocked(
                                cand["session_id"],
                                os.path.abspath(cand_cwd),
                                cand.get("title"),
                                target_updated_at=cand.get("updated_at"),
                            )
                            switched = True
                            break
                        except Exception as exc:
                            logger.warning("Fallback switch to %s failed: %s", cand["session_id"], exc)
                    if not switched:
                        fallback_dir = config.VIBEONE_WORKDIR if not self._same_dir(config.VIBEONE_WORKDIR, raw) and os.path.isdir(config.VIBEONE_WORKDIR) else os.path.expanduser("~")
                        await self._create_new_session(clear_history=True, workdir=fallback_dir)
                if self._client_views is not None:
                    for cws in affected_clients:
                        self._client_views[cws] = {"profile": self.current_profile, "session_id": self.session_id}
                    if affected_clients:
                        await self.broadcast(
                            {"type": "snapshot", "data": self.get_snapshot(sid=self.session_id)},
                            targets=affected_clients,
                        )
                elif was_current_dir:
                    await self.broadcast({"type": "snapshot", "data": self.get_snapshot()})

            await self.broadcast({"type": "sessions_changed"})
            logger.info("Removed workdir from sidebar: %s (sessions=%d, was_current=%s)", raw, len(matching_sids), was_current_dir)
            return {
                "ok": True,
                "removed_workdir": raw,
                "removed_count": len(matching_sids),
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
            # Do NOT cancel background running sessions — switch UI focus while previous session keeps running!
            await self.start()

            prev_sid = current_sid or self.session_id
            prev_empty = bool(
                prev_sid
                and prev_sid != sid
                and self._is_session_empty(prev_sid)
                and not self._is_session_viewed_by_other(prev_sid, exclude_ws=exclude_ws)
            )
            needs_remote_sync = self._has_newer_remote_update(sid)

            # Fast-path: if target session is already loaded in ACP (e.g. running in background)
            # and has no newer external updates on disk, switch focus instantly!
            existing_rt = self._runtimes.get(sid)
            if existing_rt and existing_rt.loaded_in_acp and (existing_rt.is_busy or not needs_remote_sync):
                target_cwd = self._normalize_workdir(workdir) if workdir else existing_rt.workdir
                existing_rt.workdir = target_cwd
                if title and existing_rt.title in ("会话", "新会话", "Agy Remote", "VibeOne", ""):
                    existing_rt.title = title
                self.session_id = sid
                self.workdir = target_cwd
                await self._apply_preferred_model(rt=existing_rt)
                if prev_empty and prev_sid and prev_sid != sid:
                    await self._reclaim_empty_session_unlocked(prev_sid)
                self._set_profile_session_id(sid)
                self._set_profile_workdir(self.workdir)
                self.store.set_meta("session_title", existing_rt.title)
                self._remember_workdir(self.workdir)
                logger.info(
                    "Switched focus to live ACP session %s (cwd=%s, items=%d, busy=%s)",
                    sid,
                    self.workdir,
                    len(existing_rt.items),
                    existing_rt.is_busy,
                )
                if broadcast_all:
                    await self.broadcast({"type": "snapshot", "data": self.get_snapshot(sid=sid)})
                if prev_empty or broadcast_all:
                    await self.broadcast({"type": "sessions_changed"})
                return sid

            # Resolve target workdir, title & real last-conversation timestamp (do NOT bump updated_at on open)
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
            remote_ts = float(self._remote_updated_at.get(sid) or 0.0)
            force_load = needs_remote_sync or self._has_newer_remote_update(sid, remote_ts=remote_ts)
            if force_load:
                load_res, replayed_items = await self._load_or_resume_session(sid, target_cwd, force_load=True)
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
            added_count = self._apply_loaded_items(loaded_sid, replayed_items)

            if prev_empty and prev_sid and prev_sid != loaded_sid:
                await self._reclaim_empty_session_unlocked(prev_sid)

            stored_sess = self.store.get_session(loaded_sid)
            rt.title = target_title or (stored_sess["title"] if stored_sess else None) or rt.title or "会话"
            rt.usage = self._get_saved_usage(loaded_sid)

            self._set_profile_session_id(loaded_sid)
            self._set_profile_workdir(self.workdir)
            self.store.set_meta("session_title", rt.title)
            effective_updated_at = max(float(target_updated_at or 0.0), remote_ts) or None
            should_touch = bool(force_load and (added_count > 0 or remote_ts > float(target_updated_at or 0.0)))
            self.store.upsert_session(
                loaded_sid,
                self.workdir,
                rt.title,
                updated_at=effective_updated_at,
                touch=should_touch,
                profile=self.current_profile,
            )
            self._remember_workdir(self.workdir)

            logger.info(
                "Switched ACP session to %s (cwd=%s, items=%d)",
                loaded_sid,
                self.workdir,
                len(rt.items),
            )
            if broadcast_all:
                await self.broadcast({"type": "snapshot", "data": self.get_snapshot(sid=loaded_sid)})
            if prev_empty or broadcast_all:
                await self.broadcast({"type": "sessions_changed"})
            return loaded_sid

    async def set_mode(self, mode_id: str, sid: Optional[str] = None) -> None:
        await self.start()
        target_sid = sid or self.session_id
        if not target_sid:
            return
        await self._send_request(
            "session/set_mode",
            {"sessionId": target_sid, "modeId": mode_id},
            timeout=10.0,
        )
        rt = self._runtimes.get(target_sid)
        if rt:
            rt.current_mode = mode_id
        else:
            self.current_mode = mode_id
        await self.broadcast_status()

    async def set_model(self, model_id: str, sid: Optional[str] = None) -> None:
        model_id = model_id.strip()
        if not model_id:
            raise ValueError("模型 ID 不能为空")
        await self.start()
        target_sid = sid or self.session_id
        if not target_sid:
            raise RuntimeError("ACP 会话未就绪")
        valid_ids = self._get_available_model_ids()
        if valid_ids and model_id not in valid_ids:
            raise ValueError(f"不支持的模型: {model_id}")
        await self._send_request(
            "session/set_model",
            {"sessionId": target_sid, "modelId": model_id},
            timeout=10.0,
        )
        rt = self._runtimes.get(target_sid)
        if rt:
            rt.current_model = model_id
        else:
            self.current_model = model_id
        self._set_preferred_model(model_id)
        logger.info("Switched ACP model to: %s (profile=%s)", model_id, self.current_profile)
        await self.broadcast_status()
