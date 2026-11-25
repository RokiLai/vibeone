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

from .. import config, quota
from ..push import WebPushManager
from ..store import SessionStore

logger = config.logger

# ---------------------------------------------------------------------------
# ACP Subprocess Manager (JSON-RPC 2.0 over stdio)
# ---------------------------------------------------------------------------

class SessionRuntime:
    """Isolated per-session runtime state enabling concurrent multi-session execution."""

    def __init__(self, session_id: str, workdir: str, title: str = "会话"):
        self.session_id: str = session_id
        self.workdir: str = workdir
        self.title: str = title
        self.items: List[Dict[str, Any]] = []
        self.items_by_id: Dict[str, Dict[str, Any]] = {}
        self.is_busy: bool = False
        self.active_assistant_id: Optional[str] = None
        self.active_thought_id: Optional[str] = None
        self.prompt_task: Optional[asyncio.Task] = None
        self.pending_permissions: Dict[str, Dict[str, Any]] = {}
        self.usage: Dict[str, Any] = {"used": 0, "size": 0}
        self.current_mode: str = config.VIBEONE_DEFAULT_MODE
        self.current_model: str = ""
        self.loaded_in_acp: bool = False
        self.queued_prompts: List[Dict[str, Any]] = []
        self.steer_next_prompt: Optional[Dict[str, Any]] = None
        self.cancel_requested: bool = False
        self.unsolicited_push_task: Optional[asyncio.Task] = None


