"""Shared engine runtime state and engine-agnostic base manager."""

import asyncio
import os
from typing import Any, Dict, List, Optional, Set

from fastapi import WebSocket

from .. import config
from ..push import WebPushManager
from ..store import SessionStore
from .state_helpers import EngineStateHelpersMixin
from .transport import (
    WS_CLIENT_QUEUE_MAXSIZE,
    WS_CLOSE_TIMEOUT_SECONDS,
    WS_SEND_TIMEOUT_SECONDS,
    ClientTransportMixin,
    SessionRuntime,
    _ClientSendChannel,
)

logger = config.logger

__all__ = [
    "WS_SEND_TIMEOUT_SECONDS",
    "WS_CLIENT_QUEUE_MAXSIZE",
    "WS_CLOSE_TIMEOUT_SECONDS",
    "_ClientSendChannel",
    "SessionRuntime",
    "BaseEngineManager",
]


class BaseEngineManager(ClientTransportMixin, EngineStateHelpersMixin):
    """Engine-agnostic state, profile metadata, workdir, usage, and queue management."""

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
        self._recent_stderr: List[str] = []
        self._write_lock = asyncio.Lock()
        self._start_lock = asyncio.Lock()
        self._session_lock = asyncio.Lock()

        self._rpc_id = 0
        self._pending_rpc: Dict[int, asyncio.Future] = {}

        # Active WebSocket subscribers, foreground visibility, and per-client (profile, session_id) views
        self.clients: Set[WebSocket] = clients if clients is not None else set()
        self._client_visible: Dict[WebSocket, bool] = client_visible if client_visible is not None else {}
        self._client_views: Optional[Dict[WebSocket, Dict[str, Optional[str]]]] = client_views
        self._client_senders: Dict[WebSocket, _ClientSendChannel] = {}
        self._ws_send_timeout: float = WS_SEND_TIMEOUT_SECONDS
        self._ws_queue_maxsize: int = WS_CLIENT_QUEUE_MAXSIZE
        self._push_tasks: Set[asyncio.Task] = set()

        # Global agent metadata
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
        self._dirty_items: Dict[tuple, Dict[str, Any]] = {}
        self._dirty_flush_handle: Optional[asyncio.TimerHandle] = None
        self._dirty_flush_interval: float = 0.5

        # Fallback attributes when no session_id is active yet
        self._fallback_title: str = "VibeOne"
        self._fallback_mode: str = config.VIBEONE_DEFAULT_MODE
        self._fallback_model: str = ""
        self._fallback_usage: Dict[str, Any] = {"used": 0, "size": 0}
        self._fallback_items: List[Dict[str, Any]] = []
        self._fallback_items_by_id: Dict[str, Dict[str, Any]] = {}
        self._fallback_permissions: Dict[str, Dict[str, Any]] = {}

        # Replay buffer used during history streaming
        self._replaying_session_id: Optional[str] = None
        self._replay_items: List[Dict[str, Any]] = []
        self._replay_by_id: Dict[str, Dict[str, Any]] = {}
        self._replay_user_id: Optional[str] = None
        self._replay_assistant_id: Optional[str] = None
        self._replay_thought_id: Optional[str] = None

        # Track latest remote updatedAt reported by session/list for external CLI sync
        self._remote_updated_at: Dict[str, float] = {}
        self._syncing_sessions: Set[str] = set()
        self._acp_session_list_cache: Optional[List[Dict[str, Any]]] = None
        self._acp_session_list_cache_time: float = 0.0
        self._acp_session_list_in_flight: Optional[asyncio.Future] = None
        self._acp_session_list_bg_refresh: Optional[asyncio.Task] = None

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

    def _load_cached_profile_models(self) -> None:
        pass

    def _unhide_workdir(self, path: str) -> None:
        pass

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
