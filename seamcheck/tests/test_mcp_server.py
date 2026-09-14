import asyncio
import json
import tempfile
from pathlib import Path
from unittest import mock

from django.test import SimpleTestCase, override_settings

from seamcheck.envelope import TooLarge
from seamcheck.mcp_server import (
    seamcheck_check,
    seamcheck_explain,
    seamcheck_report,
    seamcheck_scope,
    seamcheck_triage,
)

FIXTURES_DIR = str(Path(__file__).parent / "fixtures")
_CONFIG = {
    "urlconf_module": "seamcheck.tests.fixtures.fixture_urls",
    "js_entry_files": ["fixture_entry.js"],
    "js_project_root": FIXTURES_DIR,
    "entry_point_files": [],
}
GET_THING = "view:seamcheck.tests.fixtures.fixture_views.get_thing"


@override_settings(SEAMCHECK_CONFIG=_CONFIG)
class McpToolFunctionTests(SimpleTestCase):
    def test_check_returns_a_json_serializable_dict(self):
        result = seamcheck_check(repo_root=".")

        self.assertIn("passed", result)
        self.assertIsInstance(result["new_unresolved"], list)
        self.assertIn("counts", result)

    def test_explain_returns_markdown_for_a_real_symbol(self):
        text = seamcheck_explain(GET_THING, repo_root=".")

        self.assertIn("get_thing", text)

    def test_explain_is_honest_about_an_unknown_symbol(self):
        self.assertIn("No symbol", seamcheck_explain("view:nope", repo_root="."))

    def test_explain_suggests_a_near_id_on_a_miss(self):
        # The CLI and the MCP server must never disagree about what a typo meant - both
        # go through api.explain_with_hint now, not just api.explain.
        text = seamcheck_explain(GET_THING[:-1], repo_root=".")  # GET_THING minus its last char

        self.assertIn("Did you mean", text)
        self.assertIn(GET_THING, text)

    def test_triage_rejects_an_unknown_status(self):
        result = seamcheck_triage(GET_THING, "bogus", repo_root=".")

        self.assertFalse(result["ok"])
        self.assertIn("Unknown status", result["message"])

    def test_triage_writes_an_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = seamcheck_triage(GET_THING, "approved", repo_root=tmp, reason="fine")

            self.assertTrue(result["ok"], result["message"])
            self.assertTrue((Path(tmp) / "seamcheck" / "triage.json").is_file())

    def test_scope_returns_a_well_formed_envelope(self):
        # This repo's own working tree, whatever it happens to hold staged right now -
        # deliberately not asserting ON that content (ambient, not controlled), only that
        # the envelope plumbing (queries.scope -> api.scoped_findings -> the pydantic
        # model above) is genuinely callable end to end, same as seamcheck_check's own
        # "returns a json-serializable dict" test just above.
        result = seamcheck_scope(repo_root=".", scope="commit")

        self.assertTrue(result["ok"], result.get("message"))
        self.assertEqual(result["data"]["scope"], "commit")
        self.assertIsInstance(result["data"]["changed_files"], list)
        self.assertIsInstance(result["data"]["pages"], dict)

    def test_scope_rejects_an_unknown_mode(self):
        # A failing envelope comes back as a CallToolResult (not a plain dict), so
        # isError is actually set - see _tool_result's own docstring on why.
        result = seamcheck_scope(repo_root=".", scope="sideways")

        self.assertFalse(result.structuredContent["ok"])
        self.assertEqual(result.structuredContent["error"]["code"], "bad_argument")

    def test_every_tool_is_registered_on_the_server(self):
        from seamcheck.mcp_server import mcp

        self.assertEqual(mcp.name, "seamcheck")

        # mcp.list_tools() is FastMCP's public registry accessor (the same call the
        # MCP protocol's tools/list request serves); it's async because that request is.
        # The full set, pinned: a tool added to the module but not registered is one an
        # agent can never call, and this stayed red for four additions before anyone read it.
        registered = {tool.name for tool in asyncio.run(mcp.list_tools())}
        self.assertEqual(
            registered,
            {"seamcheck_check", "seamcheck_explain", "seamcheck_triage", "seamcheck_report",
             "seamcheck_services", "seamcheck_unverified", "seamcheck_share", "seamcheck_why_wrong",
             "seamcheck_findings", "seamcheck_symbols", "seamcheck_diff", "seamcheck_snapshot",
             "seamcheck_scope"},
        )


