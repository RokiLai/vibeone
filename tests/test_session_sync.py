import asyncio
import os
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any, Dict, List

os.environ.setdefault("VIBEONE_WEB_TOKEN", "test-token")

from vibeone.engines.acp import AgyACPManager
from vibeone.store import SessionStore


class SessionHistorySyncTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.workdir = self.root / "project"
        self.workdir.mkdir()
        self.store = SessionStore(self.root / "vibeone.db")

    def tearDown(self) -> None:
        self.store._conn.close()
        self.tmp.cleanup()

    def _make_manager(self) -> AgyACPManager:
        mgr = AgyACPManager(str(self.workdir), self.store, profile="agy2")
        mgr.connected = True
        mgr.proc = type("FakeProc", (), {"returncode": None})()
        return mgr

    def test_lossless_suffix_merge_preserves_web_messages_and_images(self) -> None:
        mgr = self._make_manager()
        sid = "sess_merge_1"
        self.store.upsert_session(sid, str(self.workdir), "Demo", updated_at=100.0, touch=True, profile="agy2")

        local_items = [
            {
                "id": "user_local_1",
                "type": "user",
                "text": "看看这张图",
                "images": [{"url": "/api/uploads/img1.png", "name": "img1.png"}],
                "ts": 100.0,
            },
            {"id": "tool_tc1", "type": "tool", "toolCallId": "tc1", "name": "read_file", "title": "read", "status": "completed", "ts": 101.0},
            {"id": "asst_local_1", "type": "assistant", "text": "好的", "ts": 102.0},
            # Simulate a web-sent user message omitted by ACP replay
            {"id": "user_local_2", "type": "user", "text": "继续改一下", "images": [], "ts": 103.0},
            {"id": "asst_local_2", "type": "assistant", "text": "好的", "ts": 104.0},
        ]
        self.store.replace_session_items(sid, local_items)

        # Replay omits user_local_2 and image previews, AND includes 2 new terminal turns (ending in "好的" again!)
        replayed_items = [
            {"id": "user_rep_1", "type": "user", "text": "看看这张图", "images": [], "ts": 200.0},
            {"id": "tool_tc1", "type": "tool", "toolCallId": "tc1", "name": "read_file", "title": "read", "status": "completed", "ts": 201.0},
            {"id": "asst_rep_1", "type": "assistant", "text": "好的", "ts": 202.0},
            {"id": "asst_rep_2", "type": "assistant", "text": "好的", "ts": 204.0},
            {"id": "user_rep_3", "type": "user", "text": "在终端里继续问", "images": [], "ts": 205.0},
            {"id": "asst_rep_3", "type": "assistant", "text": "好的", "ts": 206.0},
        ]

        added = mgr._apply_loaded_items(sid, replayed_items)
        self.assertEqual(added, 2)

        merged = self.store.load_items(sid)
        self.assertEqual(len(merged), 7)
        # Original local user message + image preview preserved intact
        self.assertEqual(merged[0]["id"], "user_local_1")
        self.assertEqual(merged[0]["images"], [{"url": "/api/uploads/img1.png", "name": "img1.png"}])
        # Omitted web-sent user message preserved intact
        self.assertEqual(merged[3]["id"], "user_local_2")
        self.assertEqual(merged[3]["text"], "继续改一下")
        # New terminal turns appended in order
        self.assertEqual(merged[5]["text"], "在终端里继续问")
        self.assertEqual(merged[6]["text"], "好的")

    async def test_switch_session_reloads_when_remote_updated_at_is_newer(self) -> None:
        mgr = self._make_manager()
        sid = "sess_cli_cont"
        self.store.upsert_session(sid, str(self.workdir), "CLI Sync", updated_at=1000.0, touch=True, profile="agy2")
        self.store.replace_session_items(
            sid,
            [
                {"id": "u1", "type": "user", "text": "hello", "images": [], "ts": 1000.0},
                {"id": "a1", "type": "assistant", "text": "hi", "ts": 1001.0},
            ],
        )

        rt = mgr._get_or_create_runtime(sid, workdir=str(self.workdir), title="CLI Sync")
        rt.loaded_in_acp = True
        rt.items = self.store.load_items(sid)
        rt.items_by_id = {it["id"]: it for it in rt.items}
        mgr.session_id = "other_sess"

        rpc_calls: List[str] = []

        async def fake_start() -> None:
            return None

        async def fake_send_request(method: str, params: Dict[str, Any], timeout: float = 30.0) -> Dict[str, Any]:
            rpc_calls.append(method)
            if method == "session/load":
                mgr._replay_items = [
                    {"id": "u1_r", "type": "user", "text": "hello", "images": [], "ts": 1000.0},
                    {"id": "a1_r", "type": "assistant", "text": "hi", "ts": 1001.0},
                    {"id": "u2_r", "type": "user", "text": "from terminal", "images": [], "ts": 1020.0},
                    {"id": "a2_r", "type": "assistant", "text": "terminal reply", "ts": 1021.0},
                ]
                return {"sessionId": sid}
            return {"sessionId": sid}

        mgr.start = fake_start  # type: ignore[method-assign]
        mgr._send_request = fake_send_request  # type: ignore[method-assign]

        # 1) Without newer remote timestamp, fast-path should NOT call session/load
        await mgr.switch_session(sid)
        self.assertNotIn("session/load", rpc_calls)
        self.assertNotIn("session/resume", rpc_calls)
        rpc_calls.clear()

        # 2) Simulate ACP session/list reporting newer updatedAt from terminal
        mgr._remote_updated_at[sid] = 1025.0
        await mgr.switch_session(sid)
        self.assertEqual(rpc_calls, ["session/load"])

        items = self.store.load_items(sid)
        self.assertEqual([it["text"] for it in items], ["hello", "hi", "from terminal", "terminal reply"])
        # SQLite updated_at should now be synced to >= 1025.0 so subsequent switches use fast-path again
        sess = self.store.get_session(sid)
        self.assertIsNotNone(sess)
        self.assertGreaterEqual(float(sess["updated_at"]), 1025.0)
        self.assertFalse(mgr._has_newer_remote_update(sid))

    async def test_sync_session_history_broadcasts_and_skips_busy(self) -> None:
        mgr = self._make_manager()
        sid = "sess_active"
        self.store.upsert_session(sid, str(self.workdir), "Active", updated_at=500.0, touch=True, profile="agy2")
        self.store.replace_session_items(
            sid,
            [
                {"id": "u1", "type": "user", "text": "first", "images": [], "ts": 500.0},
                {"id": "a1", "type": "assistant", "text": "reply1", "ts": 501.0},
            ],
        )
        rt = mgr._get_or_create_runtime(sid, workdir=str(self.workdir), title="Active")
        rt.loaded_in_acp = True
        rt.items = self.store.load_items(sid)
        rt.items_by_id = {it["id"]: it for it in rt.items}
        mgr.session_id = sid

        broadcasts: List[Dict[str, Any]] = []

        async def fake_start() -> None:
            return None

        async def fake_broadcast(payload: Dict[str, Any], **kwargs) -> None:
            broadcasts.append(payload)

        async def fake_send_request(method: str, params: Dict[str, Any], timeout: float = 30.0) -> Dict[str, Any]:
            if method == "session/load":
                mgr._replay_items = [
                    {"id": "u1_r", "type": "user", "text": "first", "images": [], "ts": 500.0},
                    {"id": "a1_r", "type": "assistant", "text": "reply1", "ts": 501.0},
                    {"id": "u2_r", "type": "user", "text": "second in cli", "images": [], "ts": 520.0},
                ]
                return {"sessionId": sid}
            return {"sessionId": sid}

        mgr.start = fake_start  # type: ignore[method-assign]
        mgr.broadcast = fake_broadcast  # type: ignore[method-assign]
        mgr._send_request = fake_send_request  # type: ignore[method-assign]

        # Busy session should be skipped safely
        rt.is_busy = True
        res_busy = await mgr.sync_session_history(sid, force=True)
        self.assertFalse(res_busy["synced"])
        self.assertEqual(res_busy["reason"], "busy")

        # Idle session should sync and broadcast snapshot
        rt.is_busy = False
        res_ok = await mgr.sync_session_history(sid, force=True)
        self.assertTrue(res_ok["synced"])
        self.assertEqual(res_ok["added"], 1)
        self.assertTrue(any(b.get("type") == "snapshot" for b in broadcasts))
        self.assertEqual(len(self.store.load_items(sid)), 3)

    def test_init_preserves_per_profile_session_title_over_global_meta(self) -> None:
        from vibeone.engines.codex import CodexAppServerManager

        self.store.upsert_session("sid-agy2", str(self.workdir), "Reporting A Bug", profile="agy2")
        self.store.upsert_session("sid-codex", str(self.workdir), "仓库公开前检查", profile="codex")
        self.store.upsert_session("sid-agy1", str(self.workdir), "排查本地网络", profile="agy")
        self.store.set_meta(
            "active_sessions_by_profile",
            {"agy2": "sid-agy2", "codex": "sid-codex", "agy": "sid-agy1"},
        )
        # Global meta holds another profile's latest title
        self.store.set_meta("session_title", "Reporting A Bug")

        codex_mgr = CodexAppServerManager(str(self.workdir), self.store, profile="codex")
        agy_mgr = AgyACPManager(str(self.workdir), self.store, profile="agy")

        self.assertEqual(codex_mgr._get_or_create_runtime("sid-codex").title, "仓库公开前检查")
        self.assertEqual(self.store.get_session("sid-codex")["title"], "仓库公开前检查")
        self.assertEqual(agy_mgr._get_or_create_runtime("sid-agy1").title, "排查本地网络")
        self.assertEqual(self.store.get_session("sid-agy1")["title"], "排查本地网络")

    async def test_remove_and_archive_close_current_session_and_ignore_subsequent_updates(self) -> None:
        mgr = AgyACPManager(str(self.workdir), self.store)
        mgr.connected = True

        sid_del = "sess_to_delete"
        sid_arch = "sess_to_archive"
        self.store.upsert_session(sid_del, str(self.workdir), "Delete Me")
        self.store.upsert_session(sid_arch, str(self.workdir), "Archive Me")
        self.store.replace_session_items(
            sid_arch,
            [{"id": "u1", "type": "user", "text": "keep", "images": [], "ts": 1.0}],
        )

        rt_del = mgr._get_or_create_runtime(sid_del, workdir=str(self.workdir), title="Delete Me")
        mgr.session_id = sid_del
        mgr._schedule_unsolicited_push(rt_del, delay=60.0)
        pending_del_task = rt_del.unsolicited_push_task
        self.assertIsNotNone(pending_del_task)

        rpc_calls: List[tuple] = []
        broadcasts: List[Dict[str, Any]] = []

        async def fake_start() -> None:
            return None

        async def fake_broadcast(payload: Dict[str, Any], **kwargs) -> None:
            broadcasts.append(payload)

        async def fake_send_request(method: str, params: Dict[str, Any], timeout: float = 30.0) -> Dict[str, Any]:
            rpc_calls.append((method, params))
            if method == "session/new":
                return {"sessionId": "sess_fresh"}
            return {"sessionId": params.get("sessionId", "")}

        async def fake_list_sessions(**kwargs) -> List[Dict[str, Any]]:
            return []

        mgr.start = fake_start  # type: ignore[method-assign]
        mgr.broadcast = fake_broadcast  # type: ignore[method-assign]
        mgr._send_request = fake_send_request  # type: ignore[method-assign]
        mgr.list_sessions = fake_list_sessions  # type: ignore[method-assign]

        # Remove current session -> cancels push task and sends session/close
        await mgr.remove_session(sid_del)
        await asyncio.sleep(0)
        self.assertTrue(pending_del_task.cancelled())
        self.assertIn(("session/close", {"sessionId": sid_del}), rpc_calls)

        # Unsolicited update for deleted session must be ignored (no DB resurrection, no broadcast)
        broadcasts.clear()
        await mgr._handle_session_update(
            {
                "sessionId": sid_del,
                "update": {
                    "sessionUpdate": "agent_message_chunk",
                    "content": {"type": "text", "text": "late schedule message"},
                },
            }
        )
        self.assertNotIn(sid_del, mgr._runtimes)
        self.assertIsNone(self.store.get_session(sid_del))
        self.assertEqual(broadcasts, [])

        # Archive current session -> sends session/close and ignores subsequent updates
        rt_arch = mgr._get_or_create_runtime(sid_arch, workdir=str(self.workdir), title="Archive Me")
        mgr.session_id = sid_arch
        mgr._schedule_unsolicited_push(rt_arch, delay=60.0)
        pending_arch_task = rt_arch.unsolicited_push_task
        rpc_calls.clear()
        await mgr.archive_sessions([sid_arch])
        await asyncio.sleep(0)
        self.assertTrue(pending_arch_task.cancelled())
        self.assertIn(("session/close", {"sessionId": sid_arch}), rpc_calls)

        broadcasts.clear()
        await mgr._handle_session_update(
            {
                "sessionId": sid_arch,
                "update": {
                    "sessionUpdate": "agent_message_chunk",
                    "content": {"type": "text", "text": "late archived message"},
                },
            }
        )
        self.assertNotIn(sid_arch, mgr._runtimes)
        self.assertEqual(len(self.store.load_items(sid_arch)), 1)
        self.assertEqual(broadcasts, [])


if __name__ == "__main__":
    unittest.main()
