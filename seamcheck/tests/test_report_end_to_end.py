import subprocess
import tempfile
from pathlib import Path
from unittest import mock

from django.test import SimpleTestCase, override_settings

from seamcheck import api
from seamcheck.envelope import TooLarge
from seamcheck.graph import Graph, Status, Symbol
from seamcheck.mcp_server import seamcheck_report
from seamcheck.snapshot import save_snapshot

FIXTURES_DIR = str(Path(__file__).parent / "fixtures")
_CONFIG = {
    "urlconf_module": "seamcheck.tests.fixtures.fixture_urls",
    "js_entry_files": ["fixture_entry.js"],
    "js_project_root": FIXTURES_DIR,
    "entry_point_files": [],
}


@override_settings(SEAMCHECK_CONFIG=_CONFIG)
class EndToEndReportTests(SimpleTestCase):
    def test_every_format_renders_from_a_real_scan(self):
        with self.subTest(fmt="terminal"):
            # "Seamcheck" alone appears in all three renderers' output (markdown's
            # "## Seamcheck" heading, html's <title>/<h1>), so it can't tell terminal
            # apart from a dispatch bug that wired "terminal" to another renderer.
            # Only the terminal renderer opens the string this way.
            self.assertTrue(api.report(".", "terminal").startswith("Seamcheck —"))

        with self.subTest(fmt="markdown"):
            self.assertIn("## Seamcheck", api.report(".", "markdown"))

        with self.subTest(fmt="html"):
            self.assertIn("<!doctype html>", api.report(".", "html"))

    def test_an_unknown_format_raises_with_the_allowed_list(self):
        with self.assertRaises(ValueError) as raised:
            api.report(".", "yaml")

        self.assertIn("markdown", str(raised.exception))

    def test_the_refusal_names_every_real_format_not_just_the_renderers_dict(self):
        # The validity check used to be `renderers.keys()` (terminal/markdown/html)
        # UNIONED with a second, hand-typed tuple of the other five - but the refusal
        # message was built from `renderers` alone, so a typo was told only a third of
        # the real menu existed. sarif/github/json/map/console are all real, accepted
        # values (see cliflags.FORMATS) that never appeared in their own error message.
        from seamcheck.cliflags import FORMATS

        with self.assertRaises(ValueError) as raised:
            api.report(".", "yaml")

        message = str(raised.exception)
        for fmt in FORMATS:
            with self.subTest(fmt=fmt):
                self.assertIn(fmt, message)

    def test_every_real_format_is_actually_accepted(self):
        # The other half of the same guarantee: FORMATS must not claim a value the
        # validity check would then refuse.
        from seamcheck.cliflags import FORMATS

        for fmt in FORMATS:
            with self.subTest(fmt=fmt):
                try:
                    api.report(".", fmt)
                except ValueError:
                    self.fail(f"{fmt!r} is in cliflags.FORMATS but api.report() refused it")

    def test_the_real_fixture_scan_renders_the_uncertain_gloss_sentence(self):
        # Not a test that uncertain symbols are excluded from groups/new_findings - that
        # guarantee is structural, in report.py's _FINDING_STATUSES, and cannot reach a
        # renderer at all. This only checks the gloss sentence text is present on a real
        # scan's output.
        out = api.report(".", "markdown")

        self.assertIn("no evidence either way", out.lower())

    def test_the_mcp_tool_returns_a_rendered_report(self):
        self.assertIn("## Seamcheck", seamcheck_report(repo_root="."))

    def test_the_mcp_tool_threads_fmt_through_rather_than_ignoring_it(self):
        # Same discriminating marker as the terminal case above: proves the "fmt"
        # argument the wrapper takes actually reaches api.report, not just repo_root.
        self.assertTrue(seamcheck_report(fmt="terminal", repo_root=".").startswith("Seamcheck —"))


