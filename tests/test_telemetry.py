import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("VIBEONE_WEB_TOKEN", "test-master-token")

from fastapi import HTTPException
from fastapi.responses import JSONResponse
from starlette.requests import Request

import server
from vibeone.routes.telemetry import TelemetryLogStore


def _make_request(
    method: str = "POST",
    path: str = "/api/telemetry",
    body: bytes = b"",
    headers: dict = None,
) -> Request:
    raw_headers = [
        (k.lower().encode("latin-1"), str(v).encode("latin-1"))
        for k, v in (headers or {}).items()
    ]
    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "query_string": b"",
        "headers": raw_headers,
        "client": ("127.0.0.1", 12345),
    }

    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.request", "body": b"", "more_body": False}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(scope, receive)


class TelemetryBackendTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="vibeone-telemetry-test-")
        self.log_path = Path(self._tmp.name) / "frontend-telemetry.jsonl"
        self.orig_path = server.telemetry_store.log_path
        server.telemetry_store.log_path = self.log_path

    def tearDown(self):
        server.telemetry_store.log_path = self.orig_path
        self._tmp.cleanup()

    def test_store_rotation_and_filtering(self):
        store = TelemetryLogStore(log_path=self.log_path, max_bytes=4096, max_rotated=2)
        batch1 = [
            {
                "server_ts": 1000 + i,
                "ts": 1000 + i,
                "cid": "c_alpha",
                "page_id": "p_1",
                "event": "jank_freeze" if i % 5 == 0 else "ui_click",
                "cat": "jank" if i % 5 == 0 else "behavior",
                "level": "error" if i % 5 == 0 else "info",
                "trace_id": f"t_trace_{i % 3}",
                "session_id": "sid-1",
                "data": {"padding": "x" * 120},
            }
            for i in range(40)
        ]
        store.append_records(batch1)
        store.append_records(
            [
                {
                    "server_ts": 2000,
                    "ts": 2000,
                    "cid": "c_beta",
                    "page_id": "p_2",
                    "event": "ws_reconnect",
                    "cat": "ws",
                    "level": "warn",
                    "trace_id": "t_special",
                    "session_id": "sid-2",
                }
            ]
        )
        self.assertTrue(Path(f"{self.log_path}.1").is_file())

        by_trace = store.query_recent(trace_id="t_special")
        self.assertEqual(len(by_trace), 1)
        self.assertEqual(by_trace[0]["cid"], "c_beta")

        by_cat = store.query_recent(cat="jank", limit=20)
        self.assertGreaterEqual(len(by_cat), 1)
        self.assertTrue(all(r["cat"] == "jank" for r in by_cat))

    async def test_telemetry_ingest_query_validation_and_trace_middleware(self):
        # Invalid JSON rejected with 400
        with self.assertRaises(HTTPException) as bad_err:
            await server.ingest_telemetry(_make_request(body=b"not-json"), _=None)
        self.assertEqual(bad_err.exception.status_code, 400)

        # Oversized payload rejected with 413
        with self.assertRaises(HTTPException) as huge_err:
            await server.ingest_telemetry(_make_request(body=b"x" * (65 * 1024)), _=None)
        self.assertEqual(huge_err.exception.status_code, 413)

        # Valid ingest
        payload = {
            "cid": "c_ios01",
            "page_id": "p_boot1",
            "meta": {"vw": 390, "vh": 844, "standalone": True},
            "events": [
                {
                    "ts": 1700000001000,
                    "event": "app_boot",
                    "cat": "lifecycle",
                    "level": "info",
                    "trace_id": "t_boot",
                    "session_id": "s-100",
                    "profile": "agy2",
                },
                {
                    "ts": 1700000002500,
                    "event": "jank_freeze",
                    "cat": "jank",
                    "level": "error",
                    "trace_id": "t_click_1",
                    "session_id": "s-100",
                    "profile": "agy2",
                    "dur_ms": 412.5,
                    "data": {"action": "#btn-send", "ws_1s": 28},
                },
            ],
        }
        res = await server.ingest_telemetry(
            _make_request(body=json.dumps(payload).encode("utf-8")),
            _=None,
        )
        self.assertEqual(res, {"ok": True, "accepted": 2})

        # Query by trace_id
        q = await server.get_recent_telemetry(
            limit=50,
            trace_id="t_click_1",
            cid=None,
            session_id=None,
            cat=None,
            event=None,
            level=None,
            since_ts=None,
            _=None,
        )
        self.assertTrue(q["ok"])
        self.assertEqual(q["count"], 1)
        self.assertEqual(q["items"][0]["event"], "jank_freeze")
        self.assertEqual(q["items"][0]["dur_ms"], 412.5)
        self.assertEqual(q["items"][0]["data"]["ws_1s"], 28)

        # Trace header echoed by middleware
        req = _make_request(
            method="GET",
            path="/api/sessions",
            headers={"X-VibeOne-Trace": "t_query_99", "X-VibeOne-Client": "c_ios01"},
        )

        async def _fake_next(_r):
            return JSONResponse({"ok": True})

        resp = await server.add_security_headers(req, _fake_next)
        self.assertEqual(resp.headers.get("X-VibeOne-Trace"), "t_query_99")


