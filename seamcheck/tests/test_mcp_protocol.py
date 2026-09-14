"""Through the protocol, not around it.

Every existing MCP test calls the decorated functions as plain Python, so nothing exercises
the layer a client actually talks to: not the schemas, not isError, not structuredContent.
"""
import asyncio
import json
import subprocess
import tempfile
from pathlib import Path
from unittest import mock

from django.test import SimpleTestCase, override_settings

from seamcheck import mcp_server, snapshot
from seamcheck.graph import Graph, Status, Symbol
from seamcheck.mcp_server import (
    seamcheck_diff,
    seamcheck_findings,
    seamcheck_report,
    seamcheck_snapshot,
    seamcheck_symbols,
)

FIXTURES_DIR = str(Path(__file__).parent / "fixtures")
_CONFIG = {
    "urlconf_module": "seamcheck.tests.fixtures.fixture_urls",
    "js_entry_files": ["fixture_entry.js"],
    "js_project_root": FIXTURES_DIR,
    "entry_point_files": [],
}
GET_THING = "view:seamcheck.tests.fixtures.fixture_views.get_thing"


def _tools():
    return asyncio.run(mcp_server.mcp.list_tools())


def _symbol(kind, label, file, status=Status.CONNECTED, owner=""):
    return Symbol(id=f"{kind}:{label}", kind=kind, label=label, sub="", file=file, line=7,
                  status=status, snippet=f"def {label}", chain=[], note="note", owner=owner)


GRAPH = Graph(symbols=[
    _symbol("view", "submit_push", "app/views.py"),
    _symbol("url", "api/submit/", "app/urls.py"),
    _symbol("redis_key", "user:*:pushes", "app/cache.py", Status.UNRESOLVED),
    _symbol("css_selector", "btn-push", "static/site.css", Status.UNUSED),
], edges=[])


class ProtocolTests(SimpleTestCase):
    def test_the_server_tells_a_client_what_the_loop_is(self):
        # The docstring that explains when to use what was never sent to anyone.
        instructions = mcp_server.mcp._mcp_server.instructions or ""

        self.assertIn("unverified", instructions)
        self.assertIn("evidence", instructions)

    def test_the_closed_vocabularies_are_enums(self):
        by_name = {tool.name: tool for tool in _tools()}

        triage = by_name["seamcheck_triage"].inputSchema["properties"]
        self.assertIn("enum", triage["status"])
        self.assertIn("approved", triage["status"]["enum"])

    def test_report_cannot_be_asked_for_the_whole_graph(self):
        # fmt="json" returned 72 MB and fmt="map" 8.6 MB, neither documented.
        fmt = {t.name: t for t in _tools()}["seamcheck_report"].inputSchema["properties"]["fmt"]

        self.assertEqual(set(fmt["enum"]), {"terminal", "markdown", "html"})

    def test_the_reading_tools_say_they_only_read(self):
        by_name = {tool.name: tool for tool in _tools()}

        self.assertTrue(by_name["seamcheck_findings"].annotations.readOnlyHint)
        self.assertFalse(by_name["seamcheck_triage"].annotations.readOnlyHint)

    def test_the_agent_entry_points_exist(self):
        names = {tool.name for tool in _tools()}

        self.assertLessEqual({"seamcheck_findings", "seamcheck_symbols", "seamcheck_diff",
                              "seamcheck_snapshot"}, names)


