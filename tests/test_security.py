import io
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock

os.environ.setdefault("VIBEONE_WEB_TOKEN", "test-master-token")

from fastapi import HTTPException, UploadFile  # noqa: E402
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

    async def test_verify_ws_origin(self) -> None:
        def _make_ws(headers, peer="127.0.0.1"):
            ws = MagicMock()
            ws.headers = headers
            ws.client = MagicMock()
            ws.client.host = peer
            return ws

        # Same-origin (exact & default port normalization)
        self.assertTrue(
            server.verify_ws_origin(_make_ws({"host": "127.0.0.1:8765", "origin": "http://127.0.0.1:8765"}))
        )
        self.assertTrue(
            server.verify_ws_origin(_make_ws({"host": "vibe.example.com:443", "origin": "https://vibe.example.com"}))
        )
        # Missing origin (non-browser client) allowed
        self.assertTrue(server.verify_ws_origin(_make_ws({"host": "127.0.0.1:8765"})))
        # Trusted loopback proxy with X-Forwarded-Host
        self.assertTrue(
            server.verify_ws_origin(
                _make_ws(
                    {
                        "host": "127.0.0.1:8765",
                        "x-forwarded-host": "vibe.ts.net",
                        "origin": "https://vibe.ts.net",
                    },
                    peer="127.0.0.1",
                )
            )
        )
        # Cross-origin rejected
        self.assertFalse(
            server.verify_ws_origin(_make_ws({"host": "vibe.example.com", "origin": "https://evil.example.com"}))
        )
        # Null origin rejected
        self.assertFalse(
            server.verify_ws_origin(_make_ws({"host": "127.0.0.1:8765", "origin": "null"}))
        )
        # Untrusted peer spoofing X-Forwarded-Host rejected
        self.assertFalse(
            server.verify_ws_origin(
                _make_ws(
                    {
                        "host": "vibe.example.com",
                        "x-forwarded-host": "evil.example.com",
                        "origin": "https://evil.example.com",
                    },
                    peer="203.0.113.9",
                )
            )
        )

    async def test_security_headers_middleware_sets_defaults_without_overwriting_sandbox(self) -> None:
        req = MagicMock()

        async def _normal_next(_request):
            return Response(content="ok", media_type="text/plain")

        resp = await server.add_security_headers(req, _normal_next)
        self.assertEqual(resp.headers.get("X-Content-Type-Options"), "nosniff")
        self.assertEqual(resp.headers.get("X-Frame-Options"), "DENY")
        self.assertEqual(resp.headers.get("Referrer-Policy"), "no-referrer")
        self.assertIn("camera=()", resp.headers.get("Permissions-Policy", ""))
        self.assertIn("frame-ancestors 'none'", resp.headers.get("Content-Security-Policy", ""))

        async def _sandbox_next(_request):
            return Response(content="img", headers={"Content-Security-Policy": "sandbox"})

        sandbox_resp = await server.add_security_headers(req, _sandbox_next)
        self.assertEqual(sandbox_resp.headers.get("Content-Security-Policy"), "sandbox")
        self.assertEqual(sandbox_resp.headers.get("X-Content-Type-Options"), "nosniff")

    async def test_streaming_upload_enforces_limit_and_cleans_partial_files(self) -> None:
        orig_dir = config.UPLOADS_DIR
        orig_max = config.MAX_UPLOAD_BYTES
        tmp_uploads = Path(self._tmp.name) / "uploads"
        tmp_uploads.mkdir(parents=True, exist_ok=True)
        config.UPLOADS_DIR = tmp_uploads
        config.MAX_UPLOAD_BYTES = 1024
        try:
            # Valid upload
            valid_file = UploadFile(filename="ok.png", file=io.BytesIO(b"\x89PNG" + b"a" * 100))
            res = await server.upload_image(file=valid_file, _token="valid")
            self.assertEqual(res["size"], 104)
            self.assertTrue((tmp_uploads / res["filename"]).is_file())

            # Oversized upload -> 413 and no partial file left behind
            before_files = set(p.name for p in tmp_uploads.iterdir())
            big_file = UploadFile(filename="too_big.png", file=io.BytesIO(b"x" * 2048))
            with self.assertRaises(HTTPException) as ctx:
                await server.upload_image(file=big_file, _token="valid")
            self.assertEqual(ctx.exception.status_code, 413)
            self.assertEqual(set(p.name for p in tmp_uploads.iterdir()), before_files)

            # Empty upload -> 400 and no empty file left behind
            empty_file = UploadFile(filename="empty.png", file=io.BytesIO(b""))
            with self.assertRaises(HTTPException) as ctx:
                await server.upload_image(file=empty_file, _token="valid")
            self.assertEqual(ctx.exception.status_code, 400)
            self.assertEqual(set(p.name for p in tmp_uploads.iterdir()), before_files)
        finally:
            config.UPLOADS_DIR = orig_dir
            config.MAX_UPLOAD_BYTES = orig_max

    async def test_cleanup_orphan_uploads_respects_references_and_age(self) -> None:
        tmp_uploads = Path(self._tmp.name) / "uploads_gc"
        tmp_uploads.mkdir(parents=True, exist_ok=True)

        referenced_file = tmp_uploads / "ref_img.png"
        meta_referenced_file = tmp_uploads / "queued_img.png"
        recent_orphan = tmp_uploads / "recent_orphan.png"
        old_orphan = tmp_uploads / "old_orphan.png"

        for f, data in (
            (referenced_file, b"1234"),
            (meta_referenced_file, b"5678"),
            (recent_orphan, b"abcd"),
            (old_orphan, b"efghij"),
        ):
            f.write_bytes(data)

        old_ts = time.time() - 7200
        os.utime(referenced_file, (old_ts, old_ts))
        os.utime(meta_referenced_file, (old_ts, old_ts))
        os.utime(old_orphan, (old_ts, old_ts))

        self.test_store.upsert_item(
            "sess_1",
            {
                "id": "u1",
                "type": "user",
                "text": "check image",
                "images": [{"filename": "ref_img.png", "url": "/uploads/ref_img.png"}],
            },
        )
        self.test_store.set_meta(
            "queued_prompts:sess_1",
            [{"id": "q1", "images": [{"url": "/uploads/queued_img.png"}]}],
        )

        res = server.cleanup_orphan_uploads(tmp_uploads, min_age_seconds=3600.0)
        self.assertEqual(res["deleted"], 1)
        self.assertEqual(res["freed_bytes"], 6)
        self.assertEqual(res["kept"], 3)
        self.assertTrue(referenced_file.exists())
        self.assertTrue(meta_referenced_file.exists())
        self.assertTrue(recent_orphan.exists())
        self.assertFalse(old_orphan.exists())


if __name__ == "__main__":
    unittest.main()
