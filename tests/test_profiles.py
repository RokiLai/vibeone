import base64
import json
import os
from pathlib import Path
import tempfile
import subprocess
import unittest
from unittest.mock import AsyncMock, patch

os.environ.setdefault("VIBEONE_WEB_TOKEN", "test-token")

from vibeone import AgyACPManager, ProfileManagerPool, SessionStore, config


class ProfileListTest(unittest.TestCase):
    def test_only_configured_accounts_are_listed(self):
        for has_home_token in (False, True):
            for current in (None, "default", "work", "codex"):
                with self.subTest(home_token=has_home_token, current=current):
                    with tempfile.TemporaryDirectory() as td:
                        home = Path(td)
                        profiles = home / "profiles"
                        for name in ("work", "personal"):
                            token = profiles / name / ".gemini/antigravity-cli/antigravity-oauth-token"
                            token.parent.mkdir(parents=True)
                            token.write_text(json.dumps({
                                "token": {"access_token": "test-token"},
                                "id_token_email": name + "@example.com",
                            }), encoding="utf-8")
                        (profiles / "signed-out").mkdir()
                        if has_home_token:
                            token = home / ".gemini/antigravity-cli/antigravity-oauth-token"
                            token.parent.mkdir(parents=True)
                            token.write_text('{"token":{"access_token":"test-token"}}', encoding="utf-8")
                        with patch.object(config, "REAL_HOME", home), \
                                patch.object(config, "AGY_PROFILES_DIR", profiles), \
                                patch.object(config, "resolve_codex_binary", return_value="codex"), \
                                patch.object(config, "_read_codex_auth_hint", return_value=(True, "codex@example.com")):
                            result = config.list_agy_profiles(current)
                        expected = ["personal", "signed-out", "work"]
                        if has_home_token:
                            expected.append("agy")
                        self.assertEqual([p["name"] for p in result], expected + ["codex"])
                        accounts = {p["name"]: p for p in result}
                        self.assertEqual(accounts["work"]["email"], "work@example.com")
                        self.assertTrue(accounts["work"]["logged_in"])
                        self.assertFalse(accounts["signed-out"]["logged_in"])
                        self.assertEqual([p["name"] for p in result if p["is_current"]],
                                         ["agy"] if current == "default" and has_home_token else
                                         [current] if current in accounts else [])
                        if has_home_token:
                            self.assertTrue(token.is_file())

    def test_no_configured_accounts_does_not_create_default(self):
        with tempfile.TemporaryDirectory() as td:
            with patch.object(config, "REAL_HOME", Path(td)), \
                    patch.object(config, "AGY_PROFILES_DIR", Path(td) / "missing"), \
                    patch.object(config, "resolve_codex_binary", return_value=""):
                self.assertEqual(config.list_agy_profiles("default"), [])


class KeychainAccountTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name).resolve()
        for target, value in (("REAL_HOME", self.home), ("AGY_PROFILES_DIR", self.home / "profiles")):
            p = patch.object(config, target, value)
            p.start()
            self.addCleanup(p.stop)
        for p in (patch.object(config.sys, "platform", "darwin"),
                  patch.object(Path, "home", return_value=self.home)):
            p.start()
            self.addCleanup(p.stop)
        self.auth = {"token": {"access_token": "fake-access"}, "id_token_email": "home@example.com"}
        self.encoded = "go-keyring-base64:" + base64.b64encode(json.dumps(self.auth).encode()).decode()
        p = patch.object(config.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, self.encoded, ""))
        self.security = p.start()
        self.addCleanup(p.stop)

    def test_keychain_account_identity_and_home(self):
        for payload in (self.encoded, json.dumps(self.auth)):
            self.security.return_value.stdout = payload
            self.assertEqual(config.read_agy_auth("agy"), self.auth)
        self.assertEqual(config.resolve_profile_home("agy"), self.home)
        self.assertEqual(config.resolve_profile_home("default"), self.home)
        with patch.object(config, "resolve_codex_binary", return_value=""):
            self.assertEqual(config.list_agy_profiles("agy"), [{
                "name": "agy", "backend": "agy", "email": "home@example.com",
                "logged_in": True, "is_current": True,
            }])
        self.security.assert_called_with(
            ["/usr/bin/security", "find-generic-password", "-s", "gemini", "-a", "antigravity", "-w"],
            capture_output=True, text=True, timeout=3, check=True,
        )

    def test_keychain_failure_and_malformed_credentials(self):
        for error in (OSError(), subprocess.TimeoutExpired("security", 3),
                      subprocess.CalledProcessError(44, "security")):
            self.security.side_effect = error
            self.assertEqual(config.read_agy_auth("agy"), {})
        self.security.side_effect = None
        for value in ("bad-json", "go-keyring-base64:!", "[]", '{"token":1}', '{}'):
            self.security.return_value.stdout = value
            self.assertEqual(config.read_agy_auth("agy"), {})
        with patch.object(config, "resolve_codex_binary", return_value=""):
            self.assertEqual(config.list_agy_profiles("agy"), [])

    def test_file_credentials_and_isolation(self):
        for name in ("agy2", "agy"):
            folder = config.AGY_PROFILES_DIR / name
            folder.mkdir(parents=True)
            self.assertEqual(config.read_agy_auth(name), {})
            token = config.get_profile_token_file(name)
            token.parent.mkdir(parents=True)
            token.write_text(json.dumps(self.auth), encoding="utf-8")
            self.assertEqual(config.read_agy_auth(name), self.auth)
            token.write_text("bad-json", encoding="utf-8")
            self.assertEqual(config.read_agy_auth(name), {})
        self.security.assert_not_called()
        self.assertEqual(config.resolve_profile_home("agy"), config.AGY_PROFILES_DIR / "agy")

    def test_other_platform_or_home_does_not_read_login_keychain(self):
        with patch.object(config.sys, "platform", "linux"):
            self.assertEqual(config.read_agy_auth("agy"), {})
        with patch.object(Path, "home", return_value=self.home / "other"):
            self.assertEqual(config.read_agy_auth("agy"), {})
        self.security.assert_not_called()

    async def test_pool_switch_and_spawn_storage_mode(self):
        store = SessionStore(self.home / "test.db")
        self.addCleanup(store._conn.close)
        store.set_meta("active_profile", "agy")
        pool = ProfileManagerPool(str(self.home), store)
        with patch.object(AgyACPManager, "start", new_callable=AsyncMock):
            mgr, hot = await pool.ensure_profile_ready("agy")
        self.assertFalse(hot)
        self.assertEqual(mgr._current_profile_email(), "home@example.com")
        (config.AGY_PROFILES_DIR / "agy2").mkdir(parents=True)
        for profile in ("agy", "agy2"):
            mgr.current_profile = profile
            spawn = AsyncMock(side_effect=RuntimeError("stop before real spawn"))
            with patch.object(config, "resolve_agy_binary", return_value="agy"), \
                    patch.object(config, "ensure_profile_home", side_effect=config.resolve_profile_home), \
                    patch.dict(os.environ, {"AGY_CLI_FORCE_FILE_TOKEN_STORAGE": "1"}), \
                    patch("vibeone.engines.acp.asyncio.create_subprocess_exec", spawn):
                with self.assertRaisesRegex(RuntimeError, "stop before real spawn"):
                    await mgr._spawn_and_initialize()
            env = spawn.call_args.kwargs["env"]
            self.assertEqual(env["HOME"], str(config.resolve_profile_home(profile)))
            self.assertEqual(env.get("AGY_CLI_FORCE_FILE_TOKEN_STORAGE"), None if profile == "agy" else "1")

        # When REAL_HOME has an explicit file token, `agy` must force file storage so it matches read_agy_auth()
        home_token = config.get_profile_token_file("agy")
        home_token.parent.mkdir(parents=True, exist_ok=True)
        home_token.write_text(json.dumps(self.auth), encoding="utf-8")
        mgr.current_profile = "agy"
        spawn = AsyncMock(side_effect=RuntimeError("stop before real spawn"))
        clean_env = {k: v for k, v in os.environ.items() if k != "AGY_CLI_FORCE_FILE_TOKEN_STORAGE"}
        with patch.object(config, "resolve_agy_binary", return_value="agy"), \
                patch.object(config, "ensure_profile_home", side_effect=config.resolve_profile_home), \
                patch.dict(os.environ, clean_env, clear=True), \
                patch("vibeone.engines.acp.asyncio.create_subprocess_exec", spawn):
            with self.assertRaisesRegex(RuntimeError, "stop before real spawn"):
                await mgr._spawn_and_initialize()
        env = spawn.call_args.kwargs["env"]
        self.assertEqual(env["HOME"], str(self.home))
        self.assertEqual(env.get("AGY_CLI_FORCE_FILE_TOKEN_STORAGE"), "1")


if __name__ == "__main__":
    unittest.main()
