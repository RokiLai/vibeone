import asyncio
import os
import unittest

os.environ.setdefault("VIBEONE_WEB_TOKEN", "test-token")

from vibeone.engines.acp import AgyACPManager
from vibeone.engines.acp_parts.history import ACPHistoryMixin


class ACPHistoryContentExtractTests(unittest.TestCase):
    def test_extract_content_text_handles_list_and_nested_content(self) -> None:
        payload = [
            {"type": "content", "content": {"type": "text", "text": "line 1"}},
            {"type": "text", "text": "line 2"},
            "line 3",
            {"text": "line 4"},
            None,
        ]
        expected = "line 1\nline 2\nline 3\nline 4"
        self.assertEqual(ACPHistoryMixin._extract_content_text(payload), expected)
        self.assertEqual(AgyACPManager._extract_content_text(payload), expected)

    def test_extract_content_text_nested_dict_only(self) -> None:
        payload = {"type": "content", "content": {"type": "text", "text": "nested"}}
        self.assertEqual(AgyACPManager._extract_content_text(payload), "nested")

    def test_handle_session_update_tool_call_with_list_content(self) -> None:
        mgr = AgyACPManager.__new__(AgyACPManager)
        mgr._btw_sessions = {}
        mgr._replaying_session_id = None
        mgr._runtimes = {}
        mgr.session_id = "sid-test"
        mgr._get_hidden_sessions = lambda: set()
        mgr._get_archived_sessions = lambda: set()

        class DummyRuntime:
            def __init__(self) -> None:
                self.session_id = "sid-test"
                self.is_busy = True
                self.active_assistant_id = None
                self.active_thought_id = None
                self.items_by_id = {}

        rt = DummyRuntime()
        mgr._get_or_create_runtime = lambda _sid: rt
        saved = []
        broadcasted = []
        mgr._add_or_update_item = lambda item, sid=None: saved.append((sid, item))

        async def _broadcast(msg, sid=None):
            broadcasted.append((sid, msg))

        mgr.broadcast = _broadcast

        params = {
            "sessionId": "sid-test",
            "update": {
                "sessionUpdate": "tool_call_update",
                "toolCallId": "tc-1",
                "name": "run_command",
                "status": "completed",
                "content": [
                    {"type": "content", "content": {"type": "text", "text": "ok-1"}},
                    {"type": "text", "text": "ok-2"},
                ],
            },
        }
        asyncio.run(mgr._handle_session_update(params))

        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0][1]["output"], "ok-1\nok-2")
        self.assertEqual(len(broadcasted), 1)
        self.assertEqual(broadcasted[0][1]["type"], "item_upsert")


if __name__ == "__main__":
    unittest.main()