class AgyACPManager:
    """
    Hosts `agy acp --stdio` via asyncio.create_subprocess_exec.
    - Independent of WebSocket client lifecycles (tasks keep running when phone locks/closes).
    - Supports concurrent execution across multiple sessions via per-session SessionRuntime.
    - Persists structured items in SQLite + memory for instant reconnect recovery.
    - Supports bidirectional `session/request_permission` approvals and `session/cancel`.
    """

    def __init__(
        self,
        workdir: str,
        store: SessionStore,
        push: Optional[WebPushManager] = None,
        profile: Optional[str] = None,
        clients: Optional[Set[WebSocket]] = None,
        client_visible: Optional[Dict[WebSocket, bool]] = None,
        client_views: Optional[Dict[WebSocket, Dict[str, Optional[str]]]] = None,
        is_active_fn: Optional[Any] = None,
    ):
        self.workdir = workdir
        self.store = store
        self.push = push
        self._is_active_fn = is_active_fn
        self.proc: Optional[asyncio.subprocess.Process] = None
        self._stdout_task: Optional[asyncio.Task] = None
        self._stderr_task: Optional[asyncio.Task] = None
        self._write_lock = asyncio.Lock()
        self._start_lock = asyncio.Lock()
        self._session_lock = asyncio.Lock()

        self._rpc_id = 0
        self._pending_rpc: Dict[int, asyncio.Future] = {}

        # Active WebSocket subscribers, foreground visibility, and per-client (profile, session_id) views
        self.clients: Set[WebSocket] = clients if clients is not None else set()
        self._client_visible: Dict[WebSocket, bool] = client_visible if client_visible is not None else {}
        self._client_views: Optional[Dict[WebSocket, Dict[str, Optional[str]]]] = client_views
        self._push_tasks: Set[asyncio.Task] = set()

        # Global ACP agent metadata
        self.agent_info: Dict[str, Any] = {}
        self.supports_image_prompt: bool = True
        self.available_modes: List[Dict[str, Any]] = []
        self.available_models: List[Dict[str, Any]] = []
        self.available_commands: List[Dict[str, Any]] = []
        self.connected: bool = False
        self.last_error: Optional[str] = None

        # Multi-session concurrent runtimes keyed by session_id
        self._runtimes: Dict[str, SessionRuntime] = {}
        self._perm_to_sid: Dict[str, str] = {}
        self.session_id: Optional[str] = None

        # Fallback attributes when no session_id is active yet
        self._fallback_title: str = "VibeOne"
        self._fallback_mode: str = config.VIBEONE_DEFAULT_MODE
        self._fallback_model: str = ""
        self._fallback_usage: Dict[str, Any] = {"used": 0, "size": 0}
        self._fallback_items: List[Dict[str, Any]] = []
        self._fallback_items_by_id: Dict[str, Dict[str, Any]] = {}
        self._fallback_permissions: Dict[str, Dict[str, Any]] = {}

        # Replay buffer used during `session/load` history streaming
        self._replaying_session_id: Optional[str] = None
        self._replay_items: List[Dict[str, Any]] = []
        self._replay_by_id: Dict[str, Dict[str, Any]] = {}
        self._replay_user_id: Optional[str] = None
        self._replay_assistant_id: Optional[str] = None
        self._replay_thought_id: Optional[str] = None

        # Track latest remote updatedAt reported by ACP session/list for external CLI sync
        self._remote_updated_at: Dict[str, float] = {}
        self._syncing_sessions: Set[str] = set()

        # Ephemeral single-turn /btw side-question sessions (never persisted or listed)
        self._btw_sessions: Dict[str, Dict[str, Any]] = {}
        self._ignored_close_sent_at: Dict[str, float] = {}
        self._btw_id_to_sid: Dict[str, str] = {}

        # Profile (multi-account) state
        if profile:
            self.current_profile = profile
        else:
            saved_profile = str(self.store.get_meta("active_profile") or "").strip()
            if saved_profile:
                try:
                    config.resolve_profile_home(saved_profile)
                    self.current_profile = saved_profile
                except Exception:
                    self.current_profile = config.DEFAULT_AGY_PROFILE
            else:
                self.current_profile = config.DEFAULT_AGY_PROFILE

        # Restore persisted workdir & history from previous session if any
        self._load_cached_profile_models()
        self._fallback_model = self._get_preferred_model()
        self.default_workdir = self._canonical_workdir(workdir) or workdir
        self.workdir = self.default_workdir
        saved_workdir = self._get_profile_workdir()
        if saved_workdir and os.path.isdir(saved_workdir):
            self.workdir = self._canonical_workdir(saved_workdir)
        self._unhide_workdir(self.workdir)
        self._remember_workdir(self.workdir)

        saved_sid = self._get_profile_session_id()
        if saved_sid:
            saved_title = self._resolve_stored_session_title(saved_sid, fallback="VibeOne")
            rt = self._get_or_create_runtime(saved_sid, workdir=self.workdir, title=saved_title)
            if not rt.current_model and self._fallback_model:
                rt.current_model = self._fallback_model
            self._fallback_title = rt.title
            self._fallback_usage = rt.usage
            self._fallback_items = rt.items
            self._fallback_items_by_id = rt.items_by_id
            self.store.upsert_session(saved_sid, self.workdir, rt.title, profile=self.current_profile)

    def _get_profile_session_id(self) -> Optional[str]:
        m = self.store.get_meta("active_sessions_by_profile", {})
        if isinstance(m, dict) and self.current_profile in m:
            return m[self.current_profile] or None
        if self.current_profile == config.DEFAULT_AGY_PROFILE:
            return self.store.get_meta("active_session_id")
        return None

    def _set_profile_session_id(self, sid: Optional[str]) -> None:
        m = self.store.get_meta("active_sessions_by_profile", {})
        if not isinstance(m, dict):
            m = {}
        if sid:
            m[self.current_profile] = sid
        else:
            m.pop(self.current_profile, None)
        self.store.set_meta("active_sessions_by_profile", m)
        if self.current_profile == config.DEFAULT_AGY_PROFILE and sid:
            self.store.set_meta("active_session_id", sid)

    def _get_profile_workdir(self) -> Optional[str]:
        m = self.store.get_meta("active_workdirs_by_profile", {})
        if isinstance(m, dict) and self.current_profile in m:
            return m[self.current_profile] or None
        if self.current_profile == config.DEFAULT_AGY_PROFILE:
            return self.store.get_meta("active_workdir")
        return None

    def _set_profile_workdir(self, cwd: str) -> None:
        m = self.store.get_meta("active_workdirs_by_profile", {})
        if not isinstance(m, dict):
            m = {}
        m[self.current_profile] = cwd
        self.store.set_meta("active_workdirs_by_profile", m)
        if self.current_profile == config.DEFAULT_AGY_PROFILE:
            self.store.set_meta("active_workdir", cwd)

    def _get_preferred_model(self) -> str:
        m = self.store.get_meta("preferred_models_by_profile", {})
        if isinstance(m, dict):
            val = str(m.get(self.current_profile) or "").strip()
            if val:
                return val
        if config.is_codex_profile(self.current_profile):
            return str(self.store.get_meta("codex_preferred_model") or "").strip()
        if self.current_profile == config.DEFAULT_AGY_PROFILE:
            return str(self.store.get_meta("preferred_model") or "").strip()
        return ""

    def _set_preferred_model(self, model_id: str) -> None:
        mid = str(model_id or "").strip()
        if not mid:
            return
        m = self.store.get_meta("preferred_models_by_profile", {})
        if not isinstance(m, dict):
            m = {}
        m[self.current_profile] = mid
        self.store.set_meta("preferred_models_by_profile", m)
        if config.is_codex_profile(self.current_profile):
            self.store.set_meta("codex_preferred_model", mid)
        elif self.current_profile == config.DEFAULT_AGY_PROFILE:
            self.store.set_meta("preferred_model", mid)

    def _current_profile_email(self) -> Optional[str]:
        try:
            return config.read_agy_auth(self.current_profile).get("id_token_email") or None
        except Exception:
            pass
        return None

    def _resolve_stored_session_title(self, sid: str, fallback: str = "会话") -> str:
        """Look up a session's own persisted title in `sessions` without leaking global meta across profiles."""
        stored = self.store.get_session(sid)
        stored_title = str(stored.get("title") or "").strip() if stored else ""
        if stored_title:
            return stored_title
        return fallback

    def _get_or_create_runtime(
        self,
        sid: str,
        workdir: Optional[str] = None,
        title: Optional[str] = None,
    ) -> SessionRuntime:
        rt = self._runtimes.get(sid)
        if rt is None:
            stored = self.store.get_session(sid)
            eff_cwd = (
                self._canonical_workdir(workdir)
                if workdir
                else (
                    self._canonical_workdir(stored["workdir"])
                    if stored and stored.get("workdir")
                    else self.workdir
                )
            )
            eff_title = title or (stored["title"] if stored else None) or "会话"
            rt = SessionRuntime(sid, eff_cwd or self.workdir, eff_title)
            rt.items = self.store.load_items(sid)
            rt.items_by_id = {it["id"]: it for it in rt.items}
            rt.usage = self._get_saved_usage(sid)
            rt.queued_prompts = self._get_saved_queue(sid)
            self._runtimes[sid] = rt
        else:
            if workdir:
                rt.workdir = self._canonical_workdir(workdir) or rt.workdir
            if title and title not in ("会话", "新会话", "Agy Remote", "VibeOne", "Codex"):
                rt.title = title
        return rt

    def _active_runtime(self) -> Optional[SessionRuntime]:
        if not self.session_id:
            return None
        return self._get_or_create_runtime(self.session_id)

    @property
    def is_busy(self) -> bool:
        rt = self._active_runtime()
        return rt.is_busy if rt else False

    @is_busy.setter
    def is_busy(self, val: bool) -> None:
        rt = self._active_runtime()
        if rt:
            rt.is_busy = bool(val)

    @property
    def items(self) -> List[Dict[str, Any]]:
        rt = self._active_runtime()
        return rt.items if rt else self._fallback_items

    @items.setter
    def items(self, val: List[Dict[str, Any]]) -> None:
        rt = self._active_runtime()
        if rt:
            rt.items = val
            rt.items_by_id = {it["id"]: it for it in val}
        else:
            self._fallback_items = val
            self._fallback_items_by_id = {it["id"]: it for it in val}

    @property
    def _items_by_id(self) -> Dict[str, Dict[str, Any]]:
        rt = self._active_runtime()
        return rt.items_by_id if rt else self._fallback_items_by_id

    @_items_by_id.setter
    def _items_by_id(self, val: Dict[str, Dict[str, Any]]) -> None:
        rt = self._active_runtime()
        if rt:
            rt.items_by_id = val
        else:
            self._fallback_items_by_id = val

    @property
    def pending_permissions(self) -> Dict[str, Dict[str, Any]]:
        rt = self._active_runtime()
        return rt.pending_permissions if rt else self._fallback_permissions

    @property
    def usage(self) -> Dict[str, Any]:
        rt = self._active_runtime()
        return rt.usage if rt else self._fallback_usage

    @usage.setter
    def usage(self, val: Dict[str, Any]) -> None:
        rt = self._active_runtime()
        if rt:
            rt.usage = val
        else:
            self._fallback_usage = val

    @property
    def current_mode(self) -> str:
        rt = self._active_runtime()
        return rt.current_mode if rt else self._fallback_mode

    @current_mode.setter
    def current_mode(self, val: str) -> None:
        rt = self._active_runtime()
        if rt:
            rt.current_mode = val
        else:
            self._fallback_mode = val

    @property
    def current_model(self) -> str:
        rt = self._active_runtime()
        return rt.current_model if rt else self._fallback_model

    @current_model.setter
    def current_model(self, val: str) -> None:
        rt = self._active_runtime()
        if rt:
            rt.current_model = val
        else:
            self._fallback_model = val

    @property
    def session_title(self) -> str:
        rt = self._active_runtime()
        return rt.title if rt else self._fallback_title

    @session_title.setter
    def session_title(self, val: str) -> None:
        rt = self._active_runtime()
        if rt:
            rt.title = val
        else:
            self._fallback_title = val

    @property
    def _prompt_task(self) -> Optional[asyncio.Task]:
        rt = self._active_runtime()
        return rt.prompt_task if rt else None

    @_prompt_task.setter
    def _prompt_task(self, val: Optional[asyncio.Task]) -> None:
        rt = self._active_runtime()
        if rt:
            rt.prompt_task = val

    def _get_saved_usage(self, sid: Optional[str]) -> Dict[str, Any]:
        if not sid:
            return {"used": 0, "size": 0}
        raw = self.store.get_meta("session_usage", {})
        if isinstance(raw, dict):
            entry = raw.get(sid)
            if isinstance(entry, dict) and int(entry.get("size") or 0) > 0:
                return {
                    "used": int(entry.get("used") or 0),
                    "size": int(entry.get("size") or 0),
                }
        return {"used": 0, "size": 0}

    def _save_usage(self, sid: Optional[str], usage: Dict[str, Any]) -> None:
        if not sid or not isinstance(usage, dict):
            return
        size = int(usage.get("size") or 0)
        used = int(usage.get("used") or 0)
        if size <= 0:
            return
        raw = self.store.get_meta("session_usage", {})
        if not isinstance(raw, dict):
            raw = {}
        raw[sid] = {"used": used, "size": size}
        # Keep map bounded to recent 200 sessions
        if len(raw) > 200:
            for old_key in list(raw.keys())[:-200]:
                raw.pop(old_key, None)
        self.store.set_meta("session_usage", raw)

    def _delete_saved_usage(self, sid: Optional[str]) -> None:
        if not sid:
            return
        raw = self.store.get_meta("session_usage", {})
        if isinstance(raw, dict) and sid in raw:
            raw.pop(sid, None)
            self.store.set_meta("session_usage", raw)

    def _get_saved_queue(self, sid: Optional[str]) -> List[Dict[str, Any]]:
        if not sid:
            return []
        raw = self.store.get_meta("session_queues", {})
        if isinstance(raw, dict):
            entries = raw.get(sid)
            if isinstance(entries, list):
                return [e for e in entries if isinstance(e, dict) and e.get("id")]
        return []

    def _save_queue(self, sid: Optional[str], queue: List[Dict[str, Any]]) -> None:
        if not sid:
            return
        raw = self.store.get_meta("session_queues", {})
        if not isinstance(raw, dict):
            raw = {}
        if queue:
            raw[sid] = list(queue)
        else:
            raw.pop(sid, None)
        if len(raw) > 100:
            for old_key in list(raw.keys())[:-100]:
                raw.pop(old_key, None)
        self.store.set_meta("session_queues", raw)

    def _delete_saved_queue(self, sid: Optional[str]) -> None:
        if not sid:
            return
        raw = self.store.get_meta("session_queues", {})
        if isinstance(raw, dict) and sid in raw:
            raw.pop(sid, None)
            self.store.set_meta("session_queues", raw)

    @staticmethod
    def _canonical_workdir(path: str) -> str:
        raw = str(path or "").strip()
        if not raw:
            return ""
        if raw.startswith("file://"):
            raw = unquote(urlparse(raw).path)
        expanded = os.path.abspath(os.path.expanduser(raw))
        try:
            return os.path.realpath(expanded)
        except OSError:
            return expanded

    @classmethod
    def _normalize_workdir(cls, path: str) -> str:
        raw = str(path or "").strip()
        if not raw:
            raise ValueError("目录路径不能为空")
        expanded = os.path.abspath(os.path.expanduser(raw))
        if not os.path.exists(expanded):
            raise ValueError(f"目录不存在: {expanded}")
        if not os.path.isdir(expanded):
            raise ValueError(f"路径不是目录: {expanded}")
        return cls._canonical_workdir(expanded)

    def _remember_workdir(self, path: str) -> None:
        try:
            norm = os.path.abspath(os.path.expanduser(path))
            if not os.path.isdir(norm):
                return
            recent = self.store.get_meta("recent_workdirs", [])
            if not isinstance(recent, list):
                recent = []
            seen_real = {os.path.realpath(norm)}
            updated = [norm]
            for item in recent:
                if not isinstance(item, str) or not os.path.isdir(item):
                    continue
                real = os.path.realpath(item)
                if real not in seen_real:
                    seen_real.add(real)
                    updated.append(os.path.abspath(item))
            self.store.set_meta("recent_workdirs", updated[:12])
        except Exception:
            pass

    def get_shortcut_dirs(self) -> List[Dict[str, str]]:
        candidates: List[Tuple[str, str]] = []
        candidates.append((Path(self.workdir).name or self.workdir, self.workdir))

        recent = self.store.get_meta("recent_workdirs", [])
        if isinstance(recent, list):
            for p in recent:
                if isinstance(p, str) and os.path.isdir(p):
                    candidates.append((Path(p).name or p, os.path.abspath(p)))

        default_parent = os.path.dirname(config.VIBEONE_WORKDIR)
        if default_parent and os.path.isdir(default_parent):
            candidates.append((Path(default_parent).name or default_parent, default_parent))

        home = os.path.expanduser("~")
        projects_dir = os.path.join(home, "Projects")
        if os.path.isdir(projects_dir):
            candidates.append(("Projects", os.path.abspath(projects_dir)))
        if os.path.isdir(home):
            candidates.append(("~ (Home)", os.path.abspath(home)))

        shortcuts: List[Dict[str, str]] = []
        seen_real: Set[str] = set()
        for label, p in candidates:
            if not os.path.isdir(p):
                continue
            real = os.path.realpath(p)
            if real in seen_real:
                continue
            seen_real.add(real)
            shortcuts.append({"label": label, "path": p})
        return shortcuts[:12]

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

        proc = await asyncio.create_subprocess_exec(
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

    async def list_sessions(
        self,
        include_hidden: bool = False,
        active_session_id: Optional[str] = None,
        include_archived: bool = False,
    ) -> List[Dict[str, Any]]:
        hidden = set() if include_hidden else self._get_hidden_sessions()
        hidden_dirs = [] if include_hidden else self._get_hidden_workdirs()
        archived_map = self._get_archived_sessions()
        cur_sid = str(active_session_id or "").strip() or self.session_id
        viewed_sids = self._viewed_session_ids()
        if cur_sid:
            viewed_sids.add(cur_sid)
        merged: Dict[str, Dict[str, Any]] = {}
        inode_to_cwd: Dict[Tuple[int, int], str] = {}

        def resolve_group_cwd(raw_cwd: str) -> str:
            canon = self._canonical_workdir(raw_cwd) or raw_cwd
            if canon and os.path.isdir(canon):
                try:
                    st = os.stat(canon)
                    key = (st.st_dev, st.st_ino)
                    existing_cwd = inode_to_cwd.get(key)
                    if existing_cwd:
                        return existing_cwd
                    inode_to_cwd[key] = canon
                except OSError:
                    pass
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
                    reclaimed_sids.add(sid)
                    continue
                else:
                    self._unmark_session_empty(sid)
            if not include_archived and sid in archived_map:
                continue
            if sid not in viewed_sids and (
                sid in hidden or self._is_workdir_hidden(cwd, hidden_dirs)
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
            logger.info("Auto-reclaimed %d abandoned empty session(s)", len(reclaimed_sids))

        # 2. Live ACP session/list (includes CLI & multi-directory sessions)
        try:
            await self.start()
            res = await self._send_request("session/list", {}, timeout=15.0)
            for s in res.get("sessions") or []:
                sid = str(s.get("sessionId") or "").strip()
                if not sid or sid in self._btw_sessions:
                    continue
                raw_acp_cwd = str(s.get("cwd") or "").strip() or self.workdir
                cwd = resolve_group_cwd(raw_acp_cwd)
                if not include_archived and sid in archived_map:
                    merged.pop(sid, None)
                    continue
                if sid not in viewed_sids and (
                    sid in hidden or self._is_workdir_hidden(cwd, hidden_dirs)
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

        profile = self.current_profile
        raw_hits = await asyncio.to_thread(
            self.store.search_sessions,
            profile,
            raw_q,
            limit=max(1, min(int(limit or 50), 200)),
            excluded_session_ids=excluded_sids,
            only_session_ids=only_sids,
        )

        results: List[Dict[str, Any]] = []
        for row in raw_hits:
            sid = row["session_id"]
            if sid in self._btw_sessions:
                continue
            cwd = self._canonical_workdir(row["workdir"]) or row["workdir"]
            if sid not in viewed_sids and self._is_workdir_hidden(cwd, hidden_dirs):
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

    async def _stop_unlocked(self) -> None:
        self.connected = False
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

    # -- Low-level JSON-RPC I/O --

    async def _write_json(self, obj: Dict[str, Any]) -> None:
        if not self.proc or not self.proc.stdin:
            raise RuntimeError("ACP process is not running")
        line = (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")
        async with self._write_lock:
            self.proc.stdin.write(line)
            await self.proc.stdin.drain()

    async def _send_request(
        self, method: str, params: Dict[str, Any], timeout: Optional[float] = None
    ) -> Dict[str, Any]:
        self._rpc_id += 1
        req_id = self._rpc_id
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        self._pending_rpc[req_id] = fut

        await self._write_json(
            {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}
        )
        try:
            if timeout is not None:
                return await asyncio.wait_for(fut, timeout=timeout)
            return await fut
        finally:
            self._pending_rpc.pop(req_id, None)

    async def _send_notification(self, method: str, params: Dict[str, Any]) -> None:
        await self._write_json({"jsonrpc": "2.0", "method": method, "params": params})

    async def _read_stderr_loop(self, proc: asyncio.subprocess.Process) -> None:
        assert proc and proc.stderr
        try:
            while True:
                raw = await proc.stderr.readline()
                if not raw:
                    break
                line = raw.decode("utf-8", errors="replace").rstrip()
                if any(k in line for k in ("ERROR", "E11", "Failed", "fatal", "panic")):
                    logger.warning("[agy stderr] %s", line)
                else:
                    logger.debug("[agy stderr] %s", line)
        except asyncio.CancelledError:
            pass

    async def _read_stdout_loop(self, proc: asyncio.subprocess.Process) -> None:
        assert proc and proc.stdout
        try:
            while True:
                raw = await proc.stdout.readline()
                if not raw:
                    break
                line = raw.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    logger.warning("Non-JSON line from agy stdout: %s", line[:200])
                    continue
                await self._handle_rpc_message(msg)
        except asyncio.CancelledError:
            return
        except Exception as exc:
            logger.exception("Error in ACP stdout loop: %s", exc)
        finally:
            if self.proc is proc:
                self.connected = False
                for rt in self._runtimes.values():
                    rt.is_busy = False
                    rt.loaded_in_acp = False
                    rt.active_assistant_id = None
                    rt.active_thought_id = None
                    rt.pending_permissions.clear()
                self._perm_to_sid.clear()
                for fut in list(self._pending_rpc.values()):
                    if not fut.done():
                        fut.set_exception(RuntimeError("ACP subprocess exited unexpectedly"))
                self._pending_rpc.clear()
                self.last_error = "Agy 后台进程已退出，发送消息时将自动重启。"
                await self.broadcast_status()
                await self.broadcast({"type": "sessions_changed"})

    async def _handle_rpc_message(self, msg: Dict[str, Any]) -> None:
        # 1. Response to a client->server request
        if "id" in msg and "method" not in msg:
            req_id = msg["id"]
            fut = self._pending_rpc.get(req_id)
            if fut and not fut.done():
                if "error" in msg and msg["error"] is not None:
                    err = msg["error"]
                    fut.set_exception(RuntimeError(err.get("message", str(err))))
                else:
                    fut.set_result(msg.get("result", {}))
            return

        method = msg.get("method")
        params = msg.get("params") or {}

        # 2. Server->Client request (has id & method) -> e.g. session/request_permission
        if "id" in msg and method:
            await self._handle_server_request(msg["id"], method, params)
            return

        # 3. Server->Client notification -> e.g. session/update
        if method == "session/update":
            await self._handle_session_update(params)

    async def _handle_server_request(
        self, req_id: Any, method: str, params: Dict[str, Any]
    ) -> None:
        if method == "session/request_permission":
            key = str(req_id)
            sid = str(params.get("sessionId") or "").strip() or (self.session_id or "")
            if sid and sid in self._btw_sessions:
                await self._write_json(
                    {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "result": {"outcome": {"outcome": "cancelled"}},
                    }
                )
                return
            tool_call = params.get("toolCall") or {}
            options = params.get("options") or [
                {"optionId": "allow", "name": "Allow", "kind": "allow_once"},
                {"optionId": "deny", "name": "Deny", "kind": "reject_once"},
            ]
            perm_record = {
                "key": key,
                "request_id": req_id,
                "session_id": sid,
                "tool_call": {
                    "toolCallId": tool_call.get("toolCallId", ""),
                    "name": tool_call.get("name", "tool"),
                    "title": tool_call.get("title", tool_call.get("name", "操作权限")),
                    "kind": tool_call.get("kind", "other"),
                    "args": tool_call.get("args") or tool_call.get("rawInput") or {},
                },
                "options": options,
                "ts": time.time(),
            }
            if sid:
                rt = self._get_or_create_runtime(sid)
                rt.pending_permissions[key] = perm_record
                self._perm_to_sid[key] = sid
            else:
                self._fallback_permissions[key] = perm_record
            await self.broadcast({"type": "permission_request", "data": perm_record}, sid=sid or None)
            await self.broadcast_status()
            await self.broadcast({"type": "sessions_changed"})
            rt_title = self._runtimes[sid].title if sid in self._runtimes else "会话"
            self._schedule_push_if_unseen(
                sid,
                title=f"需确认权限 · {rt_title}",
                body=str(perm_record["tool_call"]["title"] or "有操作等待审批"),
                tag=f"agy-perm-{sid}",
            )
            logger.info("Permission requested (id=%s, sid=%s): %s", key, sid[:8], perm_record["tool_call"]["title"])
        else:
            # Unsupported server->client request: return JSON-RPC method not found
            await self._write_json(
                {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32601, "message": f"Method not supported by client: {method}"},
                }
            )

    async def respond_permission(self, key: str, option_id: Optional[str], cancelled: bool = False) -> bool:
        skey = str(key)
        sid = self._perm_to_sid.pop(skey, None)
        perm = None
        if sid and sid in self._runtimes:
            perm = self._runtimes[sid].pending_permissions.pop(skey, None)
        if perm is None:
            for r in self._runtimes.values():
                if skey in r.pending_permissions:
                    perm = r.pending_permissions.pop(skey, None)
                    sid = r.session_id
                    break
        if perm is None:
            perm = self._fallback_permissions.pop(skey, None)
        if not perm:
            return False
        if cancelled or not option_id:
            result_payload = {"outcome": {"outcome": "cancelled"}}
        else:
            result_payload = {"outcome": {"outcome": "selected", "optionId": option_id}}

        await self._write_json(
            {
                "jsonrpc": "2.0",
                "id": perm["request_id"],
                "result": result_payload,
            }
        )
        await self.broadcast(
            {
                "type": "permission_resolved",
                "data": {"key": skey, "option_id": option_id, "cancelled": cancelled},
            },
            sid=sid or None,
        )
        await self.broadcast_status()
        await self.broadcast({"type": "sessions_changed"})
        return True

    # -- Item / Stream Management --

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
                return AgyACPManager._extract_content_text(content_obj["content"])
            if "text" in content_obj:
                return str(content_obj["text"])
        if isinstance(content_obj, list):
            parts = [AgyACPManager._extract_content_text(c) for c in content_obj]
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

    async def _handle_session_update(self, params: Dict[str, Any]) -> None:
        update = params.get("update") or {}
        kind = update.get("sessionUpdate", "")
        sid = str(params.get("sessionId") or "").strip() or (self.session_id or "")

        # Ephemeral /btw side-question stream: isolate completely from main runtimes & SQLite
        btw_ctx = self._btw_sessions.get(sid) if sid else None
        if btw_ctx is not None:
            if kind == "agent_message_chunk" and not btw_ctx.get("cancelled"):
                text = self._extract_content_text(update.get("content"))
                if text:
                    btw_ctx["text"] += text
                    await self._emit_btw_event(
                        btw_ctx,
                        {"type": "btw_delta", "data": {"btw_id": btw_ctx["btw_id"], "delta": text}},
                    )
            return

        # Buffer historical items during `session/load` instead of streaming them one by one
        if (
            self._replaying_session_id
            and sid == self._replaying_session_id
            and kind in ("user_message_chunk", "agent_message_chunk", "agent_thought_chunk", "tool_call", "tool_call_update")
        ):
            self._append_replay_update(update)
            return

        if sid and (sid in self._get_hidden_sessions() or sid in self._get_archived_sessions()):
            now = time.time()
            if self.connected and (now - self._ignored_close_sent_at.get(sid, 0.0)) >= 30.0:
                self._ignored_close_sent_at[sid] = now
                asyncio.create_task(self._close_background_session(sid))
            return

        rt = self._get_or_create_runtime(sid) if sid else None
        target_sid = sid or None

        if kind == "agent_message_chunk":
            if not rt:
                return
            rt.active_thought_id = None
            text = self._extract_content_text(update.get("content"))
            if not text:
                return
            if not rt.active_assistant_id:
                item_id = f"asst_{uuid.uuid4().hex[:12]}"
                rt.active_assistant_id = item_id
                item = {"id": item_id, "type": "assistant", "text": text, "ts": time.time()}
                self._add_or_update_item(item, sid=sid)
            else:
                item = rt.items_by_id[rt.active_assistant_id]
                item["text"] += text
                self._add_or_update_item(item, sid=sid)
            await self.broadcast(
                {
                    "type": "text_delta",
                    "data": {"item_id": rt.active_assistant_id, "delta": text},
                },
                sid=target_sid,
            )
            if not rt.is_busy:
                self._schedule_unsolicited_push(rt)

        elif kind == "agent_thought_chunk":
            if not rt:
                return
            rt.active_assistant_id = None
            if not rt.is_busy:
                self._cancel_unsolicited_push(rt)
            text = self._extract_content_text(update.get("content"))
            if not text:
                return
            if not rt.active_thought_id:
                item_id = f"thought_{uuid.uuid4().hex[:12]}"
                rt.active_thought_id = item_id
                item = {"id": item_id, "type": "thought", "text": text, "ts": time.time()}
                self._add_or_update_item(item, sid=sid)
            else:
                item = rt.items_by_id[rt.active_thought_id]
                item["text"] += text
                self._add_or_update_item(item, sid=sid)
            await self.broadcast(
                {
                    "type": "thought_delta",
                    "data": {"item_id": rt.active_thought_id, "delta": text},
                },
                sid=target_sid,
            )

        elif kind in ("tool_call", "tool_call_update"):
            if not rt:
                return
            rt.active_assistant_id = None
            rt.active_thought_id = None
            if not rt.is_busy:
                self._cancel_unsolicited_push(rt)
            tc_id = update.get("toolCallId") or f"tc_{uuid.uuid4().hex[:10]}"
            item_id = f"tool_{tc_id}"
            existing = rt.items_by_id.get(item_id)

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
                "status": update.get("status") or (existing.get("status") if existing else "in_progress"),
                "args": update.get("args") or update.get("rawInput") or (existing.get("args") if existing else {}),
                "output": output_text or (existing.get("output") if existing else ""),
                "ts": existing["ts"] if existing else time.time(),
            }
            self._add_or_update_item(tool_item, sid=sid)
            await self.broadcast({"type": "item_upsert", "data": tool_item}, sid=target_sid)

        elif kind == "session_info_update":
            title = update.get("title")
            if title and rt:
                rt.title = title
                self.store.upsert_session(sid, rt.workdir, title, profile=self.current_profile)
                if sid == self.session_id:
                    self.store.set_meta("session_title", title)
                await self.broadcast_status()
                await self.broadcast({"type": "sessions_changed"})

        elif kind == "usage_update":
            new_usage = {
                "used": int(update.get("used", 0) or 0),
                "size": int(update.get("size", 0) or 0),
            }
            if rt:
                rt.usage = new_usage
                self._save_usage(sid, new_usage)
            await self.broadcast({"type": "usage", "data": new_usage}, sid=target_sid)

        elif kind == "available_commands_update":
            cmds = update.get("availableCommands")
            if isinstance(cmds, list):
                self.available_commands = [
                    {
                        "name": str(c.get("name") or "").strip(),
                        "description": str(c.get("description") or "").strip(),
                    }
                    for c in cmds
                    if isinstance(c, dict) and c.get("name")
                ]
                await self.broadcast_status()

        elif kind == "current_mode_update":
            mode_id = update.get("currentModeId") or update.get("modeId")
            if mode_id and rt:
                rt.current_mode = mode_id
                await self.broadcast_status()

        elif kind == "current_model_update":
            model_id = update.get("currentModelId") or update.get("modelId")
            if model_id and rt and model_id != rt.current_model:
                rt.current_model = model_id
                await self.broadcast_status()

        elif kind == "config_option_update":
            if not rt:
                return
            config_options = update.get("configOptions") or []
            changed = False
            cfg_model = None
            cfg_thought = None
            for opt in config_options:
                if not isinstance(opt, dict):
                    continue
                opt_id = opt.get("id")
                val = opt.get("currentValue")
                if opt_id == "mode" and val and val != rt.current_mode:
                    rt.current_mode = str(val)
                    changed = True
                elif opt_id == "model" and val:
                    cfg_model = str(val)
                elif opt_id == "thought_level" and val:
                    cfg_thought = str(val)
            if cfg_model:
                valid_ids = self._get_available_model_ids()
                candidate = f"{cfg_model}-{cfg_thought}" if cfg_thought else cfg_model
                resolved = None
                if candidate in valid_ids:
                    resolved = candidate
                elif cfg_model in valid_ids:
                    resolved = cfg_model
                if resolved and resolved != rt.current_model:
                    rt.current_model = resolved
                    changed = True
            if changed:
                await self.broadcast_status()

    # -- High-level User Actions --

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

    def _is_active(self) -> bool:
        if self._is_active_fn is not None:
            return bool(self._is_active_fn())
        return True

    def _clients_for_profile(self) -> List[WebSocket]:
        if self._client_views is None:
            return list(self.clients) if self._is_active() else []
        return [
            ws
            for ws in list(self.clients)
            if (self._client_views.get(ws) or {}).get("profile") == self.current_profile
        ]

    def _clients_for_session(self, sid: Optional[str]) -> List[WebSocket]:
        if self._client_views is None:
            if not self._is_active():
                return []
            if sid and sid != self.session_id:
                return []
            return list(self.clients)
        target_sid = sid or self.session_id
        return [
            ws
            for ws in list(self.clients)
            if (self._client_views.get(ws) or {}).get("profile") == self.current_profile
            and (not target_sid or (self._client_views.get(ws) or {}).get("session_id") == target_sid)
        ]

    def _clients_not_viewing_session(self, sid: str) -> List[WebSocket]:
        if self._client_views is None:
            if not self._is_active() or sid == self.session_id:
                return []
            return list(self.clients)
        return [
            ws
            for ws in list(self.clients)
            if (self._client_views.get(ws) or {}).get("profile") != self.current_profile
            or (self._client_views.get(ws) or {}).get("session_id") != sid
        ]

    def _viewed_session_ids(self) -> Set[str]:
        sids: Set[str] = set()
        if self.session_id:
            sids.add(self.session_id)
        if self._client_views is not None:
            for ws in list(self.clients):
                view = self._client_views.get(ws)
                if view and view.get("profile") == self.current_profile and view.get("session_id"):
                    sids.add(str(view["session_id"]))
        return sids

    def _has_visible_viewer_for(self, sid: str) -> bool:
        viewers = self._clients_for_session(sid)
        if not viewers:
            return False
        return any(self._client_visible.get(ws, True) for ws in viewers)

    def _schedule_push_if_unseen(self, sid: str, title: str, body: str, tag: str = "") -> Optional[asyncio.Task]:
        if not self.push or not self.push.supported:
            return None
        if self._has_visible_viewer_for(sid):
            logger.debug(
                "Skipped WebPush (visible viewer active, profile=%s, sid=%s)",
                self.current_profile,
                sid[:8] if sid else "-",
            )
            return None
        task = asyncio.create_task(
            self.push.send_notification(
                title=title,
                body=body,
                session_id=sid,
                tag=tag,
                profile=self.current_profile,
            )
        )
        self._push_tasks.add(task)

        def _on_push_done(t: asyncio.Task) -> None:
            self._push_tasks.discard(t)
            if t.cancelled():
                return
            exc = t.exception()
            if exc is not None:
                logger.warning(
                    "WebPush background task failed (profile=%s, sid=%s): %s",
                    self.current_profile,
                    sid[:8] if sid else "-",
                    exc,
                )

        task.add_done_callback(_on_push_done)
        return task

    def _cancel_unsolicited_push(self, rt: SessionRuntime) -> None:
        task = rt.unsolicited_push_task
        rt.unsolicited_push_task = None
        if task and not task.done():
            task.cancel()

    def _schedule_unsolicited_push(self, rt: SessionRuntime, delay: float = 4.0) -> None:
        """Debounce WebPush notifications for unsolicited background updates (e.g. /schedule)."""
        self._cancel_unsolicited_push(rt)
        sid = rt.session_id

        async def _delayed_notify() -> None:
            try:
                await asyncio.sleep(delay)
                if (
                    rt.is_busy
                    or self._runtimes.get(sid) is not rt
                    or sid in self._get_hidden_sessions()
                    or sid in self._get_archived_sessions()
                ):
                    return
                rt.active_assistant_id = None
                self.store.upsert_session(sid, rt.workdir, rt.title, touch=True, profile=self.current_profile)
                await self.broadcast({"type": "sessions_changed"})
                label = rt.title or sid[:8]
                last_asst = ""
                for it in reversed(rt.items):
                    if it.get("type") == "assistant" and str(it.get("text") or "").strip():
                        last_asst = " ".join(str(it["text"]).split())[:110]
                        break
                non_viewers = self._non_viewers_for_session(sid)
                if non_viewers:
                    await self.broadcast(
                        {
                            "type": "toast",
                            "data": {
                                "level": "info",
                                "message": f"后台会话「{label}」有新消息",
                            },
                        },
                        targets=non_viewers,
                    )
                self._schedule_push_if_unseen(
                    sid,
                    title=f"后台任务更新 · {label}",
                    body=last_asst or "收到新的后台消息，点击查看",
                    tag=f"agy-bg-{sid}",
                )
            except asyncio.CancelledError:
                return
            except Exception as exc:
                logger.warning("Unsolicited background push failed (sid=%s): %s", sid[:8], exc)
            finally:
                if rt.unsolicited_push_task is asyncio.current_task():
                    rt.unsolicited_push_task = None

        rt.unsolicited_push_task = asyncio.create_task(_delayed_notify())

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

    # -- Ephemeral Single-Turn /btw Side Questions --

    async def _emit_btw_event(self, btw_ctx: Dict[str, Any], payload: Dict[str, Any]) -> None:
        origin_ws = btw_ctx.get("origin_ws")
        if origin_ws is not None:
            await self.broadcast(payload, targets=[origin_ws])
        else:
            await self.broadcast(payload, sid=btw_ctx.get("source_sid") or None)

    def _build_btw_context(self, source_sid: Optional[str]) -> str:
        if not source_sid:
            return ""
        rt = self._runtimes.get(source_sid)
        items = list(rt.items) if (rt and rt.items) else self.store.load_items(session_id=source_sid)
        if not items:
            return ""
        snippets: List[str] = []
        total_chars = 0
        for it in reversed(items):
            itype = it.get("type")
            if itype not in ("user", "assistant"):
                continue
            txt = str(it.get("text") or "").strip()
            if not txt:
                continue
            if len(txt) > 1200:
                txt = txt[:1200] + "…"
            role = "用户" if itype == "user" else "助手"
            entry = f"[{role}]\n{txt}"
            if total_chars + len(entry) > 12000 and snippets:
                break
            snippets.append(entry)
            total_chars += len(entry)
            if len(snippets) >= 16:
                break
        snippets.reverse()
        return "\n\n".join(snippets)

    def _build_btw_fallback_prompt(self, question: str, source_sid: Optional[str]) -> str:
        ctx_text = self._build_btw_context(source_sid)
        header = (
            "[临时旁白提问 /btw]\n"
            "请仅基于下方当前会话上下文简明直接地回答用户的旁白提问。"
            "不要调用任何工具，不要修改文件或执行命令。"
        )
        if ctx_text:
            return f"{header}\n\n<当前会话上下文>\n{ctx_text}\n</当前会话上下文>\n\n用户旁白提问：\n{question}"
        return f"{header}\n\n用户旁白提问：\n{question}"

    async def send_btw_prompt(
        self,
        question: str,
        btw_id: Optional[str] = None,
        sid: Optional[str] = None,
        origin_ws: Optional[WebSocket] = None,
    ) -> Dict[str, Any]:
        clean_q = (question or "").strip()
        if not clean_q:
            raise RuntimeError("旁白提问不能为空")
        bid = str(btw_id or "").strip() or f"btw_{uuid.uuid4().hex[:10]}"
        source_sid = str(sid or "").strip() or self.session_id or ""
        btw_ctx: Dict[str, Any] = {
            "btw_id": bid,
            "source_sid": source_sid,
            "origin_ws": origin_ws,
            "question": clean_q,
            "text": "",
            "temp_sid": "",
            "cancelled": False,
        }
        task = asyncio.create_task(self._run_btw_task(btw_ctx))
        btw_ctx["task"] = task
        return {"btw_id": bid}

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

            new_res = await self._send_request(
                "session/new",
                {"cwd": workdir, "mcpServers": []},
                timeout=30.0,
            )
            temp_sid = str(new_res.get("sessionId") or "").strip()
            if not temp_sid:
                raise RuntimeError("无法创建临时旁白会话")

            btw_ctx["temp_sid"] = temp_sid
            self._btw_sessions[temp_sid] = btw_ctx
            self._btw_id_to_sid[bid] = temp_sid
            self._add_hidden_sessions([temp_sid])

            if model_id:
                try:
                    await self._send_request(
                        "session/set_model",
                        {"sessionId": temp_sid, "modelId": model_id},
                        timeout=8.0,
                    )
                except Exception:
                    pass

            if btw_ctx.get("cancelled"):
                return

            full_prompt = self._build_btw_fallback_prompt(btw_ctx["question"], source_sid)
            await self._send_request(
                "session/prompt",
                {"sessionId": temp_sid, "prompt": [{"type": "text", "text": full_prompt}]},
                timeout=180.0,
            )
        except asyncio.CancelledError:
            btw_ctx["cancelled"] = True
        except Exception as exc:
            if not btw_ctx.get("cancelled"):
                error_msg = str(exc) or type(exc).__name__
                logger.warning("ACP /btw failed (%s): %s", bid, error_msg)
        finally:
            self._btw_id_to_sid.pop(bid, None)
            if temp_sid:
                self._btw_sessions.pop(temp_sid, None)
                try:
                    await self._send_request("session/close", {"sessionId": temp_sid}, timeout=2.5)
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
        try:
            await self._send_notification("session/cancel", {"sessionId": temp_sid})
        except Exception:
            pass
        return True

    # -- WebSocket Broadcasting & Snapshots --

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

    SNAPSHOT_ITEM_LIMIT = 200

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
        dead: List[WebSocket] = []
        for ws in recipients:
            try:
                await ws.send_text(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.clients.discard(ws)
            self._client_visible.pop(ws, None)
            if self._client_views is not None:
                self._client_views.pop(ws, None)
            try:
                await ws.close(code=1011, reason="Broadcast failed")
            except Exception:
                pass


