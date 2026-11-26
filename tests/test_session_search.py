import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("VIBEONE_WEB_TOKEN", "test-token")

from fastapi import HTTPException  # noqa: E402

import server  # noqa: E402
from vibeone import AgyACPManager, CodexAppServerManager, SessionStore  # noqa: E402


class SessionFullTextSearchTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.dir_a = self.root / "proj_alpha"
        self.dir_b = self.root / "proj_beta"
        self.dir_hidden = self.root / "proj_hidden"
        self.dir_a.mkdir()
        self.dir_b.mkdir()
        self.dir_hidden.mkdir()

        self.store = SessionStore(self.root / "search_test.db")

        # Profile A (agy2) sessions
        self.store.upsert_session(
            "s_a_main", str(self.dir_a), "重构侧边栏", updated_at=500.0, touch=True, profile="agy2"
        )
        self.store.upsert_item(
            "s_a_main",
            {"id": "i_a1", "type": "thought", "text": "内部思考提到了 WebPush 证书", "ts": 501.0},
        )
        self.store.upsert_item(
            "s_a_main",
            {"id": "i_a2", "type": "assistant", "text": "已经修复了 WebPush 推送通知逻辑。", "ts": 502.0},
        )
        self.store.upsert_item(
            "s_a_main",
            {"id": "i_a3", "type": "user", "text": "请帮我排查 webpush 推送失败的原因", "ts": 503.0},
        )

        self.store.upsert_session(
            "s_a_tool", str(self.dir_b), "日常排查", updated_at=400.0, touch=True, profile="agy2"
        )
        self.store.upsert_item(
            "s_a_tool",
            {
                "id": "i_t1",
                "type": "tool",
                "toolCallId": "call_xyz_unique",
                "name": "run_command",
                "title": "pytest",
                "kind": "execute",
                "status": "completed",
                "args": {"CommandLine": "pytest tests/test_quota.py"},
                "output": "FAILED test_quota_refresh: AssertionError: expected 200",
                "ts": 401.0,
            },
        )

        self.store.upsert_session(
            "s_a_archived", str(self.dir_a), "旧归档会话", updated_at=300.0, touch=True, profile="agy2"
        )
        self.store.upsert_item(
            "s_a_archived",
            {"id": "i_arc1", "type": "assistant", "text": "归档记录里包含 秘密归档关键词", "ts": 301.0},
        )

        self.store.upsert_session(
            "s_a_hidden_sess", str(self.dir_a), "被隐藏的会话", updated_at=200.0, touch=True, profile="agy2"
        )
        self.store.upsert_item(
            "s_a_hidden_sess",
            {"id": "i_hid1", "type": "user", "text": "隐藏会话里的 webpush 内容", "ts": 201.0},
        )

        self.store.upsert_session(
            "s_a_hidden_dir", str(self.dir_hidden), "隐藏目录会话", updated_at=150.0, touch=True, profile="agy2"
        )
        self.store.upsert_item(
            "s_a_hidden_dir",
            {"id": "i_hdir1", "type": "user", "text": "隐藏目录里的 webpush 内容", "ts": 151.0},
        )

        # Profile B (codex) sessions — MUST NEVER leak into agy2 search
        self.store.upsert_session(
            "s_b_codex", str(self.dir_a), "Codex WebPush 专属任务", updated_at=900.0, touch=True, profile="codex"
        )
        self.store.upsert_item(
            "s_b_codex",
            {"id": "i_b1", "type": "user", "text": "这是 codex 账号的 webpush 和 秘密归档关键词", "ts": 901.0},
        )

        # Profile C (agy) sessions — MUST NEVER leak into agy2 or codex search
        self.store.upsert_session(
            "s_c_other", str(self.dir_a), "另一个账号", updated_at=950.0, touch=True, profile="agy"
        )
        self.store.upsert_item(
            "s_c_other",
            {"id": "i_c1", "type": "assistant", "text": "跨账号敏感内容 webpush token_secret_999", "ts": 951.0},
        )

        self.mgr_agy2 = AgyACPManager(str(self.dir_a), self.store, profile="agy2")
        self.mgr_agy2.session_id = "s_a_main"
        self.mgr_agy2._add_archived_sessions({"s_a_archived"})
        self.mgr_agy2._add_hidden_sessions({"s_a_hidden_sess"})
        self.store.set_meta("hidden_workdirs", [str(self.dir_hidden)])

        self.mgr_codex = CodexAppServerManager(str(self.dir_a), self.store, profile="codex")
        self.mgr_codex.session_id = "s_b_codex"

    async def asyncTearDown(self) -> None:
        self.store._conn.close()
        self._tmp.cleanup()

    async def test_store_search_strictly_isolates_profiles_and_prioritizes_user_snippet(self) -> None:
        # Empty profile fails closed
        self.assertEqual(self.store.search_sessions("", "webpush"), [])

        # agy2 search must NOT return codex or agy sessions
        hits_agy2 = self.store.search_sessions("agy2", "webpush")
        sids_agy2 = {h["session_id"] for h in hits_agy2}
        self.assertIn("s_a_main", sids_agy2)
        self.assertNotIn("s_b_codex", sids_agy2)
        self.assertNotIn("s_c_other", sids_agy2)

        main_hit = next(h for h in hits_agy2 if h["session_id"] == "s_a_main")
        self.assertEqual(main_hit["match_type"], "user")
        self.assertEqual(main_hit["match_count"], 3)
        self.assertIn("排查 webpush", main_hit["snippet"])

        # Searching a keyword that only exists in profile 'agy' returns nothing in 'agy2'
        self.assertEqual(self.store.search_sessions("agy2", "token_secret_999"), [])
        hits_agy = self.store.search_sessions("agy", "token_secret_999")
        self.assertEqual([h["session_id"] for h in hits_agy], ["s_c_other"])

    async def test_store_search_tool_content_and_ignores_json_structural_keys(self) -> None:
        # Matches tool output and multi-term query
        hits = self.store.search_sessions("agy2", "AssertionError 200")
        self.assertEqual([h["session_id"] for h in hits], ["s_a_tool"])
        self.assertEqual(hits[0]["match_type"], "tool")
        self.assertIn("AssertionError", hits[0]["snippet"])

        # Should NOT falsely match JSON structural keys like "toolCallId" or "call_xyz_unique"
        self.assertEqual(self.store.search_sessions("agy2", "toolCallId"), [])
        self.assertEqual(self.store.search_sessions("agy2", "call_xyz_unique"), [])

    async def test_manager_search_respects_active_archived_hidden_and_profile_scope(self) -> None:
        active_hits = await self.mgr_agy2.search_sessions("webpush", scope="active")
        self.assertEqual([h["session_id"] for h in active_hits], ["s_a_main"])

        # Archived session is excluded from active scope, included in archived scope
        self.assertEqual(await self.mgr_agy2.search_sessions("秘密归档关键词", scope="active"), [])
        arc_hits = await self.mgr_agy2.search_sessions("秘密归档关键词", scope="archived")
        self.assertEqual([h["session_id"] for h in arc_hits], ["s_a_archived"])
        self.assertTrue(arc_hits[0]["is_archived"])

        # Codex manager only sees codex sessions, never agy2 or agy
        codex_hits = await self.mgr_codex.search_sessions("webpush", scope="active")
        self.assertEqual([h["session_id"] for h in codex_hits], ["s_b_codex"])
        self.assertEqual(await self.mgr_codex.search_sessions("token_secret_999", scope="all"), [])

    async def test_http_search_endpoint_profile_isolation_and_invalid_profile(self) -> None:
        orig_managers = dict(server.manager._managers)
        orig_current = server.manager.current_profile
        try:
            server.manager._managers["agy2"] = self.mgr_agy2
            server.manager._managers["codex"] = self.mgr_codex
            server.manager.current_profile = "agy2"

            body_a = await server.search_sessions_api(
                q="webpush", profile="agy2", session_id=None, scope="active", limit=50, _token="test-token"
            )
            self.assertEqual(body_a["current_profile"], "agy2")
            self.assertEqual([r["session_id"] for r in body_a["results"]], ["s_a_main"])

            body_b = await server.search_sessions_api(
                q="webpush", profile="codex", session_id=None, scope="active", limit=50, _token="test-token"
            )
            self.assertEqual(body_b["current_profile"], "codex")
            self.assertEqual([r["session_id"] for r in body_b["results"]], ["s_b_codex"])

            # Invalid profile MUST return 400 rather than falling back to another account
            with self.assertRaises(HTTPException) as ctx:
                await server.search_sessions_api(
                    q="nonexistent_profile_xyz",
                    profile="nonexistent_profile_xyz",
                    session_id=None,
                    scope="active",
                    limit=50,
                    _token="test-token",
                )
            self.assertEqual(ctx.exception.status_code, 400)
        finally:
            server.manager._managers.clear()
            server.manager._managers.update(orig_managers)
            server.manager.current_profile = orig_current

    async def test_fts5_short_terms_special_chars_and_mutation_sync(self) -> None:
        self.assertTrue(self.store._fts5_enabled)

        # Short (<3 char) Chinese & English terms still match accurately
        short_zh = self.store.search_sessions("agy2", "推送")
        self.assertEqual([h["session_id"] for h in short_zh], ["s_a_main"])

        # Special FTS5 syntax characters should be treated literally without raising errors
        self.store.upsert_item(
            "s_a_main",
            {"id": "i_spec", "type": "assistant", "text": 'Check "foo-bar:baz*" AND OR NOT', "ts": 505.0},
        )
        spec_hits = self.store.search_sessions("agy2", '"foo-bar:baz*"')
        self.assertEqual([h["session_id"] for h in spec_hits], ["s_a_main"])

        # Updating an item replaces old FTS5 content
        self.store.upsert_item(
            "s_a_main",
            {"id": "i_spec", "type": "assistant", "text": "replaced_unique_marker_xyz", "ts": 506.0},
        )
        self.assertEqual(self.store.search_sessions("agy2", "foo-bar:baz*"), [])
        self.assertEqual(
            [h["session_id"] for h in self.store.search_sessions("agy2", "replaced_unique_marker_xyz")],
            ["s_a_main"],
        )

        # Deleting a session removes its FTS5 entries
        self.store.delete_session("s_a_tool")
        self.assertEqual(self.store.search_sessions("agy2", "AssertionError"), [])

    async def test_fts5_v2_migration_tool_output_truncation_and_clear(self) -> None:
        # Verify v2 schema & contentless_delete on SQLite >= 3.43
        self.assertEqual(self.store.get_meta("fts5_items_version"), 2)

        # Tool output beyond 4096 chars is preserved in items payload but truncated in FTS index
        long_output = "head_marker_alpha " + ("x" * 5000) + " tail_marker_omega"
        self.store.upsert_item(
            "s_a_tool",
            {
                "id": "i_long_tool",
                "type": "tool",
                "name": "run_command",
                "output": long_output,
                "ts": 410.0,
            },
        )
        stored = self.store.get_item("i_long_tool")
        self.assertIsNotNone(stored)
        self.assertIn("tail_marker_omega", stored["output"])
        self.assertEqual(
            [h["session_id"] for h in self.store.search_sessions("agy2", "head_marker_alpha")],
            ["s_a_tool"],
        )
        self.assertEqual(self.store.search_sessions("agy2", "tail_marker_omega"), [])

        # Clear single session removes its FTS entries cleanly
        self.store.clear_session("s_a_tool")
        self.assertEqual(self.store.search_sessions("agy2", "head_marker_alpha"), [])

        # Re-opening DB with old fts5_items_version=1 triggers clean v2 rebuild
        db_path = self.root / "search_test.db"
        self.store.set_meta("fts5_items_version", 1)
        reopened = SessionStore(db_path)
        try:
            self.assertEqual(reopened.get_meta("fts5_items_version"), 2)
            self.assertEqual(
                [h["session_id"] for h in reopened.search_sessions("agy2", "webpush")],
                ["s_a_main", "s_a_hidden_sess", "s_a_hidden_dir"],
            )
            reopened.optimize_fts5()
        finally:
            reopened._conn.close()

    async def test_streaming_deferred_upsert_coalesces_and_flushes(self) -> None:
        import asyncio

        upsert_calls = []
        orig_upsert_items = self.store.upsert_items

        def _spy_upsert_items(entries):
            upsert_calls.append(len(entries))
            return orig_upsert_items(entries)

        self.store.upsert_items = _spy_upsert_items
        self.mgr_agy2._dirty_flush_interval = 0.05
        sid = "s_a_main"
        item = {"id": "asst_stream_1", "type": "assistant", "text": "part1", "ts": 600.0}
        self.mgr_agy2._add_or_update_item(item, sid=sid, deferred=True)
        item["text"] += " part2_unique_stream"
        self.mgr_agy2._add_or_update_item(item, sid=sid, deferred=True)
        item["text"] += " part3"
        self.mgr_agy2._add_or_update_item(item, sid=sid, deferred=True)

        # In-memory state is updated immediately; DB write is deferred
        self.assertEqual(upsert_calls, [])
        self.assertEqual(
            self.mgr_agy2._runtimes[sid].items_by_id["asst_stream_1"]["text"],
            "part1 part2_unique_stream part3",
        )

        # Wait for scheduled timer flush
        await asyncio.sleep(0.1)
        self.assertEqual(upsert_calls, [1])
        saved = self.store.get_item("asst_stream_1")
        self.assertIsNotNone(saved)
        self.assertEqual(saved["text"], "part1 part2_unique_stream part3")
        self.assertEqual(
            [h["session_id"] for h in self.store.search_sessions("agy2", "part2_unique_stream")],
            [sid],
        )

    async def test_server_import_uses_isolated_test_db_path(self) -> None:
        from vibeone import config

        self.assertNotEqual(
            Path(server.store.db_path).resolve(),
            (config.BASE_DIR / "vibeone.db").resolve(),
        )


if __name__ == "__main__":
    unittest.main()