@override_settings(SEAMCHECK_CONFIG=_CONFIG)
class FailedEnvelopeIsErrorTests(SimpleTestCase):
    """`isError=True` is set by `mcp.server.lowlevel.server.Server.call_tool()`'s own
    handler only when the tool function RAISES or output validation fails - never by
    inspecting a returned dict's own `ok` field (read directly from the installed SDK).
    `findings`/`symbols`/`diff` used to return a correct `{"ok": false, ...}` body with
    `isError` staying `False` regardless - the same gap Task 1 closed for `check`'s
    process exit code, reopened one layer down. Dispatched through the LOW-LEVEL
    server's own registered handler (`mcp._mcp_server.request_handlers`), not FastMCP's
    `call_tool()` convenience wrapper, so this is the actual wire path a real client's
    `tools/call` request takes - not just the Python-level shortcut to it."""

    async def _dispatch(self, name, arguments):
        from mcp import types

        handler = mcp_server.mcp._mcp_server.request_handlers[types.CallToolRequest]
        request = types.CallToolRequest(
            method="tools/call",
            params=types.CallToolRequestParams(name=name, arguments=arguments),
        )
        result = await handler(request)
        return result.root  # ServerResult wraps the CallToolResult

    def test_findings_bad_status_sets_iserror_as_plain_python(self):
        # NOT dispatched through the wire path above: `status` is typed as a closed
        # Literal (FindingStatusFilter) in the tool's own signature, so a real,
        # schema-respecting client is refused by MCP's own argument validation before
        # queries.findings() - and therefore mcp_server._tool_result() - is ever reached.
        # A caller that skips the schema (or calls this as plain Python, exactly like
        # seamcheck's own tests elsewhere in this file) must still get the SAME coded
        # isError=True failure, not a bare dict - so this proves that path directly,
        # the same way ReportFormatGateTests/SnapshotToolTests already do for their tools.
        from seamcheck.mcp_server import seamcheck_findings

        result = seamcheck_findings(repo_root=".", status="wobbly")

        self.assertTrue(result.isError)
        self.assertFalse(result.structuredContent["ok"])
        self.assertEqual(result.structuredContent["error"]["code"], "bad_argument")

    def test_diff_bad_ref_sets_iserror_over_the_real_wire_path(self):
        result = asyncio.run(self._dispatch(
            "seamcheck_diff", {"repo_root": ".", "since": "totally-bogus-ref-xyz"}))

        self.assertTrue(result.isError)
        self.assertFalse(result.structuredContent["ok"])
        self.assertEqual(result.structuredContent["error"]["code"], "no_git")

    def test_symbols_ok_true_never_sets_iserror(self):
        # The success path (the overwhelming majority of calls) must stay completely
        # unaffected - a plain dict, isError False, exactly as before this fix.
        result = asyncio.run(self._dispatch("seamcheck_symbols", {"repo_root": "."}))

        self.assertFalse(result.isError)
        self.assertTrue(result.structuredContent["ok"])


class ReportFormatGateTests(SimpleTestCase):
    """`json` (72 MB) and `map` (8.6 MB) must never reach api.report at all - the schema
    enum keeps a well-behaved client from asking, but a direct call (or a client that
    ignores the schema) must still get the coded refusal, not the payload."""

    def test_json_is_refused_before_report_is_ever_called(self):
        with mock.patch("seamcheck.api.report") as report:
            result = seamcheck_report(fmt="json", repo_root=".")

        report.assert_not_called()
        # Called as plain Python here (not through the protocol - see
        # ProtocolSmokeTests._call for that path), so the coded failure comes back as the
        # CallToolResult mcp_server._tool_result() constructs directly, isError included -
        # not a plain dict, which is exactly the point: isError must be set, not just a
        # correct body a caller has to parse to notice.
        self.assertTrue(result.isError)
        self.assertFalse(result.structuredContent["ok"])
        self.assertEqual(result.structuredContent["error"]["code"], "too_large")

    def test_map_is_refused_before_report_is_ever_called(self):
        with mock.patch("seamcheck.api.report") as report:
            result = seamcheck_report(fmt="map", repo_root=".")

        report.assert_not_called()
        self.assertTrue(result.isError)
        self.assertFalse(result.structuredContent["ok"])
        self.assertEqual(result.structuredContent["error"]["code"], "too_large")


