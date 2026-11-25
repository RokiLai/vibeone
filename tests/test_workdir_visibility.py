import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

os.environ.setdefault("VIBEONE_WEB_TOKEN", "test-token")

from vibeone import AgyACPManager, SessionStore  # noqa: E402


class WorkdirVisibilityTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.dir_a = self.root / "project_a"
        self.dir_b = self.root / "project_b"
        self.dir_a.mkdir()
        self.dir_b.mkdir()
        self.dir_a_link = self.root / "project_a_link"
        self.dir_a_link.symlink_to(self.dir_a)

        self.store = SessionStore(self.root / "test.db")
        self.mgr = AgyACPManager(str(self.dir_a), self.store)
        self.mgr.connected = True
        self.mgr.session_id = "sess_a1"

        # Seed local SQLite sessions and items
        self.store.upsert_session("sess_a1", str(self.dir_a), "A1")
        self.store.upsert_session("sess_a2", str(self.dir_a), "A2")
        self.store.upsert_session("sess_b1", str(self.dir_b), "B1")
        self.store.upsert_item("sess_a1", {"id": "u1", "type": "user", "text": "hello A1", "ts": 1.0})
        self.store.upsert_item("sess_a2", {"id": "u2", "type": "user", "text": "hello A2", "ts": 2.0})
        self.store.upsert_item("sess_b1", {"id": "u3", "type": "user", "text": "hello B1", "ts": 3.0})

        self.acp_sessions = [
            {"sessionId": "sess_a1", "cwd": str(self.dir_a), "title": "A1", "updatedAt": 100.0},
            {"sessionId": "sess_a2", "cwd": str(self.dir_a), "title": "A2", "updatedAt": 200.0},
            {"sessionId": "sess_a_acp", "cwd": str(self.dir_a), "title": "A_ACP", "updatedAt": 150.0},
            {"sessionId": "sess_b1", "cwd": str(self.dir_b), "title": "B1", "updatedAt": 300.0},
        ]
        self._new_counter = 0

        async def fake_send_request(method, params, timeout=None):
            if method == "session/list":
                return {"sessions": list(self.acp_sessions)}
            if method == "session/load":
                return {"sessionId": params["sessionId"]}
            if method == "session/new":
                self._new_counter += 1
                sid = f"sess_new_{self._new_counter}"
                self.acp_sessions.append(
                    {"sessionId": sid, "cwd": params["cwd"], "title": "新会话", "updatedAt": 400.0 + self._new_counter}
                )
                return {"sessionId": sid}
            if method == "session/close":
                return {}
            return {}

        self.mgr.start = AsyncMock()
        self.mgr._send_request = AsyncMock(side_effect=fake_send_request)
        self.mgr.broadcast = AsyncMock()

    async def asyncTearDown(self) -> None:
        self.store._conn.close()
        self._tmp.cleanup()

    async def test_remove_and_reload_workdir_restores_history(self) -> None:
        # Remove project_a (currently active)
        res = await self.mgr.remove_workdir(str(self.dir_a))
        self.assertTrue(res["ok"])
        self.assertEqual(res["removed_count"], 3)
        self.assertEqual(self.mgr.session_id, "sess_b1")
        self.assertTrue(self.mgr._same_dir(self.mgr.workdir, str(self.dir_b)))

        # Sessions of project_a should be hidden from sidebar, NOT blacklisted in hidden_sessions
        listed = await self.mgr.list_sessions()
        self.assertEqual([s["session_id"] for s in listed], ["sess_b1"])
        self.assertEqual(self.mgr._get_hidden_sessions(), set())
        self.assertTrue(self.mgr._is_workdir_hidden(str(self.dir_a)))
        self.assertTrue(self.mgr._is_workdir_hidden(str(self.dir_a_link)))

        # Local SQLite cache for project_a sessions should remain intact
        self.assertEqual(self.store.count_items("sess_a1"), 1)
        self.assertEqual(self.store.count_items("sess_a2"), 1)

        # Reload project_a via symlink path -> creates new session and restores all historical sessions
        await self.mgr.reset_session(workdir=str(self.dir_a_link))
        self.assertFalse(self.mgr._is_workdir_hidden(str(self.dir_a)))

        listed_after = await self.mgr.list_sessions()
        sids_after = {s["session_id"] for s in listed_after}
        self.assertTrue({"sess_a1", "sess_a2", "sess_a_acp", "sess_b1", self.mgr.session_id}.issubset(sids_after))

    async def test_individually_removed_session_stays_hidden_after_workdir_reload(self) -> None:
        # Explicitly remove single session sess_a2
        await self.mgr.remove_session("sess_a2")
        self.assertIn("sess_a2", self.mgr._get_hidden_sessions())

        # Remove and reload project_a
        await self.mgr.remove_workdir(str(self.dir_a))
        await self.mgr.reset_session(workdir=str(self.dir_a))

        listed_after = await self.mgr.list_sessions()
        sids_after = {s["session_id"] for s in listed_after}
        self.assertIn("sess_a1", sids_after)
        self.assertIn("sess_a_acp", sids_after)
        self.assertNotIn("sess_a2", sids_after)

    async def test_switch_session_unhides_workdir(self) -> None:
        await self.mgr.remove_workdir(str(self.dir_a))
        self.assertTrue(self.mgr._is_workdir_hidden(str(self.dir_a)))

        await self.mgr.switch_session("sess_a1", workdir=str(self.dir_a), title="A1")
        self.assertFalse(self.mgr._is_workdir_hidden(str(self.dir_a)))
        listed = await self.mgr.list_sessions()
        sids = {s["session_id"] for s in listed}
        self.assertTrue({"sess_a1", "sess_a2", "sess_a_acp", "sess_b1"}.issubset(sids))

    async def test_list_sessions_deduplicates_equivalent_workdir_paths(self) -> None:
        # Mix realpath, symlink path, trailing slash, and file:// URI for the exact same directory
        self.store.upsert_session("sess_a1", str(self.dir_a), "A1")
        self.store.upsert_session("sess_a2", str(self.dir_a_link) + "/", "A2")
        self.acp_sessions = [
            {"sessionId": "sess_a1", "cwd": str(self.dir_a), "title": "A1", "updatedAt": 100.0},
            {"sessionId": "sess_a2", "cwd": str(self.dir_a_link) + "/", "title": "A2", "updatedAt": 200.0},
            {"sessionId": "sess_a3", "cwd": f"file://{self.dir_a_link}/", "title": "A3", "updatedAt": 250.0},
            {"sessionId": "sess_b1", "cwd": str(self.dir_b), "title": "B1", "updatedAt": 300.0},
        ]
        # On case-insensitive filesystems (e.g. macOS APFS), also verify case variant collapses
        upper_variant = str(self.dir_a).replace("project_a", "PROJECT_A")
        if os.path.isdir(upper_variant) and os.path.samefile(upper_variant, str(self.dir_a)):
            self.acp_sessions.append(
                {"sessionId": "sess_a4", "cwd": upper_variant, "title": "A4", "updatedAt": 260.0}
            )

        listed = await self.mgr.list_sessions()
        a_cwds = {s["cwd"] for s in listed if s["session_id"].startswith("sess_a")}
        self.assertEqual(len(a_cwds), 1)
        self.assertEqual(next(iter(a_cwds)), self.mgr.workdir)

    async def test_switch_session_does_not_pin_to_top_until_updated(self) -> None:
        # Initial order in project_a: sess_a2 (200) > sess_a_acp (150) > sess_a1 (100)
        # Even though sess_a1 is currently selected, it should NOT jump above newer sessions.
        listed = await self.mgr.list_sessions()
        a_order = [s["session_id"] for s in listed if s["session_id"].startswith("sess_a")]
        self.assertEqual(a_order, ["sess_a2", "sess_a_acp", "sess_a1"])

        # Switching to sess_a_acp without sending a message preserves original order
        await self.mgr.switch_session("sess_a_acp", workdir=str(self.dir_a), title="A_ACP")
        listed_after_switch = await self.mgr.list_sessions()
        a_order_after = [s["session_id"] for s in listed_after_switch if s["session_id"].startswith("sess_a")]
        self.assertEqual(a_order_after, ["sess_a2", "sess_a_acp", "sess_a1"])

        # Touching sess_a1 (simulating sending a message) moves it to the top of project_a
        self.store.touch_session("sess_a1", ts=500.0)
        listed_after_touch = await self.mgr.list_sessions()
        a_order_touched = [s["session_id"] for s in listed_after_touch if s["session_id"].startswith("sess_a")]
        self.assertEqual(a_order_touched, ["sess_a1", "sess_a2", "sess_a_acp"])


if __name__ == "__main__":
    unittest.main()
