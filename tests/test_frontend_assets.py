import os
import re
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
