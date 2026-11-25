import asyncio
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from vibeone.engines.acp import AgyACPManager
from vibeone.engines.codex import CodexAppServerManager
from vibeone.store import SessionStore


class DummyWS:
    def __init__(self):
        self.sent = []

    async def send_text(self, text: str):
        self.sent.append(text)


class TestBtwSideQuestion(unittest.IsolatedAsyncioTestCase):
    async def test_acp_btw_isolated_and_cleaned_up(self):
        with TemporaryDirectory() as tmp:
            store = SessionStore(Path(tmp) / "test.db")
            mgr = AgyACPManager(tmp, store)
            mgr.connected = True
            mgr.session_id = "main_sess"
            rt = mgr._get_or_create_runtime("main_sess", workdir=tmp, title="主会话")
            rt.items = [
                {"id": "u1", "type": "user", "text": "请重构 auth.py"},
                {"id": "a1", "type": "assistant", "text": "好的，正在处理 auth.py"},
            ]
            rt.items_by_id = {it["id"]: it for it in rt.items}

            origin_ws = DummyWS()
            other_ws = DummyWS()
            mgr.clients.update({origin_ws, other_ws})

            rpc_calls = []
            written_json = []

            async def fake_start():
                mgr.connected = True

            async def fake_write_json(payload):
                written_json.append(payload)

            async def fake_send_request(method, params, timeout=30.0):
                rpc_calls.append((method, params))
                if method == "session/new":
                    return {"sessionId": "temp_btw_1"}
                if method == "session/prompt":
                    # Simulate a permission request on the ephemeral session (must be auto-cancelled)
                    await mgr._handle_server_request(
                        99,
                        "session/request_permission",
                        {"sessionId": "temp_btw_1", "toolCall": {"name": "bash"}},
                    )
                    # Simulate streaming answer chunks
                    await mgr._handle_session_update(
                        {
                            "sessionId": "temp_btw_1",
                            "update": {
                                "sessionUpdate": "agent_message_chunk",
                                "content": {"type": "text", "text": "auth.py 在 "},
                            },
                        }
                    )
                    await mgr._handle_session_update(
                        {
                            "sessionId": "temp_btw_1",
                            "update": {
                                "sessionUpdate": "agent_message_chunk",
                                "content": {"type": "text", "text": "server.py 中调用。"},
                            },
                        }
                    )
                    return {"stopReason": "end_turn"}
                if method == "session/close":
                    return {}
                return {}

            mgr.start = fake_start
            mgr._write_json = fake_write_json
            mgr._send_request = fake_send_request

            res = await mgr.send_btw_prompt("auth.py 在哪调用？", btw_id="btw_test_1", sid="main_sess", origin_ws=origin_ws)
            self.assertEqual(res["btw_id"], "btw_test_1")

            # Wait for background task to complete
            for _ in range(50):
                if "temp_btw_1" not in mgr._btw_sessions and any("btw_done" in s for s in origin_ws.sent):
                    break
                await asyncio.sleep(0.01)

            # Main runtime and SQLite must be untouched
            self.assertEqual(len(rt.items), 2)
            self.assertNotIn("temp_btw_1", mgr._runtimes)
            self.assertNotIn("temp_btw_1", mgr._btw_sessions)
            self.assertEqual(len(other_ws.sent), 0)

            # Origin WS received deltas + done
            self.assertTrue(any('"btw_delta"' in s and "auth.py 在 " in s for s in origin_ws.sent))
            self.assertTrue(any('"btw_done"' in s and "server.py 中调用。" in s for s in origin_ws.sent))

            # Permission request on btw session was auto-cancelled
            self.assertTrue(any(w.get("id") == 99 and w.get("result", {}).get("outcome", {}).get("outcome") == "cancelled" for w in written_json))

            # Ephemeral session was closed and hidden
            methods = [m for m, _ in rpc_calls]
            self.assertIn("session/new", methods)
            self.assertIn("session/prompt", methods)
            self.assertIn("session/close", methods)
            self.assertIn("temp_btw_1", mgr._get_hidden_sessions())

    async def test_codex_btw_forks_readonly_and_archives(self):
        with TemporaryDirectory() as tmp:
            store = SessionStore(Path(tmp) / "test.db")
            mgr = CodexAppServerManager(tmp, store)
            mgr.connected = True
            mgr.session_id = "codex_main"
            rt = mgr._get_or_create_runtime("codex_main", workdir=tmp, title="Codex 主会话")
            rt.current_model = "gpt-5-codex::medium"

            origin_ws = DummyWS()
            mgr.clients.add(origin_ws)

            rpc_calls = []
            written_json = []

            async def fake_start():
                mgr.connected = True

            async def fake_write_json(payload):
                written_json.append(payload)

            async def fake_send_request(method, params, timeout=30.0):
                rpc_calls.append((method, params))
                if method == "thread/fork":
                    self.assertEqual(params["sandbox"], "read-only")
                    self.assertEqual(params["approvalPolicy"], "never")
                    return {"thread": {"id": "codex_btw_1"}}
                if method == "turn/start":
                    self.assertEqual(params["sandboxPolicy"], {"type": "readOnly"})

                    async def emit_stream():
                        await asyncio.sleep(0.01)
                        await mgr._handle_server_request(
                            77,
                            "item/commandExecution/requestApproval",
                            {"threadId": "codex_btw_1", "command": "rm -rf /"},
                        )
                        await mgr._handle_codex_notification(
                            "item/agentMessage/delta",
                            {"threadId": "codex_btw_1", "itemId": "m1", "delta": "只读回答内容"},
                        )
                        await mgr._handle_codex_notification(
                            "turn/completed",
                            {"threadId": "codex_btw_1", "turn": {"status": "completed"}},
                        )

                    asyncio.create_task(emit_stream())
                    return {"turn": {"id": "turn_btw_1"}}
                return {}

            mgr.start = fake_start
            mgr._write_json = fake_write_json
            mgr._send_request = fake_send_request

            await mgr.send_btw_prompt("这是什么错误？", btw_id="btw_codex_1", sid="codex_main", origin_ws=origin_ws)

            for _ in range(50):
                if "codex_btw_1" not in mgr._btw_sessions and any("btw_done" in s for s in origin_ws.sent):
                    break
                await asyncio.sleep(0.01)

            self.assertNotIn("codex_btw_1", mgr._runtimes)
            self.assertNotIn("codex_btw_1", mgr._btw_sessions)
            self.assertTrue(any('"btw_delta"' in s and "只读回答内容" in s for s in origin_ws.sent))
            self.assertTrue(any('"btw_done"' in s and "只读回答内容" in s for s in origin_ws.sent))
            self.assertTrue(any(w.get("id") == 77 and w.get("result", {}).get("decision") == "decline" for w in written_json))

            methods = [m for m, _ in rpc_calls]
            self.assertIn("thread/fork", methods)
            self.assertIn("turn/start", methods)
            self.assertIn("thread/archive", methods)
            self.assertIn("thread/unsubscribe", methods)


if __name__ == "__main__":
    unittest.main()
