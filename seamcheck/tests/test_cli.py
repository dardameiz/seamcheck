import json
import tempfile
from io import StringIO
from pathlib import Path
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, override_settings

from seamcheck import api

FIXTURES_DIR = str(Path(__file__).parent / "fixtures")
_CONFIG = {
    "urlconf_module": "seamcheck.tests.fixtures.fixture_urls",
    "js_entry_files": ["fixture_entry.js"],
    "js_project_root": FIXTURES_DIR,
    "entry_point_files": [],
}


@override_settings(SEAMCHECK_CONFIG=_CONFIG)
class DumpConnectivityMapTests(SimpleTestCase):
    def _run(self, *args):
        out = StringIO()
        call_command("seamcheck", *args, stdout=out, stderr=StringIO())
        return out.getvalue()

    def test_json_flag_prints_a_valid_graph(self):
        data = json.loads(self._run("--json"))

        self.assertIn("symbols", data)
        self.assertTrue(any(s["label"] == "get_thing" for s in data["symbols"]))

    def test_explain_prints_the_symbol_chain(self):
        output = self._run(
            "--explain", "view:seamcheck.tests.fixtures.fixture_views.get_thing"
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
                "seamcheck", "--check", "--repo-root", tmp,
                stdout=out, stderr=StringIO(),
            )

        self.assertIn("No baseline", out.getvalue())

    def test_check_exits_nonzero_when_a_finding_blocks(self):
        # The fixture graph contains a fetch to a URL that does not exist, so --check
        # must fail the build rather than print and pass.
        with self.assertRaises(SystemExit) as raised:
            call_command("seamcheck", "--check", stdout=StringIO(), stderr=StringIO())

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
        with override_settings(SEAMCHECK_CONFIG=_CONFIG):
            call_command("seamcheck", *args, stdout=out, stderr=StringIO())
        return out.getvalue()

    def test_markdown_format_prints_a_markdown_report(self):
        self.assertIn("## Seamcheck", self._run("--format", "markdown"))

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

            self.assertIn("## Seamcheck", Path(path).read_text())
            self.assertNotIn("## Seamcheck", printed)

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
        with override_settings(SEAMCHECK_CONFIG=_CONFIG), self.assertRaises(SystemExit) as raised:
            call_command(
                "seamcheck", "--check", "--format", "markdown",
                stdout=out, stderr=StringIO(),
            )

        self.assertEqual(raised.exception.code, 1)
        self.assertIn("## Seamcheck", out.getvalue())

    def test_check_composes_with_format_and_out_writes_file_and_keeps_exit_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "r.md")
            out = StringIO()
            with override_settings(SEAMCHECK_CONFIG=_CONFIG), self.assertRaises(SystemExit) as raised:
                call_command(
                    "seamcheck", "--check", "--format", "markdown", "--out", path,
                    stdout=out, stderr=StringIO(),
                )

            self.assertEqual(raised.exception.code, 1)
            self.assertIn("## Seamcheck", Path(path).read_text())

    def test_a_document_with_no_destination_lands_on_disk_and_says_where(self):
        # Never stdout: dumping a whole document into a terminal is the ~1MB (for map,
        # 3.8MB) wall of markup that reads as the command being broken. It used to raise
        # instead, which made `seamcheck map` fail on being typed; a default path plus a
        # line naming it is the same protection without the dead end.
        with tempfile.TemporaryDirectory() as repo:
            out = StringIO()
            with override_settings(SEAMCHECK_CONFIG=_CONFIG):
                call_command("seamcheck", "--format", "html", "--repo-root", repo,
                             stdout=out, stderr=StringIO())

            written = Path(repo, "docs", "maps", "connectivity-report.html")
            self.assertTrue(written.is_file())
            self.assertIn("wrote", out.getvalue())
            self.assertIn("connectivity-report.html", out.getvalue())

    def test_the_written_path_is_not_offered_as_a_file_url(self):
        # VS Code's terminal opens a file:// URL inside VS Code rather than handing it to
        # a browser, so the one clickable thing in the output went somewhere nobody asked
        # for. The serving path prints an http:// link, which it does hand over.
        with tempfile.TemporaryDirectory() as repo:
            out = StringIO()
            with override_settings(SEAMCHECK_CONFIG=_CONFIG):
                call_command("seamcheck", "--format", "html", "--repo-root", repo,
                             stdout=out, stderr=StringIO())

            self.assertNotIn("file://", out.getvalue())

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
            override_settings(SEAMCHECK_CONFIG=_CONFIG),
            mock.patch("seamcheck.api.scan", side_effect=counting_scan),
            self.assertRaises(SystemExit),
        ):
            call_command(
                "seamcheck", "--check", "--format", "markdown",
                stdout=StringIO(), stderr=StringIO(),
            )

        self.assertEqual(len(calls), 1)


@override_settings(SEAMCHECK_CONFIG=_CONFIG)
class CheckSinceExitCodeTests(SimpleTestCase):
    """`--check --since` is the CI gate. It printed findings and exited 0."""

    def _run(self, *args):
        out = StringIO()
        try:
            call_command("seamcheck", *args, stdout=out, stderr=StringIO())
        except SystemExit as exit_code:
            return out.getvalue(), int(str(exit_code.code))
        return out.getvalue(), 0

    def _diff(self, **kwargs):
        from seamcheck.diff import DiffResult

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


