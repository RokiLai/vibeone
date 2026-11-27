import os
import re
import shutil
import subprocess
import unittest
from html.parser import HTMLParser

os.environ.setdefault("VIBEONE_WEB_TOKEN", "test-master-token")
from fastapi import HTTPException
import server
from vibeone.routes.static_assets import get_app_js_bundle, render_index_html


APP_SCRIPTS = [
    "/static/app-i18n-dict.js",
    "/static/app-i18n.js",
    "/static/app-bootstrap.js",
    "/static/app-telemetry.js",
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
    def test_page_loads_bundled_deferred_scripts_and_static_tailwind(self):
        html = render_index_html()
        self.assertNotIn("tailwindcss.min.js", html)
        self.assertIn('/vendor/tailwind.min.css?v=', html)
        page = ScriptParser()
        page.feed(html)
        static_scripts = [
            attrs for attrs in page.scripts
            if (attrs.get("src") or "").startswith("/static/")
        ]
        self.assertEqual(len(static_scripts), 1)
        self.assertIn("defer", static_scripts[0])
        self.assertRegex(static_scripts[0]["src"], r"^/static/app\.bundle\.js\?v=[0-9a-f]{12}$")
        sources = [attrs.get("src") for attrs in page.scripts]
        self.assertLess(sources.index("/vendor/marked.min.js"), sources.index(static_scripts[0]["src"]))
        for vendor_src in ("/vendor/purify.min.js", "/vendor/marked.min.js", "/vendor/highlight.min.js"):
            tag = next(attrs for attrs in page.scripts if attrs.get("src") == vendor_src)
            self.assertIn("defer", tag)

    async def test_static_route_serves_all_app_scripts_and_bundle(self):
        for url in APP_SCRIPTS:
            with self.subTest(url=url):
                response = await server.serve_static_asset(url.rsplit("/", 1)[1])
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.media_type, "application/javascript; charset=utf-8")
                self.assertEqual(response.headers["cache-control"], "no-cache")
                self.assertEqual(response.path, server.config.BASE_DIR / url.lstrip("/"))

        bundle_bytes, bundle_hash = get_app_js_bundle()
        bundle_text = bundle_bytes.decode("utf-8")
        last_pos = -1
        for url in APP_SCRIPTS:
            marker = f"/* === {url.rsplit('/', 1)[1]} === */"
            pos = bundle_text.find(marker)
            self.assertGreater(pos, last_pos, f"Bundle missing or out-of-order script: {marker}")
            last_pos = pos

        res_imm = await server.serve_app_bundle(v=bundle_hash)
        self.assertEqual(res_imm.status_code, 200)
        self.assertEqual(res_imm.headers["cache-control"], "public, max-age=31536000, immutable")
        res_nocache = await server.serve_app_bundle()
        self.assertEqual(res_nocache.headers["cache-control"], "no-cache")

        with self.assertRaises(HTTPException) as error:
            await server.serve_static_asset("unknown.js")
        self.assertEqual(error.exception.status_code, 404)

    def test_service_worker_precaches_bundle_and_static_tailwind(self):
        worker = (server.config.BASE_DIR / "sw.js").read_text(encoding="utf-8")
        entries = worker.split("const PRECACHE_URLS = [", 1)[1].split("];", 1)[0]
        urls = re.findall(r"'([^']+)'", entries)
        self.assertIn("/vendor/tailwind.min.css", urls)
        self.assertNotIn("/vendor/tailwindcss.min.js", urls)
        self.assertIn("/static/app.bundle.js", urls)
        self.assertIn("/static/app.css", urls)

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

    def test_optimistic_switch_syncs_busy_state_without_flashing_running_section(self):
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
  busyBadge: {{ classList: {{ toggle() {{}} }} }},
}};
const state = {{
  currentSessionId: 'sess-a',
  currentWorkdir: '/tmp/p',
  currentProfile: 'agy2',
  isBusy: true,
  pendingPermissions: new Map([['p1', {{ key: 'p1' }}]]),
  sessionsCache: [
    {{ session_id: 'sess-a', cwd: '/tmp/p', title: 'A', updated_at: 20, is_busy: false, is_current: true }},
    {{ session_id: 'sess-b', cwd: '/tmp/p', title: 'B', updated_at: 10, is_busy: false, is_current: false }},
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
    return {{ className: '', title: '', innerHTML: '', textContent: '', addEventListener() {{}}, appendChild() {{}} }};
  }},
}};
function updateArchivedBadge() {{}}
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
let switchFn = null;
let lastActivities = [];
function renderSidebarRunningSection(groups, dirNameCounts, getSessionActivity, switchToSidebarSession) {{
  switchFn = switchToSidebarSession;
  lastActivities = [];
  groups.forEach((grp) => {{
    grp.sessions.forEach((s) => {{
      const act = getSessionActivity(s);
      lastActivities.push({{ id: s.session_id, isCurrent: act.isCurrent, sessBusy: act.sessBusy, sessPerm: act.sessPerm }});
    }});
  }});
}}
{sidebar_js}
renderSidebarSessions();
// Switch from running A (even with stale cache is_busy=false) to idle B
switchFn(state.sessionsCache[1], false);
const afterSwitchToB = JSON.parse(JSON.stringify(lastActivities));
// Switch back from idle B to running A
switchFn(state.sessionsCache[0], false);
const afterSwitchToA = JSON.parse(JSON.stringify(lastActivities));
print(JSON.stringify({{ afterSwitchToB, afterSwitchToA }}));
"""
        res = subprocess.run([jsc, "-e", harness], capture_output=True, text=True, check=True)
        import json
        out = json.loads(res.stdout.strip())
        to_b = {r["id"]: r for r in out["afterSwitchToB"]}
        self.assertEqual(to_b["sess-a"], {"id": "sess-a", "isCurrent": False, "sessBusy": True, "sessPerm": 0})
        self.assertEqual(to_b["sess-b"], {"id": "sess-b", "isCurrent": True, "sessBusy": False, "sessPerm": 0})
        to_a = {r["id"]: r for r in out["afterSwitchToA"]}
        self.assertEqual(to_a["sess-a"], {"id": "sess-a", "isCurrent": True, "sessBusy": True, "sessPerm": 0})
        self.assertEqual(to_a["sess-b"], {"id": "sess-b", "isCurrent": False, "sessBusy": False, "sessPerm": 0})

    def test_sidebar_collapses_non_current_dirs_by_default(self):
        jsc = "/System/Library/Frameworks/JavaScriptCore.framework/Versions/Current/Helpers/jsc"
        if not os.path.isfile(jsc):
            jsc = shutil.which("jsc") or ""
        if not jsc:
            self.skipTest("JavaScriptCore (jsc) not available")

        sidebar_js = (server.config.BASE_DIR / "static" / "app-sidebar.js").read_text(encoding="utf-8")
        harness = f"""
