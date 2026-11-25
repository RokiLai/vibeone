import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

os.environ.setdefault("VIBEONE_WEB_TOKEN", "test-master-token")

from fastapi import HTTPException  # noqa: E402
from starlette.responses import Response  # noqa: E402

import server  # noqa: E402
from vibeone import SessionStore, config  # noqa: E402


class SecurityHardeningTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.test_store = SessionStore(Path(self._tmp.name) / "sec.db")
        self._orig_store = server.store
        server.store = self.test_store
        server._AUTH_FAILURES.clear()
        server._AUTH_LOCKOUTS.clear()

    def tearDown(self) -> None:
        server.store = self._orig_store
        self.test_store._conn.close()
        self._tmp.cleanup()
        server._AUTH_FAILURES.clear()
        server._AUTH_LOCKOUTS.clear()

    async def test_login_issues_opaque_session_cookie_and_logout_revokes_it(self) -> None:
        req = MagicMock()
        req.url.scheme = "http"
        req.headers = {}
        req.client.host = "127.0.0.1"
        req.cookies = {}
        resp = Response()

        res = await server.login(server.AuthRequest(token=config.VIBEONE_WEB_TOKEN), req, resp)
        self.assertEqual(res, {"ok": True})

        set_cookie = resp.headers.get("set-cookie", "")
        self.assertIn(f"{config.AUTH_COOKIE_NAME}=v1s_", set_cookie)
        self.assertNotIn(config.VIBEONE_WEB_TOKEN, set_cookie)

        cookie_val = set_cookie.split(f"{config.AUTH_COOKIE_NAME}=", 1)[1].split(";", 1)[0]
        self.assertTrue(server._is_authorized_token(cookie_val))

        logout_req = MagicMock()
        logout_req.cookies = {config.AUTH_COOKIE_NAME: cookie_val}
        logout_resp = Response()
        await server.logout(logout_req, logout_resp)
        self.assertFalse(server._is_authorized_token(cookie_val))

    async def test_login_rate_limits_repeated_failures(self) -> None:
        req = MagicMock()
        req.url.scheme = "http"
        req.headers = {"x-real-ip": "10.0.0.99"}
        req.client.host = "10.0.0.99"

        for _ in range(server._AUTH_MAX_FAILURES):
            with self.assertRaises(HTTPException) as ctx:
                await server.login(server.AuthRequest(token="wrong"), req, Response())
            self.assertEqual(ctx.exception.status_code, 401)

        with self.assertRaises(HTTPException) as ctx:
            await server.login(server.AuthRequest(token=config.VIBEONE_WEB_TOKEN), req, Response())
        self.assertEqual(ctx.exception.status_code, 429)

    async def test_client_ip_only_trusts_x_real_ip_from_loopback_proxy(self) -> None:
        direct_req = MagicMock()
        direct_req.headers = {"x-real-ip": "1.2.3.4"}
        direct_req.client.host = "203.0.113.5"
        self.assertEqual(server._get_client_ip(direct_req), "203.0.113.5")

        proxied_req = MagicMock()
        proxied_req.headers = {"x-real-ip": "198.51.100.7"}
        proxied_req.client.host = "127.0.0.1"
        self.assertEqual(server._get_client_ip(proxied_req), "198.51.100.7")

    async def test_vendor_and_static_asset_whitelists(self) -> None:
        resp_vendor = await server.serve_vendor_asset("purify.min.js")
        self.assertEqual(resp_vendor.media_type, "application/javascript")

        resp_js = await server.serve_static_asset("app.js")
        self.assertIn("javascript", resp_js.media_type)

        resp_css = await server.serve_static_asset("app.css")
        self.assertIn("css", resp_css.media_type)

        with self.assertRaises(HTTPException) as ctx:
            await server.serve_vendor_asset("../server.py")
        self.assertEqual(ctx.exception.status_code, 404)

        with self.assertRaises(HTTPException) as ctx:
            await server.serve_static_asset("../server.py")
        self.assertEqual(ctx.exception.status_code, 404)

    async def test_get_auth_token_returns_master_token_with_no_store(self) -> None:
        resp = await server.get_auth_token(_token="valid-session")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.headers.get("cache-control"), "no-store")
        self.assertIn(config.VIBEONE_WEB_TOKEN.encode("utf-8"), resp.body)

        with self.assertRaises(HTTPException) as ctx:
            await server.require_http_auth(authorization=None, x_vibeone_token=None, x_agy_token=None, vibeone_token=None, legacy_cookie_token=None)
        self.assertEqual(ctx.exception.status_code, 401)


if __name__ == "__main__":
    unittest.main()
