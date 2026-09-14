import pathlib
import subprocess
import tempfile

from django.test import SimpleTestCase

from seamcheck.gitfiles import tracked_files


def _run(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _init_repo(repo):
    _run(repo, "init", "-q")
    _run(repo, "config", "user.email", "test@example.com")
    _run(repo, "config", "user.name", "Test")


class TrackedFilesTests(SimpleTestCase):
    def test_a_non_git_directory_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(tracked_files(tmp))

    def test_a_committed_file_is_tracked(self):
        with tempfile.TemporaryDirectory() as tmp:
            _init_repo(tmp)
            (pathlib.Path(tmp) / "app.py").write_text("x = 1")
            _run(tmp, "add", "app.py")
            _run(tmp, "commit", "-q", "-m", "init")

            self.assertEqual(tracked_files(tmp), frozenset({"app.py"}))

    def test_an_untracked_but_not_ignored_file_is_included(self):
        # A brand-new file mid-edit is real project input the moment it exists, staged
        # or not - not just after the first `git add`.
        with tempfile.TemporaryDirectory() as tmp:
            _init_repo(tmp)

            (pathlib.Path(tmp) / "new_file.py").write_text("x = 1")

            self.assertIn("new_file.py", tracked_files(tmp))

    def test_a_gitignored_file_is_excluded(self):
        with tempfile.TemporaryDirectory() as tmp:
            _init_repo(tmp)
            (pathlib.Path(tmp) / ".gitignore").write_text("OTHER/\n")
            other = pathlib.Path(tmp) / "OTHER"
            other.mkdir()
            (other / "scratch.js").write_text("x = 1")

            found = tracked_files(tmp)

            self.assertNotIn("OTHER/scratch.js", found)

    def test_a_nested_gitignore_is_honoured(self):
        # The exact class a hand-rolled top-level-only .gitignore parser gets wrong -
        # git's own answer honours every nested .gitignore, not just the root one.
        with tempfile.TemporaryDirectory() as tmp:
            _init_repo(tmp)
            nested = pathlib.Path(tmp) / "vendor"
            nested.mkdir()
            (nested / ".gitignore").write_text("*.log\n")
            (nested / "keep.js").write_text("x = 1")
            (nested / "drop.log").write_text("noise")

            found = tracked_files(tmp)

            self.assertIn("vendor/keep.js", found)
            self.assertNotIn("vendor/drop.log", found)
