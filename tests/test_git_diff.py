import os
import pathlib
import subprocess
import tempfile
import unittest

os.environ.setdefault("VIBEONE_WEB_TOKEN", "test-token")

import server  # noqa: E402


class GitDiffUntrackedTest(unittest.IsolatedAsyncioTestCase):
    async def test_untracked_text_binary_and_ignored_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp)
            subprocess.run(["git", "init"], cwd=repo, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            (repo / ".gitignore").write_text("ignored.txt\n", encoding="utf-8")
            (repo / "ignored.txt").write_text("secret\n", encoding="utf-8")
            (repo / "README.md").write_text("# Hello\nLine 2\n", encoding="utf-8")
            (repo / "bin.dat").write_bytes(b"\x00\x01\x02binary")

            diff_out = await server._build_untracked_diff(str(repo))

            self.assertIn("diff --git a/README.md b/README.md", diff_out)
            self.assertIn("new file mode 100644", diff_out)
            self.assertIn("--- /dev/null\n+++ b/README.md\n@@ -0,0 +1,2 @@\n+# Hello\n+Line 2", diff_out)
            self.assertIn("diff --git a/bin.dat b/bin.dat", diff_out)
            self.assertIn("Binary files /dev/null and b/bin.dat differ", diff_out)
            self.assertNotIn("diff --git a/ignored.txt", diff_out)
            self.assertNotIn("secret", diff_out)


if __name__ == "__main__":
    unittest.main()
