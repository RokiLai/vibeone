"""Per-client WebSocket send channel and session runtime container."""

import asyncio
from typing import Any, Dict, List, Optional

from fastapi import WebSocket

from .. import config

logger = config.logger

WS_SEND_TIMEOUT_SECONDS = 8.0
WS_CLIENT_QUEUE_MAXSIZE = 256
WS_CLOSE_TIMEOUT_SECONDS = 2.0


class _ClientSendChannel:
    """Per-WebSocket bounded send queue + isolated writer task with send timeout."""

    def __init__(
        self,
        ws: WebSocket,
        *,
        on_evict: Any,
        send_timeout: float = WS_SEND_TIMEOUT_SECONDS,
        queue_maxsize: int = WS_CLIENT_QUEUE_MAXSIZE,
    ):
        self.ws = ws
        self._on_evict = on_evict
        self.send_timeout = max(0.05, float(send_timeout))
        self.queue: asyncio.Queue[Optional[str]] = asyncio.Queue(maxsize=max(1, int(queue_maxsize)))
        self.closed = False
        self.task: Optional[asyncio.Task] = None

    def ensure_started(self) -> None:
        if self.closed:
            return
        if self.task is None or self.task.done():
            self.task = asyncio.create_task(self._run(), name="ws-client-sender")

    async def _run(self) -> None:
        try:
            while not self.closed:
                payload = await self.queue.get()
                if payload is None or self.closed:
                    break
                try:
                    await asyncio.wait_for(
                        self.ws.send_text(payload),
                        timeout=self.send_timeout,
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    reason = "Broadcast send timeout" if isinstance(exc, asyncio.TimeoutError) else "Broadcast send failed"
                    logger.warning(
                        "Evicting slow/broken WebSocket client (%s): %s",
                        reason,
                        str(exc) or type(exc).__name__,
                    )
                    self.closed = True
                    self._on_evict(self.ws, close_ws=True, reason=reason)
                    break
        except asyncio.CancelledError:
            pass
        finally:
            self.closed = True

    def enqueue(self, payload: str) -> bool:
        if self.closed:
            return False
        self.ensure_started()
        try:
            self.queue.put_nowait(payload)
            return True
        except asyncio.QueueFull:
            logger.warning(
                "Evicting slow WebSocket client: send queue full (maxsize=%d)",
                self.queue.maxsize,
            )
            self.closed = True
            self._on_evict(self.ws, close_ws=True, reason="Client send queue full")
            return False

    def stop(self) -> None:
        self.closed = True
        task = self.task
        self.task = None
        if task is not None and not task.done():
            try:
                current = asyncio.current_task()
            except RuntimeError:
                current = None
            if task is not current:
                task.cancel()


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


class ClientTransportMixin:
    """WebSocket client queue, eviction, and safe shutdown helpers."""

    @staticmethod
    async def _close_ws_safely(ws: WebSocket, reason: str = "Slow client") -> None:
        try:
            await asyncio.wait_for(
                ws.close(code=1011, reason=reason),
                timeout=WS_CLOSE_TIMEOUT_SECONDS,
            )
        except Exception:
            pass

    def evict_client(
        self,
        ws: WebSocket,
        *,
        close_ws: bool = True,
        reason: str = "Slow client",
    ) -> None:
        self.clients.discard(ws)
        self._client_visible.pop(ws, None)
        if self._client_views is not None:
            self._client_views.pop(ws, None)
        sender = self._client_senders.pop(ws, None)
        if sender is not None:
            sender.stop()
        if close_ws:
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(self._close_ws_safely(ws, reason=reason), name="ws-close-slow-client")
            except RuntimeError:
                pass

    def _get_or_create_sender(self, ws: WebSocket) -> _ClientSendChannel:
        sender = self._client_senders.get(ws)
        if sender is None or sender.closed:
            sender = _ClientSendChannel(
                ws,
                on_evict=self.evict_client,
                send_timeout=self._ws_send_timeout,
                queue_maxsize=self._ws_queue_maxsize,
            )
            self._client_senders[ws] = sender
        return sender

    def enqueue_client_payload(self, ws: WebSocket, payload: str) -> bool:
        sender = self._get_or_create_sender(ws)
        return sender.enqueue(payload)

    async def _yield_for_fast_senders(self) -> None:
        for _ in range(3):
            await asyncio.sleep(0)

    async def send_ws_text(self, ws: WebSocket, payload: str) -> bool:
        ok = self.enqueue_client_payload(ws, payload)
        if ok:
            await self._yield_for_fast_senders()
        return ok

    def close_all_client_senders(self) -> None:
        for ws in list(self._client_senders.keys()):
            sender = self._client_senders.pop(ws, None)
            if sender is not None:
                sender.stop()
