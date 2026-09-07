import io
import pathlib
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

from django.test import SimpleTestCase

from seamcheck.cli import COMMANDS, PRIMARY, find_project, main


class HelpTests(SimpleTestCase):
    def test_bare_help_names_every_command(self):
        # Nine equal lines is a menu, not an answer to "what do I run" - so the three a
        # person types get their summary and the rest are named on one line. Named, not
        # dropped: an agent driving this uses json, explain and triage more than a human
        # does, and a command missing from the help is a command that does not exist.
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(main(["help"]), 0)

        for name in COMMANDS:
            self.assertIn(name, out.getvalue())
        for name in PRIMARY:
            self.assertIn(COMMANDS[name].summary[:30], out.getvalue())

    def test_every_command_is_reachable_from_the_listing(self):
        # `also: scan · report · ...` is only useful if `help <name>` answers for each.
        for name in COMMANDS:
            with self.subTest(command=name):
                out = io.StringIO()
                with redirect_stdout(out):
                    self.assertEqual(main(["help", name]), 0)

                self.assertIn(f"seamcheck {name} -", out.getvalue())

    def test_no_arguments_prints_help_rather_than_scanning(self):
        # A scan takes half a minute and writes a snapshot. Someone typing `seamcheck`
        # to see what it does should not trigger one.
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(main([]), 0)

        self.assertIn("commands:", out.getvalue())