class NewToolDelegationTests(SimpleTestCase):
    """The MCP surface must not drift from the CLI: same function, same arguments, so the
    two can never disagree about what a filter means. One test per new tool, checking it
    forwards to the exact `queries` call the CLI itself makes - including the filters
    (`owner`, `include_triaged`, `refresh`, `cursor`) the CLI already exposes that the
    plan's own sketch omitted."""

    def test_findings_forwards_every_filter_queries_findings_takes(self):
        with mock.patch("seamcheck.queries.findings", return_value={"ok": True}) as findings:
            seamcheck_findings(repo_root="/r", file="a.py", kind="view", status="unused",
                               owner="fn", limit=10, cursor="5",
                               include_triaged=True, refresh=True)

        findings.assert_called_once_with("/r", "a.py", "view", "unused", "fn", 10, "5",
                                         refresh=True, include_triaged=True)

    def test_symbols_forwards_refresh(self):
        with mock.patch("seamcheck.queries.symbols", return_value={"ok": True}) as symbols:
            seamcheck_symbols(repo_root="/r", search="push", kind="view", limit=10,
                              cursor="3", refresh=True)

        symbols.assert_called_once_with("/r", "push", "view", 10, "3", refresh=True)

    def test_diff_forwards_cursor_and_refresh(self):
        with mock.patch("seamcheck.queries.diff", return_value={"ok": True}) as diff:
            seamcheck_diff(repo_root="/r", since="main", limit=10, cursor="7", refresh=True)

        diff.assert_called_once_with("/r", "main", 10, "7", refresh=True)

    def test_a_changed_row_keeps_its_extra_was_field_through_the_real_protocol(self):
        # queries.diff()'s "changed" rows are a Finding PLUS `was` - a plain `Finding`
        # output type here would let pydantic silently drop `was` from structuredContent
        # (verified empirically against this exact SDK version), which a client reading
        # structured output only would never notice. ChangedFinding is what prevents it;
        # this proves it, with real data, through mcp.call_tool() rather than a mock.
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(["git", "init", "-q"], cwd=tmp, check=True)
            subprocess.run(["git", "config", "user.email", "a@example.com"], cwd=tmp, check=True)
            subprocess.run(["git", "config", "user.name", "a"], cwd=tmp, check=True)
            (Path(tmp) / "f.txt").write_text("x")
            subprocess.run(["git", "add", "."], cwd=tmp, check=True)
            subprocess.run(["git", "commit", "-q", "-m", "x"], cwd=tmp, check=True)
            sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=tmp, check=True,
                                 capture_output=True, text=True).stdout.strip()

            before = Graph(symbols=[_symbol("view", "x", "a.py", Status.CONNECTED)], edges=[])
            snapshot.save_snapshot(before, sha, tmp)
            now = Graph(symbols=[_symbol("view", "x", "a.py", Status.UNCERTAIN)], edges=[])

            with mock.patch("seamcheck.scancache.cached_scan",
                            return_value=(now, {"cached": False})):
                _, structured = asyncio.run(
                    mcp_server.mcp.call_tool("seamcheck_diff", {"repo_root": tmp, "since": "HEAD"}))

        changed = structured["data"]["changed"]
        self.assertEqual(len(changed), 1)
        self.assertEqual(changed[0]["was"], "connected")
        self.assertEqual(changed[0]["status"], "uncertain")


class SnapshotToolTests(SimpleTestCase):
    """seamcheck_snapshot must be a THIN call into the one write path (api.write_map) -
    not a second way to persist a baseline - and must never let a non-git checkout crash
    the tool call the way a bare `snapshot.current_git_sha()` would."""

    def _git_repo(self, tmp):
        subprocess.run(["git", "init", "-q"], cwd=tmp, check=True)
        subprocess.run(["git", "config", "user.email", "a@example.com"], cwd=tmp, check=True)
        subprocess.run(["git", "config", "user.name", "a"], cwd=tmp, check=True)
        (Path(tmp) / "f.txt").write_text("x")
        subprocess.run(["git", "add", "."], cwd=tmp, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "x"], cwd=tmp, check=True)

    def test_it_calls_the_canonical_write_path_not_a_second_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._git_repo(tmp)
            with mock.patch("seamcheck.scancache.cached_scan",
                            return_value=(GRAPH, {"cached": False})), \
                 mock.patch("seamcheck.api.write_map",
                           return_value="docs/maps/connectivity-map.json") as write_map, \
                 mock.patch("seamcheck.snapshot.save_snapshot") as save_snapshot:
                result = seamcheck_snapshot(repo_root=tmp)

        write_map.assert_called_once_with(GRAPH, tmp)
        save_snapshot.assert_not_called()  # no SECOND persistence path
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["data"]["symbols"], len(GRAPH.symbols))
        self.assertEqual(result["data"]["path"], "docs/maps/connectivity-map.json")

    def test_outside_a_git_repo_is_a_coded_failure_not_a_crash(self):
        with tempfile.TemporaryDirectory() as tmp:  # never git-initialised
            result = seamcheck_snapshot(repo_root=tmp)

        # Plain-Python call (see the class docstring's `write_map` test for the `ok:True`
        # shape, still a plain dict) - a coded failure is a CallToolResult with isError
        # set, not just a dict a caller has to inspect to notice.
        self.assertTrue(result.isError)
        self.assertFalse(result.structuredContent["ok"])
        self.assertEqual(result.structuredContent["error"]["code"], "no_git")


