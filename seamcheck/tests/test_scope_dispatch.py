import io
import json
from contextlib import redirect_stdout
from unittest import mock

from django.core.management import call_command
from django.test import SimpleTestCase

from seamcheck.exitcodes import EXIT_CLEAN, EXIT_FINDINGS, EXIT_USAGE, _scope_exit_code


class ScopeExitCodeTests(SimpleTestCase):
    def test_ok_with_no_findings_is_clean(self):
        out = {"ok": True, "data": {"pages": {"p": {"findings": []}}}}

        self.assertEqual(_scope_exit_code(out), EXIT_CLEAN)

    def test_ok_with_a_finding_in_one_page_is_findings(self):
        out = {"ok": True, "data": {"pages": {
            "p1": {"findings": []}, "p2": {"findings": [{"id": "x"}]},
        }}}

        self.assertEqual(_scope_exit_code(out), EXIT_FINDINGS)

    def test_ok_with_no_pages_at_all_is_clean(self):
        out = {"ok": True, "data": {"pages": {}}}

        self.assertEqual(_scope_exit_code(out), EXIT_CLEAN)

    def test_a_failure_defers_to_the_ordinary_envelope_mapping(self):
        out = {"ok": False, "error": {"code": "no_upstream"}}

        self.assertEqual(_scope_exit_code(out), EXIT_USAGE)


class PlainDoorScopeDispatchTests(SimpleTestCase):
    def test_scope_prints_json_and_exits_clean_when_nothing_found(self):
        from seamcheck.cli import _run_without_django

        fake = {"ok": True, "data": {"scope": "commit", "changed_files": [], "pages": {}},
                "error": None}
        with (
            mock.patch("seamcheck.cli._worth_scanning", return_value=True),
            mock.patch("seamcheck.queries.scope", return_value=fake) as scope,
            redirect_stdout(io.StringIO()) as out,
        ):
            code = _run_without_django(["--scope", "commit"], verbose=False)

        self.assertEqual(code, EXIT_CLEAN)
        self.assertEqual(json.loads(out.getvalue()), fake)
        scope.assert_called_once()

    def test_scope_exits_findings_when_a_touched_page_has_one(self):
        from seamcheck.cli import _run_without_django

        fake = {"ok": True, "data": {"pages": {"p": {"findings": [{"id": "x"}]}}},
                "error": None}
        with (
            mock.patch("seamcheck.cli._worth_scanning", return_value=True),
            mock.patch("seamcheck.queries.scope", return_value=fake),
            redirect_stdout(io.StringIO()),
        ):
            code = _run_without_django(["--scope", "push"], verbose=False)

        self.assertEqual(code, EXIT_FINDINGS)

    def test_install_hooks_calls_the_installer_and_prints_its_message(self):
        from seamcheck.cli import _run_without_django

        with (
            mock.patch("seamcheck.cli._worth_scanning", return_value=True),
            mock.patch("seamcheck.hooks.install_hooks", return_value="Installed: pre-commit.") as installer,
            redirect_stdout(io.StringIO()) as out,
        ):
            code = _run_without_django(["--install-hooks"], verbose=False)

        self.assertEqual(code, EXIT_CLEAN)
        self.assertIn("Installed: pre-commit.", out.getvalue())
        installer.assert_called_once()

    def test_scope_with_serve_calls_serve_scoped_maps_instead_of_printing_json(self):
        from seamcheck.cli import _run_without_django

        with (
            mock.patch("seamcheck.cli._worth_scanning", return_value=True),
            mock.patch("seamcheck.scopedserve.serve_scoped_maps") as served,
            mock.patch("seamcheck.queries.scope") as scope,
        ):
            code = _run_without_django(["--scope", "commit", "--serve"], verbose=False)

        self.assertEqual(code, EXIT_CLEAN)
        served.assert_called_once()
        self.assertIn("local_only", served.call_args.kwargs)
        scope.assert_not_called()


class DjangoDoorScopeDispatchTests(SimpleTestCase):
    def test_scope_dispatches_through_the_management_command_too(self):
        fake = {"ok": True, "data": {"pages": {}}, "error": None}
        with (
            mock.patch("seamcheck.queries.scope", return_value=fake) as scope,
            redirect_stdout(io.StringIO()) as out,
        ):
            call_command("seamcheck", "--scope", "commit")

        self.assertEqual(json.loads(out.getvalue()), fake)
        scope.assert_called_once()

    def test_scope_raises_systemexit_findings_when_there_is_one(self):
        fake = {"ok": True, "data": {"pages": {"p": {"findings": [{"id": "x"}]}}},
                "error": None}
        with (
            mock.patch("seamcheck.queries.scope", return_value=fake),
            redirect_stdout(io.StringIO()),
            self.assertRaises(SystemExit) as ctx,
        ):
            call_command("seamcheck", "--scope", "push")

        self.assertEqual(ctx.exception.code, EXIT_FINDINGS)

    def test_install_hooks_dispatches_through_the_management_command(self):
        with (
            mock.patch("seamcheck.hooks.install_hooks", return_value="Installed: pre-push.") as installer,
            redirect_stdout(io.StringIO()) as out,
        ):
            call_command("seamcheck", "--install-hooks")

        self.assertIn("Installed: pre-push.", out.getvalue())
        installer.assert_called_once()

    def test_scope_with_serve_calls_serve_scoped_maps_instead_of_printing_json(self):
        with (
            mock.patch("seamcheck.scopedserve.serve_scoped_maps") as served,
            mock.patch("seamcheck.queries.scope") as scope,
        ):
            call_command("seamcheck", "--scope", "push", "--serve")

        served.assert_called_once()
        self.assertIn("local_only", served.call_args.kwargs)
        scope.assert_not_called()
