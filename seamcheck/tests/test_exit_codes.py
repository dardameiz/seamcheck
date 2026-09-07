"""The gate's exit code is the only thing CI reads, and it was wrong everywhere but Django.

`cli.py` asked `result.get("findings")` of a dict that has no `findings` key, so the
non-Django path returned 0 for every project that ever had a finding. Reproduced on redash:
47 unresolved, 53 unused, exit 0.
"""
import subprocess
import tempfile
from io import StringIO
from unittest import mock

from django.core.management import call_command
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
    """`check --since REF` promises "2 if no baseline" in docs/commands.md and llms.txt -
    that ladder is real, but only for a run that asked to compare (see
    GateCodeCombinationsTests). A bare `check`, which is all `cli._run_without_django`'s
    `--check` path can ever be (it has no `--since` of its own), never asked that question:
    `outcome["passed"]` already says whether the CURRENT scan has blocking findings,
    baseline or not, so a bare check with no snapshot is judged by that, exactly like a
    bare check with one."""

    def test_a_bare_check_with_no_baseline_is_still_judged_by_its_own_findings(self):
        # Previously exited 2 here, which conflated "nothing to compare" with "no findings"
        # - the gate reported the build clean-ish when `passed` said it plainly was not.
        outcome = {"passed": False,
                   "message": "No baseline snapshot stored for abc123 yet - nothing to diff against.",
                   "new_unresolved": [], "new_unused": [], "triage_invalidated": [],
                   "returned": [], "counts": {}}
        with mock.patch("seamcheck.api.check", return_value=outcome), \
             mock.patch("seamcheck.api.report", return_value="digest"), \
             mock.patch("seamcheck.cli._worth_scanning", return_value=True):
            code = cli._run_without_django(["--check"], verbose=False)

        self.assertEqual(code, 1, "a bare check never returns 2 - it did not ask to compare")

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


class GateCodeCombinationsTests(SimpleTestCase):
    """Direct coverage of `gate_code`'s six combinations - the CLI-level tests above only
    ever exercise `comparing=False` (neither call site is a `--since` one), so the
    `comparing=True` ladder had no test of its own until now.

    `gate_code()` checked the no-baseline message before it ever looked at `passed`, so a
    bare `check` on a repo with real findings and no snapshot reported "nothing to compare"
    (2) instead of "this is broken" (1) - reproduced by
    `test_check_exits_nonzero_when_a_finding_blocks` in test_cli.py, which failed until
    `comparing` existed and the two current call sites stopped asking for it."""

    _CLEAN = {"passed": True, "message": "clean"}
    _FINDINGS = {"passed": False, "message": "2 new"}
    _NO_BASELINE = {"passed": False,
                    "message": "No baseline snapshot stored for abc123 yet - nothing to diff against."}

    def test_not_comparing_clean(self):
        self.assertEqual(exitcodes.gate_code(self._CLEAN), exitcodes.EXIT_CLEAN)

    def test_not_comparing_findings(self):
        self.assertEqual(exitcodes.gate_code(self._FINDINGS), exitcodes.EXIT_FINDINGS)

    def test_not_comparing_no_baseline(self):
        # comparing=False (the default): a bare check never asked "what changed", so a
        # missing baseline is not its problem - `passed` alone decides.
        self.assertEqual(exitcodes.gate_code(self._NO_BASELINE), exitcodes.EXIT_FINDINGS)

    def test_comparing_clean(self):
        self.assertEqual(exitcodes.gate_code(self._CLEAN, comparing=True), exitcodes.EXIT_CLEAN)

    def test_comparing_findings(self):
        self.assertEqual(exitcodes.gate_code(self._FINDINGS, comparing=True), exitcodes.EXIT_FINDINGS)

    def test_comparing_no_baseline(self):
        # comparing=True (a --since run): the documented ladder - 2 when there is nothing
        # to diff against, because the question asked literally cannot be answered.
        self.assertEqual(exitcodes.gate_code(self._NO_BASELINE, comparing=True),
                          exitcodes.EXIT_NO_BASELINE)


