import json
import os
import pathlib
import tempfile
import unittest

os.environ.setdefault("VIBEONE_WEB_TOKEN", "test-token")

from vibeone import AgyACPManager, ProfileManagerPool, SessionStore, config, quota  # noqa: E402


class QuotaAndProfilePoolTest(unittest.IsolatedAsyncioTestCase):
    def test_profile_name_validation(self) -> None:
        self.assertTrue(config._is_valid_profile_name("agy2"))
        self.assertTrue(config._is_valid_profile_name("agy_r"))
        self.assertTrue(config._is_valid_profile_name("work-1"))
        self.assertFalse(config._is_valid_profile_name(""))
        self.assertFalse(config._is_valid_profile_name("../etc"))
        self.assertFalse(config._is_valid_profile_name("a/b"))

    def test_summary_splits_5h_and_weekly(self) -> None:
        payload = {
            "groups": [
                {
                    "displayName": "Claude and GPT models",
                    "description": "Models within this group: Claude Opus 5.5, GPT-OSS 120B",
                    "buckets": [
                        {"window": "weekly", "remainingFraction": 0.63, "resetTime": "W"},
                        {"window": "5h", "remainingFraction": 0.27, "resetTime": "H"},
                    ],
                }
            ]
        }
        g = quota.parse_quota_summary(payload)[0]
        self.assertEqual(g["members"], ["Claude Opus 5.5", "GPT-OSS 120B"])
        self.assertEqual([b["window"] for b in g["buckets"]], ["5h", "weekly"])
        self.assertEqual(g["buckets"][0]["remaining"], 0.27)

    def test_groups_by_pool_and_treats_missing_fraction_as_zero(self) -> None:
        payload = {
            "models": {
                "a": {"displayName": "Claude A", "quotaInfo": {"remainingFraction": 0.71, "resetTime": "T2"}},
                "b": {"displayName": "Claude B", "quotaInfo": {"remainingFraction": 0.71, "resetTime": "T2"}},
                "g": {"displayName": "Gemini 3 Flash", "quotaInfo": {"resetTime": "T1"}},
                "tab": {"displayName": None, "quotaInfo": {"remainingFraction": 1}},
                "noq": {"displayName": "NoQuota"},
                "unk": {"displayName": "Unknown", "quotaInfo": {}},
            }
        }
        groups = quota.parse_quota_response(payload)
        self.assertEqual(len(groups), 2)
        self.assertEqual(groups[0], {"remaining": 0.0, "reset_time": "T1", "models": ["Gemini 3 Flash"]})
        self.assertEqual(groups[1]["remaining"], 0.71)
        self.assertEqual(groups[1]["models"], ["Claude A", "Claude B"])
        names = [n for g in groups for n in g["models"]]
        self.assertNotIn("NoQuota", names)
        self.assertNotIn("Unknown", names)

    def test_empty_payload(self) -> None:
        self.assertEqual(quota.parse_quota_response({}), [])

    async def test_pool_hot_switch_and_failed_cold_switch(self) -> None:
        class DummyProc:
            returncode = None

        with tempfile.TemporaryDirectory() as td:
            pdir = pathlib.Path(td) / "profiles"
            for name in ("p1", "p2", "bad"):
                tf = pdir / name / ".gemini/antigravity-cli/antigravity-oauth-token"
                tf.parent.mkdir(parents=True)
                tf.write_text(json.dumps({"token": {"access_token": "tok"}, "id_token_email": f"{name}@example.com"}))
            orig_pdir, orig_home = config.AGY_PROFILES_DIR, config.REAL_HOME
            config.AGY_PROFILES_DIR = pdir
            config.REAL_HOME = pathlib.Path(td)
            try:
                st = SessionStore(pathlib.Path(td) / "test.db")
                st.set_meta("active_profile", "p1")
                pool = ProfileManagerPool(td, st)
                started = []

                orig_start = AgyACPManager.start

                async def fake_start(mgr_self):
                    started.append(mgr_self.current_profile)
                    if mgr_self.current_profile == "bad":
                        raise RuntimeError("Authentication required")
                    mgr_self.proc = DummyProc()
                    mgr_self.connected = True

                AgyACPManager.start = fake_start
                try:
                    await pool.start()
                    r1 = await pool.switch_profile("p2")
                    self.assertTrue(r1["ok"] and not r1["hot"] and pool.current_profile == "p2")
                    r2 = await pool.switch_profile("p1")
                    self.assertTrue(r2["ok"] and r2["hot"] and pool.current_profile == "p1")
                    with self.assertRaises(RuntimeError) as ctx:
                        await pool.switch_profile("bad")
                    self.assertIn("认证已过期或未登录", str(ctx.exception))
                    self.assertEqual(pool.current_profile, "p1")
                    self.assertEqual(started, ["p1", "p2", "bad"])
                finally:
                    AgyACPManager.start = orig_start
                    st._conn.close()
            finally:
                config.AGY_PROFILES_DIR = orig_pdir
                config.REAL_HOME = orig_home


class CredentialQuotaTest(unittest.TestCase):
    def test_quota_uses_shared_profile_credentials(self):
        from unittest.mock import patch
        with patch.object(config, "read_agy_auth", return_value={"token": {"access_token": "fake-keychain-token"}}) as read:
            self.assertEqual(quota._read_access_token("agy"), "fake-keychain-token")
            read.assert_called_once_with("agy")
        with patch.object(config, "read_agy_auth", return_value={}):
            with self.assertRaisesRegex(RuntimeError, "access_token"):
                quota._read_access_token("agy")

    def test_explicit_quota_token_file_still_supported(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as td:
            token = pathlib.Path(td) / "token.json"
            token.write_text('{"token":{"access_token":"fake-explicit"}}', encoding="utf-8")
            with patch.dict(os.environ, {"AGY_OAUTH_TOKEN_FILE": str(token)}), \
                    patch.object(config, "read_agy_auth", return_value={"token": {"access_token": "fake-profile"}}):
                self.assertEqual(quota._read_access_token(), "fake-explicit")
                self.assertEqual(quota._read_access_token("agy"), "fake-profile")


if __name__ == "__main__":
    unittest.main()