class TelemetryFrontendJsTest(unittest.TestCase):
    def test_frontend_telemetry_trace_fetch_and_jank_in_jsc(self):
        jsc = "/System/Library/Frameworks/JavaScriptCore.framework/Versions/Current/Helpers/jsc"
        if not os.path.isfile(jsc):
            jsc = shutil.which("jsc") or ""
        if not jsc:
            self.skipTest("JavaScriptCore (jsc) not available")

        telemetry_js = (server.config.BASE_DIR / "static" / "app-telemetry.js").read_text(encoding="utf-8")
        harness = f"""
const sentFetches = [];
const docListeners = {{}};
const winListeners = {{}};
const rafCallbacks = [];
const storage = {{}};

class Headers {{
  constructor(init) {{
    this._map = {{}};
    if (init && typeof init === 'object') {{
      Object.keys(init).forEach((k) => {{ this._map[k.toLowerCase()] = String(init[k]); }});
    }}
  }}
  has(k) {{ return Object.prototype.hasOwnProperty.call(this._map, String(k).toLowerCase()); }}
  set(k, v) {{ this._map[String(k).toLowerCase()] = String(v); }}
  get(k) {{ return this._map[String(k).toLowerCase()] || null; }}
}}

class URL {{
  constructor(input, base) {{
    const s = String(input || '');
    const full = s.startsWith('http') ? s : String(base || 'http://localhost') + s;
    const m = full.match(/^(https?:\\/\\/[^/?#]+)([^?#]*)(\\?[^#]*)?/);
    this.origin = m ? m[1] : 'http://localhost';
    this.pathname = m ? (m[2] || '/') : '/';
    const qs = m && m[3] ? m[3].slice(1) : '';
    const keys = qs ? qs.split('&').filter(Boolean).map((p) => p.split('=')[0]) : [];
    this.searchParams = {{ keys: () => keys }};
  }}
}}

const location = {{ origin: 'http://localhost' }};
const navigator = {{ onLine: true, language: 'zh-CN', platform: 'MacIntel' }};
const performance = {{ now: () => 100 }};
const state = {{ currentSessionId: 'sess-42', currentProfile: 'agy2', isBusy: true, items: [1, 2, 3], manualClose: false }};
const els = {{ authOverlay: {{ classList: {{ contains: () => true }} }} }};

function setTimeout() {{ return 1; }}
function clearTimeout() {{}}

const document = {{
  visibilityState: 'visible',
  addEventListener(ev, fn) {{ docListeners[ev] = fn; }},
}};

const window = {{
  innerWidth: 390,
  innerHeight: 844,
  devicePixelRatio: 3,
  localStorage: {{
    getItem(k) {{ return storage[k] || null; }},
    setItem(k, v) {{ storage[k] = String(v); }},
  }},
  addEventListener(ev, fn) {{ winListeners[ev] = fn; }},
  requestAnimationFrame(fn) {{ rafCallbacks.push(fn); return rafCallbacks.length; }},
  cancelAnimationFrame() {{}},
  fetch(url, init) {{
    sentFetches.push({{ url, init }});
    return Promise.resolve({{ ok: true, status: 200 }});
  }},
}};

{telemetry_js}

const tid = VibeTelemetry.startTrace('#btn-switch');
VibeTelemetry.noteWsMessage();
VibeTelemetry.noteWsMessage();

window.fetch('/api/sessions?profile=agy2', {{ method: 'GET' }});
const apiCall = sentFetches[0];

rafCallbacks.shift()(1000);
rafCallbacks.shift()(1016);
rafCallbacks.shift()(1032);
rafCallbacks.shift()(1382);

winListeners.error({{ message: 'BoomError', filename: 'http://localhost/static/app-chat.js', lineno: 42, colno: 7 }});

VibeTelemetry.flush(false);
const telemetryPost = sentFetches.find((f) => f.url === '/api/telemetry');
const postedBody = telemetryPost ? JSON.parse(telemetryPost.init.body) : null;

print(JSON.stringify({{
  cid: VibeTelemetry.getClientId(),
  tid,
  apiTraceHeader: apiCall.init.headers.get('X-VibeOne-Trace'),
  apiClientHeader: apiCall.init.headers.get('X-VibeOne-Client'),
  events: postedBody ? postedBody.events.map((e) => ({{ event: e.event, cat: e.cat, level: e.level, trace_id: e.trace_id, dur_ms: e.dur_ms, data: e.data }})) : [],
}}));
"""
        res = subprocess.run([jsc, "-e", harness], capture_output=True, text=True, check=True)
        out = json.loads(res.stdout.strip())
        self.assertTrue(out["cid"].startswith("c_"))
        self.assertEqual(out["apiTraceHeader"], out["tid"])
        self.assertEqual(out["apiClientHeader"], out["cid"])
        event_names = [e["event"] for e in out["events"]]
        self.assertIn("app_boot", event_names)
        self.assertIn("jank_freeze", event_names)
        self.assertIn("js_error", event_names)
        jank = next(e for e in out["events"] if e["event"] == "jank_freeze")
        self.assertEqual(jank["trace_id"], out["tid"])
        self.assertEqual(jank["data"]["ws_1s"], 2)
        self.assertGreaterEqual(jank["dur_ms"], 300)


if __name__ == "__main__":
    unittest.main()