class ServingTests(SimpleTestCase):
    """`map` serves by default now, so the file and the server have to coexist."""

    def _run(self, repo, *extra):
        out, err = StringIO(), StringIO()
        with override_settings(SEAMCHECK_CONFIG=_CONFIG):
            call_command("seamcheck", "--format", "map", "--repo-root", repo,
                         *extra, stdout=out, stderr=err)
        return out.getvalue()

    def test_the_file_is_written_before_the_server_starts(self):
        # Serving used to return early, so the one command that renders the UI left
        # nothing behind once you pressed Ctrl-C - and the artifact is the thing you
        # commit, diff, and open again tomorrow.
        served = {}

        def _fake_serve(self_, text, fmt, **kwargs):
            served["file_exists"] = Path(repo, "docs", "maps", "connectivity-map.html").is_file()

        with tempfile.TemporaryDirectory() as repo, mock.patch(
            "seamcheck.management.commands.seamcheck.Command._serve", _fake_serve
        ):
            self._run(repo, "--serve")

            self.assertTrue(served["file_exists"])

    def test_no_serve_writes_the_file_and_stops(self):
        # What CI and any script wants: the artifact, not a process that never exits.
        with tempfile.TemporaryDirectory() as repo, mock.patch(
            "seamcheck.management.commands.seamcheck.Command._serve"
        ) as serve:
            output = self._run(repo, "--serve", "--no-serve")

            serve.assert_not_called()
            self.assertTrue(Path(repo, "docs", "maps", "connectivity-map.html").is_file())
            self.assertIn("wrote", output)

    def test_local_only_reaches_the_server(self):
        with tempfile.TemporaryDirectory() as repo, mock.patch(
            "seamcheck.management.commands.seamcheck.Command._serve"
        ) as serve:
            self._run(repo, "--serve", "--local-only")

            self.assertTrue(serve.call_args.kwargs["local_only"])

    def test_out_dash_still_prints_and_never_serves(self):
        # An explicit "give me it on stdout" must not also hold a socket open.
        with tempfile.TemporaryDirectory() as repo, mock.patch(
            "seamcheck.management.commands.seamcheck.Command._serve"
        ) as serve:
            output = self._run(repo, "--serve", "--out", "-")

            serve.assert_not_called()
            self.assertIn("<!doctype html>", output)


class ServeAddressTests(SimpleTestCase):
    def test_both_addresses_describe_the_same_document(self):
        # Loopback is the one to click here; the LAN one is the one to type on a phone.
        # Different answers, same port and same token, or they are two documents.
        from seamcheck.serve import serve_addresses

        server, addresses = serve_addresses("<p>hi</p>")
        try:
            port = server.server_port
            self.assertIn(f":{port}/", addresses["local"])
            self.assertIn(f":{port}/", addresses["lan"])
            token = addresses["local"].rsplit("/", 1)[1]
            self.assertEqual(addresses["lan"].rsplit("/", 1)[1], token)
        finally:
            server.server_close()

    def test_local_only_offers_no_lan_address(self):
        from seamcheck.serve import serve_addresses

        server, addresses = serve_addresses("<p>hi</p>", host="127.0.0.1")
        try:
            self.assertNotIn("lan", addresses)
        finally:
            server.server_close()


@override_settings(SEAMCHECK_CONFIG=_CONFIG)
class UndoTests(SimpleTestCase):
    def _run(self, *args):
        out = StringIO()
        try:
            call_command("seamcheck", *args, stdout=out, stderr=StringIO())
        except SystemExit as exit_code:
            return out.getvalue(), int(str(exit_code.code))
        return out.getvalue(), 0

    def test_undo_removes_the_mark_and_a_second_undo_is_refused(self):
        from seamcheck.triage import load_triage

        with tempfile.TemporaryDirectory() as tmp:
            gone = "fetch:/api/does-not-exist/"
            self._run("--triage", gone, "--wrong", "consumed-by-dependency", "--repo-root", tmp)
            self.assertEqual(len(load_triage(tmp)), 1)
            out, code = self._run("--triage", gone, "--undo", "--repo-root", tmp)
            _, again = self._run("--triage", gone, "--undo", "--repo-root", tmp)

        self.assertEqual(code, 0, out)
        self.assertIn("raised again", out)
        self.assertEqual(again, 2)

    def test_check_names_a_returned_finding_with_its_date_and_reason(self):
        from seamcheck.triage import TriageEntry, TriageStatus, load_triage, save_triage

        with tempfile.TemporaryDirectory() as tmp:
            save_triage([TriageEntry(
                symbol_id="fetch:/api/does-not-exist/", fingerprint="older-evidence",
                status=TriageStatus.APPROVED, who="alice", when="2026-08-20", reason="",
                why="consumed-by-dependency",
            )], tmp)
            out, _ = self._run("--check", "--repo-root", tmp)
            stamped = load_triage(tmp)[0].expired

        self.assertIn("returned: fetch:/api/does-not-exist/", out)
        self.assertIn("alice", out)
        self.assertIn("consumed-by-dependency", out)
        self.assertIn("--undo", out)
        self.assertNotIn("mark outlived its finding", out)
        # The first scan to notice stamps the day; the file is the memory.
        self.assertTrue(stamped)
