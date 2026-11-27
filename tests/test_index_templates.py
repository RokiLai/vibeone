"""Verify index.html template assembly and static JS asset registration."""

import json
import os
import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

os.environ.setdefault("VIBEONE_WEB_TOKEN", "test-secret-token")
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from vibeone.routes.static_assets import _APP_SCRIPT_FILES, _STATIC_MEDIA_TYPES, render_index_html  # noqa: E402


class IndexTemplateAssemblyTest(unittest.TestCase):
    def test_render_index_includes_modals_and_valid_static_scripts(self):
        html = render_index_html()
        self.assertNotIn("<!-- INCLUDE:", html)
        self.assertNotIn("__APP_BUNDLE_V__", html)
        self.assertNotIn("__APP_CSS_V__", html)
        self.assertNotIn("__TAILWIND_CSS_V__", html)
        for marker in (
            'id="dir-modal"',
            'id="model-modal"',
            'id="diff-modal"',
            'id="file-preview-modal"',
            'id="settings-modal"',
            'id="image-modal"',
            'id="profile-login-modal"',
            'content="black"',
        ):
            self.assertIn(marker, html)

        self.assertRegex(html, r'<script\s+defer\s+src="/static/app\.bundle\.js\?v=[0-9a-f]{12}"')
        self.assertTrue((ROOT / "vendor" / "tailwind.min.css").is_file())
        for name in _APP_SCRIPT_FILES:
            self.assertIn(name, _STATIC_MEDIA_TYPES, f"Unregistered static script: {name}")
            self.assertTrue((ROOT / "static" / name).is_file(), f"Missing static script: {name}")

    def test_streaming_markdown_skips_highlight_until_flush(self):
        jsc = "/System/Library/Frameworks/JavaScriptCore.framework/Versions/Current/Helpers/jsc"
        if not os.path.isfile(jsc):
            jsc = shutil.which("jsc") or ""
        if not jsc:
            self.skipTest("JavaScriptCore (jsc) not available")

        render_js = (ROOT / "static" / "app-chat-render.js").read_text(encoding="utf-8")
        harness = f"""
const renderCalls = [];
let highlightCalls = 0;
let rafQueue = [];
let timeouts = [];
const state = {{ itemsById: new Map(), renderTimers: new Map() }};
const els = {{ chatList: {{}}, emptyState: {{ classList: {{ add() {{}} }} }} }};
function requestAnimationFrame(fn) {{ rafQueue.push(fn); return rafQueue.length; }}
function cancelAnimationFrame() {{}}
function setTimeout(fn, ms) {{ timeouts.push({{ fn, ms }}); return timeouts.length; }}
function clearTimeout() {{}}
function scrollToBottom() {{}}
function renderMarkdownInto(body, text, opts) {{ renderCalls.push({{ text, opts }}); }}
function highlightUnmarkedCodeIn() {{ highlightCalls += 1; }}
{render_js}
const body = {{ isConnected: true }};
const item = {{ id: 'a1', type: 'assistant', text: 'hello' }};
state.itemsById.set('a1', item);
scheduleStreamingMarkdownRender(item, body);
item.text = 'hello world streaming';
scheduleStreamingMarkdownRender(item, body);
// Drain smooth rAF frames until caught up
let guard = 0;
while (rafQueue.length && guard < 40) {{
  guard += 1;
  const fn = rafQueue.shift();
  fn();
}}
const callsDuringStream = renderCalls.slice();
flushStreamingMarkdownRenders({{ highlight: true }});
print(JSON.stringify({{ callsDuringStream, totalRenderCalls: renderCalls.length, highlightCalls }}));
"""
        res = subprocess.run([jsc, "-e", harness], capture_output=True, text=True, check=True)
        out = json.loads(res.stdout.strip())
        self.assertGreater(len(out["callsDuringStream"]), 1)
        self.assertEqual(out["callsDuringStream"][-1]["text"], "hello world streaming")
        self.assertTrue(all(c["opts"]["skipHighlight"] for c in out["callsDuringStream"]))
        self.assertEqual(out["highlightCalls"], 1)

    def test_progressive_chat_hydration_renders_tail_first_then_chunks(self):
        jsc = "/System/Library/Frameworks/JavaScriptCore.framework/Versions/Current/Helpers/jsc"
        if not os.path.isfile(jsc):
            jsc = shutil.which("jsc") or ""
        if not jsc:
            self.skipTest("JavaScriptCore (jsc) not available")

        chat_js = (ROOT / "static" / "app-chat.js").read_text(encoding="utf-8")
        harness = f"""
const rafQueue = [];
const renderedBatches = [];
let currentBatch = [];
const state = {{
  items: [],
  itemsById: new Map(),
  hasMoreItems: false,
  isBusy: false,
  suppressAutoLoadUntil: 0,
  lastScrollTop: 0,
  autoScroll: true,
}};
const els = {{
  chatList: {{
    firstChild: null,
    querySelectorAll() {{ return []; }},
    appendChild() {{ renderedBatches.push(currentBatch.slice()); currentBatch = []; }},
    insertBefore() {{ renderedBatches.push(currentBatch.slice()); currentBatch = []; }},
  }},
  chatScroll: {{ scrollTop: 0 }},
  emptyState: {{ classList: {{ add() {{}}, remove() {{}} }} }},
  btnScrollBottom: {{ classList: {{ add() {{}}, toggle() {{}} }} }},
  btnQueueRun: {{ addEventListener() {{}} }},
  btnQueueClear: {{ addEventListener() {{}} }},
}};
const document = {{
  getElementById() {{ return null; }},
  createDocumentFragment() {{ return {{ querySelectorAll() {{ return []; }}, lastElementChild: null, appendChild() {{}} }}; }},
}};
function requestAnimationFrame(fn) {{ rafQueue.push(fn); return rafQueue.length; }}
function cancelAnimationFrame() {{}}
function createOrUpdateItemDom(it) {{ currentBatch.push(it.id); }}
function updateProcessGroupSummary() {{}}
{chat_js}
const items = Array.from({{ length: 40 }}, (_, i) => ({{ id: 'm_' + i, type: i % 2 === 0 ? 'user' : 'assistant', text: 'x' }}));
renderAllItems(items);
const initialCount = renderedBatches[0].length;
while (rafQueue.length) {{
  const fn = rafQueue.shift();
  fn();
}}
print(JSON.stringify({{ initialCount, totalBatches: renderedBatches.length, totalRendered: renderedBatches.reduce((a, b) => a + b.length, 0), itemsByIdSize: state.itemsById.size }}));
"""
        res = subprocess.run([jsc, "-e", harness], capture_output=True, text=True, check=True)
        out = json.loads(res.stdout.strip())
        self.assertEqual(out["initialCount"], 20)
        self.assertGreaterEqual(out["totalBatches"], 2)
        self.assertEqual(out["totalRendered"], 40)
        self.assertEqual(out["itemsByIdSize"], 40)


if __name__ == "__main__":
    unittest.main()
