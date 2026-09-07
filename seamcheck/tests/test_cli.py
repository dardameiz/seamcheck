import json
import tempfile
from io import StringIO
from pathlib import Path
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, override_settings

from seamcheck import api, exitcodes

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

    def test_explain_suggests_near_ids_on_a_miss(self):
        # queries.near() had no caller anywhere in the codebase; wired in here so the
        # 88.5-second "No symbol with id ..." finally says what you probably meant.
        output = self._run(
            "--explain", "view:seamcheck.tests.fixtures.fixture_views.get_thin"
        )

        self.assertIn("No symbol", output)
        self.assertIn("Did you mean", output)
        self.assertIn("view:seamcheck.tests.fixtures.fixture_views.get_thing", output)

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

    def test_a_bare_check_scans_once(self):
        # _check() built its own graph (for the --since branch and the summary) and then
        # called api.check(repo_root) with no graph, which scanned a second time - about
        # 168 seconds on the reference project for one command. Count calls rather than
        # asserting "it still works", which would not catch a regression back to two scans.
        calls = []
        real_scan = api.scan

        def counting_scan(*args, **kwargs):
            calls.append(1)
            return real_scan(*args, **kwargs)

        with mock.patch("seamcheck.api.scan", side_effect=counting_scan), \
             self.assertRaises(SystemExit):
            call_command("seamcheck", "--check", stdout=StringIO(), stderr=StringIO())

        self.assertEqual(len(calls), 1)

    def test_triage_without_status_is_rejected(self):
        with self.assertRaises(SystemExit) as raised:
            self._run("--triage", "view:whatever")

        # A missing disposition is the command being wrong, not "no baseline to compare
        # against" - it must be EXIT_USAGE, never EXIT_NO_BASELINE's `2`.
        self.assertEqual(int(str(raised.exception.code)), exitcodes.EXIT_USAGE)

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

    def test_check_composes_with_sarif_format_scans_once(self):
        # `check --format sarif` is the exact Gate step docs/ci.md prescribes. `_report()`
        # used to return early through the sarif/github branch BEFORE the `if graph is
        # None: graph = scan(...)` line, discarding the graph `--check` had already built
        # and paying for a second ~168-second scan through `_findings_report` ->
        # `queries.findings` to render the very digest the first scan could answer. Count
        # calls rather than asserting "it still works", which would not catch a
        # regression back to two scans.
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
                "seamcheck", "--check", "--format", "sarif",
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
        #
        # This branch now shares gate_code()'s exact NO_BASELINE-prefix match (see
        # CheckSinceWithFormatExitCodeTests below, which already required it) instead of
        # its own hand-rolled "any truthy message means no baseline". The literal string
        # "no baseline" this test used to mock is not what `api.diff_against` actually
        # ever returns - only the old, looser ladder tolerated it - so the mock is updated
        # to the real constant rather than the production code being loosened back to
        # match an unrealistic mock.
        from seamcheck.exitcodes import NO_BASELINE

        with mock.patch.object(api, "diff_against",
                               return_value=(None, "abc", f"{NO_BASELINE} for abc yet.")):
            output, code = self._run("--check", "--since", "abc")

        self.assertIn(NO_BASELINE, output)
        self.assertEqual(code, 2)