class ExplainCachedScanTests(SimpleTestCase):
    """`seamcheck_explain` used to call `api.scan()` directly - the exact call the review
    measured at 88.5 seconds for a mistyped id, the second step of the documented
    unverified -> explain -> triage -> check agent loop. It now shares the same cache
    `seamcheck_symbols`/`seamcheck_findings`/`seamcheck_diff` already use."""

    def test_explain_goes_through_the_cache_not_a_fresh_scan(self):
        from seamcheck.graph import Graph

        empty = Graph(symbols=[], edges=[])
        with (
            mock.patch("seamcheck.scancache.cached_scan",
                       return_value=(empty, {"cached": True, "seconds": 0.0})) as cached_scan,
            mock.patch("seamcheck.api.scan") as scan,
        ):
            seamcheck_explain("url:x", repo_root=".")

        cached_scan.assert_called_once_with(".")
        scan.assert_not_called()


class ReportSizeGateTests(SimpleTestCase):
    """A tool call must return an answer, never an exception - `TooLarge` escaping here
    would crash the whole server process, not just refuse one oversized request.

    `fmt="html"`, not `fmt="json"`: json (and map) are now refused by seamcheck_report
    itself before api.report is ever called (see test_mcp_protocol.ReportFormatGateTests),
    so json can no longer reach the `except TooLarge` branch this test exists to cover.
    """

    def test_too_large_becomes_a_coded_failure_not_an_escaped_exception(self):
        with mock.patch("seamcheck.api.report", side_effect=TooLarge(72_800_000, 18_200_000)):
            result = seamcheck_report(fmt="html", repo_root=".")

        # A coded failure is a CallToolResult with isError set (mcp_server._tool_result),
        # not a plain dict a caller has to parse to notice - see FailedEnvelopeIsErrorTests
        # for why that distinction is the whole point of this fix.
        self.assertTrue(result.isError)
        self.assertFalse(result.structuredContent["ok"])
        self.assertEqual(result.structuredContent["error"]["code"], "too_large")
        self.assertIn("seamcheck_findings", result.structuredContent["error"]["hint"])


class UndoTests(SimpleTestCase):
    def test_undo_takes_the_mark_off_and_says_so(self):
        with tempfile.TemporaryDirectory() as tmp:
            seamcheck_triage(GET_THING, "approved", repo_root=tmp, reason="fine")

            result = seamcheck_triage(GET_THING, "approved", repo_root=tmp, undo=True)

            self.assertTrue(result["ok"], result["message"])
            self.assertIn("raised again", result["message"])
            data = json.loads((Path(tmp) / "seamcheck" / "triage.json").read_text())
            self.assertEqual(data["entries"], [])

    def test_undo_on_a_symbol_never_marked_is_refused_without_a_scan(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch("seamcheck.api.scan") as scan:
                result = seamcheck_triage("url:never", "approved", repo_root=tmp, undo=True)

            self.assertFalse(result["ok"])
            self.assertIn("No mark", result["message"])
            scan.assert_not_called()


class UpdateNoticeAtStartupTests(SimpleTestCase):
    """`main()` runs this once before `mcp.run()` blocks on stdin - see mcp_server.py's
    `_print_update_notice_if_any` docstring for why there is no later point in a
    long-lived stdio server where printing again would reach anyone."""

    def test_a_pending_update_is_printed_on_stderr(self):
        from seamcheck.mcp_server import _print_update_notice_if_any

        with mock.patch("seamcheck.updatecheck.notice",
                         return_value="seamcheck: a newer version is available"), \
             mock.patch("sys.stderr") as stderr:
            _print_update_notice_if_any()

        printed = "".join(call.args[0] for call in stderr.write.call_args_list)
        self.assertIn("a newer version is available", printed)

    def test_nothing_pending_prints_nothing(self):
        from seamcheck.mcp_server import _print_update_notice_if_any

        with mock.patch("seamcheck.updatecheck.notice", return_value=None), \
             mock.patch("sys.stderr") as stderr:
            _print_update_notice_if_any()

        stderr.write.assert_not_called()

    def test_a_broken_notice_does_not_stop_the_server_from_starting(self):
        from seamcheck.mcp_server import _print_update_notice_if_any

        with mock.patch("seamcheck.updatecheck.notice", side_effect=OSError("boom")):
            _print_update_notice_if_any()  # must not raise
