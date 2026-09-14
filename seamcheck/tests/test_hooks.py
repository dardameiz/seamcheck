import os
import stat
import subprocess
import tempfile
from unittest import mock

from django.test import SimpleTestCase

from seamcheck.hooks import _summary, install_hooks, run_precommit, run_prepush


class SummaryTests(SimpleTestCase):
    def test_no_changed_files_says_so(self):
        text = _summary({"scope": "commit", "changed_files": [], "pages": {}})

        self.assertIn("nothing staged", text.lower())

    def test_changed_files_matching_no_page_says_so(self):
        text = _summary({"scope": "commit", "changed_files": ["a.py"], "pages": {}})

        self.assertIn("1 file(s) changed", text)
        self.assertIn("none map to a known page", text)

    def test_a_clean_page_is_reported_clean(self):
        result = {
            "scope": "commit", "changed_files": ["push_arena/a.js"],
            "pages": {"push-arena-main": {"touched_files": ["push_arena/a.js"],
                                          "features": [], "findings": []}},
        }

        text = _summary(result)

        self.assertIn("push-arena-main", text)
        self.assertIn("clean", text)

    def test_findings_are_listed_with_a_cap(self):
        findings = [
            {"kind": "dom_selector", "label": f"#x{i}", "file": "a.js"} for i in range(7)
        ]
        result = {
            "scope": "commit", "changed_files": ["a.js"],
            "pages": {"page": {"touched_files": ["a.js"], "features": ["Achievements"],
                               "findings": findings}},
        }

        text = _summary(result)

        self.assertIn("Achievements", text)
        self.assertIn("7 unresolved/unused finding(s)", text)
        self.assertIn("...and 2 more", text)
        # Only the first 5 are printed as their own line.
        self.assertEqual(text.count("#x"), 5)


class RunHookTests(SimpleTestCase):
    def test_run_precommit_never_raises_even_if_scoped_findings_blows_up(self):
        with mock.patch("seamcheck.api.scoped_findings", side_effect=RuntimeError("boom")):
            code = run_precommit(".")

        self.assertEqual(code, 0)

    def test_run_prepush_never_raises_either(self):
        with mock.patch("seamcheck.api.scoped_findings", side_effect=RuntimeError("boom")):
            code = run_prepush(".")

        self.assertEqual(code, 0)


class InstallHooksTests(SimpleTestCase):
    def _init_repo(self, tmp):
        subprocess.run(["git", "-C", tmp, "init", "-q"], check=True, capture_output=True)

    def test_writes_both_hooks_executable_with_the_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._init_repo(tmp)

            message = install_hooks(tmp)

            self.assertIn("pre-commit", message)
            self.assertIn("pre-push", message)
            for name in ("pre-commit", "pre-push"):
                path = os.path.join(tmp, ".git", "hooks", name)
                self.assertTrue(os.path.isfile(path))
                mode = os.stat(path).st_mode
                self.assertTrue(mode & stat.S_IXUSR)
                with open(path) as handle:
                    self.assertIn("Installed by `seamcheck --install-hooks`.", handle.read())

    def test_running_twice_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._init_repo(tmp)
            install_hooks(tmp)

            message = install_hooks(tmp)

            self.assertIn("Installed", message)
            self.assertNotIn("Left alone", message)

    def test_a_hand_written_hook_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._init_repo(tmp)
            path = os.path.join(tmp, ".git", "hooks", "pre-commit")
            with open(path, "w") as handle:
                handle.write("#!/bin/sh\necho mine\n")

            message = install_hooks(tmp)

            self.assertIn("Left alone", message)
            self.assertIn("pre-commit", message)
            with open(path) as handle:
                self.assertEqual(handle.read(), "#!/bin/sh\necho mine\n")

    def test_not_a_git_repository_explains_rather_than_crashing(self):
        with tempfile.TemporaryDirectory() as tmp:
            message = install_hooks(tmp)

            self.assertIn("git repository", message)

    def test_a_worktree_writes_to_the_main_checkouts_hooks_dir(self):
        # T14's own lens: hooks are resolved via git's `--git-path hooks`, not a
        # hardcoded `.git/hooks` - a worktree's `.git` is a FILE pointing elsewhere, and
        # the old hardcoded path would have tried (and failed) to write there directly.
        with tempfile.TemporaryDirectory() as tmp:
            self._init_repo(tmp)
            subprocess.run(["git", "-C", tmp, "commit", "--allow-empty", "-q", "-m", "x"],
                           check=True, capture_output=True)
            worktree = os.path.join(tmp, "wt")
            subprocess.run(["git", "-C", tmp, "worktree", "add", "-q", worktree, "-b", "wt-branch"],
                           check=True, capture_output=True)

            message = install_hooks(worktree)

            self.assertIn("Installed", message)
            self.assertTrue(os.path.isfile(os.path.join(tmp, ".git", "hooks", "pre-commit")))

    def test_husky_managed_hooks_are_never_written_to(self):
        # T14: `core.hooksPath=.husky/_` (husky v9+) means git never reads `.git/hooks`
        # at all - installing there wrote real, marker-carrying files that never ran.
        # `.husky/_` is also regenerated by `npm install`, so writing INTO it directly
        # would be silently discarded on the next install even if git did read it.
        with tempfile.TemporaryDirectory() as tmp:
            self._init_repo(tmp)
            husky_dir = os.path.join(tmp, ".husky", "_")
            os.makedirs(husky_dir)
            subprocess.run(["git", "-C", tmp, "config", "core.hooksPath", ".husky/_"],
                           check=True, capture_output=True)

            message = install_hooks(tmp)

            self.assertIn("husky", message)
            self.assertIn(".husky/pre-commit", message)
            self.assertEqual(os.listdir(husky_dir), [])
            self.assertFalse(os.path.isfile(os.path.join(tmp, ".git", "hooks", "pre-commit")))
