import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

os.environ.setdefault("VIBEONE_WEB_TOKEN", "test-token")

from vibeone import AgyACPManager, CodexAppServerManager, SessionStore  # noqa: E402


class SessionArchiveTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.dir_a = self.root / "project_a"
        self.dir_b = self.root / "project_b"
        self.dir_a.mkdir()
        self.dir_b.mkdir()

        self.store = SessionStore(self.root / "test.db")
        self.mgr = AgyACPManager(str(self.dir_a), self.store)
        self.mgr.connected = True
        self.mgr.session_id = "sess_a1"

        self.store.upsert_session("sess_a1", str(self.dir_a), "A1", updated_at=300.0, touch=True)
        self.store.upsert_session("sess_a2", str(self.dir_a), "A2", updated_at=200.0, touch=True)
        self.store.upsert_session("sess_a3", str(self.dir_a), "A3", updated_at=100.0, touch=True)
        self.store.upsert_session("sess_b1", str(self.dir_b), "B1", updated_at=250.0, touch=True)
        for sid, text in [("sess_a1", "hi A1"), ("sess_a2", "hi A2"), ("sess_a3", "hi A3"), ("sess_b1", "hi B1")]:
            self.store.upsert_item(sid, {"id": f"u_{sid}", "type": "user", "text": text, "ts": 1.0})

        self.acp_sessions = [
            {"sessionId": "sess_a1", "cwd": str(self.dir_a), "title": "A1", "updatedAt": 300.0},
            {"sessionId": "sess_a2", "cwd": str(self.dir_a), "title": "A2", "updatedAt": 200.0},
            {"sessionId": "sess_a3", "cwd": str(self.dir_a), "title": "A3", "updatedAt": 100.0},
            {"sessionId": "sess_b1", "cwd": str(self.dir_b), "title": "B1", "updatedAt": 250.0},
        ]
        self.closed_sessions = []
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
                self.closed_sessions.append(params["sessionId"])
                return {}
            return {}

        self.mgr.start = AsyncMock()
        self.mgr._send_request = AsyncMock(side_effect=fake_send_request)
        self.mgr.broadcast = AsyncMock()

    async def asyncTearDown(self) -> None:
        self.store._conn.close()
        self._tmp.cleanup()

    async def test_batch_archive_preserves_history_and_unarchive_restores(self) -> None:
        res = await self.mgr.archive_sessions(["sess_a2", "sess_a3"])
        self.assertTrue(res["ok"])
        self.assertEqual(res["archived_count"], 2)
        self.assertFalse(res["was_current"])
        self.assertEqual(self.mgr.session_id, "sess_a1")
        self.assertEqual(set(self.closed_sessions), {"sess_a2", "sess_a3"})

        # Active list excludes archived sessions
        active = await self.mgr.list_sessions()
        self.assertEqual({s["session_id"] for s in active}, {"sess_a1", "sess_b1"})

        # include_archived=True returns both active and archived with metadata
        all_sessions = await self.mgr.list_sessions(include_archived=True)
        archived = [s for s in all_sessions if s["is_archived"]]
        self.assertEqual({s["session_id"] for s in archived}, {"sess_a2", "sess_a3"})
        self.assertTrue(all(s["archived_at"] > 0 for s in archived))

        # Local SQLite items remain intact and not added to hidden_sessions
        self.assertEqual(self.store.count_items("sess_a2"), 1)
        self.assertEqual(self.store.count_items("sess_a3"), 1)
        self.assertEqual(self.mgr._get_hidden_sessions(), set())

        # Unarchive restores sessions back to active list
        unres = await self.mgr.unarchive_sessions(["sess_a2", "sess_a3"])
        self.assertTrue(unres["ok"])
        self.assertEqual(unres["unarchived_count"], 2)
        active_after = await self.mgr.list_sessions()
        self.assertEqual({s["session_id"] for s in active_after}, {"sess_a1", "sess_a2", "sess_a3", "sess_b1"})

    async def test_archive_including_current_switches_to_same_dir_first(self) -> None:
        res = await self.mgr.archive_sessions(["sess_a1", "sess_a3"])
        self.assertTrue(res["ok"])
        self.assertTrue(res["was_current"])
        # Should prefer sess_a2 (same dir) over sess_b1 (higher updated_at in other dir)
        self.assertEqual(self.mgr.session_id, "sess_a2")
        self.assertTrue(self.mgr._same_dir(self.mgr.workdir, str(self.dir_a)))

    async def test_remove_archived_session_cleans_archive_metadata(self) -> None:
        await self.mgr.archive_sessions(["sess_a2"])
        self.assertIn("sess_a2", self.mgr._get_archived_sessions())

        await self.mgr.remove_session("sess_a2")
        self.assertNotIn("sess_a2", self.mgr._get_archived_sessions())
        self.assertIn("sess_a2", self.mgr._get_hidden_sessions())

    async def test_codex_batch_archive_and_unarchive(self) -> None:
        cmgr = CodexAppServerManager(str(self.dir_a), self.store)
        cmgr.connected = True
        cmgr.session_id = "th_1"

        self.store.upsert_session("th_1", str(self.dir_a), "C1", updated_at=300.0, touch=True, profile=cmgr.current_profile)
        self.store.upsert_session("th_2", str(self.dir_a), "C2", updated_at=200.0, touch=True, profile=cmgr.current_profile)
        self.store.upsert_item("th_1", {"id": "cu1", "type": "user", "text": "hello c1", "ts": 1.0})
        self.store.upsert_item("th_2", {"id": "cu2", "type": "user", "text": "hello c2", "ts": 2.0})

        unsubscribed = []

        async def fake_codex_rpc(method, params, timeout=None):
            if method == "thread/list":
                return {
                    "data": [
                        {"id": "th_1", "cwd": str(self.dir_a), "name": "C1", "updatedAt": 300.0},
                        {"id": "th_2", "cwd": str(self.dir_a), "name": "C2", "updatedAt": 200.0},
                    ]
                }
            if method == "thread/unsubscribe":
                unsubscribed.append(params["threadId"])
                return {}
            return {}

        cmgr.start = AsyncMock()
        cmgr._send_request = AsyncMock(side_effect=fake_codex_rpc)
        cmgr.broadcast = AsyncMock()

        res = await cmgr.archive_sessions(["th_2"])
        self.assertTrue(res["ok"])
        self.assertEqual(unsubscribed, ["th_2"])
        self.assertEqual(self.store.count_items("th_2"), 1)

        active = await cmgr.list_sessions()
        self.assertEqual([s["session_id"] for s in active], ["th_1"])

        all_items = await cmgr.list_sessions(include_archived=True)
        archived = [s for s in all_items if s["is_archived"]]
        self.assertEqual([s["session_id"] for s in archived], ["th_2"])

        await cmgr.unarchive_sessions(["th_2"])
        active_after = await cmgr.list_sessions()
        self.assertEqual({s["session_id"] for s in active_after}, {"th_1", "th_2"})


if __name__ == "__main__":
    unittest.main()
