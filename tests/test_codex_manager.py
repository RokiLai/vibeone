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

    def test_pool_switches_between_agy_and_codex(self) -> None:
        pdir = self.root / "profiles"
        tf = pdir / "agy2" / ".gemini/antigravity-cli/antigravity-oauth-token"
        tf.parent.mkdir(parents=True)
        tf.write_text(json.dumps({"token": {"access_token": "tok"}, "id_token_email": "a@example.com"}))

        orig_pdir, orig_home, orig_resolve = config.AGY_PROFILES_DIR, config.REAL_HOME, config.resolve_codex_binary
        config.AGY_PROFILES_DIR = pdir
        config.REAL_HOME = self.root
        config.resolve_codex_binary = lambda: "/opt/homebrew/bin/codex"
        try:
            self.store.set_meta("active_profile", "agy2")
            pool = ProfileManagerPool(str(self.root), self.store)

            orig_acp_start = AgyACPManager.start
            orig_codex_start = CodexAppServerManager.start

            async def fake_start(mgr_self):
                mgr_self.proc = DummyProc()
                mgr_self.connected = True

            AgyACPManager.start = fake_start
            CodexAppServerManager.start = fake_start
            try:
                asyncio.run(pool.start())
                r1 = asyncio.run(pool.switch_profile("codex"))
                self.assertTrue(r1["ok"])
                self.assertFalse(r1["hot"])
                self.assertIsInstance(pool.active, CodexAppServerManager)

                r2 = asyncio.run(pool.switch_profile("agy2"))
                self.assertTrue(r2["ok"])
                self.assertTrue(r2["hot"])
                self.assertNotIsInstance(pool.active, CodexAppServerManager)
            finally:
                AgyACPManager.start = orig_acp_start
                CodexAppServerManager.start = orig_codex_start
        finally:
            config.AGY_PROFILES_DIR = orig_pdir
            config.REAL_HOME = orig_home
            config.resolve_codex_binary = orig_resolve

    async def test_pool_background_prewarm_enables_hot_switch_and_cleans_up_on_stop(self):
        orig_pdir = config.AGY_PROFILES_DIR
        orig_home = config.REAL_HOME
        orig_resolve = config.resolve_codex_binary
        orig_default = config.DEFAULT_AGY_PROFILE
        orig_env = os.environ.get("VIBEONE_PREWARM_PROFILES")
        try:
            config.AGY_PROFILES_DIR = self.root / "profiles"
            config.REAL_HOME = self.root / "home"
            config.DEFAULT_AGY_PROFILE = "agy2"
            os.environ.pop("VIBEONE_PREWARM_PROFILES", None)
            tok = config.AGY_PROFILES_DIR / "agy2/.gemini/antigravity-cli/antigravity-oauth-token"
            tok.parent.mkdir(parents=True, exist_ok=True)
            tok.write_text('{"token":{"access_token":"t"},"id_token_email":"a@example.com"}', encoding="utf-8")
            config.resolve_codex_binary = lambda: "/usr/local/bin/codex"

            self.store.set_meta("active_profile", "agy2")
            pool = ProfileManagerPool(str(self.root), self.store)

            orig_acp_start = AgyACPManager.start
            orig_codex_start = CodexAppServerManager.start
            started = []

            async def fake_acp_start(mgr_self):
                started.append(mgr_self.current_profile)
                mgr_self.proc = DummyProc()
                mgr_self.connected = True

            async def fake_codex_start(mgr_self):
                started.append(mgr_self.current_profile)
                await asyncio.sleep(0.01)
                mgr_self.proc = DummyProc()
                mgr_self.connected = True

            AgyACPManager.start = fake_acp_start
            CodexAppServerManager.start = fake_codex_start
            try:
                await pool.start()
                tasks = pool.schedule_prewarm()
                self.assertEqual(len(tasks), 1)
                await asyncio.gather(*tasks)

                # Codex should now be warm and hot-switchable
                info = {p["name"]: p for p in pool.get_profiles_info()}
                self.assertTrue(info["agy2"]["warm"])
                self.assertTrue(info["codex"]["warm"])

                res = await pool.switch_profile("codex")
                self.assertTrue(res["ok"])
                self.assertTrue(res["hot"])
                self.assertEqual(started, ["agy2", "codex"])

                # VIBEONE_PREWARM_PROFILES=none disables prewarming
                os.environ["VIBEONE_PREWARM_PROFILES"] = "none"
                self.assertEqual(config.get_prewarm_profiles(), [])

                await pool.stop()
            finally:
                AgyACPManager.start = orig_acp_start
                CodexAppServerManager.start = orig_codex_start
        finally:
            config.AGY_PROFILES_DIR = orig_pdir
            config.REAL_HOME = orig_home
            config.resolve_codex_binary = orig_resolve
            config.DEFAULT_AGY_PROFILE = orig_default
            if orig_env is None:
                os.environ.pop("VIBEONE_PREWARM_PROFILES", None)
            else:
                os.environ["VIBEONE_PREWARM_PROFILES"] = orig_env

    async def test_pool_scans_and_async_starts_all_logged_in_profiles_without_primary_blocking(self):
        orig_pdir = config.AGY_PROFILES_DIR
        orig_home = config.REAL_HOME
        orig_resolve = config.resolve_codex_binary
        orig_default = config.DEFAULT_AGY_PROFILE
        orig_env = os.environ.get("VIBEONE_PREWARM_PROFILES")
        try:
            config.AGY_PROFILES_DIR = self.root / "profiles"
            config.REAL_HOME = self.root / "home"
            config.DEFAULT_AGY_PROFILE = "agy2"
            os.environ.pop("VIBEONE_PREWARM_PROFILES", None)

            for pname in ("agy2", "agy3", "work"):
                tok = config.AGY_PROFILES_DIR / pname / ".gemini/antigravity-cli/antigravity-oauth-token"
                tok.parent.mkdir(parents=True, exist_ok=True)
                tok.write_text(
                    f'{{"token":{{"access_token":"t-{pname}"}},"id_token_email":"{pname}@example.com"}}',
                    encoding="utf-8",
                )
            (config.AGY_PROFILES_DIR / "signed_out").mkdir(parents=True, exist_ok=True)
            config.resolve_codex_binary = lambda: "/usr/local/bin/codex"

            self.store.set_meta("active_profile", "agy2")
            pool = ProfileManagerPool(str(self.root), self.store)

            # Simulate a WS client already connected to agy2 before async startup completes
            sent_events = []

            class DummyWS:
                async def send_text(self, text):
                    sent_events.append(json.loads(text))

            ws = DummyWS()
            pool.clients.add(ws)
            pool.set_client_view(ws, "agy2", None)

            orig_acp_start = AgyACPManager.start
            orig_codex_start = CodexAppServerManager.start
            started = []

            async def fake_acp_start(mgr_self):
                started.append(mgr_self.current_profile)
                await asyncio.sleep(0.01)
                mgr_self.proc = DummyProc()
                mgr_self.connected = True
                mgr_self.session_id = f"sess-{mgr_self.current_profile}"

            async def fake_codex_start(mgr_self):
                started.append(mgr_self.current_profile)
                await asyncio.sleep(0.01)
                mgr_self.proc = DummyProc()
                mgr_self.connected = True
                mgr_self.session_id = "sess-codex"

            AgyACPManager.start = fake_acp_start
            CodexAppServerManager.start = fake_codex_start
            try:
                # Without calling pool.start() first, schedule_prewarm() starts ALL logged-in profiles asynchronously
                tasks = pool.schedule_prewarm()
                self.assertEqual(len(tasks), 4)
                await asyncio.gather(*tasks)

                self.assertEqual(set(started), {"agy2", "agy3", "work", "codex"})
                info = {p["name"]: p for p in pool.get_profiles_info()}
                for pname in ("agy2", "agy3", "work", "codex"):
                    self.assertTrue(info[pname]["warm"], f"{pname} should be warm")
                self.assertFalse(info["signed_out"]["warm"])

                # Connected viewer on agy2 should have its session_id populated and receive snapshot + sessions_changed
                self.assertEqual(pool.get_client_view(ws), ("agy2", "sess-agy2"))
                self.assertTrue(any(e.get("type") == "snapshot" and e["data"].get("connected") for e in sent_events))
                self.assertTrue(any(e.get("type") == "sessions_changed" for e in sent_events))

                # Explicit list and `all` keyword still work
                os.environ["VIBEONE_PREWARM_PROFILES"] = "agy3,codex"
                self.assertEqual(config.get_prewarm_profiles(), ["agy3", "codex"])
                os.environ["VIBEONE_PREWARM_PROFILES"] = "all"
                self.assertEqual(set(config.get_prewarm_profiles()), {"agy2", "agy3", "work", "codex"})

                await pool.stop()
            finally:
                AgyACPManager.start = orig_acp_start
                CodexAppServerManager.start = orig_codex_start
        finally:
            config.AGY_PROFILES_DIR = orig_pdir
            config.REAL_HOME = orig_home
            config.resolve_codex_binary = orig_resolve
            config.DEFAULT_AGY_PROFILE = orig_default
            if orig_env is None:
                os.environ.pop("VIBEONE_PREWARM_PROFILES", None)
            else:
                os.environ["VIBEONE_PREWARM_PROFILES"] = orig_env

    async def test_preferred_model_persisted_and_restored_per_agent_and_profile(self):
        orig_default = config.DEFAULT_AGY_PROFILE
        config.DEFAULT_AGY_PROFILE = "agy2"
        try:
            # 1. Legacy fallback: agy2 falls back to preferred_model; agy_r does not; codex falls back to codex_preferred_model
            self.store.set_meta("preferred_model", "gemini-4-neon-medium")
            self.store.set_meta("codex_preferred_model", "gpt-5.3-codex::high")

            agy2 = AgyACPManager(str(self.root), self.store, profile="agy2")
            agy_r = AgyACPManager(str(self.root), self.store, profile="agy_r")
            codex = CodexAppServerManager(str(self.root), self.store, profile="codex")

            self.assertEqual(agy2._get_preferred_model(), "gemini-4-neon-medium")
            self.assertEqual(agy_r._get_preferred_model(), "")
            self.assertEqual(codex._get_preferred_model(), "gpt-5.3-codex::high")

            # 2. Setting model on each profile keeps them strictly isolated
            agy2.connected = True
            agy2.session_id = "s_agy2"
            agy2._get_or_create_runtime("s_agy2", workdir=str(self.root))
            agy2.available_models = [
                {"modelId": "gemini-4-neon-high", "name": "Gemini 4 Neon (High)"},
                {"modelId": "gemini-4-neon-medium", "name": "Gemini 4 Neon (Medium)"},
                {"modelId": "claude-opus-4-6-thinking-low", "name": "Claude Opus (Low)"},
            ]
            agy2.start = AsyncMock()
            agy2.broadcast = AsyncMock()
            agy2._send_request = AsyncMock(return_value={})

            await agy2.set_model("gemini-4-neon-medium")

            agy_r.connected = True
            agy_r.session_id = "s_agyr"
            agy_r._get_or_create_runtime("s_agyr", workdir=str(self.root))
            agy_r.available_models = list(agy2.available_models)
            agy_r.start = AsyncMock()
            agy_r.broadcast = AsyncMock()
            agy_r._send_request = AsyncMock(return_value={})
            await agy_r.set_model("claude-opus-4-6-thinking-low")

            codex.connected = True
            codex.session_id = "thr_c1"
            codex._get_or_create_runtime("thr_c1", workdir=str(self.root))
            codex.available_models = [
                {"modelId": "gpt-5.4::medium", "name": "GPT-5.4 (medium)"},
                {"modelId": "gpt-5.4::xhigh", "name": "GPT-5.4 (xhigh)"},
            ]
            codex.start = AsyncMock()
            codex.broadcast = AsyncMock()
            await codex.set_model("gpt-5.4::xhigh")

            self.assertEqual(agy2._get_preferred_model(), "gemini-4-neon-medium")
            self.assertEqual(agy_r._get_preferred_model(), "claude-opus-4-6-thinking-low")
            self.assertEqual(codex._get_preferred_model(), "gpt-5.4::xhigh")

            # 3. ACP resume / switch_session restores preferred model when ACP defaults to neon-high
            agy2._send_request.reset_mock()

            async def fake_load_or_resume(sid, cwd):
                return (
                    {
                        "sessionId": sid,
                        "modes": {"currentModeId": "auto", "availableModes": [{"id": "auto", "name": "Auto"}]},
                        "models": {
                            "currentModelId": "gemini-4-neon-high",
                            "availableModels": agy2.available_models,
                        },
                    },
                    None,
                )

            agy2._load_or_resume_session = fake_load_or_resume
            await agy2.switch_session("s_resumed", workdir=str(self.root))
            self.assertEqual(agy2.current_model, "gemini-4-neon-medium")
            agy2._send_request.assert_any_await(
                "session/set_model",
                {"sessionId": "s_resumed", "modelId": "gemini-4-neon-medium"},
                timeout=10.0,
            )

            # 4. Fast-path switch on idle ACP session also aligns to preferred model, but skips busy sessions
            idle_rt = agy2._get_or_create_runtime("s_idle", workdir=str(self.root))
            idle_rt.loaded_in_acp = True
            idle_rt.current_model = "gemini-4-neon-high"
            agy2._send_request.reset_mock()
            await agy2.switch_session("s_idle", workdir=str(self.root))
            self.assertEqual(idle_rt.current_model, "gemini-4-neon-medium")

            busy_rt = agy2._get_or_create_runtime("s_busy", workdir=str(self.root))
            busy_rt.loaded_in_acp = True
            busy_rt.is_busy = True
            busy_rt.current_model = "gemini-4-neon-high"
            agy2._send_request.reset_mock()
            await agy2.switch_session("s_busy", workdir=str(self.root))
            self.assertEqual(busy_rt.current_model, "gemini-4-neon-high")
            set_model_calls = [
                c for c in agy2._send_request.await_args_list if c.args and c.args[0] == "session/set_model"
            ]
            self.assertEqual(set_model_calls, [])

            # 5. Codex fast-path switch also aligns idle thread to preferred model::effort
            c_idle = codex._get_or_create_runtime("thr_idle", workdir=str(self.root))
            c_idle.loaded_in_acp = True
            c_idle.current_model = "gpt-5.4::medium"
            await codex.switch_session("thr_idle", workdir=str(self.root))
            self.assertEqual(c_idle.current_model, "gpt-5.4::xhigh")
        finally:
            config.DEFAULT_AGY_PROFILE = orig_default

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
