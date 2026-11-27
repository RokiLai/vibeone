import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

os.environ.setdefault("VIBEONE_WEB_TOKEN", "test-master-token")

from vibeone.engines.acp import AgyACPManager
from vibeone.store import SessionStore


class FastSessionSwitchTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "vibeone.db"
        self.store = SessionStore(self.db_path)
        self.workdir = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    async def test_can_fast_switch_local_and_snapshot_limit(self):
        mgr = AgyACPManager(self.workdir, self.store, profile="agy2")
        sid = "sess-local-fast"
        self.store.upsert_session(sid, self.workdir, "Local Fast", profile="agy2")
        for i in range(80):
            self.store.upsert_item(sid, {"id": f"m_{i}", "type": "user", "text": f"msg {i}", "ts": float(i)})

        self.assertTrue(mgr.can_fast_switch_local(sid))
        snap = mgr.get_snapshot(sid=sid)
        self.assertEqual(len(snap["items"]), mgr.SNAPSHOT_ITEM_LIMIT)
        self.assertEqual(snap["total_items"], 80)
        self.assertTrue(snap["has_more_items"])

        rt = mgr._get_or_create_runtime(sid)
        rt.loaded_in_acp = True
        self.assertFalse(mgr.can_fast_switch_local(sid))

    async def test_schedule_background_resume_and_await(self):
        mgr = AgyACPManager(self.workdir, self.store, profile="agy2")
        sid = "sess-bg-resume"
        self.store.upsert_session(sid, self.workdir, "BG Resume", profile="agy2")
        self.store.upsert_item(sid, {"id": "u_1", "type": "user", "text": "hi", "ts": 1.0})

        gate = asyncio.Event()
        called = []

        async def fake_switch(target_sid, **kwargs):
            called.append(target_sid)
            await gate.wait()
            rt = mgr._get_or_create_runtime(target_sid)
            rt.loaded_in_acp = True
            return target_sid

        mgr.switch_session = AsyncMock(side_effect=fake_switch)
        task = mgr.schedule_background_resume(sid, workdir=self.workdir, title="BG Resume")
        self.assertFalse(task.done())

        waiter = asyncio.create_task(mgr.await_session_resume(sid))
        await asyncio.sleep(0)
        self.assertFalse(waiter.done())
        gate.set()
        await waiter
        self.assertEqual(called, [sid])
        self.assertTrue(mgr._runtimes[sid].loaded_in_acp)
        self.assertNotIn(sid, mgr._pending_resume_tasks)
