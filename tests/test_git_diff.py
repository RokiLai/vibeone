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

    async def test_lazy_diff_summary_and_per_file_endpoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp)
            subprocess.run(["git", "init"], cwd=repo, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.name", "Tester"], cwd=repo, check=True)

            (repo / "tracked.txt").write_text("line1\nline2\n", encoding="utf-8")
            (repo / "staged.txt").write_text("old\n", encoding="utf-8")
            subprocess.run(["git", "add", "tracked.txt", "staged.txt"], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, stdout=subprocess.DEVNULL)

            # Working tree modification
            (repo / "tracked.txt").write_text("line1\nline2-edited\nline3\n", encoding="utf-8")
            # Staged modification
            (repo / "staged.txt").write_text("new\n", encoding="utf-8")
            subprocess.run(["git", "add", "staged.txt"], cwd=repo, check=True)
            # Untracked file
            (repo / "new_file.txt").write_text("hello\nworld\n", encoding="utf-8")

            summary = await server.get_git_diff(workdir=str(repo), _token="ok")
            self.assertTrue(summary["is_git_repo"])
            self.assertTrue(summary["lazy"])
            self.assertNotIn("diff", summary)

            staged_paths = {f["path"]: f for f in summary["staged_files"]}
            self.assertIn("staged.txt", staged_paths)
            self.assertEqual(staged_paths["staged.txt"]["status"], "M")
            self.assertEqual(staged_paths["staged.txt"]["added"], 1)
            self.assertEqual(staged_paths["staged.txt"]["deleted"], 1)

            working_paths = {f["path"]: f for f in summary["working_files"]}
            self.assertIn("tracked.txt", working_paths)
            self.assertEqual(working_paths["tracked.txt"]["scope"], "working")
            self.assertEqual(working_paths["tracked.txt"]["added"], 2)
            self.assertEqual(working_paths["tracked.txt"]["deleted"], 1)
            self.assertIn("new_file.txt", working_paths)
            self.assertEqual(working_paths["new_file.txt"]["scope"], "untracked")
            self.assertEqual(working_paths["new_file.txt"]["status"], "A")

            # Fetch per-file diffs on demand
            f_work = await server.get_git_diff_file(path="tracked.txt", scope="working", workdir=str(repo), _token="ok")
            self.assertIn("+line2-edited", f_work["diff"])
            self.assertFalse(f_work["truncated"])

            f_staged = await server.get_git_diff_file(path="staged.txt", scope="staged", workdir=str(repo), _token="ok")
            self.assertIn("+new", f_staged["diff"])
            self.assertIn("-old", f_staged["diff"])

            f_untracked = await server.get_git_diff_file(path="new_file.txt", scope="untracked", workdir=str(repo), _token="ok")
            self.assertIn("+++ b/new_file.txt", f_untracked["diff"])
            self.assertIn("+hello", f_untracked["diff"])

            # Path traversal must be rejected
            from fastapi import HTTPException
            with self.assertRaises(HTTPException) as ctx:
                await server.get_git_diff_file(path="../outside.txt", scope="working", workdir=str(repo), _token="ok")
            self.assertEqual(ctx.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main()
