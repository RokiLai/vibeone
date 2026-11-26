"""ACP Subprocess Manager (JSON-RPC 2.0 over stdio)."""

import asyncio

from .. import config
from .acp_parts.history import ACPHistoryMixin
from .acp_parts.lifecycle import ACPLifecycleMixin
from .acp_parts.notifications import ACPNotificationsMixin
from .acp_parts.rpc import ACPRpcMixin
from .acp_parts.session_ops import ACPSessionOpsMixin
from .acp_parts.sessions import ACPSessionsMixin
from .acp_parts.snapshots import ACPSnapshotsMixin
from .acp_parts.turns import ACPTurnsMixin
from .acp_parts.visibility import ACPVisibilityMixin
from .base import BaseEngineManager, SessionRuntime

logger = config.logger

__all__ = [
    "SessionRuntime",
    "AgyACPManager",
]


class AgyACPManager(
    ACPLifecycleMixin,
    ACPHistoryMixin,
    ACPVisibilityMixin,
    ACPSessionsMixin,
    ACPSessionOpsMixin,
    ACPRpcMixin,
    ACPTurnsMixin,
    ACPNotificationsMixin,
    ACPSnapshotsMixin,
    BaseEngineManager,
):
    """
    Hosts `agy acp --stdio` via asyncio.create_subprocess_exec.
    - Independent of WebSocket client lifecycles (tasks keep running when phone locks/closes).
    - Supports concurrent execution across multiple sessions via per-session SessionRuntime.
    - Persists structured items in SQLite + memory for instant reconnect recovery.
    - Supports bidirectional `session/request_permission` approvals and `session/cancel`.
    """