class LegacyTriageDataTests(SimpleTestCase):
    """A hand-edited triage.json can carry a `why` written before WhyWrong existed - the
    module's own docstring says so. MarkRow.why is typed `str`, not the closed WhyReason
    literal, exactly so this keeps working. This seeds one on disk and calls seamcheck_check
    through the real protocol, so a future "tighten why to the enum" change is caught by a
    crashing test instead of a green suite and a broken tool call in someone's real repo."""

    def test_a_pre_enum_why_value_does_not_crash_the_protocol_call(self):
        symbol = Symbol(id="view:x", kind="view", label="x", sub="", file="a.py", line=1,
                        status=Status.UNRESOLVED, snippet="def x(): ...", chain=[], note="")
        graph = Graph(symbols=[symbol], edges=[])
        legacy_why = "reasoned about it before the enum existed"

        with tempfile.TemporaryDirectory() as tmp:
            triage_path = Path(tmp) / "seamcheck" / "triage.json"
            triage_path.parent.mkdir(parents=True)
            triage_path.write_text(json.dumps({
                "entries": [{
                    "symbol_id": "view:x",
                    # Deliberately wrong, so the mark is STALE (triage.stale_entries) and
                    # therefore a "returned" finding (triage.returned) - the one path in
                    # api.check() that renders a MarkRow via report.mark_dict().
                    "fingerprint": "does-not-match-the-current-evidence",
                    "status": "approved",
                    "who": "alice",
                    "when": "2020-01-01",
                    "reason": "fine at the time",
                    "why": legacy_why,
                    "expired": "",
                }]
            }))

            with mock.patch("seamcheck.api.scan", return_value=graph):
                _, structured = asyncio.run(
                    mcp_server.mcp.call_tool("seamcheck_check", {"repo_root": tmp}))

        self.assertEqual(len(structured["returned"]), 1)
        self.assertEqual(structured["returned"][0]["why"], legacy_why)