@override_settings(SEAMCHECK_CONFIG=_CONFIG)
class CheckSinceWithFormatExitCodeTests(SimpleTestCase):
    """`check --since REF --format sarif` is the exact docs/ci.md Gate step: the digest
    (SARIF, markdown, whatever) goes to the pull request, the exit code gates the build.

    `--format` routes through `_exit_on_check`, a second implementation of the same gate
    that called `api.check()` with no way to pass `since` at all - so it always diffed
    against HEAD, never called `gate_code()`, and could never return EXIT_NO_BASELINE.
    Reproduced against a real project (pointlessbutton): `--check --since <sha with no
    stored snapshot> --format sarif` exited 0, not 2."""

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

    def test_a_gate_with_no_baseline_exits_2_even_with_a_format(self):
        # gate_code() matches the exact NO_BASELINE prefix, unlike _check()'s own
        # any-truthy-message check - use the real constant so this exercises the actual
        # decision this call site now makes.
        from seamcheck.exitcodes import NO_BASELINE

        with mock.patch.object(api, "diff_against",
                               return_value=(None, "abc", f"{NO_BASELINE} for abc yet.")):
            _, code = self._run("--check", "--since", "abc", "--format", "sarif")

        self.assertEqual(code, 2)

    def test_a_gate_that_found_nothing_new_passes_even_with_an_existing_backlog(self):
        # `has_blocking_findings` (the bare-check question, "any finding at all") would
        # say True here - the whole point of --since is that an existing backlog must not
        # block a build that added nothing to it.
        with (
            mock.patch.object(api, "diff_against", return_value=(self._diff(), "abc", "")),
            mock.patch.object(api, "has_blocking_findings", return_value=True),
        ):
            _, code = self._run("--check", "--since", "abc", "--format", "sarif")

        self.assertEqual(code, 0)

    def test_a_gate_that_found_something_new_fails_with_a_format_too(self):
        symbol = mock.Mock(id="url:gone")
        with mock.patch.object(api, "diff_against",
                               return_value=(self._diff(new_unresolved=[symbol]), "abc", "")):
            _, code = self._run("--check", "--since", "abc", "--format", "sarif")

        self.assertEqual(code, 1)


@override_settings(SEAMCHECK_CONFIG=_CONFIG)
class CheckSinceBadRefExitCodeTests(SimpleTestCase):
    """A `since` ref that cannot be resolved AT ALL - a typo, a CI variable that came
    through empty - is a USAGE error, not "no baseline yet" (that needs a real commit
    with nothing stored for it). Both `_check()` and `_exit_on_check()` used to treat any
    non-empty message from `diff_against` as "no baseline" and exit 2 either way, so a
    mistyped `$BASE_SHA` in CI silently read as a clean first run instead of the broken
    invocation it is. Uses a real nonexistent ref rather than mocking `diff_against`, so
    this also proves the real `git rev-parse` failure is what `sha == ""` actually means.
    """

    def _run(self, *args):
        out = StringIO()
        try:
            call_command("seamcheck", *args, stdout=out, stderr=StringIO())
        except SystemExit as exit_code:
            return out.getvalue(), int(str(exit_code.code))
        return out.getvalue(), 0

    def test_a_nonexistent_ref_exits_usage_and_names_the_ref(self):
        output, code = self._run("--check", "--since", "totally-bogus-ref-xyz")

        self.assertEqual(code, 3)
        self.assertIn("totally-bogus-ref-xyz", output)

    def test_a_nonexistent_ref_exits_usage_even_composed_with_a_format(self):
        out, err = StringIO(), StringIO()
        with override_settings(SEAMCHECK_CONFIG=_CONFIG), self.assertRaises(SystemExit) as raised:
            call_command(
                "seamcheck", "--check", "--since", "totally-bogus-ref-xyz",
                "--format", "sarif", stdout=out, stderr=err,
            )

        self.assertEqual(raised.exception.code, 3)
        self.assertIn("totally-bogus-ref-xyz", err.getvalue())


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
        # A failed triage (nothing to undo, here) is a bad argument, not "no baseline to
        # compare against" - EXIT_USAGE, not the bare `2` that used to collide with
        # EXIT_NO_BASELINE (check --since's own, unrelated question).
        self.assertEqual(again, exitcodes.EXIT_USAGE)

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


