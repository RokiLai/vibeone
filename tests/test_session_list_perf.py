import asyncio
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

os.environ.setdefault("VIBEONE_WEB_TOKEN", "test-token")

from vibeone.engines.acp import AgyACPManager  # noqa: E402
from vibeone.engines.codex import CodexAppServerManager  # noqa: E402
from vibeone.store import SessionStore  # noqa: E402


class SessionListPerfAndCacheTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.dir_a = self.root / "proj_a"
        self.dir_b = self.root / "proj_b"
        self.dir_a.mkdir()
        self.dir_b.mkdir()
        self.dir_a_link = self.root / "proj_a_link"
        self.dir_a_link.symlink_to(self.dir_a)
        self.store = SessionStore(self.root / "test.db")

    async def asyncTearDown(self) -> None:
        self.store._conn.close()
        self._tmp.cleanup()

    def test_store_sessions_with_items_cache_consistency(self) -> None:
        self.assertEqual(self.store.get_sessions_with_items(), set())

        self.store.upsert_session("s1", str(self.dir_a), "S1")
        self.store.upsert_session("s2", str(self.dir_a), "S2")
        self.store.upsert_session("s_empty", str(self.dir_a), "Empty")
        self.store.upsert_item("s1", {"id": "i1", "type": "user", "text": "hello", "ts": 1.0})
        self.assertEqual(self.store.get_sessions_with_items(), {"s1"})

        # Mutating returned set must not corrupt internal cache
        snap = self.store.get_sessions_with_items()
        snap.add("fake")
        self.assertEqual(self.store.get_sessions_with_items(), {"s1"})

        self.store.replace_session_items(
            "s2", [{"id": "i2", "type": "assistant", "text": "reply", "ts": 2.0}]
        )
        self.assertEqual(self.store.get_sessions_with_items(), {"s1", "s2"})

        # Deleting an empty session succeeds cleanly and preserves other sessions
        self.store.delete_session("s_empty")
        self.assertIsNone(self.store.get_session("s_empty"))
        self.assertEqual(self.store.get_sessions_with_items(), {"s1", "s2"})

        # Deleting a non-empty session removes it from cache and FTS5
        self.store.delete_session("s1")
        self.assertEqual(self.store.get_sessions_with_items(), {"s2"})

        # Clearing remaining session empties cache
        self.store.clear_session("s2")
        self.assertEqual(self.store.get_sessions_with_items(), set())

    def test_hidden_workdir_matcher_matches_symlinks_and_inodes(self) -> None:
        mgr = AgyACPManager(str(self.dir_b), self.store)
        matcher = mgr._build_hidden_workdir_matcher([str(self.dir_a)])
        self.assertTrue(matcher(str(self.dir_a)))
        self.assertTrue(matcher(str(self.dir_a_link)))
        st_a = os.stat(self.dir_a)
        self.assertTrue(matcher("/nonexistent/alias", (st_a.st_dev, st_a.st_ino)))
        self.assertFalse(matcher(str(self.dir_b)))

    async def test_acp_session_list_cache_swr_and_force_refresh(self) -> None:
        mgr = AgyACPManager(str(self.dir_a), self.store)
        mgr.connected = True
        mgr.session_id = "s1"
        mgr.start = AsyncMock()
        mgr.broadcast = AsyncMock()

        rpc_calls = 0
        remote_rows = [
            {"sessionId": "s1", "cwd": str(self.dir_a), "title": "S1", "updatedAt": 100.0},
        ]

        async def fake_rpc(method, params, timeout=None):
            nonlocal rpc_calls
            if method == "session/list":
                rpc_calls += 1
                return {"sessions": list(remote_rows)}
            if method == "session/new":
                return {"sessionId": "s_new"}
            return {}

        mgr._send_request = AsyncMock(side_effect=fake_rpc)

        # 1st call populates cache
        res1 = await mgr.list_sessions()
        self.assertEqual(rpc_calls, 1)
        self.assertEqual({s["session_id"] for s in res1}, {"s1"})

        # 2nd call within TTL hits cache without RPC
        res2 = await mgr.list_sessions()
        self.assertEqual(rpc_calls, 1)
        self.assertEqual({s["session_id"] for s in res2}, {"s1"})

        # Creating a new session updates cache incrementally
        await mgr._create_new_session(clear_history=True, workdir=str(self.dir_a))
        res3 = await mgr.list_sessions()
        self.assertEqual(rpc_calls, 1)
        self.assertIn("s_new", {s["session_id"] for s in res3})

        # Stale cache (>15s, <120s) returns immediately and triggers background refresh
        remote_rows.append(
            {"sessionId": "s_cli", "cwd": str(self.dir_a), "title": "CLI", "updatedAt": 200.0}
        )
        mgr._acp_session_list_cache_time = time.time() - 30.0
        res_stale = await mgr.list_sessions()
        self.assertNotIn("s_cli", {s["session_id"] for s in res_stale})
        self.assertIsNotNone(mgr._acp_session_list_bg_refresh)
        await mgr._acp_session_list_bg_refresh
        self.assertEqual(rpc_calls, 2)

        res_after_swr = await mgr.list_sessions()
        self.assertIn("s_cli", {s["session_id"] for s in res_after_swr})

        # force_refresh bypasses fresh cache
        await mgr.list_sessions(force_refresh=True)
        self.assertEqual(rpc_calls, 3)

    async def test_codex_thread_list_swr_and_incremental_remove(self) -> None:
        cmgr = CodexAppServerManager(str(self.dir_a), self.store)
        cmgr.connected = True
        cmgr.session_id = "th_1"
        cmgr.start = AsyncMock()
        cmgr.broadcast = AsyncMock()

        rpc_calls = 0
        remote_threads = [
            {"id": "th_1", "cwd": str(self.dir_a), "name": "T1", "updatedAt": 100.0},
            {"id": "th_2", "cwd": str(self.dir_a), "name": "T2", "updatedAt": 200.0},
        ]

        async def fake_rpc(method, params, timeout=None):
            nonlocal rpc_calls
            if method == "thread/list":
                rpc_calls += 1
                return {"data": list(remote_threads)}
            return {}

        cmgr._send_request = AsyncMock(side_effect=fake_rpc)

        # Populate cache
        res1 = await cmgr.list_sessions()
        self.assertEqual(rpc_calls, 1)
        self.assertEqual({s["session_id"] for s in res1}, {"th_1", "th_2"})

        # Removing th_2 updates cache incrementally instead of invalidating the whole cache
        await cmgr.remove_session("th_2")
        res2 = await cmgr.list_sessions()
        self.assertEqual(rpc_calls, 1)
        self.assertEqual({s["session_id"] for s in res2}, {"th_1"})

        # SWR refresh when cache is stale (>15s, <120s)
        remote_threads.append(
            {"id": "th_3", "cwd": str(self.dir_a), "name": "T3", "updatedAt": 300.0}
        )
        cmgr._thread_list_cache_time = time.time() - 30.0
        res_stale = await cmgr.list_sessions()
        self.assertNotIn("th_3", {s["session_id"] for s in res_stale})
        self.assertIsNotNone(cmgr._thread_list_bg_refresh)
        await cmgr._thread_list_bg_refresh
        self.assertEqual(rpc_calls, 2)

        res_refreshed = await cmgr.list_sessions()
        self.assertIn("th_3", {s["session_id"] for s in res_refreshed})

        # force_refresh forces an immediate RPC
        await cmgr.list_sessions(force_refresh=True)
        self.assertEqual(rpc_calls, 3)


if __name__ == "__main__":
    unittest.main()
