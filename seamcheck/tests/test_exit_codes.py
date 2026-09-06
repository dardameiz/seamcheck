"""The gate's exit code is the only thing CI reads, and it was wrong everywhere but Django.

`cli.py` asked `result.get("findings")` of a dict that has no `findings` key, so the
non-Django path returned 0 for every project that ever had a finding. Reproduced on redash:
47 unresolved, 53 unused, exit 0.
"""
import subprocess
import tempfile
from unittest import mock

from django.test import SimpleTestCase

from seamcheck import api, cli, exitcodes


class NonDjangoGateTests(SimpleTestCase):
    def test_a_project_with_findings_fails_the_gate(self):
        outcome = {"passed": False, "message": "2 new", "new_unresolved": [{"id": "url:x"}],
                   "new_unused": [], "triage_invalidated": [], "returned": [],
                   "counts": {"unresolved": 1}}
        with mock.patch("seamcheck.api.check", return_value=outcome), \
             mock.patch("seamcheck.api.report", return_value="digest"), \
             mock.patch("seamcheck.cli._worth_scanning", return_value=True):
            code = cli._run_without_django(["--check"], verbose=False)

        self.assertEqual(code, 1, "a project with findings must fail the gate")

    def test_a_clean_project_still_passes_after_the_fix(self):
        # Guards against the fix inverting the clean case. The next task refactors
        # this branch to route through a shared `gate_code()` helper, so this test
        # catches it if that rewrite breaks the passing path.
        outcome = {"passed": True, "message": "clean", "new_unresolved": [],
                   "new_unused": [], "triage_invalidated": [], "returned": [],
                   "counts": {}}
        with mock.patch("seamcheck.api.check", return_value=outcome), \
             mock.patch("seamcheck.api.report", return_value="digest"), \
             mock.patch("seamcheck.cli._worth_scanning", return_value=True):
            code = cli._run_without_django(["--check"], verbose=False)

        self.assertEqual(code, 0)


class BaselineExitCodeTests(SimpleTestCase):
    """Help, docs/commands.md and llms.txt all promise "2 if no baseline". Reproduced on the
    reference project with no snapshot: exit 1. A CI job cannot tell a regression from a
    first run, which is the whole reason that code was documented."""

    def test_no_baseline_exits_two_not_one(self):
        outcome = {"passed": False,
                   "message": "No baseline snapshot stored for abc123 yet - nothing to diff against.",
                   "new_unresolved": [], "new_unused": [], "triage_invalidated": [],
                   "returned": [], "counts": {}}
        with mock.patch("seamcheck.api.check", return_value=outcome), \
             mock.patch("seamcheck.api.report", return_value="digest"), \
             mock.patch("seamcheck.cli._worth_scanning", return_value=True):
            code = cli._run_without_django(["--check"], verbose=False)

        self.assertEqual(code, 2, "no baseline is not the same answer as a regression")

    def test_real_api_message_uses_the_constant(self):
        """The NO_BASELINE prefix is load-bearing: gate_code reads it. If it drifts from
        exitcodes.NO_BASELINE, CI silently misclassifies first runs as regressions.
        This test exercises the real api.diff_against to catch the drift."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Initialize a git repo with one commit
            subprocess.run(["git", "init"], cwd=tmpdir, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"],
                          cwd=tmpdir, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test User"],
                          cwd=tmpdir, check=True, capture_output=True)
            subprocess.run(["touch", "file.txt"], cwd=tmpdir, check=True, capture_output=True)
            subprocess.run(["git", "add", "."], cwd=tmpdir, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "initial"], cwd=tmpdir, check=True,
                          capture_output=True)

            # Create a minimal graph (api.diff_against needs it)
            from seamcheck.graph import Graph
            graph = Graph(symbols={}, edges=[])

            # Call the real diff_against with no snapshot stored
            result, sha, message = api.diff_against(graph, "HEAD", tmpdir)

            # The message must start with the constant
            self.assertTrue(message.startswith(exitcodes.NO_BASELINE),
                           f"Message '{message}' does not start with constant '{exitcodes.NO_BASELINE}'")
