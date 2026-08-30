import json
import tempfile
from io import StringIO
from pathlib import Path
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, override_settings

from signal_map import api

FIXTURES_DIR = str(Path(__file__).parent / "fixtures")
_CONFIG = {
    "urlconf_module": "signal_map.tests.fixtures.fixture_urls",
    "js_entry_files": ["fixture_entry.js"],
    "js_project_root": FIXTURES_DIR,
    "entry_point_files": [],
}


@override_settings(SIGNAL_MAP_CONFIG=_CONFIG)
class DumpConnectivityMapTests(SimpleTestCase):
    def _run(self, *args):
        out = StringIO()
        call_command("dump_connectivity_map", *args, stdout=out, stderr=StringIO())
        return out.getvalue()

    def test_json_flag_prints_a_valid_graph(self):
        data = json.loads(self._run("--json"))

        self.assertIn("symbols", data)
        self.assertTrue(any(s["label"] == "get_thing" for s in data["symbols"]))

    def test_explain_prints_the_symbol_chain(self):
        output = self._run(
            "--explain", "view:signal_map.tests.fixtures.fixture_views.get_thing"
        )

        self.assertIn("get_thing", output)
        self.assertIn("status", output)

    def test_explain_reports_unknown_symbol_clearly(self):
        output = self._run("--explain", "view:nope")

        self.assertIn("No symbol", output)

    def test_check_says_so_plainly_when_no_baseline_snapshot_exists(self):
        # Fabricating a diff against a snapshot that was never taken would report the
        # entire graph as "new" on the first run.
        # A temp root has no stored snapshots, so this asserts the contract rather
        # than whatever the developer's working tree happens to contain.
        out = StringIO()
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(SystemExit):
            call_command(
                "dump_connectivity_map", "--check", "--repo-root", tmp,
                stdout=out, stderr=StringIO(),
            )

        self.assertIn("No baseline", out.getvalue())

    def test_check_exits_nonzero_when_a_finding_blocks(self):
        # The fixture graph contains a fetch to a URL that does not exist, so --check
        # must fail the build rather than print and pass.
        with self.assertRaises(SystemExit) as raised:
            call_command("dump_connectivity_map", "--check", stdout=StringIO(), stderr=StringIO())

        self.assertEqual(raised.exception.code, 1)

    def test_triage_without_status_is_rejected(self):
        with self.assertRaises(SystemExit):
            self._run("--triage", "view:whatever")

    def test_out_without_format_is_rejected(self):
        # --out is only ever read inside _format_report(); every other path (bare
        # --check, --since, the no-flags summary) used to silently ignore it, so
        # "--check --out report.md" wrote nothing and said nothing about why.
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "report.md")
            with self.assertRaises(CommandError) as raised:
                self._run("--check", "--out", path)

        self.assertIn("--out", str(raised.exception))

    def test_json_flag_composes_with_out_via_the_shared_format_path(self):
        # --json used to be a second, drifting copy of the "--format json" dump (one
        # always re-scanned, the other reused a shared graph) and, unlike --format
        # json, never looked at --out at all. Prove the merge by observing --out now
        # actually being honoured for --json too.
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "g.json")
            printed = self._run("--json", "--out", path)

            data = json.loads(Path(path).read_text())
            self.assertIn("symbols", data)
            self.assertNotIn("symbols", printed)


