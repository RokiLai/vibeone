import asyncio
import json
import os
import pathlib
import tempfile
import unittest
from unittest.mock import AsyncMock

os.environ.setdefault("VIBEONE_WEB_TOKEN", "test-token")

from vibeone import (  # noqa: E402
    AgyACPManager,
    CodexAppServerManager,
    ProfileManagerPool,
    SessionStore,
    config,
)


class DummyProc:
    returncode = None


class CodexManagerTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self._tmp.name)
        self.store = SessionStore(self.root / "test.db")
        self.mgr = CodexAppServerManager(str(self.root), self.store)
        self.mgr.connected = True
        self.mgr.session_id = "thr_1"
        self.mgr._get_or_create_runtime("thr_1", workdir=str(self.root), title="Test Codex")
        self.mgr.broadcast = AsyncMock()
        self.written = []

        async def fake_write(obj):
            self.written.append(obj)

        self.mgr._write_json = AsyncMock(side_effect=fake_write)

    async def asyncTearDown(self) -> None:
        self.store._conn.close()
        self._tmp.cleanup()

    def test_model_list_expansion_and_mode_mapping(self) -> None:
        models, default_id = self.mgr._build_codex_models(
            [
                {
                    "id": "gpt-5.3-codex",
                    "displayName": "GPT-5.3 Codex",
                    "isDefault": True,
                    "defaultReasoningEffort": "medium",
                    "supportedReasoningEfforts": [
                        {"reasoningEffort": "low", "description": "Fast"},
                        {"reasoningEffort": "medium", "description": "Balanced"},
                        {"reasoningEffort": "high", "description": "Deep"},
                    ],
                },
                {
                    "id": "hidden-model",
                    "displayName": "Hidden",
                    "hidden": True,
                },
            ]
        )
        self.assertEqual(len(models), 3)
        self.assertEqual(default_id, "gpt-5.3-codex::medium")
        self.assertEqual(models[2]["name"], "GPT-5.3 Codex (high)")

        ap, sb, sp = self.mgr._mode_to_codex_policy("auto")
        self.assertEqual((ap, sb, sp["type"]), ("never", "workspace-write", "workspaceWrite"))
        ap2, sb2, sp2 = self.mgr._mode_to_codex_policy("read-only")
        self.assertEqual((ap2, sb2, sp2["type"]), ("on-request", "read-only", "readOnly"))

    async def test_streaming_notifications_and_tool_lifecycle(self) -> None:
        await self.mgr._handle_codex_notification(
            "item/reasoning/summaryTextDelta",
            {"threadId": "thr_1", "itemId": "r1", "delta": "thinking..."},
        )
        await self.mgr._handle_codex_notification(
            "item/started",
            {
                "threadId": "thr_1",
                "item": {"id": "c1", "type": "commandExecution", "command": "ls -la", "cwd": str(self.root), "status": "inProgress"},
            },
        )
        await self.mgr._handle_codex_notification(
            "item/commandExecution/outputDelta",
            {"threadId": "thr_1", "itemId": "c1", "delta": "file.txt\n"},
        )
        await self.mgr._handle_codex_notification(
            "item/completed",
            {
                "threadId": "thr_1",
                "item": {"id": "c1", "type": "commandExecution", "command": "ls -la", "status": "completed", "aggregatedOutput": "file.txt\n"},
            },
        )
        await self.mgr._handle_codex_notification(
            "item/agentMessage/delta",
            {"threadId": "thr_1", "itemId": "m1", "delta": "Done!"},
        )
        await self.mgr._handle_codex_notification(
            "thread/tokenUsage/updated",
            {"threadId": "thr_1", "tokenUsage": {"last": {"totalTokens": 1200}, "modelContextWindow": 200000}},
        )

        rt = self.mgr._runtimes["thr_1"]
        types = [it["type"] for it in rt.items]
        self.assertEqual(types, ["thought", "tool", "assistant"])
        self.assertEqual(rt.items[1]["output"], "file.txt\n")
        self.assertEqual(rt.items[1]["status"], "completed")
        self.assertEqual(rt.items[2]["text"], "Done!")
        self.assertEqual(rt.usage, {"used": 1200, "size": 200000})

    async def test_permission_request_and_response(self) -> None:
        await self.mgr._handle_server_request(
            42,
            "item/commandExecution/requestApproval",
            {"threadId": "thr_1", "itemId": "cmd1", "command": "rm -rf /tmp/x", "cwd": str(self.root)},
        )
        rt = self.mgr._runtimes["thr_1"]
        self.assertIn("42", rt.pending_permissions)

        ok = await self.mgr.respond_permission("42", "acceptForSession", cancelled=False)
        self.assertTrue(ok)
        self.assertEqual(self.written[-1], {"jsonrpc": "2.0", "id": 42, "result": {"decision": "acceptForSession"}})

    async def test_steer_uses_native_turn_steer(self) -> None:
        rt = self.mgr._runtimes["thr_1"]
        rt.is_busy = True
        self.mgr._thread_turn_ids["thr_1"] = "turn_99"
        rt.queued_prompts.append({"id": "q1", "text": "focus on tests", "display_text": "focus on tests", "images": []})
        self.mgr._send_request = AsyncMock(return_value={"turnId": "turn_99"})

        ok = await self.mgr.steer_queued_prompt("q1", sid="thr_1")
        self.assertTrue(ok)
        self.mgr._send_request.assert_awaited_once()
        method, params = self.mgr._send_request.call_args[0]
        self.assertEqual(method, "turn/steer")
        self.assertEqual(params["expectedTurnId"], "turn_99")
        self.assertEqual(params["input"][0]["text"], "focus on tests")

    async def test_fetch_quota_conversion(self) -> None:
        self.mgr._send_request = AsyncMock(
            return_value={
                "rateLimits": {
                    "limitId": "codex",
                    "limitName": None,
                    "planType": "plus",
                    "primary": {"usedPercent": 25, "windowDurationMins": 300, "resetsAt": 1800000000},
                    "secondary": {"usedPercent": 60, "windowDurationMins": 10080, "resetsAt": 1800500000},
                },
                "rateLimitResetCredits": {"availableCount": 3},
            }
        )
        q = await self.mgr.fetch_quota()
        self.assertEqual(len(q["summary"]), 1)
        self.assertEqual(q["summary"][0]["name"], "Codex Plus 额度")
        self.assertIn("免费额度重置次数: 3 次", q["summary"][0]["members"])
        buckets = q["summary"][0]["buckets"]
        self.assertEqual(buckets[0]["window"], "5h")
        self.assertAlmostEqual(buckets[0]["remaining"], 0.75)
        self.assertEqual(buckets[1]["window"], "weekly")
        self.assertAlmostEqual(buckets[1]["remaining"], 0.40)

    async def test_codex_skills_and_native_commands(self) -> None:
        # 1. skills/list merges built-ins + native skills
        self.mgr._send_request = AsyncMock(
            return_value={
                "data": [
                    {
                        "cwd": str(self.root),
                        "skills": [
                            {"name": "deploy-check", "path": "/tmp/deploy-check/SKILL.md", "description": "Check deploy", "enabled": True}
                        ],
                    }
                ]
            }
        )
        await self.mgr._refresh_codex_skills()
        names = [c["name"] for c in self.mgr.available_commands]
        self.assertEqual(names[:3], ["compact", "review", "fork"])
        self.assertIn("deploy-check", names)

        blocks = await self.mgr._build_codex_input("/deploy-check verify prod")
        self.assertEqual(blocks[0], {"type": "skill", "name": "deploy-check", "path": "/tmp/deploy-check/SKILL.md"})
        self.assertEqual(blocks[1], {"type": "text", "text": "verify prod"})

        # 2. /review target parsing
        self.assertEqual(self.mgr._parse_review_target(""), {"type": "uncommittedChanges"})
        self.assertEqual(self.mgr._parse_review_target("branch main"), {"type": "baseBranch", "branch": "main"})
        self.assertEqual(self.mgr._parse_review_target("commit abc123"), {"type": "commit", "sha": "abc123"})
        self.assertEqual(self.mgr._parse_review_target("check security"), {"type": "custom", "instructions": "check security"})

        # 3. /compact triggers thread/compact/start and resolves on thread/compacted
        async def fake_compact_req(method, params, timeout=None):
            if method == "thread/compact/start":
                asyncio.get_running_loop().call_soon(
                    lambda: asyncio.create_task(self.mgr._handle_codex_notification("thread/compacted", {"threadId": "thr_1"}))
                )
                return {}
            return {}

        self.mgr._send_request = AsyncMock(side_effect=fake_compact_req)
        await self.mgr._run_prompt_turn("thr_1", "/compact")
        self.assertEqual(self.mgr._send_request.call_args_list[0][0][0], "thread/compact/start")
        self.assertTrue(any("上下文压缩" in str(it.get("text", "")) for it in self.mgr._runtimes["thr_1"].items))

        # 4. /fork triggers thread/fork and switches session preserving items
        self.mgr.start = AsyncMock()
        self.mgr._send_request = AsyncMock(return_value={"thread": {"id": "thr_forked", "name": "Test Codex (分支)"}})
        res = await self.mgr.send_prompt("/fork")
        self.assertEqual(res.get("forked_session_id"), "thr_forked")
        self.assertEqual(self.mgr.session_id, "thr_forked")
        self.assertEqual(len(self.mgr._runtimes["thr_forked"].items), len(self.mgr._runtimes["thr_1"].items))

    def test_store_items_index_and_batch_lookup(self) -> None:
        c = self.store._conn.cursor()
        indexes = [
            r[0]
            for r in c.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='items'"
            ).fetchall()
        ]
        self.assertIn("idx_items_session_id", indexes)

        self.assertEqual(self.store.get_sessions_with_items(), set())
        self.store.upsert_item("s1", {"id": "it_1", "type": "user_message", "content": "hello"})
        self.store.upsert_item("s2", {"id": "it_2", "type": "assistant_message", "content": "hi"})
        self.assertEqual(self.store.get_sessions_with_items(), {"s1", "s2"})

    async def test_codex_thread_list_caching_and_invalidation(self) -> None:
        call_count = 0

        async def fake_send_request(method, params=None, timeout=None):
            nonlocal call_count
            if method == "thread/list":
                call_count += 1
                return {
                    "data": [
                        {
                            "id": "thr_cached_1",
                            "name": "Cached Thread 1",
                            "cwd": str(self.root),
                            "updatedAt": 1700000000,
                        }
                    ]
                }
            return {}

        self.mgr._send_request = AsyncMock(side_effect=fake_send_request)
        self.mgr.start = AsyncMock()

        # First call fetches from RPC
        s1 = await self.mgr.list_sessions()
        self.assertEqual(call_count, 1)
        self.assertTrue(any(s["session_id"] == "thr_cached_1" for s in s1))

        # Second call within TTL hits cache
        s2 = await self.mgr.list_sessions()
        self.assertEqual(call_count, 1)
        self.assertEqual(len(s1), len(s2))

        # Explicit invalidation re-fetches
        self.mgr._invalidate_thread_cache()
        s3 = await self.mgr.list_sessions()
        self.assertEqual(call_count, 2)

    async def test_per_account_model_catalog_cache_and_isolation(self) -> None:
        acct_a_home = self.root / "codex_a"
        acct_b_home = self.root / "codex_b"
        acct_a_home.mkdir()
        acct_b_home.mkdir()

        (acct_a_home / "auth.json").write_text(
            json.dumps({"tokens": {"account_id": "acct-alpha"}}), encoding="utf-8"
        )
        (acct_b_home / "auth.json").write_text(
            json.dumps({"tokens": {"account_id": "acct-beta"}}), encoding="utf-8"
        )
        (acct_a_home / "models_cache.json").write_text(
            json.dumps({"models": [{"slug": "model-a", "display_name": "Model A"}]}), encoding="utf-8"
        )
        (acct_b_home / "models_cache.json").write_text(
            json.dumps({"models": [{"slug": "model-b", "display_name": "Model B"}]}), encoding="utf-8"
        )

        orig_cache_dir = config.CODEX_MODEL_CACHE_DIR
        config.CODEX_MODEL_CACHE_DIR = self.root / "model_cache"
        try:
            key_a = config.get_codex_account_cache_key(acct_a_home)
            key_b = config.get_codex_account_cache_key(acct_b_home)
            self.assertNotEqual(key_a, key_b)

            cat_a = self.mgr._sync_codex_model_catalog_from_home(acct_a_home, key_a)
            cat_b = self.mgr._sync_codex_model_catalog_from_home(acct_b_home, key_b)
            self.assertIsNotNone(cat_a)
            self.assertIsNotNone(cat_b)
            self.assertNotEqual(cat_a, cat_b)
            data_a = json.loads(cat_a.read_text(encoding="utf-8"))
            data_b = json.loads(cat_b.read_text(encoding="utf-8"))
            self.assertEqual(data_a["models"][0]["slug"], "model-a")
            self.assertEqual(data_b["models"][0]["slug"], "model-b")
        finally:
            config.CODEX_MODEL_CACHE_DIR = orig_cache_dir

    async def test_create_new_session_preserves_thread_cache_and_reclaims_async(self) -> None:
        list_calls = 0
        archive_started = asyncio.Event()

        async def fake_send_request(method, params=None, timeout=None):
            nonlocal list_calls
            if method == "thread/list":
                list_calls += 1
                return {
                    "data": [
                        {
                            "id": "thr_existing",
                            "name": "Existing",
                            "cwd": str(self.root),
                            "updatedAt": 1700000000,
                        }
                    ]
                }
            if method == "thread/start":
                return {
                    "thread": {
                        "id": "thr_new_2",
                        "name": "新会话",
                        "cwd": params.get("cwd", str(self.root)),
                        "createdAt": 1700000100,
                        "updatedAt": 1700000100,
                    },
                    "model": "gpt-5.3-codex",
                }
            if method == "thread/archive":
                archive_started.set()
                await asyncio.sleep(0.05)
                return {}
            return {}

        self.mgr._send_request = AsyncMock(side_effect=fake_send_request)
        self.mgr.start = AsyncMock()

        # Warm the thread list cache first
        await self.mgr.list_sessions()
        self.assertEqual(list_calls, 1)

        # Create a new session in a different workdir (reclaiming empty thr_1 in background)
        other_dir = self.root / "other_proj"
        other_dir.mkdir()
        new_sid = await self.mgr.reset_session(workdir=str(other_dir))
        self.assertEqual(new_sid, "thr_new_2")

        # Subsequent list_sessions should hit the warm cache without another thread/list RPC
        sessions = await self.mgr.list_sessions()
        self.assertEqual(list_calls, 1)
        self.assertTrue(any(s["session_id"] == "thr_new_2" for s in sessions))

        # Background thread/archive should have been triggered without blocking reset_session
        await asyncio.wait_for(archive_started.wait(), timeout=1.0)

if __name__ == "__main__":
    unittest.main()
