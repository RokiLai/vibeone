import json
import tempfile
import unittest
from pathlib import Path

from vibeone.engines.acp import AgyACPManager, SessionRuntime
from vibeone.store import SessionStore


class FakeWS:
    def __init__(self, name: str):
        self.name = name
        self.messages = []

    async def send_text(self, payload: str) -> None:
        self.messages.append(json.loads(payload))

    async def close(self, code: int = 1000, reason: str = "") -> None:
        pass


class PerClientViewRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.workdir = self._tmp.name
        self.store = SessionStore(Path(self.workdir) / "test.db")
        self.clients = set()
        self.client_visible = {}
        self.client_views = {}

        self.mgr_agy = AgyACPManager(
            self.workdir,
            self.store,
            profile="agy2",
            clients=self.clients,
            client_visible=self.client_visible,
            client_views=self.client_views,
        )
        self.mgr_agy.connected = True
        self.mgr_agy.session_id = "s1"
        self.mgr_agy._runtimes["s1"] = SessionRuntime("s1", self.workdir, "Session 1")
        self.mgr_agy._runtimes["s2"] = SessionRuntime("s2", self.workdir, "Session 2")

        self.mgr_codex = AgyACPManager(
            self.workdir,
            self.store,
            profile="codex",
            clients=self.clients,
            client_visible=self.client_visible,
            client_views=self.client_views,
        )
        self.mgr_codex.connected = True
        self.mgr_codex.session_id = "c1"
        self.mgr_codex._runtimes["c1"] = SessionRuntime("c1", self.workdir, "Codex 1")

    async def asyncTearDown(self):
        self._tmp.cleanup()

    async def test_session_events_route_only_to_matching_viewers(self):
        ws_phone = FakeWS("phone")
        ws_desktop_agy = FakeWS("desktop_agy")
        ws_desktop_codex = FakeWS("desktop_codex")
        ws_phone_mirror = FakeWS("phone_mirror")

        for ws in (ws_phone, ws_desktop_agy, ws_desktop_codex, ws_phone_mirror):
            self.clients.add(ws)
            self.client_visible[ws] = True

        self.client_views[ws_phone] = {"profile": "agy2", "session_id": "s1"}
        self.client_views[ws_phone_mirror] = {"profile": "agy2", "session_id": "s1"}
        self.client_views[ws_desktop_agy] = {"profile": "agy2", "session_id": "s2"}
        self.client_views[ws_desktop_codex] = {"profile": "codex", "session_id": "c1"}

        # Event on agy2/s1 reaches ws_phone & ws_phone_mirror, NOT desktop_agy or desktop_codex
        await self.mgr_agy.broadcast({"type": "text_delta", "data": {"delta": "hello"}}, sid="s1")
        self.assertEqual(len(ws_phone.messages), 1)
        self.assertEqual(len(ws_phone_mirror.messages), 1)
        self.assertEqual(len(ws_desktop_agy.messages), 0)
        self.assertEqual(len(ws_desktop_codex.messages), 0)

        # Event on codex/c1 reaches ONLY ws_desktop_codex
        await self.mgr_codex.broadcast({"type": "text_delta", "data": {"delta": "world"}}, sid="c1")
        self.assertEqual(len(ws_desktop_codex.messages), 1)
        self.assertEqual(len(ws_phone.messages), 1)

        # broadcast_status on agy2 sends tailored per-session status to s1 vs s2 viewers, not codex
        self.mgr_agy._runtimes["s2"].is_busy = True
        await self.mgr_agy.broadcast_status()
        phone_status = ws_phone.messages[-1]["data"]
        desktop_agy_status = ws_desktop_agy.messages[-1]["data"]
        self.assertEqual(phone_status["session_id"], "s1")
        self.assertFalse(phone_status["is_busy"])
        self.assertEqual(phone_status["bg_busy_count"], 1)
        self.assertEqual(desktop_agy_status["session_id"], "s2")
        self.assertTrue(desktop_agy_status["is_busy"])
        self.assertEqual(desktop_agy_status["bg_busy_count"], 0)
        # Codex viewer did not receive agy2 status
        self.assertEqual(len(ws_desktop_codex.messages), 1)

    async def test_visible_viewer_check_is_per_profile_and_session(self):
        ws_desktop = FakeWS("desktop")
        self.clients.add(ws_desktop)
        self.client_visible[ws_desktop] = True
        self.client_views[ws_desktop] = {"profile": "codex", "session_id": "c1"}

        self.assertTrue(self.mgr_codex._has_visible_viewer_for("c1"))
        self.assertFalse(self.mgr_agy._has_visible_viewer_for("s1"))
        self.assertFalse(self.mgr_agy._has_visible_viewer_for("s2"))


if __name__ == "__main__":
    unittest.main()