class ReportFormatTests(SimpleTestCase):
    def _run(self, *args):
        out = StringIO()
        with override_settings(SIGNAL_MAP_CONFIG=_CONFIG):
            call_command("dump_connectivity_map", *args, stdout=out, stderr=StringIO())
        return out.getvalue()

    def test_markdown_format_prints_a_markdown_report(self):
        self.assertIn("## Signal Map", self._run("--format", "markdown"))

    def test_html_format_prints_a_complete_document(self):
        self.assertIn("<!doctype html>", self._run("--format", "html", "--out", "-"))

    def test_json_format_still_returns_the_whole_graph(self):
        # --json has existing callers; changing what it returns would break them.
        data = json.loads(self._run("--format", "json"))

        self.assertIn("symbols", data)

    def test_out_writes_to_a_file_instead_of_stdout(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "r.md")
            printed = self._run("--format", "markdown", "--out", path)

            self.assertIn("## Signal Map", Path(path).read_text())
            self.assertNotIn("## Signal Map", printed)

    def test_an_unknown_format_is_rejected(self):
        with self.assertRaises(SystemExit):
            self._run("--format", "yaml")

    def test_an_empty_format_value_is_rejected_not_silently_treated_as_unset(self):
        # `--format ""` is falsy, so a bare `if options["format"]:` used to fall through
        # to the no-flags summary path, which writes the whole (18 MB on a real
        # project) connectivity map to disk - silently, with no hint --format was even
        # seen.
        with self.assertRaises(CommandError) as raised:
            self._run("--format", "")

        self.assertIn("--format", str(raised.exception))

    def test_check_composes_with_format_prints_digest_and_keeps_exit_code(self):
        # --check --format markdown is the CI use case: post the digest as a comment,
        # fail the build. Asserting only the exit code would pass even if nothing were
        # printed - assert both, in the same test, against the fixture's real finding.
        out = StringIO()
        with override_settings(SIGNAL_MAP_CONFIG=_CONFIG), self.assertRaises(SystemExit) as raised:
            call_command(
                "dump_connectivity_map", "--check", "--format", "markdown",
                stdout=out, stderr=StringIO(),
            )

        self.assertEqual(raised.exception.code, 1)
        self.assertIn("## Signal Map", out.getvalue())

    def test_check_composes_with_format_and_out_writes_file_and_keeps_exit_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "r.md")
            out = StringIO()
            with override_settings(SIGNAL_MAP_CONFIG=_CONFIG), self.assertRaises(SystemExit) as raised:
                call_command(
                    "dump_connectivity_map", "--check", "--format", "markdown", "--out", path,
                    stdout=out, stderr=StringIO(),
                )

            self.assertEqual(raised.exception.code, 1)
            self.assertIn("## Signal Map", Path(path).read_text())

    def test_html_with_no_out_and_no_report_output_raises_command_error(self):
        # The design doc requires --out for html when report_output isn't configured -
        # falling through to stdout would dump the whole ~1MB document into a terminal,
        # exactly what the config-fallback branch two lines above exists to prevent.
        with override_settings(SIGNAL_MAP_CONFIG=_CONFIG), self.assertRaises(CommandError) as raised:
            call_command("dump_connectivity_map", "--format", "html", stdout=StringIO(), stderr=StringIO())

        self.assertIn("--out", str(raised.exception))

    def test_since_composes_with_format_using_the_given_ref_not_head(self):
        # An unresolvable ref surfaces its own name in the "No baseline" message, which
        # only happens if --since actually reached api.report() rather than the default
        # "HEAD" - this is the forwarding proof, not a real second-snapshot round trip.
        output = self._run("--format", "markdown", "--since", "not-a-real-ref-xyz")

        self.assertIn("not-a-real-ref-xyz", output)

    def test_check_composes_with_format_scans_once(self):
        # --check --format markdown must not pay for two full scans just because two
        # flags are set; count calls rather than asserting "it still works", which
        # would not catch a regression back to scanning twice.
        calls = []
        real_scan = api.scan

        def counting_scan(*args, **kwargs):
            calls.append(1)
            return real_scan(*args, **kwargs)

        with (
            override_settings(SIGNAL_MAP_CONFIG=_CONFIG),
            mock.patch("signal_map.api.scan", side_effect=counting_scan),
            self.assertRaises(SystemExit),
        ):
            call_command(
                "dump_connectivity_map", "--check", "--format", "markdown",
                stdout=StringIO(), stderr=StringIO(),
            )

        self.assertEqual(len(calls), 1)


@override_settings(SIGNAL_MAP_CONFIG=_CONFIG)
class CheckSinceExitCodeTests(SimpleTestCase):
    """`--check --since` is the CI gate. It printed findings and exited 0."""

    def _run(self, *args):
        out = StringIO()
        try:
            call_command("dump_connectivity_map", *args, stdout=out, stderr=StringIO())
        except SystemExit as exit_code:
            return out.getvalue(), int(str(exit_code.code))
        return out.getvalue(), 0

    def _diff(self, **kwargs):
        from signal_map.diff import DiffResult

        return DiffResult(**{"new_unresolved": [], "new_unused": [], "resolved": [],
                             "triage_invalidated": [], **kwargs})

    def test_a_gate_that_found_something_new_fails_the_build(self):
        symbol = mock.Mock(id="url:gone")
        with mock.patch.object(api, "diff_against",
                               return_value=(self._diff(new_unresolved=[symbol]), "abc", "")):
            output, code = self._run("--check", "--since", "abc")

        self.assertIn("new_unresolved: url:gone", output)
        self.assertEqual(code, 1)

    def test_a_gate_that_found_nothing_new_passes(self):
        with mock.patch.object(api, "diff_against", return_value=(self._diff(), "abc", "")):
            _, code = self._run("--check", "--since", "abc")

        self.assertEqual(code, 0)

    def test_since_without_check_only_reports_and_never_fails(self):
        symbol = mock.Mock(id="url:gone")
        with mock.patch.object(api, "diff_against",
                               return_value=(self._diff(new_unresolved=[symbol]), "abc", "")):
            output, code = self._run("--since", "abc")

        self.assertIn("new_unresolved: url:gone", output)
        self.assertEqual(code, 0)

    def test_a_gate_with_no_baseline_did_not_pass_it_did_not_run(self):
        # Exiting 0 here tells CI the build is clean when nothing was compared at all.
        with mock.patch.object(api, "diff_against", return_value=(None, "abc", "no baseline")):
            output, code = self._run("--check", "--since", "abc")

        self.assertIn("no baseline", output)
        self.assertEqual(code, 2)
