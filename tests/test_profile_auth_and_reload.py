import asyncio
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from vibeone import config
from vibeone.auth_flow import (
    ProfileAuthFlowManager,
    extract_auth_url,
    extract_device_code,
    strip_ansi,
)
from vibeone.engines.acp import SessionRuntime
from vibeone.engines.pool import ProfileManagerPool
from vibeone.store import SessionStore


class FakeProfileManager:
    def __init__(self, profile: str, email: str = "user@example.com"):
        self.current_profile = profile
        self._email = email
        self.connected = False
        self.proc = type("Proc", (), {"returncode": None})()
        self.session_id = f"sess_{profile}"
        self._runtimes = {self.session_id: SessionRuntime(self.session_id, "/tmp", "Test")}
        self.started = False
        self.stopped = False
        self.broadcasts = []

    async def start(self):
        self.started = True
        self.connected = True

    async def stop(self):
        self.stopped = True
        self.connected = False

    def _current_profile_email(self):
        return self._email

    def get_snapshot(self, sid=None):
        return {"profile": self.current_profile, "session_id": sid or self.session_id}

    async def broadcast(self, event, *, sid=None, targets=None):
        self.broadcasts.append((event, targets))


class AuthFlowParsingTests(unittest.TestCase):
    def test_strip_ansi_and_extract_codex_device_auth(self):
        sample = (
            "\x1b[1mFollow these steps to sign in with ChatGPT:\x1b[0m\n"
            "  1. Open this link in your browser: \x1b[36mhttps://auth.openai.com/codex/device\x1b[0m\n"
            "  2. Enter this one-time code: \x1b[32mABCD-1234\x1b[0m\n"
        )
        self.assertEqual(extract_auth_url(sample), "https://auth.openai.com/codex/device")
        self.assertEqual(extract_device_code(sample), "ABCD-1234")
        self.assertNotIn("\x1b", strip_ansi(sample))

    def test_extract_agy_oauth_url(self):
        sample = (
            "Open this link in a browser to log in with Google:\n\n"
            "  https://accounts.google.com/o/oauth2/auth?client_id=123&redirect_uri=https%3A%2F%2Fantigravity.google%2Foauth-callback\n\n"
            "Then paste the authorization code, or the full URL of the page you land on, and press Enter:\n"
        )
        self.assertTrue(extract_auth_url(sample).startswith("https://accounts.google.com/o/oauth2/auth?"))


class ProfileReloadAndAuthFlowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._tmp.name)
        self.profiles_dir = self.tmp_path / "profiles"
        self.profiles_dir.mkdir(parents=True, exist_ok=True)
        self.agy_profile = self.profiles_dir / "agy_test"
        (self.agy_profile / ".gemini/antigravity-cli").mkdir(parents=True, exist_ok=True)
        (self.agy_profile / ".gemini/antigravity-cli/antigravity-oauth-token").write_text(
            json.dumps({"token": {"access_token": "tok"}, "id_token_email": "old@example.com"}),
            encoding="utf-8",
        )
        self.store = SessionStore(self.tmp_path / "test.db")

    async def asyncTearDown(self):
        self._tmp.cleanup()

    def _make_fake_cli(self, name: str, script_body: str) -> str:
        p = self.tmp_path / name
        p.write_text(f"#!{sys.executable}\n{script_body}", encoding="utf-8")
        p.chmod(p.stat().st_mode | stat.S_IXUSR)
        return str(p)

    async def test_pool_reload_profile_checks_busy_and_restarts_only_target(self):
        with mock.patch.object(config, "AGY_PROFILES_DIR", self.profiles_dir), \
             mock.patch.object(config, "REAL_HOME", self.tmp_path):
            pool = ProfileManagerPool(str(self.tmp_path), self.store)
            old_mgr = FakeProfileManager("agy_test", email="old@example.com")
            old_mgr.connected = True
            old_mgr._runtimes[old_mgr.session_id].is_busy = True
            pool._managers["agy_test"] = old_mgr

            res_busy = await pool.reload_profile("agy_test", force=False)
            self.assertFalse(res_busy["ok"])
            self.assertTrue(res_busy["needs_confirm"])
            self.assertEqual(res_busy["busy_count"], 1)
            self.assertFalse(old_mgr.stopped)

            new_mgr = FakeProfileManager("agy_test", email="new@example.com")
            with mock.patch.object(pool, "_create_manager", return_value=new_mgr):
                res_ok = await pool.reload_profile("agy_test", force=True)
            self.assertTrue(res_ok["ok"])
            self.assertTrue(old_mgr.stopped)
            self.assertTrue(new_mgr.started)
            self.assertIs(pool._managers["agy_test"], new_mgr)
            self.assertEqual(res_ok["email"], "new@example.com")

    async def test_codex_device_auth_flow_and_auto_reload(self):
        fake_codex = self._make_fake_cli(
            "fake_codex.py",
            """
import sys, time
if len(sys.argv) > 1 and sys.argv[1] == "logout":
    sys.exit(0)
print("Open https://auth.openai.com/codex/device", flush=True)
print("Code: WXYZ-9876", flush=True)
time.sleep(0.25)
print("Successfully logged in", flush=True)
sys.exit(0)
""",
        )
        pool = mock.MagicMock()
        pool.reload_profile = mock.AsyncMock(return_value={"ok": True, "email": "codex@example.com"})
        mgr = ProfileAuthFlowManager(pool)

        with mock.patch.object(config, "resolve_codex_binary", return_value=fake_codex), \
             mock.patch.object(config, "REAL_HOME", self.tmp_path):
            started = await mgr.start_login("codex", logout_first=True)
            self.assertEqual(started["backend"], "codex")
            # Wait for flow completion
            for _ in range(40):
                cur = mgr.get_flow(started["flow_id"])
                if cur["status"] in ("succeeded", "failed"):
                    break
                await asyncio.sleep(0.05)
            cur = mgr.get_flow(started["flow_id"])
            self.assertEqual(cur["status"], "succeeded")
            self.assertEqual(cur["auth_url"], "https://auth.openai.com/codex/device")
            self.assertEqual(cur["user_code"], "WXYZ-9876")
            self.assertEqual(cur["email"], "codex@example.com")
            pool.reload_profile.assert_awaited_once_with("codex", force=True)

    async def test_agy_oauth_flow_submit_code_and_auto_reload(self):
        fake_agy = self._make_fake_cli(
            "fake_agy.py",
            """
import sys
print("Open this link in a browser to log in with Google:", flush=True)
print("  https://accounts.google.com/o/oauth2/auth?state=abc&redirect_uri=https%3A%2F%2Fantigravity.google%2Foauth-callback", flush=True)
print("Then paste the authorization code:", flush=True)
line = sys.stdin.readline().strip()
if line == "my-oauth-code-123":
    print("Login succeeded", flush=True)
    sys.exit(0)
print("Invalid code", flush=True)
sys.exit(1)
""",
        )
        pool = mock.MagicMock()
        pool.reload_profile = mock.AsyncMock(return_value={"ok": True, "email": "agy@example.com"})
        mgr = ProfileAuthFlowManager(pool)

        with mock.patch.object(config, "AGY_PROFILES_DIR", self.profiles_dir), \
             mock.patch.object(config, "REAL_HOME", self.tmp_path), \
             mock.patch.object(config, "resolve_agy_binary", return_value=fake_agy):
            started = await mgr.start_login("agy_new", create_if_missing=True)
            self.assertEqual(started["backend"], "agy")
            self.assertEqual(started["status"], "awaiting_user")
            self.assertTrue(started["needs_code_input"])
            self.assertTrue(started["auth_url"].startswith("https://accounts.google.com/o/oauth2/auth"))
            self.assertTrue((self.profiles_dir / "agy_new").is_dir())

            after_submit = await mgr.submit_code(started["flow_id"], "my-oauth-code-123")
            self.assertEqual(after_submit["status"], "succeeded")
            self.assertEqual(after_submit["email"], "agy@example.com")
            pool.reload_profile.assert_awaited_once_with("agy_new", force=True)

    async def test_codex_multi_profile_isolation_and_create_login(self):
        codex_profiles_dir = self.tmp_path / "codex_profiles"
        default_codex_skills = self.tmp_path / ".codex" / "skills"
        default_codex_skills.mkdir(parents=True, exist_ok=True)
        (default_codex_skills / "demo").mkdir()

        fake_codex = self._make_fake_cli(
            "fake_codex_multi.py",
            """
import json, os, sys
from pathlib import Path
ch = Path(os.environ["CODEX_HOME"])
print("Open https://auth.openai.com/codex/device", flush=True)
print("Code: MNOP-4321", flush=True)
(ch / "auth.json").write_text(json.dumps({"OPENAI_API_KEY": "sk-test-codex2"}), encoding="utf-8")
sys.exit(0)
""",
        )
        pool = mock.MagicMock()
        pool.reload_profile = mock.AsyncMock(return_value={"ok": True, "email": "API Key"})
        mgr = ProfileAuthFlowManager(pool)

        with mock.patch.object(config, "AGY_PROFILES_DIR", self.profiles_dir), \
             mock.patch.object(config, "CODEX_PROFILES_DIR", codex_profiles_dir), \
             mock.patch.object(config, "REAL_HOME", self.tmp_path), \
             mock.patch.object(config, "resolve_codex_binary", return_value=fake_codex):
            started = await mgr.start_login("codex2", backend="codex", create_if_missing=True)
            self.assertEqual(started["backend"], "codex")
            for _ in range(40):
                cur = mgr.get_flow(started["flow_id"])
                if cur["status"] in ("succeeded", "failed"):
                    break
                await asyncio.sleep(0.05)
            cur = mgr.get_flow(started["flow_id"])
            self.assertEqual(cur["status"], "succeeded")
            self.assertTrue(config.is_codex_profile("codex2"))
            self.assertTrue((codex_profiles_dir / "codex2" / "auth.json").is_file())
            self.assertTrue((codex_profiles_dir / "codex2" / "skills").is_symlink())
            listed = {p["name"]: p for p in config.list_agy_profiles("codex2")}
            self.assertIn("codex2", listed)
            self.assertEqual(listed["codex2"]["backend"], "codex")
            self.assertTrue(listed["codex2"]["logged_in"])
            self.assertTrue(listed["codex2"]["is_current"])
            pool.reload_profile.assert_awaited_once_with("codex2", force=True)


if __name__ == "__main__":
    unittest.main()