@override_settings(SEAMCHECK_CONFIG=_CONFIG)
class JsonSizeGateTests(SimpleTestCase):
    """api.report() is the one implementation the management command, the plain CLI door
    and the MCP server all call for fmt="json" - so the size gate lives here, once, rather
    than duplicated per caller. It raises TooLarge instead of exiting the process: this is
    a library function the MCP server calls too, and a server has no process to exit."""

    def test_past_the_size_gate_it_raises_with_the_size_on_it(self):
        with mock.patch("seamcheck.api.JSON_WARN", 1), self.assertRaises(TooLarge) as raised:
            api.report(".", "json")

        self.assertGreater(raised.exception.size_bytes, 1)
        self.assertGreater(raised.exception.tokens, 0)

    def test_full_true_is_the_only_way_past_the_gate(self):
        with mock.patch("seamcheck.api.JSON_WARN", 1):
            text = api.report(".", "json", full=True)

        self.assertIn('"symbols"', text)


def _git(repo_root, *args):
    return subprocess.run(
        ["git", "-C", repo_root, *args], capture_output=True, text=True, check=True,
    ).stdout


def _unresolved_symbol(id_):
    return Symbol(
        id=id_, kind="url", label=id_, sub="", file="a.py", line=1,
        status=Status.UNRESOLVED, snippet=f"<{id_}>", chain=[id_], note="",
    )


class BaselineShaWiringTests(SimpleTestCase):
    def test_report_baseline_sha_is_the_resolved_ref_not_head(self):
        # api.py substituted the just-scanned commit's sha (current_git_sha) for
        # baseline_sha unconditionally, discarding the sha diff_against() actually
        # resolved `ref` to - so `--since <older-ref>` named the wrong commit in both
        # the report header and the "NEW SINCE" heading. Every renderer-suite fixture
        # hand-builds Report(baseline_sha=..., git_sha=...) directly, which proves the
        # renderers read the field but hides that the producer (api.report) never set
        # it correctly - so this exercises the real producer, api.report(), against a
        # real stored snapshot in a real (temporary, throwaway) git repo.
        with tempfile.TemporaryDirectory() as tmp:
            _git(tmp, "init")
            _git(tmp, "config", "user.email", "t@example.com")
            _git(tmp, "config", "user.name", "t")
            (Path(tmp) / "f.txt").write_text("1")
            _git(tmp, "add", ".")
            _git(tmp, "commit", "-m", "one")
            baseline_sha = _git(tmp, "rev-parse", "HEAD").strip()

            (Path(tmp) / "f.txt").write_text("2")
            _git(tmp, "add", ".")
            _git(tmp, "commit", "-m", "two")
            head_sha = _git(tmp, "rev-parse", "HEAD").strip()
            self.assertNotEqual(baseline_sha, head_sha)

            save_snapshot(Graph(symbols=[], edges=[]), baseline_sha, tmp)
            new_graph = Graph(symbols=[_unresolved_symbol("api/ghost/")], edges=[])

            out = api.report(tmp, "terminal", ref="HEAD~1", graph=new_graph)

        self.assertIn(f"Seamcheck — {head_sha[:12]}", out)
        self.assertIn(f"NEW SINCE {baseline_sha[:12]}", out)
        self.assertNotEqual(baseline_sha[:12], head_sha[:12])


class CachedScanRoutingTests(SimpleTestCase):
    """`check`, `report` and `unverified` used to call `scan()` directly whenever a
    caller had no graph in hand - MCP's seamcheck_check/_report/_unverified, and the
    plain CLI door's own check/report dispatch, neither of which pre-scan. That made the
    documented unverified -> explain -> triage -> check agent loop pay for four full,
    uncached scans - the entire efficiency argument the scan cache exists to deliver,
    unmet for the tool's own headline commands (Task 4's cached_scan() was reached only
    from queries.py and the MCP snapshot tool). They now go through the same
    scancache.cached_scan() symbols/findings/diff already use.

    A graph already in hand (both CLI doors' own "scan once, share it between the digest
    and the exit code" combo for `--check --format X`) must keep bypassing the cache
    lookup entirely - re-fetching it would be a second call for no reason, not a bug the
    cache itself could ever catch, so this pins it directly.
    """

    GRAPH = Graph(symbols=[], edges=[])

    def test_check_with_no_graph_goes_through_the_cache(self):
        with (
            mock.patch("seamcheck.scancache.cached_scan",
                       return_value=(self.GRAPH, {"cached": True})) as cached_scan,
            mock.patch("seamcheck.api.scan") as scan,
        ):
            api.check(".")

        cached_scan.assert_called_once_with(".")
        scan.assert_not_called()

    def test_check_with_a_graph_already_in_hand_never_touches_the_cache(self):
        with mock.patch("seamcheck.scancache.cached_scan") as cached_scan:
            api.check(".", graph=self.GRAPH)

        cached_scan.assert_not_called()

    def test_report_with_no_graph_goes_through_the_cache(self):
        with (
            mock.patch("seamcheck.scancache.cached_scan",
                       return_value=(self.GRAPH, {"cached": True})) as cached_scan,
            mock.patch("seamcheck.api.scan") as scan,
        ):
            api.report(".", "terminal")

        cached_scan.assert_called_once_with(".")
        scan.assert_not_called()

    def test_report_with_a_graph_already_in_hand_never_touches_the_cache(self):
        with mock.patch("seamcheck.scancache.cached_scan") as cached_scan:
            api.report(".", "terminal", graph=self.GRAPH)

        cached_scan.assert_not_called()

    def test_unverified_goes_through_the_cache(self):
        with (
            mock.patch("seamcheck.scancache.cached_scan",
                       return_value=(self.GRAPH, {"cached": True})) as cached_scan,
            mock.patch("seamcheck.api.scan") as scan,
        ):
            api.unverified(".")

        cached_scan.assert_called_once_with(".")
        scan.assert_not_called()


