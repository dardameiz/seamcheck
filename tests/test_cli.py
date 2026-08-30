import json
import tempfile
from io import StringIO
from pathlib import Path

from django.core.management import call_command
from django.test import SimpleTestCase, override_settings

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
