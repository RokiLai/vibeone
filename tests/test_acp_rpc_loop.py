import asyncio
import json
import os
import unittest

os.environ.setdefault("VIBEONE_WEB_TOKEN", "test-token")

from vibeone.engines.acp import AgyACPManager


class _FakeStdout:
    def __init__(self, lines):
        self._items = list(lines)

    async def readline(self):
        if not self._items:
            return b""
        nxt = self._items.pop(0)
        if isinstance(nxt, BaseException):
            raise nxt
        return nxt


class _FakeProc:
    def __init__(self, stdout_lines, stderr_lines=None, returncode=None):
        self.stdout = _FakeStdout(stdout_lines)
        self.stderr = _FakeStdout(stderr_lines or [])
        self.returncode = returncode
        self.terminated = False

    def terminate(self):
        self.terminated = True


def _make_stub_manager():
    mgr = AgyACPManager.__new__(AgyACPManager)
    mgr.connected = True
    mgr.last_error = None
    mgr._runtimes = {}
    mgr._perm_to_sid = {}
    mgr._pending_rpc = {}
    mgr._recent_stderr = []
    mgr._flush_pending_items = lambda sid=None: None

    async def _noop(*args, **kwargs):
        return None

    mgr.broadcast_status = _noop
    mgr.broadcast = _noop
    return mgr


class ACPRpcLoopResilienceTests(unittest.TestCase):
    def test_single_message_handler_error_does_not_abort_stdout_loop(self) -> None:
        async def _run():
            mgr = _make_stub_manager()
            loop = asyncio.get_running_loop()
            fut = loop.create_future()
            mgr._pending_rpc[42] = fut

            bad_notif = json.dumps({"jsonrpc": "2.0", "method": "session/update", "params": {"bad": True}}).encode() + b"\n"
            good_resp = json.dumps({"jsonrpc": "2.0", "id": 42, "result": {"stopReason": "end_turn"}}).encode() + b"\n"
            proc = _FakeProc([bad_notif, good_resp], returncode=0)
            mgr.proc = proc

            async def _fake_update(_params):
                raise RuntimeError("simulated notification bug")

            mgr._handle_session_update = _fake_update

            with self.assertLogs("vibeone", level="ERROR") as logs:
                await mgr._read_stdout_loop(proc)

            self.assertTrue(fut.done())
            self.assertEqual(fut.result(), {"stopReason": "end_turn"})
            self.assertTrue(any("Error handling RPC message" in line for line in logs.output))

        asyncio.run(_run())

    def test_true_subprocess_eof_includes_returncode_and_stderr_tail(self) -> None:
        async def _run():
            mgr = _make_stub_manager()
            loop = asyncio.get_running_loop()
            fut = loop.create_future()
            mgr._pending_rpc[7] = fut

            proc = _FakeProc(
                [],
                stderr_lines=[b"info line\n", b"ERROR: upstream crashed\n"],
                returncode=137,
            )
            mgr.proc = proc

            await mgr._read_stderr_loop(proc)
            await mgr._read_stdout_loop(proc)

            self.assertFalse(mgr.connected)
            self.assertTrue(fut.done())
            with self.assertRaises(RuntimeError) as ctx:
                fut.result()
            msg = str(ctx.exception)
            self.assertIn("code=137", msg)
            self.assertIn("ERROR: upstream crashed", msg)

        asyncio.run(_run())

    def test_stdout_transport_error_terminates_live_proc_and_reports_reason(self) -> None:
        async def _run():
            mgr = _make_stub_manager()
            loop = asyncio.get_running_loop()
            fut = loop.create_future()
            mgr._pending_rpc[9] = fut

            proc = _FakeProc([ValueError("Separator is found, but chunk is longer than limit")], returncode=None)
            mgr.proc = proc

            with self.assertLogs("vibeone", level="ERROR"):
                await mgr._read_stdout_loop(proc)

            self.assertTrue(proc.terminated)
            self.assertFalse(mgr.connected)
            with self.assertRaises(RuntimeError) as ctx:
                fut.result()
            msg = str(ctx.exception)
            self.assertIn("ACP stdout loop failed", msg)
            self.assertIn("ValueError", msg)

        asyncio.run(_run())


if __name__ == "__main__":
    unittest.main()
