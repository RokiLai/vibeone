"""Codex App-Server Subprocess Manager (JSON-RPC 2.0 over stdio)."""

import asyncio
from typing import Any, Dict, List, Optional, Set

from fastapi import WebSocket

from .. import config
from ..push import WebPushManager
from ..store import SessionStore
from .acp import AgyACPManager
from .codex_parts.catalog import (
    CODEX_BUILTIN_COMMANDS,
    CODEX_MODES,
    CodexCatalogMixin,
)
from .codex_parts.items import CodexItemsMixin
from .codex_parts.lifecycle import CodexLifecycleMixin
from .codex_parts.protocol import CodexProtocolMixin
from .codex_parts.sessions import CodexSessionsMixin
from .codex_parts.turns import CodexTurnsMixin

logger = config.logger

__all__ = [
    "CODEX_MODES",
    "CODEX_BUILTIN_COMMANDS",
    "CodexAppServerManager",
]


class CodexAppServerManager(
    CodexCatalogMixin,
    CodexItemsMixin,
    CodexLifecycleMixin,
    CodexSessionsMixin,
    CodexTurnsMixin,
    CodexProtocolMixin,
    AgyACPManager,
):
    """
    Hosts `codex app-server --stdio` via asyncio.create_subprocess_exec.
    Implements the same public interface & WS/SQLite item model as AgyACPManager
    so ProfileManagerPool and the frontend work seamlessly across both engines.
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
        super().__init__(
            workdir,
            store,
            push=push,
            profile=profile or config.CODEX_PROFILE_NAME,
            clients=clients,
            client_visible=client_visible,
            client_views=client_views,
            is_active_fn=is_active_fn,
        )
        self.backend = "codex"
        self.available_modes = list(CODEX_MODES)
        self.available_commands = list(CODEX_BUILTIN_COMMANDS)
        self._fallback_mode = self._get_preferred_mode()
        self.agent_info = {"name": "codex", "title": "OpenAI Codex"}
        self._codex_account: Dict[str, Any] = {}
        self._codex_rate_limits: Optional[Dict[str, Any]] = None
        self._codex_skills: Dict[str, Dict[str, Any]] = {}
        self._thread_turn_ids: Dict[str, str] = {}
        self._turn_futures: Dict[str, asyncio.Future] = {}
        self._compact_sids: Set[str] = set()
        self._default_model_id: str = ""
        self._recent_stderr: List[str] = []
        self._thread_list_cache: Optional[List[Dict[str, Any]]] = None
        self._thread_list_cache_time: float = 0.0
        self._thread_list_in_flight: Optional[asyncio.Future] = None
        self._thread_list_bg_refresh: Optional[asyncio.Task] = None
        self._codex_account_key: str = config.get_codex_account_cache_key(
            config.resolve_codex_home(self.current_profile)
        )
        self._account_refresh_task: Optional[asyncio.Task] = None
        self._load_cached_codex_account_state()