const sections = [];
const els = {{
  settingsModal: null,
  sidebarSearch: {{ value: '' }},
  sidebarSessions: {{
    innerHTML: '',
    appendChild(el) {{ sections.push(el); }},
  }},
}};
const state = {{
  currentSessionId: 's1',
  currentWorkdir: '/tmp/dir_a',
  currentProfile: 'agy2',
  isBusy: false,
  pendingPermissions: new Map(),
  sessionsCache: [
    {{ session_id: 's1', cwd: '/tmp/dir_a', title: 'S1', updated_at: 20 }},
    {{ session_id: 's2', cwd: '/tmp/dir_b', title: 'S2', updated_at: 10 }},
  ],
  sidebarSearchQuery: '',
  sidebarSearchProfile: '',
  sidebarSearchHits: new Map(),
  sidebarSearchLoading: false,
  batchArchiveDir: null,
  batchSelectedIds: new Set(),
  collapsedDirs: new Set(),
  expandedDirs: new Set(),
}};
const document = {{
  createElement(tag) {{
    return {{
      tag,
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
function normalizeDirKey(p) {{ return p || '/'; }}
function basenamePath(p) {{ return String(p || '').split('/').pop() || '/'; }}
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
// Each section has headerRow (children[0]); expanded section also has listWrap (children[1])
const childCounts = sections.map((sec) => sec.children.length);
print(JSON.stringify(childCounts));
"""
        res = subprocess.run([jsc, "-e", harness], capture_output=True, text=True, check=True)
        import json
        # dir_a (current) is expanded (2 children: header + listWrap); dir_b is collapsed (1 child: header only)
        self.assertEqual(json.loads(res.stdout.strip()), [2, 1])

    def test_warm_profile_switch_uses_hot_ws_path_and_caches(self):
        jsc = "/System/Library/Frameworks/JavaScriptCore.framework/Versions/Current/Helpers/jsc"
        if not os.path.isfile(jsc):
            jsc = shutil.which("jsc") or ""
        if not jsc:
            self.skipTest("JavaScriptCore (jsc) not available")

        profiles_js = (server.config.BASE_DIR / "static" / "app-profiles.js").read_text(encoding="utf-8")
        harness = f"""
const WebSocket = {{ OPEN: 1 }};
const wsSent = [];
let fetchCalls = 0;
let cachedCount = 0;
let restoredTarget = null;
let immediateSidebarLoads = 0;
const els = {{ busyBadge: {{ classList: {{ toggle() {{}} }} }} }};
const state = {{
  ws: {{ readyState: 1 }},
  currentProfile: 'agy2',
  currentProfileEmail: 'a@example.com',
  currentSessionId: 's-agy',
  isBusy: false,
  switchingProfile: '',
  reloadingProfile: '',
  profiles: [
    {{ name: 'agy2', backend: 'agy', email: 'a@example.com', logged_in: true, warm: true, is_current: true }},
    {{ name: 'codex', backend: 'codex', email: 'c@example.com', logged_in: true, warm: true, is_current: false }},
  ],
  sessionsCache: [{{ session_id: 's-agy', is_busy: false }}],
  archivedSessionsCache: [],
  sessionsByProfile: new Map([['codex', {{ sessions: [{{ session_id: 's-cdx', is_busy: true }}], archived: [] }}]]),
  lastSessionByProfile: new Map([['codex', 's-cdx']]),
  pendingPermissions: new Map(),
}};
function $(id) {{ return null; }}
function escapeHtml(s) {{ return String(s || ''); }}
function fetch() {{ fetchCalls += 1; return Promise.resolve({{ ok: true, json: () => Promise.resolve({{ profiles: state.profiles }}) }}); }}
function cacheCurrentSessionView() {{ cachedCount += 1; }}
function restoreCachedSessionView(p, sid) {{ restoredTarget = `${{p}}:${{sid}}`; state.currentSessionId = sid; return true; }}
function showSessionSwitchPlaceholder() {{}}
function updateComposerActionButton() {{}}
function renderPermissions() {{}}
function resetFullTextSearchState() {{}}
function persistCurrentView() {{}}
function renderSidebarSessions() {{}}
function sendWs(m) {{ wsSent.push(m); return true; }}
function loadSidebarSessions(imm) {{ if (imm) immediateSidebarLoads += 1; }}
function showToast() {{}}
{profiles_js}
switchProfile('codex');
print(JSON.stringify({{
  profile: state.currentProfile,
  sid: state.currentSessionId,
  isBusy: state.isBusy,
  cachedCount,
  restoredTarget,
  immediateSidebarLoads,
  wsSent,
  savedAgySid: state.lastSessionByProfile.get('agy2'),
  httpSwitchCalled: fetchCalls > 1,
}}));
"""
        res = subprocess.run([jsc, "-e", harness], capture_output=True, text=True, check=True)
        import json
        out = json.loads(res.stdout.strip())
        self.assertEqual(out["profile"], "codex")
        self.assertEqual(out["sid"], "s-cdx")
        self.assertTrue(out["isBusy"])
        self.assertEqual(out["cachedCount"], 1)
        self.assertEqual(out["restoredTarget"], "codex:s-cdx")
        self.assertEqual(out["immediateSidebarLoads"], 1)
        self.assertEqual(out["savedAgySid"], "s-agy")
        self.assertEqual(out["wsSent"], [{"type": "switch_profile", "profile": "codex"}])
        self.assertFalse(out["httpSwitchCalled"])