class ProjectDiscoveryTests(SimpleTestCase):
    def test_it_reads_the_settings_module_out_of_manage_py(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            (root / "manage.py").write_text(
                'import os\nos.environ.setdefault("DJANGO_SETTINGS_MODULE", "myproj.settings")\n'
            )
            nested = root / "app" / "views"
            nested.mkdir(parents=True)

            # From a subdirectory, because running from inside an app is normal.
            self.assertEqual(find_project(nested), ("myproj.settings", root))

    def test_finding_a_project_does_not_move_the_caller(self):
        # It used to chdir from inside the lookup, leaving the process somewhere it never
        # asked to be - and, once a temporary directory was cleaned up, nowhere at all.
        import os

        before = os.getcwd()
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            (root / "manage.py").write_text('"DJANGO_SETTINGS_MODULE", "p.settings"')
            find_project(root)

        self.assertEqual(os.getcwd(), before)

    def test_no_project_anywhere_returns_none_rather_than_guessing(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(find_project(pathlib.Path(tmp) / "nowhere"))


class DispatchTests(SimpleTestCase):
    def test_an_unknown_word_is_a_typo_not_a_flag(self):
        # Falling through to the default command would run a scan nobody asked for.
        err = io.StringIO()
        with redirect_stderr(err):
            self.assertEqual(main(["chekc"]), 2)

        self.assertIn("no command named 'chekc'", err.getvalue())

    def test_extra_flags_are_passed_through_untouched(self):
        with (
            mock.patch("seamcheck.cli.find_project", return_value=None),
            mock.patch.dict("os.environ", {"DJANGO_SETTINGS_MODULE": "x.settings"}),
            mock.patch("django.setup"),
            mock.patch("django.core.management.call_command") as called,
        ):
            main(["map", "--since", "main", "--out", "m.html"])

        self.assertEqual(
            called.call_args.args,
            ("seamcheck", "--format", "map", "--serve", "--since", "main", "--out", "m.html"),
        )

    def test_the_check_exit_code_survives_the_wrapper(self):
        # `check` is only useful in CI if a non-zero exit reaches the shell.
        with (
            mock.patch("seamcheck.cli.find_project", return_value=None),
            mock.patch.dict("os.environ", {"DJANGO_SETTINGS_MODULE": "x.settings"}),
            mock.patch("django.setup"),
            mock.patch("django.core.management.call_command", side_effect=SystemExit(1)),
        ):
            self.assertEqual(main(["check"]), 1)

    def test_outside_a_django_project_the_scan_still_runs(self):
        # It used to answer "no Django project here" and stop - which is why an Express
        # or Nest repository, five of the six adapters, could never be scanned from the
        # CLI. No settings module means no Django bootstrap, not no scan.
        with (
            mock.patch("seamcheck.cli.find_project", return_value=None),
            mock.patch.dict("os.environ", {}, clear=True),
            mock.patch("seamcheck.cli._run_without_django", return_value=0) as run,
        ):
            self.assertEqual(main(["scan"]), 0)

        run.assert_called_once()


class _Dispatch:
    """Run main() without a project, capturing the arguments it forwards."""

    def __enter__(self):
        self._patches = [
            mock.patch("seamcheck.cli.find_project", return_value=None),
            mock.patch.dict("os.environ", {"DJANGO_SETTINGS_MODULE": "x.settings"}),
            mock.patch("django.setup"),
            mock.patch("django.core.management.call_command"),
        ]
        started = [patch.start() for patch in self._patches]
        # start() hands back the mock; .new is still the sentinel at this point.
        self.called = started[-1]
        return self

    def __exit__(self, *exc):
        for patch in reversed(self._patches):
            patch.stop()

    @property
    def args(self):
        return self.called.call_args.args


class BackfillArgumentTests(SimpleTestCase):
    def test_bare_backfill_runs_rather_than_dying_on_a_missing_number(self):
        # It used to forward a valueless --backfill, and argparse answered "expected one
        # argument" - a front door that fails on being opened. The listing advertises the
        # command with no arguments, so it has to work with no arguments.
        with _Dispatch() as run:
            self.assertEqual(main(["backfill"]), 0)

            self.assertEqual(run.args, ("seamcheck", "--backfill", "20"))

    def test_a_bare_number_is_the_count(self):
        with _Dispatch() as run:
            main(["backfill", "100"])

            self.assertEqual(run.args, ("seamcheck", "--backfill", "100"))

    def test_a_number_composes_with_the_other_flags(self):
        with _Dispatch() as run:
            main(["backfill", "50", "--backfill-ref", "main"])

            self.assertEqual(
                run.args, ("seamcheck", "--backfill", "50", "--backfill-ref", "main")
            )

    def test_the_long_flag_still_wins_if_someone_types_it(self):
        # No second --backfill appended behind their back.
        with _Dispatch() as run:
            main(["backfill", "--backfill", "7"])

            self.assertEqual(run.args, ("seamcheck", "--backfill", "7"))


class PerCommandHelpTests(SimpleTestCase):
    def test_it_explains_the_command_rather_than_dumping_flags(self):
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(main(["help", "map"]), 0)

        text = out.getvalue()
        self.assertIn("seamcheck map -", text)
        self.assertIn("examples:", text)
        self.assertIn("seamcheck map --open", text)

    def test_a_command_with_dash_dash_help_gets_the_same_explanation(self):
        # `seamcheck map --help` is the question people actually type, and forwarding it
        # to argparse answered with every flag every command shares.
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(main(["map", "--help"]), 0)

        self.assertIn("examples:", out.getvalue())

    def test_double_dash_reaches_the_real_flag_listing(self):
        with _Dispatch() as run:
            main(["map", "--", "--help"])

            self.assertEqual(run.args, ("seamcheck", "--format", "map", "--serve", "--help"))

    def test_help_for_a_command_that_does_not_exist_says_so(self):
        err = io.StringIO()
        out = io.StringIO()
        with redirect_stderr(err), redirect_stdout(out):
            self.assertEqual(main(["help", "mpa"]), 2)

        self.assertIn("no command named 'mpa'", err.getvalue())

    def test_every_command_has_prose_and_at_least_one_example(self):
        # A summary line is a label; the examples are what make it usable.
        for name, entry in COMMANDS.items():
            with self.subTest(command=name):
                self.assertGreater(len(entry.detail), 120)
                self.assertTrue(entry.examples)


class FrontDoorFlagTests(SimpleTestCase):
    def test_verbose_and_quiet_are_consumed_rather_than_forwarded(self):
        # The management command has never heard of --verbose; forwarding it turns a
        # convenience into an error.
        with _Dispatch() as run:
            main(["scan", "--verbose", "-q"])

            self.assertEqual(run.args, ("seamcheck",))

    def test_quiet_reaches_the_command_that_owns_the_bar(self):
        import os

        with _Dispatch():
            main(["scan", "-q"])

            self.assertEqual(os.environ.get("SEAMCHECK_NO_PROGRESS"), "1")

    def test_the_host_projects_logging_is_silenced_around_the_whole_run(self):
        # Not just django.setup(): a project logs on import, and again the first time the
        # scan touches its app registry.
        import logging

        seen = []
        with _Dispatch(), mock.patch(
            "django.core.management.call_command",
            side_effect=lambda *a, **k: seen.append(
                logging.getLogger("host").isEnabledFor(logging.WARNING)
            ),
        ):
            main(["scan"])

        self.assertEqual(seen, [False])

    def test_verbose_leaves_the_host_projects_logging_alone(self):
        import logging

        seen = []
        with _Dispatch(), mock.patch(
            "django.core.management.call_command",
            side_effect=lambda *a, **k: seen.append(
                logging.getLogger("host").isEnabledFor(logging.WARNING)
            ),
        ):
            main(["scan", "-v"])

        self.assertEqual(seen, [True])


class VersionTests(SimpleTestCase):
    def test_it_reports_a_version(self):
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(main(["--version"]), 0)

        self.assertIn("seamcheck ", out.getvalue())

    def test_a_source_install_says_the_number_can_be_stale(self):
        # An editable install records its version once and never revisits it, so a checkout
        # whose pyproject has moved on reports the old number while running the new code -
        # indistinguishable from a failed upgrade unless the path is shown.
        import seamcheck.cli
        from seamcheck.cli import version_line

        line = version_line()
        self.assertIn("seamcheck ", line)

        installed_normally = "site-packages" in pathlib.Path(seamcheck.cli.__file__).parts
        if not installed_normally:
            self.assertIn("running from", line)
            self.assertIn("may lag the code", line)
        else:
            self.assertNotIn("may lag the code", line)

    def test_version_wins_over_being_read_as_a_command(self):
        # `version` is not in COMMANDS; without this it is a typo and exits 2.
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(main(["version"]), 0)

        self.assertIn("seamcheck ", out.getvalue())


class UndoFlagTests(SimpleTestCase):
    """`seamcheck triage X --undo` takes the mark off, on both front doors."""

    def test_the_django_door_forwards_undo(self):
        with _Dispatch() as run:
            main(["triage", "url:x", "--undo"])

        self.assertEqual(run.args, ("seamcheck", "--triage", "url:x", "--undo"))

    def test_the_plain_door_undoes_without_a_scan(self):
        import os

        from seamcheck.cli import _run_without_django
        from seamcheck.triage import TriageEntry, TriageStatus, load_triage, save_triage

        with tempfile.TemporaryDirectory() as tmp:
            pathlib.Path(tmp, "package.json").write_text("{}")
            save_triage([TriageEntry(symbol_id="url:x", fingerprint="f", status=TriageStatus.APPROVED,
                                     who="a", when="2026-08-20", reason="")], tmp)
            cwd = os.getcwd()
            os.chdir(tmp)
            try:
                with (mock.patch("seamcheck.cli._worth_scanning", return_value=True),
                      mock.patch("seamcheck.api.scan") as scan,
                      redirect_stdout(io.StringIO()) as out):
                    code = _run_without_django(["--triage", "url:x", "--undo"], verbose=False)
                    again = _run_without_django(["--triage", "url:x", "--undo"], verbose=False)
            finally:
                os.chdir(cwd)

            self.assertEqual(code, 0)
            self.assertIn("raised again", out.getvalue())
            self.assertEqual(load_triage(tmp), [])
            self.assertEqual(again, 2)
            scan.assert_not_called()


class PlainJsonSizeGateTests(SimpleTestCase):
    """`api.report()`'s size gate has three callers: the Django management command, this
    plain (non-Django) door, and the MCP server. Only the first one used to see TooLarge -
    on an Express or Flask repo `seamcheck json` still dumped everything, and --full/--yes
    were parsed here but never read, so they looked like coverage and did nothing."""

    def test_the_plain_door_refuses_without_both_flags_and_prints_nothing_to_stdout(self):
        import os

        from seamcheck.cli import _run_without_django
        from seamcheck.envelope import TooLarge
        from seamcheck.exitcodes import EXIT_USAGE

        with tempfile.TemporaryDirectory() as tmp:
            pathlib.Path(tmp, "package.json").write_text("{}")
            cwd = os.getcwd()
            os.chdir(tmp)
            try:
                with (mock.patch("seamcheck.cli._worth_scanning", return_value=True),
                      mock.patch("seamcheck.api.report",
                                 side_effect=TooLarge(72_800_000, 18_200_000)),
                      redirect_stdout(io.StringIO()) as out,
                      redirect_stderr(io.StringIO()) as err):
                    code = _run_without_django(["--json"], verbose=False)
            finally:
                os.chdir(cwd)

        self.assertEqual(code, EXIT_USAGE)
        self.assertEqual(out.getvalue(), "", "nothing may reach stdout when the gate refuses")
        self.assertIn("Refusing to print it", err.getvalue())
        self.assertIn("--full --yes", err.getvalue())


class PlainCheckFormatTests(SimpleTestCase):
    """`_run_without_django`'s `--check` branch used to hardcode `fmt="terminal"` and
    ignore `--format`/`--out` outright - `seamcheck check --format sarif --out FILE` on a
    non-Django project (redash: 47 unresolved, 53 unused) printed the terminal digest to
    stdout and never wrote FILE at all. Manual proof against a real corpus project does not
    stop this regressing; this pins the plain path's own format handling directly."""

    def test_check_composes_with_format_and_out_on_the_plain_door(self):
        import os

        from seamcheck.cli import _run_without_django

        clean = {"passed": True, "message": "", "new_unresolved": [], "new_unused": [],
                 "triage_invalidated": [], "returned": [], "counts": {}}

        def fake_report(*, repo_root, fmt, **kwargs):
            return f"RENDERED AS {fmt}"

        with tempfile.TemporaryDirectory() as tmp:
            pathlib.Path(tmp, "package.json").write_text("{}")
            out_path = str(pathlib.Path(tmp) / "out.sarif")
            cwd = os.getcwd()
            os.chdir(tmp)
            try:
                with (
                    mock.patch("seamcheck.cli._worth_scanning", return_value=True),
                    mock.patch("seamcheck.api.check", return_value=clean),
                    mock.patch("seamcheck.api.report", side_effect=fake_report),
                    redirect_stdout(io.StringIO()) as out,
                    redirect_stderr(io.StringIO()),
                ):
                    code = _run_without_django(
                        ["--check", "--format", "sarif", "--out", out_path], verbose=False
                    )
            finally:
                os.chdir(cwd)

            # Read while the temp directory still exists - it is deleted the moment this
            # `with` block exits, and checking after that would report FileNotFoundError
            # for the app being wrong when it is actually the test that closed too soon.
            written = pathlib.Path(out_path).read_text()
            printed = out.getvalue()

        self.assertEqual(code, 0)
        self.assertEqual(written, "RENDERED AS sarif")
        self.assertEqual(printed, "", "sarif with --out must not also print to stdout")


class TunnelSettingArgumentTests(SimpleTestCase):
    """`seamcheck config --tunnel always` is the sentence a person types.

    The management command cannot spell that flag `--tunnel`: there it is the per-run
    boolean that opens a tunnel now. Two meanings behind one word is how somebody ends up
    publishing a report they meant to configure, so the front door translates it into
    `--set-tunnel` and only one of the two is ever typed.
    """

    def test_config_tunnel_always_stores_rather_than_opening_one(self):
        with _Dispatch() as run:
            self.assertEqual(main(["config", "--tunnel", "always"]), 0)

            self.assertEqual(
                run.args, ("seamcheck", "--show-config", "--set-tunnel", "always"))

    def test_config_tunnel_never_is_translated_the_same_way(self):
        with _Dispatch() as run:
            main(["config", "--tunnel", "never"])

            self.assertEqual(
                run.args, ("seamcheck", "--show-config", "--set-tunnel", "never"))

    def test_the_per_run_flag_on_map_is_left_alone(self):
        # It keeps its own meaning: open one NOW, store nothing.
        with _Dispatch() as run:
            main(["map", "--tunnel"])

            self.assertIn("--tunnel", run.args)
            self.assertNotIn("--set-tunnel", run.args)


class LimitFlagParityTests(SimpleTestCase):
    """`--limit` must mean the same thing on both front doors.

    The Django management command parses `--limit` with argparse's `type=int`, which
    accepts a negative value and leaves clamping to `envelope.page()`. The hand-rolled
    `_plain_args` used to guard with `following.isdigit()`, which is False for "-5" - so
    the flag was silently dropped and the default 25 used instead, and the same command
    line answered with a different row count depending on which project type it ran
    against. That is exactly what `_plain_args`'s own docstring warns a divergent flag
    would do.
    """

    def test_a_negative_limit_is_parsed_not_dropped(self):
        from seamcheck.cli import _plain_args

        options = _plain_args(["--limit", "-5"])

        # page() does the clamping (see its own test for that half); this half only
        # guards that _plain_args does not throw the value away before page() sees it.
        self.assertEqual(options["limit"], -5)

    def test_a_non_numeric_limit_keeps_the_default(self):
        from seamcheck.cli import _plain_args

        options = _plain_args(["--limit", "banana"])

        self.assertEqual(options["limit"], 25)


class SinceFlagParityTests(SimpleTestCase):
    """`--since` must mean the same thing on both front doors, same as `--limit` above and
    `--format` before it - this is the third time one flag worked on the Django management
    command and was silently absent from `_plain_args`, so `seamcheck check --since REF` on
    a non-Django project (redash, most of the corpus) ran a bare check against HEAD with no
    error and no warning: the wrong question, answered as if it were the right one.

    Parses the SAME argv on both doors and compares the result directly, rather than just
    asserting a value against `_plain_args` alone - that is what let `--format` drift
    silently before it: each half looked correct read on its own.
    """

    def _django_since(self, *argv):
        from seamcheck.management.commands.seamcheck import Command

        parser = Command().create_parser("manage.py", "seamcheck")
        return parser.parse_args(list(argv)).since

    def test_the_same_argv_parses_to_the_same_since_value(self):
        from seamcheck.cli import _plain_args

        argv = ["--since", "origin/main"]

        self.assertEqual(_plain_args(argv)["since"], self._django_since(*argv))

    def test_neither_door_invents_a_since_when_none_is_given(self):
        from seamcheck.cli import _plain_args

        self.assertIsNone(_plain_args([])["since"])
        self.assertIsNone(self._django_since())


class RefreshFlagParityTests(SimpleTestCase):
    """`--refresh` - the scan cache's escape hatch for a tree it cannot judge on its own
    (a fresh checkout, a restored backup, a clock that just got corrected) - must exist and
    mean the same thing on both front doors, the same way `--since` above had to.
    """

    def _django_refresh(self, *argv):
        from seamcheck.management.commands.seamcheck import Command

        parser = Command().create_parser("manage.py", "seamcheck")
        return parser.parse_args(list(argv)).refresh

    def test_the_same_argv_parses_to_the_same_refresh_value(self):
        from seamcheck.cli import _plain_args

        self.assertEqual(_plain_args(["--refresh"])["refresh"], self._django_refresh("--refresh"))

    def test_neither_door_turns_it_on_by_default(self):
        from seamcheck.cli import _plain_args

        self.assertFalse(_plain_args([])["refresh"])
        self.assertFalse(self._django_refresh())


class IncludeTriagedFlagParityTests(SimpleTestCase):
    """`--include-triaged` - the opt-out from `findings()`'s default of hiding anything
    carrying a triage mark - must exist and mean the same thing on both front doors."""

    def _django_include_triaged(self, *argv):
        from seamcheck.management.commands.seamcheck import Command

        parser = Command().create_parser("manage.py", "seamcheck")
        return parser.parse_args(list(argv)).include_triaged

    def test_the_same_argv_parses_to_the_same_value(self):
        from seamcheck.cli import _plain_args

        argv = ["--include-triaged"]

        self.assertEqual(_plain_args(argv)["include_triaged"],
                         self._django_include_triaged(*argv))

    def test_neither_door_turns_it_on_by_default(self):
        from seamcheck.cli import _plain_args

        self.assertFalse(_plain_args([])["include_triaged"])
        self.assertFalse(self._django_include_triaged())


class DiffFlagParityTests(SimpleTestCase):
    """`--diff` must exist and mean the same thing on both front doors, the same way
    `--findings` and `--symbols` already did."""

    def _django_diff(self, *argv):
        from seamcheck.management.commands.seamcheck import Command

        parser = Command().create_parser("manage.py", "seamcheck")
        return parser.parse_args(list(argv)).diff

    def test_the_same_argv_parses_to_the_same_value(self):
        from seamcheck.cli import _plain_args

        self.assertEqual(_plain_args(["--diff"])["diff"], self._django_diff("--diff"))

    def test_neither_door_turns_it_on_by_default(self):
        from seamcheck.cli import _plain_args

        self.assertFalse(_plain_args([])["diff"])
        self.assertFalse(self._django_diff())


class FlagTableParityTests(SimpleTestCase):
    """The two doors must accept the SAME flag set - derived from
    `seamcheck.cliflags.FLAGS` and from argparse's own parser introspection, not from a
    second hand-written list of names (that would just be another copy of the thing that
    kept drifting: `--since`, then `--limit`, then `--format`, each added to one door and
    silently absent from the other).
    """

    def _our_own_option_strings(self) -> set[str]:
        # Diffed against a VANILLA BaseCommand's parser rather than a hand-typed
        # exclusion list, so Django's own --settings/--verbosity/--version/etc. never
        # have to be named here by hand either - only what `add_arguments` itself
        # registers survives the subtraction.
        from django.core.management.base import BaseCommand

        from seamcheck.management.commands.seamcheck import Command

        ours = Command().create_parser("manage.py", "seamcheck")
        vanilla = BaseCommand().create_parser("manage.py", "seamcheck")
        return set(ours._option_string_actions) - set(vanilla._option_string_actions)

    def test_the_table_is_exactly_what_add_arguments_registers(self):
        from seamcheck.cliflags import FLAGS

        table_names = {name for flag in FLAGS for name in flag.names}

        self.assertEqual(table_names, self._our_own_option_strings())

    def test_every_table_flag_is_either_understood_or_explicitly_refused(self):
        # Nothing may fall through silently: a plain=True flag must be recognised (not
        # collected into "unknown"), and a plain=False flag - real, but one the plain
        # door cannot honour without Django - must land in "unknown" by name, not vanish.
        from seamcheck.cli import _plain_args
        from seamcheck.cliflags import FLAGS

        for flag in FLAGS:
            for name in flag.names:
                argv = [name] if flag.kind == "flag" else [name, "x"]
                with self.subTest(flag=name):
                    unknown = _plain_args(argv)["unknown"]
                    if flag.plain:
                        self.assertNotIn(name, unknown)
                    else:
                        self.assertIn(name, unknown)


class UnknownFlagTests(SimpleTestCase):
    """A flag that works on Django and is silently ignored on Express is worse than one
    that does not exist: `check --since $BASE` used to read as working and compare
    against nothing. `_since` itself is fixed now (see SinceFlagParityTests above) - these
    exercise the general refusal with flags that still cannot be honoured here: an
    outright typo, and `--backfill`, a real seamcheck flag the plain door genuinely
    cannot run without Django (seamcheck.cliflags.FLAGS marks it plain=False - it needs
    django.setup() to read SEAMCHECK_CONFIG, twice over).
    """

    def test_a_typo_is_refused_not_silently_ignored(self):
        from seamcheck.cli import _run_without_django
        from seamcheck.exitcodes import EXIT_USAGE

        with mock.patch("seamcheck.cli._worth_scanning", return_value=True):
            code = _run_without_django(["--frobnicate", "x"], verbose=False)

        self.assertEqual(code, EXIT_USAGE)

    def test_a_real_django_only_flag_is_refused_not_silently_ignored(self):
        from seamcheck.cli import _run_without_django
        from seamcheck.exitcodes import EXIT_USAGE

        err = io.StringIO()
        with (
            mock.patch("seamcheck.cli._worth_scanning", return_value=True),
            redirect_stderr(err),
        ):
            code = _run_without_django(["--backfill", "30"], verbose=False)

        self.assertEqual(code, EXIT_USAGE)
        self.assertIn("--backfill", err.getvalue())
        self.assertIn("not supported on this project type", err.getvalue())

    def test_nothing_reaches_stdout_when_the_run_is_refused(self):
        # A refusal that also prints a report is a mixed signal - CI or an agent parsing
        # stdout must never see a report next to an EXIT_USAGE.
        from seamcheck.cli import _run_without_django

        out = io.StringIO()
        with (
            mock.patch("seamcheck.cli._worth_scanning", return_value=True),
            redirect_stdout(out),
            redirect_stderr(io.StringIO()),
        ):
            _run_without_django(["--frobnicate"], verbose=False)

        self.assertEqual(out.getvalue(), "")


class RepoRootPlainDoorTests(SimpleTestCase):
    """`--repo-root` used to be silently ignored on the plain (non-Django) door - every
    call in `_run_without_django` read `pathlib.Path.cwd()` directly, so
    `seamcheck scan --repo-root ../other` scanned the CURRENT directory instead, with no
    warning. Now that it is in the shared flag table, the plain door has to honour it
    properly: resolve it, and refuse clearly when the path does not exist.
    """

    def test_a_relative_repo_root_is_resolved_against_the_cwd_not_ignored(self):
        import os

        from seamcheck.cli import _resolve_repo_root

        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            (root / "project").mkdir()
            cwd = os.getcwd()
            os.chdir(tmp)
            try:
                resolved = _resolve_repo_root("project")
            finally:
                os.chdir(cwd)

        self.assertEqual(resolved, str((root / "project").resolve()))

    def test_a_nonexistent_repo_root_resolves_to_none(self):
        from seamcheck.cli import _resolve_repo_root

        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(_resolve_repo_root(str(pathlib.Path(tmp) / "nope")))

    def test_the_plain_door_actually_scans_the_named_repo_root_not_the_cwd(self):
        # The behavioural version of the test above: --repo-root ../other must change
        # WHICH project gets scanned, not just parse without error.
        import os

        from seamcheck.cli import _run_without_django

        with tempfile.TemporaryDirectory() as tmp:
            cwd_dir = pathlib.Path(tmp) / "cwd"
            other_dir = pathlib.Path(tmp) / "other"
            cwd_dir.mkdir()
            other_dir.mkdir()
            (other_dir / "package.json").write_text("{}")
            cwd = os.getcwd()
            os.chdir(cwd_dir)
            try:
                with (
                    mock.patch("seamcheck.cli._worth_scanning", return_value=True),
                    mock.patch("seamcheck.api.report", return_value="RENDERED") as report,
                    redirect_stdout(io.StringIO()) as out,
                ):
                    code = _run_without_django(["--repo-root", "../other"], verbose=False)
            finally:
                os.chdir(cwd)

        self.assertEqual(code, 0)
        self.assertEqual(report.call_args.kwargs["repo_root"], str(other_dir.resolve()))
        self.assertIn("RENDERED", out.getvalue())

    def test_a_repo_root_that_does_not_exist_is_refused_clearly(self):
        from seamcheck.cli import _run_without_django
        from seamcheck.exitcodes import EXIT_USAGE

        err = io.StringIO()
        with redirect_stderr(err):
            code = _run_without_django(["--repo-root", "definitely-not-here"], verbose=False)

        self.assertEqual(code, EXIT_USAGE)
        self.assertIn("--repo-root", err.getvalue())
        self.assertIn("does not exist", err.getvalue())
