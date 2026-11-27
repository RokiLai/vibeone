import os
import re
import shutil
import subprocess
import unittest
from html.parser import HTMLParser

os.environ.setdefault("VIBEONE_WEB_TOKEN", "test-master-token")

from fastapi import HTTPException

import server


APP_SCRIPTS = [
    "/static/app-i18n-dict.js",
    "/static/app-i18n.js",
    "/static/app-bootstrap.js",
    "/static/app-sidebar-gestures.js",
    "/static/app-sidebar-search.js",
    "/static/app-sidebar-actions.js",
    "/static/app-sidebar.js",
    "/static/app-chat-markdown.js",
    "/static/app-chat-helpers.js",
    "/static/app-chat-render.js",
    "/static/app-chat.js",
    "/static/app-pickers.js",
    "/static/app-websocket.js",
    "/static/app-auth.js",
    "/static/app-composer-cards.js",
    "/static/app-composer-slash.js",
    "/static/app-composer.js",
    "/static/app-picker-events.js",
    "/static/app-profiles.js",
    "/static/app-profile-login.js",
    "/static/app-settings.js",
    "/static/app-image-viewer.js",
    "/static/app-git-diff-format.js",
    "/static/app-git-diff.js",
    "/static/app-file-tools.js",
    "/static/app.js",
]


class ScriptParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.scripts = []

    def handle_starttag(self, tag, attrs):
        if tag == "script":
            self.scripts.append(dict(attrs))