class SarifBlockingParityTests(SimpleTestCase):
    """`check --format sarif` (the exact recipe docs/ci.md prescribes) used to be able to
    fail the build over a finding that was invisible in the SARIF file meant to explain
    the failure - `has_blocking_findings()` (the gate) and `queries.findings()`'s default
    (which fed SARIF/GitHub) disagreed about exactly one status: CONFIRMED, which the
    gate treats as still-blocking but the old default excluded from "what is wrong"
    along with every other judged mark. Both now derive from `triage.blocking_ids`."""

    def test_a_confirmed_finding_blocks_the_gate_and_appears_in_the_sarif(self):
        import json

        from seamcheck.triage import TriageEntry, TriageStatus, fingerprint_for_symbol, save_triage

        symbol = Symbol(id="url:x", kind="url", label="x", sub="", file="a.py", line=1,
                        status=Status.UNRESOLVED, snippet="<url:x>", chain=[], note="")
        graph = Graph(symbols=[symbol], edges=[])

        with tempfile.TemporaryDirectory() as tmp:
            save_triage([TriageEntry(
                symbol_id="url:x", fingerprint=fingerprint_for_symbol(symbol),
                status=TriageStatus.CONFIRMED, who="a", when="2026-01-01",
                reason="real bug, acknowledged",
            )], tmp)

            outcome = api.check(tmp, graph=graph)
            sarif_text = api.report(tmp, "sarif", graph=graph)

        self.assertFalse(outcome["passed"], "a CONFIRMED finding must still block the gate")
        results = json.loads(sarif_text)["runs"][0]["results"]
        sarif_ids = {result["properties"]["seamcheckId"] for result in results}
        self.assertIn(
            "url:x", sarif_ids,
            "the finding that failed the build must be visible in the SARIF meant to "
            "explain why - a red build with an empty report tells nobody where to look")

    def test_an_approved_finding_does_not_block_and_does_not_appear_in_the_sarif(self):
        # The other half: this fix must not overcorrect into showing EVERY judged
        # finding - APPROVED still silences one, on the gate and in the SARIF alike.
        import json

        from seamcheck.triage import TriageEntry, TriageStatus, fingerprint_for_symbol, save_triage

        symbol = Symbol(id="url:x", kind="url", label="x", sub="", file="a.py", line=1,
                        status=Status.UNRESOLVED, snippet="<url:x>", chain=[], note="")
        graph = Graph(symbols=[symbol], edges=[])

        with tempfile.TemporaryDirectory() as tmp:
            save_triage([TriageEntry(
                symbol_id="url:x", fingerprint=fingerprint_for_symbol(symbol),
                status=TriageStatus.APPROVED, who="a", when="2026-01-01",
                reason="false positive",
            )], tmp)

            outcome = api.check(tmp, graph=graph)
            sarif_text = api.report(tmp, "sarif", graph=graph)

        self.assertTrue(outcome["passed"], "an APPROVED finding must not block the gate")
        results = json.loads(sarif_text)["runs"][0]["results"]
        sarif_ids = {result["properties"]["seamcheckId"] for result in results}
        self.assertNotIn("url:x", sarif_ids)
