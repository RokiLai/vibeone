import asyncio
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("VIBEONE_WEB_TOKEN", "test-secret-token")

from fastapi import HTTPException
from fastapi.middleware.gzip import GZipMiddleware

import server
from vibeone import config
from vibeone.engines.acp import AgyACPManager, SessionRuntime
from vibeone.routes import uploads as uploads_mod
from vibeone.store import SessionStore


class FastWS:
    def __init__(self, name: str = "fast"):
        self.name = name
        self.messages = []
        self.closed = False
        self.close_args = None

    async def send_text(self, payload: str) -> None:
        self.messages.append(json.loads(payload))

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed = True
        self.close_args = (code, reason)


class HangingWS:
    def __init__(self, name: str = "hung"):
        self.name = name
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.closed = False
        self.close_args = None

    async def send_text(self, payload: str) -> None:
        self.started.set()
        await self.release.wait()

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed = True
        self.close_args = (code, reason)


class BrokenWS:
    def __init__(self):
        self.closed = False
        self.close_args = None

    async def send_text(self, payload: str) -> None:
        raise RuntimeError("connection reset by peer")

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed = True
        self.close_args = (code, reason)


class StallingUploadFile:
    def __init__(self, filename: str = "photo.png", content_type: str = "image/png"):
        self.filename = filename
        self.content_type = content_type
        self._calls = 0

    async def read(self, size: int = -1) -> bytes:
        self._calls += 1
        if self._calls == 1:
            return b"\x89PNG\r\n\x1a\n" + b"a" * 64
        await asyncio.sleep(5.0)
        return b""


class WeakNetworkServerProtectionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.workdir = self._tmp.name
        self.store = SessionStore(Path(self.workdir) / "test.db")
        self.clients = set()
        self.client_visible = {}
        self.client_views = {}
        self.client_senders = {}

        self.mgr = AgyACPManager(
            self.workdir,
            self.store,
            profile="agy2",
            clients=self.clients,
            client_visible=self.client_visible,
            client_views=self.client_views,
        )
        self.mgr._client_senders = self.client_senders
        self.mgr.connected = True
        self.mgr.session_id = "s1"
        self.mgr._runtimes["s1"] = SessionRuntime("s1", self.workdir, "Session 1")

    async def asyncTearDown(self):
        self.mgr.close_all_client_senders()
        await asyncio.sleep(0)
        self._tmp.cleanup()

    async def test_slow_client_does_not_block_broadcast_and_times_out(self):
        self.mgr._ws_send_timeout = 0.08
        ws_fast = FastWS()
        ws_hung = HangingWS()
        for ws in (ws_fast, ws_hung):
            self.clients.add(ws)
            self.client_visible[ws] = True
            self.client_views[ws] = {"profile": "agy2", "session_id": "s1"}

        t0 = time.monotonic()
        await self.mgr.broadcast({"type": "text_delta", "data": {"delta": "chunk-1"}}, sid="s1")
        elapsed = time.monotonic() - t0

        self.assertLess(elapsed, 0.05)
        self.assertEqual(len(ws_fast.messages), 1)
        self.assertEqual(ws_fast.messages[0]["data"]["delta"], "chunk-1")

        # Subsequent broadcasts still reach fast client immediately while hung client is stuck
        await self.mgr.broadcast({"type": "text_delta", "data": {"delta": "chunk-2"}}, sid="s1")
        self.assertEqual(len(ws_fast.messages), 2)

        # After send timeout elapses, hung client is evicted and closed without impacting fast client
        await asyncio.sleep(0.15)
        self.assertNotIn(ws_hung, self.clients)
        self.assertNotIn(ws_hung, self.client_visible)
        self.assertNotIn(ws_hung, self.client_views)
        self.assertNotIn(ws_hung, self.client_senders)
        self.assertTrue(ws_hung.closed)
        self.assertEqual(ws_hung.close_args[0], 1011)
        self.assertIn(ws_fast, self.clients)
        self.assertFalse(ws_fast.closed)

    async def test_queue_overflow_evicts_slow_client(self):
        self.mgr._ws_send_timeout = 5.0
        self.mgr._ws_queue_maxsize = 2
        ws_fast = FastWS()
        ws_hung = HangingWS()
        for ws in (ws_fast, ws_hung):
            self.clients.add(ws)
            self.client_visible[ws] = True
            self.client_views[ws] = {"profile": "agy2", "session_id": "s1"}

        # First message enters send_text and hangs; next 2 fill queue; 4th overflows and evicts ws_hung
        for i in range(5):
            await self.mgr.broadcast({"type": "text_delta", "data": {"delta": f"m{i}"}}, sid="s1")
            if i == 0:
                await asyncio.wait_for(ws_hung.started.wait(), timeout=1.0)

        await asyncio.sleep(0.02)
        self.assertEqual(len(ws_fast.messages), 5)
        self.assertNotIn(ws_hung, self.clients)
        self.assertNotIn(ws_hung, self.client_senders)
        self.assertTrue(ws_hung.closed)
        self.assertEqual(ws_hung.close_args[0], 1011)
        ws_hung.release.set()

    async def test_broken_client_evicted_cleanly(self):
        ws_broken = BrokenWS()
        ws_fast = FastWS()
        for ws in (ws_broken, ws_fast):
            self.clients.add(ws)
            self.client_visible[ws] = True
            self.client_views[ws] = {"profile": "agy2", "session_id": "s1"}

        await self.mgr.broadcast({"type": "toast", "data": {"message": "hi"}}, sid="s1")
        await asyncio.sleep(0.02)

        self.assertEqual(len(ws_fast.messages), 1)
        self.assertNotIn(ws_broken, self.clients)
        self.assertNotIn(ws_broken, self.client_senders)
        self.assertTrue(ws_broken.closed)

    async def test_upload_chunk_timeout_returns_408_and_cleans_partial_file(self):
        tmp_uploads = Path(self.workdir) / "uploads"
        tmp_uploads.mkdir(parents=True, exist_ok=True)
        _, handlers = uploads_mod.build_uploads_router(
            require_http_auth=lambda: "ok",
            get_store=lambda: self.store,
        )
        upload_image = handlers["upload_image"]

        with (
            mock.patch.object(config, "UPLOADS_DIR", tmp_uploads),
            mock.patch.object(uploads_mod, "_UPLOAD_CHUNK_TIMEOUT_SECONDS", 0.05),
        ):
            with self.assertRaises(HTTPException) as ctx:
                await upload_image(file=StallingUploadFile(), _token="ok")

        self.assertEqual(ctx.exception.status_code, 408)
        self.assertEqual(list(tmp_uploads.iterdir()), [])

    def test_gzip_middleware_registered(self):
        middleware_classes = [m.cls for m in server.app.user_middleware]
        self.assertIn(GZipMiddleware, middleware_classes)


if __name__ == "__main__":
    unittest.main()