class EnvironmentExitCodeTests(SimpleTestCase):
    """EXIT_NO_BASELINE (2) is a CI-gate answer about findings history. Two "the machine is
    wrong, not the invocation" cases were returning it anyway, which meant a CI job could
    not tell "your Postgres driver is missing" from "this is the first run" - they printed
    different messages but exited identically. EXIT_ENVIRONMENT (4) existed for exactly
    this and nothing used it."""

    def test_nothing_to_scan_is_an_environment_problem_not_a_baseline_one(self):
        with mock.patch("seamcheck.cli._worth_scanning", return_value=False):
            code = cli._run_without_django([], verbose=False)

        self.assertEqual(code, exitcodes.EXIT_ENVIRONMENT)

    def test_a_missing_project_dependency_is_an_environment_problem_not_a_baseline_one(self):
        # What happens when seamcheck is installed globally (pipx, uv tool, a system pip)
        # rather than into the project's own virtualenv: the project imports something
        # that resolves in ITS environment but not in seamcheck's.
        with (
            mock.patch("seamcheck.cli.find_project", return_value=None),
            mock.patch.dict("os.environ", {"DJANGO_SETTINGS_MODULE": "x.settings"}),
            mock.patch("django.setup",
                       side_effect=ModuleNotFoundError("No module named 'psycopg2'", name="psycopg2")),
        ):
            code = cli.main(["check"])

        self.assertEqual(code, exitcodes.EXIT_ENVIRONMENT)


class TriageExitCodeTests(SimpleTestCase):
    """`--triage` failing (an id the current scan does not have, a status/why word outside
    the fixed set, an `--undo` with no mark to remove, or no disposition given at all) used
    to `return`/`raise SystemExit` a bare literal `2` on BOTH doors - colliding with
    `EXIT_NO_BASELINE`, `check --since`'s own, unrelated "nothing to compare against"
    answer. A CI job that reads exit codes across a pipeline running both commands could not
    tell a failed triage apart from a first run with no baseline. `docs/commands.md` ships
    the 0-4 table as a closed set, so this collision was the table shipping a code the
    triage path did not actually mean.

    Fixed to route through `exitcodes.EXIT_USAGE` on both doors: a bad triage argument is
    "the command was wrong", exactly what EXIT_USAGE already documents - not a new code.
    """

    _FAILED = {"ok": False, "message": "No symbol with id `bogus:id` in the current scan."}

    def test_plain_door_reports_usage_not_no_baseline(self):
        with mock.patch("seamcheck.api.triage", return_value=self._FAILED), \
             mock.patch("seamcheck.cli._worth_scanning", return_value=True):
            code = cli._run_without_django(
                ["--triage", "bogus:id", "--wrong", "consumed-by-dependency"], verbose=False)

        self.assertEqual(code, exitcodes.EXIT_USAGE)
        self.assertNotEqual(code, exitcodes.EXIT_NO_BASELINE)

    def test_django_door_reports_usage_not_no_baseline(self):
        with mock.patch("seamcheck.api.triage", return_value=self._FAILED), \
             self.assertRaises(SystemExit) as raised:
            call_command("seamcheck", "--triage", "bogus:id", "--wrong",
                         "consumed-by-dependency", stdout=StringIO(), stderr=StringIO())

        code = int(str(raised.exception.code))
        self.assertEqual(code, exitcodes.EXIT_USAGE)
        self.assertNotEqual(code, exitcodes.EXIT_NO_BASELINE)

    def test_the_two_doors_agree(self):
        with mock.patch("seamcheck.api.triage", return_value=self._FAILED), \
             mock.patch("seamcheck.cli._worth_scanning", return_value=True):
            plain_code = cli._run_without_django(
                ["--triage", "bogus:id", "--wrong", "consumed-by-dependency"], verbose=False)

        with mock.patch("seamcheck.api.triage", return_value=self._FAILED), \
             self.assertRaises(SystemExit) as raised:
            call_command("seamcheck", "--triage", "bogus:id", "--wrong",
                         "consumed-by-dependency", stdout=StringIO(), stderr=StringIO())
        django_code = int(str(raised.exception.code))

        self.assertEqual(plain_code, django_code)

    def test_django_door_missing_disposition_reports_usage_not_no_baseline(self):
        # No --status and no --wrong at all - api.triage is never even reached; this is
        # the management command's own pre-check (seamcheck.py's `_triage`).
        with self.assertRaises(SystemExit) as raised:
            call_command("seamcheck", "--triage", "bogus:id",
                         stdout=StringIO(), stderr=StringIO())

        code = int(str(raised.exception.code))
        self.assertEqual(code, exitcodes.EXIT_USAGE)
        self.assertNotEqual(code, exitcodes.EXIT_NO_BASELINE)