class FrontendAssetTest(unittest.IsolatedAsyncioTestCase):
    def test_page_loads_classic_scripts_in_dependency_order(self):
        page = ScriptParser()
        page.feed((server.config.BASE_DIR / "index.html").read_text(encoding="utf-8"))
        static_scripts = [
            attrs for attrs in page.scripts
            if (attrs.get("src") or "").startswith("/static/")
        ]
        self.assertEqual(static_scripts, [{"src": path} for path in APP_SCRIPTS])
        sources = [attrs.get("src") for attrs in page.scripts]
        self.assertLess(sources.index("/vendor/marked.min.js"), sources.index(APP_SCRIPTS[0]))

    async def test_static_route_serves_all_app_scripts(self):
        for url in APP_SCRIPTS:
            with self.subTest(url=url):
                response = await server.serve_static_asset(url.rsplit("/", 1)[1])
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.media_type, "application/javascript; charset=utf-8")
                self.assertEqual(response.headers["cache-control"], "no-cache")
                self.assertEqual(response.path, server.config.BASE_DIR / url.lstrip("/"))

        with self.assertRaises(HTTPException) as error:
            await server.serve_static_asset("unknown.js")
        self.assertEqual(error.exception.status_code, 404)

    def test_service_worker_precaches_all_app_scripts(self):
        worker = (server.config.BASE_DIR / "sw.js").read_text(encoding="utf-8")
        entries = worker.split("const PRECACHE_URLS = [", 1)[1].split("];", 1)[0]
        urls = re.findall(r"'([^']+)'", entries)
        for url in APP_SCRIPTS:
            with self.subTest(url=url):
                self.assertEqual(urls.count(url), 1)

    def test_sidebar_stale_is_current_does_not_flash_idle_session_as_running(self):
        jsc = "/System/Library/Frameworks/JavaScriptCore.framework/Versions/Current/Helpers/jsc"
        if not os.path.isfile(jsc):
            jsc = shutil.which("jsc") or ""
        if not jsc:
            self.skipTest("JavaScriptCore (jsc) not available")

        sidebar_js = (server.config.BASE_DIR / "static" / "app-sidebar.js").read_text(encoding="utf-8")
        harness = f"""
const els = {{
  settingsModal: null,
  sidebarSearch: {{ value: '' }},
  sidebarSessions: {{ innerHTML: '', appendChild() {{}} }},
}};
const state = {{
  currentSessionId: 'sess-running',
  currentWorkdir: '/tmp/proj',
  currentProfile: 'agy2',
  isBusy: true,
  pendingPermissions: new Map(),
  sessionsCache: [
    {{ session_id: 'sess-running', cwd: '/tmp/proj', title: 'Running', updated_at: 20, is_busy: true, is_current: false }},
    {{ session_id: 'sess-idle', cwd: '/tmp/proj', title: 'Idle', updated_at: 10, is_busy: false, is_current: true }},
  ],
  sidebarSearchQuery: '',
  sidebarSearchProfile: '',
  sidebarSearchHits: new Map(),
  sidebarSearchLoading: false,
  batchArchiveDir: null,
  batchSelectedIds: new Set(),
  collapsedDirs: new Set(),
}};
const document = {{
  createElement() {{
    return {{
      className: '',
      title: '',
      innerHTML: '',
      textContent: '',
      children: [],
      addEventListener() {{}},
      appendChild(c) {{ this.children.push(c); }},
    }};
  }},
}};
function updateArchivedBadge() {{}}
function normalizeDirKey(p) {{ return String(p || '').replace(/\\/+$/, '') || '/'; }}
function basenamePath(p) {{ return String(p || '').split('/').filter(Boolean).pop() || '/'; }}
function parentPathHint() {{ return ''; }}
function escapeHtml(s) {{ return String(s || ''); }}
function formatRelativeTime() {{ return ''; }}
function closeSidebar() {{}}
function showToast() {{}}
function sendWs() {{}}
function syncCurrentSession() {{}}
function exitBatchArchiveMode() {{}}
let captured = [];
function renderSidebarRunningSection(groups, dirNameCounts, getSessionActivity) {{
  groups.forEach((grp) => {{
    grp.sessions.forEach((s) => {{
      const act = getSessionActivity(s);
      captured.push({{ id: s.session_id, isCurrent: act.isCurrent, sessBusy: act.sessBusy }});
    }});
  }});
}}
{sidebar_js}
renderSidebarSessions();
print(JSON.stringify(captured));
"""
        res = subprocess.run([jsc, "-e", harness], capture_output=True, text=True, check=True)
        import json
        captured = {row["id"]: row for row in json.loads(res.stdout.strip())}
        self.assertEqual(
            captured["sess-running"],
            {"id": "sess-running", "isCurrent": True, "sessBusy": True},
        )
        self.assertEqual(
            captured["sess-idle"],
            {"id": "sess-idle", "isCurrent": False, "sessBusy": False},
        )

    def test_sidebar_defers_rerender_while_pointer_is_down(self):
        jsc = "/System/Library/Frameworks/JavaScriptCore.framework/Versions/Current/Helpers/jsc"
        if not os.path.isfile(jsc):
            jsc = shutil.which("jsc") or ""
        if not jsc:
            self.skipTest("JavaScriptCore (jsc) not available")

        sidebar_js = (server.config.BASE_DIR / "static" / "app-sidebar.js").read_text(encoding="utf-8")
        harness = f"""
const listeners = {{}};
const winListeners = {{}};
let renderCount = 0;
let queuedTimers = [];
function setTimeout(fn) {{ queuedTimers.push(fn); return queuedTimers.length; }}
function clearTimeout() {{}}
const window = {{
  addEventListener(ev, fn) {{ winListeners[ev] = fn; }},
}};
const els = {{
  settingsModal: null,
  sidebarSearch: {{ value: '' }},
  sidebarSessions: {{
    innerHTML: '',
    addEventListener(ev, fn) {{ listeners[ev] = fn; }},
    appendChild() {{}},
  }},
}};
const state = {{
  currentSessionId: 's1',
  currentWorkdir: '/tmp/p',
  currentProfile: 'agy2',
  isBusy: false,
  pendingPermissions: new Map(),
  sessionsCache: [{{ session_id: 's1', cwd: '/tmp/p', title: 'S1', updated_at: 10 }}],
  sidebarSearchQuery: '',
  sidebarSearchProfile: '',
  sidebarSearchHits: new Map(),
  sidebarSearchLoading: false,
  batchArchiveDir: null,
  batchSelectedIds: new Set(),
  collapsedDirs: new Set(),
}};
const document = {{
  createElement() {{
    return {{ className: '', title: '', innerHTML: '', textContent: '', addEventListener() {{}}, appendChild() {{}} }};
  }},
}};
function updateArchivedBadge() {{ renderCount += 1; }}
function normalizeDirKey(p) {{ return p || '/'; }}
function basenamePath(p) {{ return 'p'; }}
function parentPathHint() {{ return ''; }}
function escapeHtml(s) {{ return String(s || ''); }}
function formatRelativeTime() {{ return ''; }}
function closeSidebar() {{}}
function showToast() {{}}
function sendWs() {{ return true; }}
function syncCurrentSession() {{}}
function exitBatchArchiveMode() {{}}
function renderSidebarRunningSection() {{}}
{sidebar_js}
renderSidebarSessions();
const countBefore = renderCount;
listeners.pointerdown();
renderSidebarSessions();
const countDuringPress = renderCount;
winListeners.pointerup();
const releaseCb = queuedTimers.pop();
if (releaseCb) releaseCb();
const countAfterRelease = renderCount;
print(JSON.stringify({{ countBefore, countDuringPress, countAfterRelease }}));
"""
        res = subprocess.run([jsc, "-e", harness], capture_output=True, text=True, check=True)
        import json
        out = json.loads(res.stdout.strip())
        self.assertEqual(out["countBefore"], 1)
        self.assertEqual(out["countDuringPress"], 1)
        self.assertEqual(out["countAfterRelease"], 2)
