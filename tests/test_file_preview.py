import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("VIBEONE_WEB_TOKEN", "test-master-token")

from fastapi import HTTPException  # noqa: E402

import server  # noqa: E402


class FilePreviewApiTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    async def test_read_code_and_relative_path(self) -> None:
        src = self.root / "pkg" / "hello.py"
        src.parent.mkdir(parents=True, exist_ok=True)
        src.write_text("def greet():\n    return 'hi'\n", encoding="utf-8")

        res_abs = await server.read_file_preview(
            path=str(src),
            workdir=str(self.root),
            _token="valid",
        )
        self.assertEqual(res_abs["kind"], "text")
        self.assertEqual(res_abs["language"], "python")
        self.assertEqual(res_abs["name"], "hello.py")
        self.assertEqual(res_abs["display_path"], os.path.join("pkg", "hello.py"))
        self.assertEqual(res_abs["line_count"], 2)
        self.assertFalse(res_abs["truncated"])
        self.assertIn("def greet():", res_abs["content"])

        res_rel = await server.read_file_preview(
            path="pkg/hello.py",
            workdir=str(self.root),
            _token="valid",
        )
        self.assertEqual(res_rel["path"], str(src))
        self.assertEqual(res_rel["content"], res_abs["content"])

    async def test_markdown_binary_and_truncation(self) -> None:
        md = self.root / "README.md"
        md.write_text("# Title\n\nBody\n", encoding="utf-8")
        res_md = await server.read_file_preview(path=str(md), workdir=str(self.root), _token="valid")
        self.assertEqual(res_md["kind"], "text")
        self.assertEqual(res_md["language"], "markdown")

        bin_file = self.root / "data.bin"
        bin_file.write_bytes(b"\x89BIN\x00\x01\x02\x03")
        res_bin = await server.read_file_preview(path=str(bin_file), workdir=str(self.root), _token="valid")
        self.assertEqual(res_bin["kind"], "binary")
        self.assertEqual(res_bin["content"], "")

        big_file = self.root / "big.log"
        big_file.write_bytes(b"a" * (server._MAX_FILE_PREVIEW_BYTES + 100))
        res_big = await server.read_file_preview(path=str(big_file), workdir=str(self.root), _token="valid")
        self.assertEqual(res_big["kind"], "text")
        self.assertTrue(res_big["truncated"])
        self.assertEqual(res_big["shown_bytes"], server._MAX_FILE_PREVIEW_BYTES)
        self.assertEqual(len(res_big["content"]), server._MAX_FILE_PREVIEW_BYTES)

    async def test_image_preview_and_raw_endpoint(self) -> None:
        img = self.root / "demo.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\n")
        res_img = await server.read_file_preview(path=str(img), workdir=str(self.root), _token="valid")
        self.assertEqual(res_img["kind"], "image")

        raw_resp = await server.serve_preview_file_raw(path=str(img), workdir=str(self.root), _token="valid")
        self.assertEqual(raw_resp.media_type, "image/png")
        self.assertEqual(raw_resp.headers.get("content-security-policy"), "sandbox")

        txt = self.root / "note.txt"
        txt.write_text("hello", encoding="utf-8")
        with self.assertRaises(HTTPException) as ctx:
            await server.serve_preview_file_raw(path=str(txt), workdir=str(self.root), _token="valid")
        self.assertEqual(ctx.exception.status_code, 400)

    async def test_missing_and_directory_errors(self) -> None:
        with self.assertRaises(HTTPException) as ctx_404:
            await server.read_file_preview(path=str(self.root / "nope.py"), workdir=str(self.root), _token="valid")
        self.assertEqual(ctx_404.exception.status_code, 404)

        with self.assertRaises(HTTPException) as ctx_dir:
            await server.read_file_preview(path=str(self.root), workdir=str(self.root), _token="valid")
        self.assertEqual(ctx_dir.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main()