class UnknownCommandExitCodeTests(SimpleTestCase):
    """`_resolve()` refusing an unrecognised command word (`seamcheck frobnicate`) used
    to return the same bare `2` as everything else in this family. Both doors are the
    SAME code here - `_resolve` is shared by `main()` regardless of which door it then
    dispatches to - so there is only one call site to test, not two to compare."""

    def test_an_unrecognised_command_reports_usage_not_no_baseline(self):
        err = StringIO()
        with mock.patch("sys.stderr", err):
            code = cli.main(["frobnicate"])

        self.assertEqual(code, exitcodes.EXIT_USAGE)
        self.assertNotEqual(code, exitcodes.EXIT_NO_BASELINE)
        self.assertIn("no command named 'frobnicate'", err.getvalue())


class HelpBadNameExitCodeTests(SimpleTestCase):
    """`seamcheck help <bad-name>` - also `_resolve`-adjacent, shared `main()` logic,
    one call site for both doors."""

    def test_help_for_an_unknown_command_reports_usage_not_no_baseline(self):
        err = StringIO()
        with mock.patch("sys.stderr", err), mock.patch("sys.stdout", StringIO()):
            code = cli.main(["help", "frobnicate"])

        self.assertEqual(code, exitcodes.EXIT_USAGE)
        self.assertNotEqual(code, exitcodes.EXIT_NO_BASELINE)


class SetTunnelExitCodeTests(SimpleTestCase):
    """`--set-tunnel` outside `always`/`never` on the plain door - the Django door never
    reaches this code at all (argparse's own `choices=` on the shared flag table refuses
    it first, as a `CommandError` - see `CommandErrorExitCodeTests` below), so there is
    no second door to compare against here."""

    def test_plain_door_bad_value_reports_usage_not_no_baseline(self):
        code = cli._set_tunnel_plain("sometimes")

        self.assertEqual(code, exitcodes.EXIT_USAGE)
        self.assertNotEqual(code, exitcodes.EXIT_NO_BASELINE)


class CommandErrorExitCodeTests(SimpleTestCase):
    """`main()`'s generic `except CommandError` wrapper around `call_command()` - the
    plain `seamcheck` executable's own dispatch to a Django project, not `manage.py
    seamcheck` invoked directly (that goes through Django's OWN `run_from_argv`, whose
    `CommandError` handling is entirely Django's, outside anything `seamcheck.exitcodes`
    can reach - a pre-existing, separate fact about that entry point, not something this
    fix changes or claims to unify)."""

    def test_a_command_error_from_call_command_reports_usage_not_no_baseline(self):
        from django.core.management.base import CommandError

        with (
            mock.patch("seamcheck.cli.find_project", return_value=None),
            mock.patch.dict("os.environ", {"DJANGO_SETTINGS_MODULE": "x.settings"}),
            mock.patch("django.setup"),
            mock.patch("django.core.management.call_command",
                       side_effect=CommandError("--format was given an empty value")),
        ):
            code = cli.main(["check"])

        self.assertEqual(code, exitcodes.EXIT_USAGE)
        self.assertNotEqual(code, exitcodes.EXIT_NO_BASELINE)