@override_settings(SEAMCHECK_CONFIG=_CONFIG)
class ProtocolSmokeTests(SimpleTestCase):
    """Calls every tool through mcp.call_tool() - the actual wire path - against the real
    fixture project, so a TypedDict output schema that does not match what the function
    really returns fails HERE (pydantic either raises or silently drops a field) instead
    of only ever being checked by list_tools(), which never invokes a tool at all."""

    def _call(self, name, **arguments):
        # FastMCP's own convert_result() (server/fastmcp/utilities/func_metadata.py)
        # returns a bare CallToolResult, unchanged, when the tool function returned one
        # itself (mcp_server._tool_result()'s ok:False path, so isError survives to the
        # wire) - and a (content, structuredContent) TUPLE otherwise, its ordinary shape
        # for every ok:True call. Normalised to the tuple shape here so every existing
        # `_, structured = self._call(...)` caller keeps working for both.
        from mcp.types import CallToolResult

        result = asyncio.run(mcp_server.mcp.call_tool(name, arguments))
        if isinstance(result, CallToolResult):
            return result.content, result.structuredContent
        return result

    def test_check(self):
        self._call("seamcheck_check", repo_root=".")

    def test_scope(self):
        # This repo's own working tree - whatever it happens to hold staged is fine, the
        # point is that ScopeData/ScopePage's TypedDict shape survives the real wire path.
        self._call("seamcheck_scope", repo_root=".", scope="commit")

    def test_symbols(self):
        # A schema-only assertion passes even if the tool returns the wrong rows entirely
        # (convert_result only checks shape) - this checks the actual fixture content:
        # both real symbols named for get_thing, one of them the connected view itself.
        _, structured = self._call("seamcheck_symbols", repo_root=".", search="get_thing")

        self.assertTrue(structured["ok"])
        self.assertEqual(structured["command"], "symbols")
        rows = {row["id"]: row for row in structured["data"]["symbols"]}
        self.assertIn(GET_THING, rows)
        self.assertEqual(rows[GET_THING]["kind"], "view")
        self.assertEqual(rows[GET_THING]["status"], "connected")

    def test_symbols_limit_actually_bounds_the_rows(self):
        _, structured = self._call("seamcheck_symbols", repo_root=".", limit=3)

        self.assertEqual(len(structured["data"]["symbols"]), 3)
        self.assertEqual(structured["truncated"]["returned"], 3)
        self.assertGreater(structured["truncated"]["total"], 3)
        self.assertNotEqual(structured["truncated"]["cursor"], "")

    def test_findings(self):
        # The fixture project has exactly three findings, all unresolved fetch targets
        # nothing in the project ever calls the routes for (fixture_module.js) - real ids,
        # not just "some list came back".
        _, structured = self._call("seamcheck_findings", repo_root=".")

        self.assertTrue(structured["ok"])
        self.assertEqual(structured["command"], "findings")
        ids = {row["id"] for row in structured["data"]["findings"]}
        self.assertEqual(ids, {"fetch:/api/does-not-exist/", "fetch:/api/from-arrow/",
                               "fetch:/api/log/"})
        self.assertEqual(structured["data"]["by_status"], {"unresolved": 3})

    def test_findings_limit_actually_bounds_the_rows(self):
        _, structured = self._call("seamcheck_findings", repo_root=".", limit=2)

        self.assertEqual(len(structured["data"]["findings"]), 2)
        self.assertEqual(structured["truncated"]["total"], 3)
        self.assertNotEqual(structured["truncated"]["cursor"], "")

    def test_diff_against_an_unresolvable_ref_is_still_a_clean_protocol_round_trip(self):
        # No stored snapshot for this ref in the fixture project - exercises the
        # DiffEnvelope failure branch (data=None) through the real protocol.
        _, structured = self._call("seamcheck_diff", repo_root=".", since="HEAD")

        self.assertFalse(structured["ok"])
        self.assertEqual(structured["command"], "diff")
        self.assertEqual(structured["error"]["code"], "no_baseline")

    def test_diff_shows_a_real_status_change_against_the_fixture(self):
        # `before` needs only the ONE symbol whose status is being flipped - everything
        # else in the real "now" scan simply shows up as `appeared`, which this does not
        # assert on. The real content checked here is the row that DID change: the same
        # get_thing view the other tests use, reported unresolved a moment ago and
        # connected now, cross-checked byte for byte against the real fixture id.
        before = Graph(symbols=[Symbol(
            id=GET_THING, kind="view", label="seamcheck.tests.fixtures.fixture_views.get_thing",
            sub="", file="seamcheck/tests/fixtures/fixture_views.py", line=4,
            status=Status.UNRESOLVED, snippet="def get_thing(request): ...", chain=[], note="",
        )], edges=[])

        with mock.patch("seamcheck.snapshot.load_snapshot", return_value=before):
            _, structured = self._call("seamcheck_diff", repo_root=".", since="HEAD")

        self.assertTrue(structured["ok"])
        self.assertEqual(structured["command"], "diff")
        changed = {row["id"]: row for row in structured["data"]["changed"]}
        self.assertIn(GET_THING, changed)
        self.assertEqual(changed[GET_THING]["was"], "unresolved")
        self.assertEqual(changed[GET_THING]["status"], "connected")

    def test_unverified(self):
        self._call("seamcheck_unverified", repo_root=".")

    def test_why_wrong(self):
        self._call("seamcheck_why_wrong")

    def test_services(self):
        self._call("seamcheck_services", repo_root=".")

    def test_explain(self):
        self._call("seamcheck_explain", symbol_id=GET_THING, repo_root=".")

    def test_share(self):
        self._call("seamcheck_share", repo_root=".")

    def test_report_terminal(self):
        self._call("seamcheck_report", fmt="terminal", repo_root=".")

    def test_triage_then_undo(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._call("seamcheck_triage", symbol_id=GET_THING, status="approved",
                      repo_root=tmp, reason="fine")
            self._call("seamcheck_triage", symbol_id=GET_THING, status="approved",
                      repo_root=tmp, undo=True)

    def test_snapshot_real_round_trip(self):
        # Unmocked: a real scan, a real api.write_map, through the real protocol - so a
        # mismatch between SnapshotData and what those two actually produce fails HERE,
        # not only in SnapshotToolTests (which mocks both away to test call shape).
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(["git", "init", "-q"], cwd=tmp, check=True)
            subprocess.run(["git", "config", "user.email", "a@example.com"], cwd=tmp, check=True)
            subprocess.run(["git", "config", "user.name", "a"], cwd=tmp, check=True)
            (Path(tmp) / "f.txt").write_text("x")
            subprocess.run(["git", "add", "."], cwd=tmp, check=True)
            subprocess.run(["git", "commit", "-q", "-m", "x"], cwd=tmp, check=True)

            _, structured = self._call("seamcheck_snapshot", repo_root=tmp)

            self.assertTrue(structured["ok"])
            self.assertEqual(structured["command"], "snapshot")
            expected_path = str(Path(tmp) / "docs" / "maps" / "connectivity-map.json")
            self.assertEqual(structured["data"]["path"], expected_path)
            self.assertGreater(structured["data"]["symbols"], 0)
            # Not a hardcoded count (that only pins today's fixture): the number snapshot
            # reports for the tree it just scanned must equal what symbols() reports for
            # the SAME repo_root - real content, not "some positive integer came back".
            # (repo_root itself, not just SEAMCHECK_CONFIG, affects the scan - confirmed
            # by first writing this against repo_root="." here and getting a genuine
            # mismatch, 21 vs 22, from comparing two different trees.)
            _, symbols_structured = self._call("seamcheck_symbols", repo_root=tmp)
            self.assertEqual(structured["data"]["symbols"],
                             symbols_structured["truncated"]["total"])

            self.assertTrue((Path(tmp) / "docs" / "maps" / "connectivity-map.json").is_file())
            self.assertTrue((Path(tmp) / "OTHER" / "seamcheck" / "scans").is_dir())
