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