@override_settings(SEAMCHECK_CONFIG=_CONFIG)
class DiffCommandTests(SimpleTestCase):
    """`seamcheck diff --since REF` - `queries.diff()` had no command wired to it anywhere,
    reachable only as a library call, despite the plan's own before/after table promising
    it. Wired exactly as `--findings` is, on both doors, with --limit/--cursor/--refresh.
    """

    def test_the_django_door_wires_diff_with_since_limit_and_cursor(self):
        out = StringIO()
        with mock.patch("seamcheck.queries.diff",
                        return_value={"ok": True, "data": {}}) as diff:
            call_command("seamcheck", "--diff", "--since", "origin/main", "--limit", "10",
                        "--cursor", "5", stdout=out, stderr=StringIO())

        diff.assert_called_once_with(".", "origin/main", 10, "5", refresh=False)
        self.assertIn('"ok": true', out.getvalue())

    def test_the_django_door_defaults_since_to_head_tilde_1(self):
        with mock.patch("seamcheck.queries.diff", return_value={"ok": True}) as diff:
            call_command("seamcheck", "--diff", stdout=StringIO(), stderr=StringIO())

        diff.assert_called_once_with(".", "HEAD~1", 25, "", refresh=False)

    def test_the_django_door_forwards_refresh(self):
        with mock.patch("seamcheck.queries.diff", return_value={"ok": True}) as diff:
            call_command("seamcheck", "--diff", "--refresh", stdout=StringIO(), stderr=StringIO())

        diff.assert_called_once_with(".", "HEAD~1", 25, "", refresh=True)

    def test_the_plain_door_wires_diff_too(self):
        import os

        from seamcheck.cli import _run_without_django

        with tempfile.TemporaryDirectory() as tmp:
            real_tmp = os.path.realpath(tmp)
            Path(tmp, "package.json").write_text("{}")
            cwd = os.getcwd()
            os.chdir(tmp)
            try:
                with (
                    mock.patch("seamcheck.cli._worth_scanning", return_value=True),
                    mock.patch("seamcheck.queries.diff",
                              return_value={"ok": True, "data": {}}) as diff,
                ):
                    code = _run_without_django(
                        ["--diff", "--since", "origin/main", "--limit", "10"], verbose=False
                    )
            finally:
                os.chdir(cwd)

        self.assertEqual(code, 0)
        diff.assert_called_once_with(real_tmp, "origin/main", 10, "", refresh=False)


class ExplainWithHintTests(SimpleTestCase):
    """`queries.near()` had no caller anywhere in the codebase. `api.explain_with_hint()`
    is the one wired into both CLI doors and the MCP server - direct, graph-only tests
    here rather than a full scan, since the matching itself is `queries.near`'s job
    (already tested in test_queries.py); this only checks the wiring and the wording."""

    def _graph(self):
        from seamcheck.graph import Graph, Status, Symbol

        return Graph(symbols=[
            Symbol(id="url:api/submit/", kind="url", label="api/submit/", sub="",
                  file="app/urls.py", line=3, status=Status.CONNECTED, snippet="",
                  chain=[], note=""),
        ], edges=[])

    def test_a_correct_id_gets_no_hint_section(self):
        text = api.explain_with_hint(self._graph(), "url:api/submit/")

        self.assertIn("api/submit/", text)
        self.assertNotIn("Did you mean", text)

    def test_a_near_miss_names_the_real_id(self):
        text = api.explain_with_hint(self._graph(), "url:api/submit")

        self.assertIn("No symbol", text)
        self.assertIn("Did you mean", text)
        self.assertIn("url:api/submit/", text)

    def test_nothing_close_enough_gets_no_hint_section(self):
        text = api.explain_with_hint(self._graph(), "totally-unrelated-xyz")

        self.assertIn("No symbol", text)
        self.assertNotIn("Did you mean", text)

    def test_the_plain_non_django_door_offers_the_same_hint(self):
        # Both CLI doors call the same api.explain_with_hint - proved here by observing
        # the plain (non-Django) door's own output, not by re-testing the matching logic.
        import io
        import os
        from contextlib import redirect_stdout

        from seamcheck.cli import _run_without_django

        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "package.json").write_text("{}")
            cwd = os.getcwd()
            os.chdir(tmp)
            try:
                with (
                    mock.patch("seamcheck.cli._worth_scanning", return_value=True),
                    mock.patch("seamcheck.api.scan", return_value=self._graph()),
                    redirect_stdout(io.StringIO()) as out,
                ):
                    code = _run_without_django(["--explain", "url:api/submit"], verbose=False)
            finally:
                os.chdir(cwd)

        self.assertEqual(code, 0)
        self.assertIn("Did you mean", out.getvalue())
        self.assertIn("url:api/submit/", out.getvalue())
