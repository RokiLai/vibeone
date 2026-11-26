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


class CodexPoolTest(unittest.IsolatedAsyncioTestCase):
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

if __name__ == "__main__":
    unittest.main()