class ObserveBrowserUnavailableExitCodeTests(SimpleTestCase):
    """`--observe`'s `BrowserUnavailable` (Playwright missing, or no browser downloaded)
    used to be wrapped as a `CommandError` - reading identically to "you typed this
    wrong" once that generic handler above was fixed to EXIT_USAGE, even though a
    missing browser is the MACHINE being wrong, exactly like the ModuleNotFoundError
    case `EXIT_ENVIRONMENT` already exists for. `--observe` is Django-only
    (`cliflags.FLAGS`'s `plain=False`), so there is no plain-door equivalent to compare."""

    def test_a_missing_browser_reports_environment_not_usage_or_no_baseline(self):
        from seamcheck.browser import BrowserUnavailable

        with (
            mock.patch("seamcheck.api.scan", side_effect=RuntimeError("no scan needed")),
            mock.patch("seamcheck.browser.observe_pages",
                       side_effect=BrowserUnavailable("no chromium downloaded")),
            self.assertRaises(SystemExit) as raised,
        ):
            call_command("seamcheck", "--observe", "http://example.com/",
                         stdout=StringIO(), stderr=StringIO())

        code = int(str(raised.exception.code))
        self.assertEqual(code, exitcodes.EXIT_ENVIRONMENT)
        self.assertNotEqual(code, exitcodes.EXIT_USAGE)
        self.assertNotEqual(code, exitcodes.EXIT_NO_BASELINE)


class BadFormatValueExitCodeTests(SimpleTestCase):
    """An unrecognised `--format` value (`api._report`'s own `ValueError`) - the Django
    door already caught this (`_format_report`'s `except ValueError`, previously a bare
    `SystemExit(2)`); the plain door did not catch it AT ALL, so this reached the
    interpreter as an uncaught traceback rather than any controlled exit code - arguably
    worse than the raw-`2` bug, found only by checking whether the two doors could even
    be compared here."""

    def test_django_door_reports_usage_not_no_baseline(self):
        with (
            mock.patch("seamcheck.api.report",
                       side_effect=ValueError("Unknown format 'bogus'.")),
            self.assertRaises(SystemExit) as raised,
        ):
            call_command("seamcheck", "--format", "bogus",
                         stdout=StringIO(), stderr=StringIO())

        code = int(str(raised.exception.code))
        self.assertEqual(code, exitcodes.EXIT_USAGE)
        self.assertNotEqual(code, exitcodes.EXIT_NO_BASELINE)

    def test_plain_door_no_longer_crashes_uncaught_and_reports_usage(self):
        with (
            mock.patch("seamcheck.cli._worth_scanning", return_value=True),
            mock.patch("seamcheck.api.report",
                       side_effect=ValueError("Unknown format 'bogus'.")),
        ):
            code = cli._run_without_django(["--format", "bogus"], verbose=False)

        self.assertEqual(code, exitcodes.EXIT_USAGE)
        self.assertNotEqual(code, exitcodes.EXIT_NO_BASELINE)

    def test_the_two_doors_agree(self):
        with (
            mock.patch("seamcheck.cli._worth_scanning", return_value=True),
            mock.patch("seamcheck.api.report",
                       side_effect=ValueError("Unknown format 'bogus'.")),
        ):
            plain_code = cli._run_without_django(["--format", "bogus"], verbose=False)

        with (
            mock.patch("seamcheck.api.report",
                       side_effect=ValueError("Unknown format 'bogus'.")),
            self.assertRaises(SystemExit) as raised,
        ):
            call_command("seamcheck", "--format", "bogus",
                         stdout=StringIO(), stderr=StringIO())
        django_code = int(str(raised.exception.code))

        self.assertEqual(plain_code, django_code)
