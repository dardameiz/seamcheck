import subprocess
import tempfile
from pathlib import Path

from django.test import SimpleTestCase, override_settings

from seamcheck import api
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
