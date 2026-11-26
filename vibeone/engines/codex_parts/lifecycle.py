"""Codex subprocess lifecycle, thread creation/resume, and cache helpers."""

import asyncio
import json
import os
from typing import Any, Dict, List, Optional, Tuple

from fastapi import WebSocket

from ... import config
from ..base import SessionRuntime

logger = config.logger


class CodexLifecycleMixin:
    """Subprocess spawn/stop, thread cache helpers, and thread start/resume."""

    def _invalidate_thread_cache(self) -> None:
        self._thread_list_cache = None
        self._thread_list_cache_time = 0.0

    def _upsert_thread_cache(self, thread_obj: Dict[str, Any]) -> None:
        if self._thread_list_cache is None or not isinstance(thread_obj, dict):
            return
        tid = str(thread_obj.get("id") or "").strip()
        if not tid:
            return
        updated = [t for t in self._thread_list_cache if str(t.get("id") or "").strip() != tid]
        updated.insert(0, thread_obj)
        self._thread_list_cache = updated

    def _remove_from_thread_cache(self, sid: str) -> None:
        if self._thread_list_cache is None or not sid:
            return
        self._thread_list_cache = [
            t for t in self._thread_list_cache if str(t.get("id") or "").strip() != sid
        ]

    async def _spawn_and_initialize(self) -> None:
        if self.proc or self._stdout_task or self._stderr_task:
            await self._stop_unlocked()

        codex_bin = config.resolve_codex_binary()
        if not codex_bin:
            raise RuntimeError("未找到 `codex` 可执行文件，请先安装 Codex CLI")

        codex_home = config.ensure_codex_home(self.current_profile)
        self._codex_account_key = config.get_codex_account_cache_key(codex_home)
        self._load_cached_codex_account_state(self._codex_account_key)
        catalog_path = self._sync_codex_model_catalog_from_home(codex_home, self._codex_account_key)

        env = os.environ.copy()
        env["HOME"] = str(config.REAL_HOME)
        env["USER_REAL_HOME"] = str(config.REAL_HOME)
        env["CODEX_HOME"] = str(codex_home)

        cmd: List[str] = [codex_bin, "app-server"]
        if catalog_path is not None:
            cmd.extend(["-c", f"model_catalog_json={json.dumps(str(catalog_path))}"])
        cmd.append("--stdio")
        logger.info(
            "Starting Codex app-server: %s (cwd=%s, catalog_cache=%s)",
            " ".join(cmd),
            self.workdir,
            catalog_path.name if catalog_path else "none",
        )

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self.workdir,
            env=env,
            limit=16 * 1024 * 1024,
        )
        self._recent_stderr.clear()
        self.proc = proc
        self._stdout_task = asyncio.create_task(self._read_stdout_loop(proc))
        self._stderr_task = asyncio.create_task(self._read_stderr_loop(proc))

        try:
            try:
                init_res = await self._send_request(
                    "initialize",
                    {
                        "clientInfo": {"name": "vibeone", "title": "VibeOne", "version": "1.0.0"},
                        "capabilities": {"experimentalApi": True},
                    },
                    timeout=20.0,
                )
            except asyncio.TimeoutError as exc:
                tail = " | ".join(self._recent_stderr[-5:])
                raise RuntimeError(f"Codex app-server initialize 超时 (20s){': ' + tail if tail else ''}") from exc

            await self._send_notification("initialized", {})
            self.agent_info = {
                "name": "codex",
                "title": "OpenAI Codex",
                "userAgent": init_res.get("userAgent", ""),
            }
            self.supports_image_prompt = True
            logger.info("Codex app-server initialized: %s", self.agent_info.get("userAgent", ""))

            async def _fetch_account() -> None:
                try:
                    acc_res = await self._send_request("account/read", {}, timeout=10.0)
                    acct = acc_res.get("account") or {}
                    if acct:
                        self._codex_account = acct
                        self._save_cached_codex_account_state(self._codex_account_key)
                except asyncio.CancelledError:
                    pass
                except Exception as exc:
                    logger.warning("Codex account/read failed: %s", str(exc) or type(exc).__name__)

            async def _fetch_models() -> None:
                try:
                    models_res = await self._send_request("model/list", {"limit": 100}, timeout=10.0)
                    models, def_id = self._build_codex_models(models_res.get("data") or [])
                    if models:
                        self.available_models = models
                        self._default_model_id = def_id
                        self._save_cached_codex_account_state(self._codex_account_key)
                    if catalog_path is None:
                        self._sync_codex_model_catalog_from_home(codex_home, self._codex_account_key)
                except Exception as exc:
                    logger.warning("Codex model/list failed: %s", str(exc) or type(exc).__name__)

            async def _fetch_skills() -> None:
                await self._refresh_codex_skills()

            await asyncio.gather(_fetch_models(), _fetch_skills())
            if self._account_refresh_task and not self._account_refresh_task.done():
                self._account_refresh_task.cancel()
            self._account_refresh_task = asyncio.create_task(_fetch_account())

            saved_sid = self._get_profile_session_id()
            resumed = False
            if saved_sid:
                try:
                    load_res, replayed_items = await self._load_or_resume_session(saved_sid, self.workdir)
                    thread_obj = load_res.get("thread") or {}
                    loaded_sid = str(thread_obj.get("id") or saved_sid)
                    self.session_id = loaded_sid
                    thread_name = str(thread_obj.get("name") or "").strip()
                    preview_title = str(thread_obj.get("preview") or "").strip()
                    if preview_title:
                        preview_title = " ".join(preview_title.split())[:60]
                    resolved_title = (
                        thread_name
                        or self._resolve_stored_session_title(loaded_sid, fallback="")
                        or preview_title
                        or "Codex"
                    )
                    rt = self._get_or_create_runtime(
                        loaded_sid,
                        workdir=self.workdir,
                        title=resolved_title,
                    )
                    rt.loaded_in_acp = True
                    self._apply_codex_session_meta(load_res, rt=rt)
                    self._apply_loaded_items(loaded_sid, replayed_items)
                    rt.usage = self._get_saved_usage(loaded_sid)
                    self.store.upsert_session(loaded_sid, self.workdir, rt.title, profile=self.current_profile)
                    resumed = True
                except Exception as exc:
                    logger.info("Could not resume Codex thread %s (%s); creating new thread", saved_sid, exc)

            if not resumed:
                await self._create_new_session(clear_history=not bool(self.items))

            self.connected = True
            self.last_error = None
            await self.broadcast_status()
        except BaseException:
            await self._stop_unlocked()
            raise

    async def _load_or_resume_session(
        self, sid: str, cwd: str, force_load: bool = False
    ) -> Tuple[Dict[str, Any], Optional[List[Dict[str, Any]]]]:
        has_local = self.store.count_items(sid) > 0
        should_load_turns = (not has_local) or force_load
        approval_policy, sandbox_mode, _ = self._mode_to_codex_policy(self._get_preferred_mode())
        params: Dict[str, Any] = {
            "threadId": sid,
            "cwd": cwd,
            "excludeTurns": not should_load_turns,
            "approvalPolicy": approval_policy,
            "sandbox": sandbox_mode,
        }
        base_model, _ = self._split_model_and_effort(
            str(self._get_preferred_model() or self._default_model_id or "")
        )
        if base_model:
            params["model"] = base_model
        res = await self._send_request("thread/resume", params, timeout=25.0)
        if not should_load_turns:
            return res, None
        turns = (res.get("thread") or {}).get("turns") or []
        return res, self._convert_codex_turns_to_items(turns)

    async def _create_new_session(
        self, clear_history: bool = True, workdir: Optional[str] = None
    ) -> None:
        if workdir is not None:
            self.workdir = self._normalize_workdir(workdir)
        self._set_profile_workdir(self.workdir)
        self._unhide_workdir(self.workdir)
        self._remember_workdir(self.workdir)

        pref_mode = self._get_preferred_mode()
        approval_policy, sandbox_mode, _ = self._mode_to_codex_policy(pref_mode)
        pref_model = str(self._get_preferred_model() or self._default_model_id or "")
        base_model, _ = self._split_model_and_effort(pref_model)
        params: Dict[str, Any] = {
            "cwd": self.workdir,
            "approvalPolicy": approval_policy,
            "sandbox": sandbox_mode,
        }
        if base_model:
            params["model"] = base_model

        new_res = await self._send_request("thread/start", params, timeout=30.0)
        thread_obj = new_res.get("thread") or {}
        new_sid = str(thread_obj.get("id") or "").strip()
        if not new_sid:
            raise RuntimeError("Codex thread/start 未返回 thread.id")

        rt = SessionRuntime(new_sid, self.workdir, str(thread_obj.get("name") or "新会话"))
        rt.loaded_in_acp = True
        self._runtimes[new_sid] = rt
        self.session_id = new_sid
        self._apply_codex_session_meta(new_res, rt=rt)
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
        self._upsert_thread_cache(
            {
                "id": new_sid,
                "cwd": self.workdir,
                "name": rt.title,
                "createdAt": thread_obj.get("createdAt") or 0,
                "updatedAt": thread_obj.get("updatedAt") or 0,
            }
        )
        logger.info(
            "Created new Codex thread: %s (cwd=%s, mode=%s, model=%s)",
            new_sid,
            self.workdir,
            rt.current_mode,
            rt.current_model,
        )

    async def _archive_thread_bg(self, sid: str) -> None:
        if not self.connected or not sid:
            return
        try:
            await self._send_request("thread/archive", {"threadId": sid}, timeout=2.5)
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
        if self._thread_list_cache is not None:
            self._thread_list_cache = [
                t for t in self._thread_list_cache if str(t.get("id") or "").strip() != sid
            ]
        if self.connected:
            asyncio.create_task(self._archive_thread_bg(sid))
        return True

    async def _stop_unlocked(self) -> None:
        if self._account_refresh_task and not self._account_refresh_task.done():
            self._account_refresh_task.cancel()
        self._account_refresh_task = None
        if self._thread_list_bg_refresh and not self._thread_list_bg_refresh.done():
            self._thread_list_bg_refresh.cancel()
        self._thread_list_bg_refresh = None
        self._invalidate_thread_cache()
        for fut in list(self._turn_futures.values()):
            if not fut.done():
                fut.cancel()
        self._turn_futures.clear()
        self._thread_turn_ids.clear()
        await super()._stop_unlocked()

    async def _fork_current_session(
        self, sid: str, *, origin_ws: Optional[WebSocket] = None
    ) -> Dict[str, Any]:
        async with self._session_lock:
            rt = self._get_or_create_runtime(sid)
            approval_policy, sandbox_mode, _ = self._mode_to_codex_policy(rt.current_mode)
            base_model, _ = self._split_model_and_effort(rt.current_model)
            params: Dict[str, Any] = {
                "threadId": sid,
                "cwd": rt.workdir,
                "approvalPolicy": approval_policy,
                "sandbox": sandbox_mode,
            }
            if base_model:
                params["model"] = base_model
            res = await self._send_request("thread/fork", params, timeout=25.0)
            thread_obj = res.get("thread") or {}
            new_sid = str(thread_obj.get("id") or "").strip()
            if not new_sid:
                raise RuntimeError("Codex thread/fork 未返回新会话 ID")

            base_title = rt.title if rt.title and rt.title not in ("新会话", "VibeOne", "Codex") else "会话"
            fork_title = str(thread_obj.get("name") or f"{base_title} (分支)").strip()
            new_rt = SessionRuntime(new_sid, rt.workdir, fork_title)
            new_rt.loaded_in_acp = True
            new_rt.current_mode = rt.current_mode
            new_rt.current_model = rt.current_model
            new_rt.usage = dict(rt.usage)
            copied_items = [dict(it) for it in rt.items]
            new_rt.items = copied_items
            new_rt.items_by_id = {it["id"]: it for it in copied_items}

            self._runtimes[new_sid] = new_rt
            self.session_id = new_sid
            self.workdir = rt.workdir
            self._set_profile_session_id(new_sid)
            self._set_profile_workdir(self.workdir)
            self.store.set_meta("session_title", fork_title)
            self.store.upsert_session(new_sid, self.workdir, fork_title, touch=True, profile=self.current_profile)
            self._invalidate_thread_cache()
            if copied_items:
                self.store.replace_session_items(new_sid, copied_items)
            self._save_usage(new_sid, new_rt.usage)
            if origin_ws is not None and self._client_views is not None:
                self._client_views[origin_ws] = {"profile": self.current_profile, "session_id": new_sid}
                await self.broadcast({"type": "snapshot", "data": self.get_snapshot(sid=new_sid)}, targets=[origin_ws])
                await self.broadcast(
                    {
                        "type": "toast",
                        "data": {"level": "info", "message": f"已分叉出新会话：{fork_title}"},
                    },
                    targets=[origin_ws],
                )
            else:
                await self.broadcast({"type": "snapshot", "data": self.get_snapshot(sid=new_sid)})
                await self.broadcast(
                    {
                        "type": "toast",
                        "data": {"level": "info", "message": f"已分叉出新会话：{fork_title}"},
                    }
                )
            await self.broadcast({"type": "sessions_changed"})
            logger.info("Forked Codex thread %s -> %s", sid[:8], new_sid[:8])
            return {"queued": False, "forked_session_id": new_sid}
