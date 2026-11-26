"""ACP subprocess startup/shutdown, model/mode setup, and profile switching."""

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


class ACPLifecycleMixin:
    """ACP subprocess startup/shutdown, model/mode setup, and profile switching."""

    async def start(self) -> None:
        async with self._start_lock:
            if self.proc and self.proc.returncode is None and self.connected:
                return
            await self._spawn_and_initialize()

    async def _spawn_and_initialize(self) -> None:
        if self.proc or self._stdout_task or self._stderr_task:
            await self._stop_unlocked()

        agy_bin = config.resolve_agy_binary(for_profile=self.current_profile)
        profile_home = config.ensure_profile_home(self.current_profile)
        env = os.environ.copy()
        env["HOME"] = str(profile_home)
        env["USER_REAL_HOME"] = str(config.REAL_HOME)
        token_file = config.get_profile_token_file(self.current_profile)
        if profile_home == config.REAL_HOME and not token_file.is_file():
            env.pop("AGY_CLI_FORCE_FILE_TOKEN_STORAGE", None)
        else:
            env.setdefault("AGY_CLI_FORCE_FILE_TOKEN_STORAGE", "1")
        logger.info(
            "Starting ACP subprocess: %s acp --stdio (profile=%s, cwd=%s)",
            agy_bin,
            self.current_profile,
            self.workdir,
        )

        from .. import acp as _acp_mod
        proc = await _acp_mod.asyncio.create_subprocess_exec(
            agy_bin,
            "acp",
            "--stdio",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self.workdir,
            env=env,
            limit=16 * 1024 * 1024,  # 16MB line buffer for large diffs/outputs
        )
        self.proc = proc
        self._stdout_task = asyncio.create_task(self._read_stdout_loop(proc))
        self._stderr_task = asyncio.create_task(self._read_stderr_loop(proc))

        try:
            # 1. JSON-RPC initialize
            init_res = await self._send_request(
                "initialize",
                {
                    "protocolVersion": 1,
                    "clientCapabilities": {},
                    "clientInfo": {"name": "vibeone", "version": "1.0.0"},
                },
                timeout=20.0,
            )
            self.agent_info = init_res.get("agentInfo", {})
            prompt_caps = (init_res.get("agentCapabilities") or {}).get("promptCapabilities") or {}
            if "image" in prompt_caps:
                self.supports_image_prompt = bool(prompt_caps.get("image"))
            logger.info("ACP initialized: %s (image_prompt=%s)", self.agent_info, self.supports_image_prompt)

            # 2. Try loading previous ACP session (with full history replay) if stored, else create new session
            saved_sid = self._get_profile_session_id()
            resumed = False
            if saved_sid:
                try:
                    load_res, replayed_items = await self._load_or_resume_session(saved_sid, self.workdir)
                    loaded_sid = load_res.get("sessionId", saved_sid)
                    self.session_id = loaded_sid
                    rt = self._get_or_create_runtime(
                        loaded_sid,
                        workdir=self.workdir,
                        title=self._resolve_stored_session_title(loaded_sid, fallback="VibeOne"),
                    )
                    rt.loaded_in_acp = True
                    self._apply_session_meta(load_res, rt=rt)
                    await self._apply_default_mode(rt=rt)
                    await self._apply_preferred_model(rt=rt)
                    self._apply_loaded_items(loaded_sid, replayed_items)
                    rt.usage = self._get_saved_usage(loaded_sid)
                    self.store.upsert_session(loaded_sid, self.workdir, rt.title, profile=self.current_profile)
                    resumed = True
                    logger.info(
                        "Loaded ACP session: %s (cwd=%s, items=%d)",
                        loaded_sid,
                        self.workdir,
                        len(rt.items),
                    )
                except Exception as exc:
                    logger.info("Could not load previous session %s (%s); creating new session", saved_sid, exc)

            if not resumed:
                await self._create_new_session(clear_history=not bool(self.items))

            self.connected = True
            self.last_error = None
            await self.broadcast_status()
        except BaseException:
            await self._stop_unlocked()
            raise

    def _has_newer_remote_update(self, sid: str, remote_ts: Optional[float] = None) -> bool:
        if not sid:
            return False
        r_ts = float(remote_ts) if remote_ts is not None else float(self._remote_updated_at.get(sid, 0.0))
        if r_ts <= 0:
            return False
        local = self.store.get_session(sid)
        local_ts = float(local.get("updated_at") or 0.0) if local else 0.0
        # 2s grace window avoids redundant reloads right after VibeOne finishes a turn
        return r_ts > local_ts + 2.0

    async def _load_or_resume_session(
        self, sid: str, cwd: str, force_load: bool = False
    ) -> Tuple[Dict[str, Any], Optional[List[Dict[str, Any]]]]:
        # If we already have local SQLite history for this session and no external update
        # requires replay, prefer `session/resume` so locally-recorded user messages &
        # uploaded images are NEVER overwritten by incomplete ACP `session/load` replays.
        if not force_load and self.store.count_items(sid) > 0:
            try:
                res = await self._send_request(
                    "session/resume",
                    {"sessionId": sid, "cwd": cwd, "mcpServers": []},
                    timeout=35.0,
                )
                return res, None
            except Exception as exc:
                logger.warning(
                    "session/resume failed for %s (%s: %s); falling back to session/load",
                    sid,
                    type(exc).__name__,
                    exc,
                )

        self._replaying_session_id = sid
        self._replay_items = []
        self._replay_by_id = {}
        self._replay_user_id = None
        self._replay_assistant_id = None
        self._replay_thought_id = None
        try:
            res = await self._send_request(
                "session/load",
                {"sessionId": sid, "cwd": cwd, "mcpServers": []},
                timeout=30.0,
            )
            replayed = list(self._replay_items)
            return res, replayed
        except Exception as exc:
            logger.warning("session/load failed for %s (%s); falling back to session/resume", sid, exc)
            self._replay_items = []
            self._replay_by_id = {}
            res = await self._send_request(
                "session/resume",
                {"sessionId": sid, "cwd": cwd, "mcpServers": []},
                timeout=20.0,
            )
            return res, None
        finally:
            self._replaying_session_id = None
            self._replay_items = []
            self._replay_by_id = {}
            self._replay_user_id = None
            self._replay_assistant_id = None
            self._replay_thought_id = None

    def _apply_session_meta(self, res: Dict[str, Any], rt: Optional[SessionRuntime] = None) -> None:
        target = rt or self._active_runtime()
        modes = res.get("modes") or {}
        if modes:
            if target:
                target.current_mode = modes.get("currentModeId", target.current_mode)
            else:
                self._fallback_mode = modes.get("currentModeId", self._fallback_mode)
            self.available_modes = modes.get("availableModes", self.available_modes)
        models = res.get("models") or {}
        if models:
            if target:
                target.current_model = models.get("currentModelId", target.current_model)
            else:
                self._fallback_model = models.get("currentModelId", self._fallback_model)
            avail = models.get("availableModels")
            if isinstance(avail, list) and avail:
                self.available_models = avail
                self._save_cached_profile_models(avail)

    def _load_cached_profile_models(self) -> None:
        if self.available_models:
            return
        raw = self.store.get_meta("profile_models_cache", {})
        if not isinstance(raw, dict):
            return
        cached = raw.get(self.current_profile)
        if isinstance(cached, list) and cached:
            self.available_models = [dict(m) for m in cached if isinstance(m, dict)]

    def _save_cached_profile_models(self, models: List[Dict[str, Any]]) -> None:
        if not models or not self.current_profile:
            return
        raw = self.store.get_meta("profile_models_cache", {})
        if not isinstance(raw, dict):
            raw = {}
        raw[self.current_profile] = list(models)
        self.store.set_meta("profile_models_cache", raw)

    def _resolve_default_mode_id(self) -> str:
        want = config.VIBEONE_DEFAULT_MODE.lower()
        for m in self.available_modes:
            mid = str(m.get("id") or "").strip()
            name = str(m.get("name") or "").strip()
            if mid.lower() == want or name.lower() == want:
                return mid or config.VIBEONE_DEFAULT_MODE
        return config.VIBEONE_DEFAULT_MODE

    async def _apply_default_mode(self, rt: Optional[SessionRuntime] = None) -> None:
        target = rt or self._active_runtime()
        if not target or not target.session_id:
            return
        target_mode = self._resolve_default_mode_id()
        if not target_mode or target.current_mode == target_mode:
            return
        try:
            await self._send_request(
                "session/set_mode",
                {"sessionId": target.session_id, "modeId": target_mode},
                timeout=10.0,
            )
            target.current_mode = target_mode
            logger.info("Applied default ACP mode: %s (session=%s)", target_mode, target.session_id)
        except Exception as exc:
            logger.warning("Could not apply default mode %s: %s", target_mode, exc)

    async def _apply_preferred_model(self, rt: Optional[SessionRuntime] = None) -> None:
        target = rt or self._active_runtime()
        if not target or not target.session_id or target.is_busy:
            return
        preferred_model = self._get_preferred_model()
        if not preferred_model or preferred_model == target.current_model:
            return
        valid_ids = self._get_available_model_ids()
        if valid_ids and preferred_model not in valid_ids:
            return
        try:
            await self._send_request(
                "session/set_model",
                {"sessionId": target.session_id, "modelId": preferred_model},
                timeout=10.0,
            )
            target.current_model = preferred_model
            logger.info(
                "Restored preferred ACP model: %s (profile=%s, session=%s)",
                preferred_model,
                self.current_profile,
                target.session_id,
            )
        except Exception as exc:
            logger.warning("Could not restore preferred model %s: %s", preferred_model, exc)

    async def _create_new_session(
        self, clear_history: bool = True, workdir: Optional[str] = None
    ) -> None:
        if workdir is not None:
            self.workdir = self._normalize_workdir(workdir)
        self._set_profile_workdir(self.workdir)
        self._unhide_workdir(self.workdir)
        self._remember_workdir(self.workdir)

        new_res = await self._send_request(
            "session/new",
            {"cwd": self.workdir, "mcpServers": []},
            timeout=30.0,
        )
        new_sid = new_res["sessionId"]
        rt = SessionRuntime(new_sid, self.workdir, "新会话")
        rt.loaded_in_acp = True
        self._runtimes[new_sid] = rt
        self.session_id = new_sid
        self._apply_session_meta(new_res, rt=rt)
        await self._apply_default_mode(rt=rt)
        await self._apply_preferred_model(rt=rt)
        self._set_profile_session_id(new_sid)

        if clear_history:
            rt.items = []
            rt.items_by_id = {}
            rt.title = "新会话"
            self.store.set_meta("session_title", rt.title)
            rt.usage = {"used": 0, "size": 0}
            self.store.upsert_session(
                new_sid, self.workdir, rt.title, updated_at=0.0, touch=False, profile=self.current_profile
            )
            self._mark_session_empty(new_sid)
        else:
            self.store.upsert_session(new_sid, self.workdir, rt.title, touch=False, profile=self.current_profile)
        self._upsert_acp_session_list_cache(
            {
                "sessionId": new_sid,
                "cwd": self.workdir,
                "title": rt.title,
                "updatedAt": 0.0 if clear_history else time.time(),
            }
        )
        logger.info(
            "Created new ACP session: %s (cwd=%s, mode=%s, model=%s)",
            new_sid,
            self.workdir,
            rt.current_mode,
            rt.current_model,
        )

    def _get_available_model_ids(self) -> Set[str]:
        ids: Set[str] = set()
        for m in self.available_models:
            mid = m.get("modelId") or m.get("id")
            if mid:
                ids.add(str(mid))
        return ids

    def get_model_display_name(self, model_id: str) -> str:
        for m in self.available_models:
            mid = m.get("modelId") or m.get("id")
            if mid == model_id:
                return str(m.get("name") or model_id)
        return model_id

    async def _stop_unlocked(self) -> None:
        self._flush_pending_items()
        self.connected = False
        if self._acp_session_list_bg_refresh and not self._acp_session_list_bg_refresh.done():
            self._acp_session_list_bg_refresh.cancel()
        self._acp_session_list_bg_refresh = None
        self._invalidate_acp_session_list_cache()
        for fut in list(self._pending_rpc.values()):
            if not fut.done():
                fut.cancel()
        self._pending_rpc.clear()

        for rt in self._runtimes.values():
            self._cancel_unsolicited_push(rt)
        tasks = [t for t in (self._stdout_task, self._stderr_task) if t and not t.done()]
        self._stdout_task = None
        self._stderr_task = None
        for t in tasks:
            t.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        proc = self.proc
        self.proc = None
        if proc and proc.returncode is None:
            try:
                proc.terminate()
                await asyncio.wait_for(proc.wait(), timeout=3.0)
            except Exception:
                try:
                    proc.kill()
                    await asyncio.wait_for(proc.wait(), timeout=1.0)
                except Exception:
                    pass

    async def stop(self) -> None:
        await self._stop_unlocked()

    async def switch_profile(self, profile: str, force: bool = False) -> Dict[str, Any]:
        profile = str(profile or "").strip()
        config.resolve_profile_home(profile)
        profiles = {p["name"]: p for p in config.list_agy_profiles(self.current_profile)}
        info = profiles.get(profile)
        if not info:
            raise ValueError(f"未找到账号配置: {profile}")
        if not info.get("logged_in"):
            raise ValueError(f"账号 {profile} 尚未登录，请先在终端登录该账号")

        if profile == self.current_profile and self.connected:
            return {"ok": True, "profile": self.current_profile, "email": info.get("email")}

        async with self._session_lock:
            busy_count = sum(1 for rt in self._runtimes.values() if rt.is_busy)
            if busy_count > 0 and not force:
                return {"ok": False, "needs_confirm": True, "busy_count": busy_count}

            for rt in list(self._runtimes.values()):
                if rt.prompt_task and not rt.prompt_task.done():
                    rt.prompt_task.cancel()
                for perm in list(rt.pending_permissions.values()):
                    fut = perm.get("future")
                    if fut and not fut.done():
                        fut.set_result({"outcome": {"outcome": "cancelled"}})
                rt.pending_permissions.clear()

            prev_profile = self.current_profile
            prev_workdir = self.workdir

            async with self._start_lock:
                await self._stop_unlocked()

                self._runtimes.clear()
                self._perm_to_sid.clear()
                self.session_id = None
                self._fallback_title = "VibeOne"
                self._fallback_usage = {"used": 0, "size": 0}
                self._fallback_items = []
                self._fallback_items_by_id = {}
                self._fallback_permissions = {}
                self.available_commands = []
                self.available_models = []
                self.last_error = None

                self.current_profile = profile
                self.store.set_meta("active_profile", profile)
                saved_workdir = self._get_profile_workdir()
                if saved_workdir and os.path.isdir(saved_workdir):
                    self.workdir = self._canonical_workdir(saved_workdir)
                else:
                    self.workdir = self.default_workdir
                self._unhide_workdir(self.workdir)
                self._remember_workdir(self.workdir)

                quota.clear_quota_cache()

                try:
                    await self._spawn_and_initialize()
                except Exception as exc:
                    logger.warning(
                        "Switching to profile %s failed (%s); rolling back to %s",
                        profile,
                        exc,
                        prev_profile,
                    )
                    self.current_profile = prev_profile
                    self.store.set_meta("active_profile", prev_profile)
                    self.workdir = prev_workdir
                    if prev_profile != profile:
                        try:
                            await self._spawn_and_initialize()
                        except Exception as rb_exc:
                            logger.error("Rollback to profile %s also failed: %s", prev_profile, rb_exc)
                    await self.broadcast({"type": "snapshot", "data": self.get_snapshot()})
                    await self.broadcast({"type": "sessions_changed"})
                    err_hint = (
                        "认证已过期或未登录，请先在电脑终端重新登录"
                        if "Authentication required" in str(exc)
                        else str(exc)
                    )
                    raise RuntimeError(
                        f"切换到账号 {profile} 失败（{err_hint}），已自动保持在 {prev_profile}"
                    ) from exc

            await self.broadcast({"type": "snapshot", "data": self.get_snapshot()})
            await self.broadcast({"type": "sessions_changed"})
            return {
                "ok": True,
                "profile": self.current_profile,
                "email": self._current_profile_email(),
            }
