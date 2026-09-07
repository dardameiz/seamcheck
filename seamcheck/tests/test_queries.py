"""The questions an agent actually asks, answered without handing over the graph.

`seamcheck json` is 72.6 MB on the reference project - about 18 million tokens - and it is
the command the code names as the agent interface. These functions exist so that "what is
this called", "what is wrong in this file" and "what changed" each cost a few hundred tokens.
"""
import json
import os
from unittest import mock

from django.test import SimpleTestCase

from seamcheck import queries
from seamcheck.graph import Graph, Status, Symbol


def _symbol(kind, label, file, status=Status.CONNECTED, owner=""):
    return Symbol(id=f"{kind}:{label}", kind=kind, label=label, sub="", file=file, line=7,
                  status=status, snippet=f"def {label}", chain=[], note="", owner=owner)


GRAPH = Graph(symbols=[
    _symbol("view", "submit_push", "app/views.py"),
    _symbol("url", "api/submit/", "app/urls.py"),
    _symbol("redis_key", "user:*:pushes", "app/cache.py", Status.UNRESOLVED),
    _symbol("css_selector", "btn-push", "static/site.css", Status.UNUSED),
], edges=[])


class SymbolsTests(SimpleTestCase):
    def setUp(self):
        patch = mock.patch("seamcheck.scancache.cached_scan",
                           return_value=(GRAPH, {"cached": True, "seconds": 0.0}))
        patch.start()
        self.addCleanup(patch.stop)

    def test_a_search_finds_by_substring_and_says_where(self):
        out = queries.symbols(".", search="push")

        found = {row["id"]: row for row in out["data"]["symbols"]}
        self.assertIn("view:submit_push", found)
        self.assertEqual(found["view:submit_push"]["file"], "app/views.py")
        self.assertEqual(found["view:submit_push"]["line"], 7)

    def test_it_is_bounded_and_says_what_it_left_out(self):
        out = queries.symbols(".", search="", limit=2)

        self.assertEqual(len(out["data"]["symbols"]), 2)
        self.assertEqual(out["truncated"]["total"], 4)
        self.assertEqual(out["truncated"]["cursor"], "2")

    def test_a_kind_filter_narrows_it(self):
        out = queries.symbols(".", kind="url")

        self.assertEqual([row["id"] for row in out["data"]["symbols"]], ["url:api/submit/"])

    def test_near_suggests_ids_for_a_typo(self):
        # The 88.5-second "No symbol with id `urls.py`" is the thing this kills.
        self.assertIn("url:api/submit/", queries.near(".", "url:api/submit"))

    def test_near_uses_a_graph_handed_in_instead_of_scanning_again(self):
        # `explain`, on a miss, already paid for a scan - `near()` must not pay for a
        # second cache lookup right behind the first just to answer the same question.
        with mock.patch("seamcheck.scancache.cached_scan") as cached_scan:
            matches = queries.near(".", "url:api/submit", graph=GRAPH)

        cached_scan.assert_not_called()
        self.assertIn("url:api/submit/", matches)

    def test_refresh_reaches_the_scan_cache(self):
        with mock.patch("seamcheck.scancache.cached_scan",
                        return_value=(GRAPH, {"cached": False, "seconds": 1.0})) as cached_scan:
            queries.symbols(".", refresh=True)

        cached_scan.assert_called_once_with(".", refresh=True)


class FindingsTests(SimpleTestCase):
    def setUp(self):
        patch = mock.patch("seamcheck.scancache.cached_scan",
                           return_value=(GRAPH, {"cached": True, "seconds": 0.0}))
        patch.start()
        self.addCleanup(patch.stop)

    def test_only_findings_are_returned_not_the_graph(self):
        out = queries.findings(".")

        statuses = {row["status"] for row in out["data"]["findings"]}
        self.assertEqual(statuses, {"unresolved", "unused"},
                         "connected symbols are not findings")

    def test_a_file_filter_answers_what_is_wrong_in_this_file(self):
        out = queries.findings(".", file="app/cache.py")

        self.assertEqual([row["id"] for row in out["data"]["findings"]],
                         ["redis_key:user:*:pushes"])

    def test_the_census_tells_an_agent_the_vocabulary(self):
        out = queries.findings(".")

        self.assertEqual(out["data"]["by_status"], {"unresolved": 1, "unused": 1})
        self.assertIn("redis_key", out["data"]["by_kind"])

    def test_a_status_outside_the_vocabulary_is_a_coded_failure(self):
        out = queries.findings(".", status="wobbly")

        self.assertFalse(out["ok"])
        self.assertEqual(out["error"]["code"], "bad_argument")
        self.assertIn("unresolved", out["error"]["hint"])

    def test_the_answer_names_which_statuses_it_actually_searched(self):
        out = queries.findings(".")

        self.assertEqual(out["data"]["statuses"], ["unresolved", "unused"])

    def test_an_absolute_path_under_the_repo_root_still_matches(self):
        absolute = os.path.join(os.getcwd(), "app", "cache.py")

        out = queries.findings(".", file=absolute)

        self.assertEqual([row["id"] for row in out["data"]["findings"]],
                         ["redis_key:user:*:pushes"])
        self.assertEqual(out["warnings"], [])

    def test_a_dot_slash_prefixed_path_still_matches(self):
        out = queries.findings(".", file="./app/cache.py")

        self.assertEqual([row["id"] for row in out["data"]["findings"]],
                         ["redis_key:user:*:pushes"])

    def test_a_file_that_matches_nothing_gets_a_warning_not_silence(self):
        out = queries.findings(".", file="no/such/file.py")

        self.assertEqual(out["data"]["findings"], [])
        self.assertTrue(any("no/such/file.py" in w for w in out["warnings"]),
                        out["warnings"])

    def test_a_file_with_no_findings_but_a_real_path_gets_no_warning(self):
        # app/urls.py exists in the graph (as a connected symbol) but has no findings -
        # that is "clean file", not "wrong path", and must not be reported as the latter.
        out = queries.findings(".", file="app/urls.py")

        self.assertEqual(out["data"]["findings"], [])
        self.assertEqual(out["warnings"], [])

    def test_uncertain_and_connected_are_excluded_by_default_but_honoured_when_asked(self):
        # A local fixture, not the shared GRAPH: adding a 5th symbol there would change the
        # total SymbolsTests.test_it_is_bounded_and_says_what_it_left_out asserts against.
        graph = Graph(symbols=[
            _symbol("view", "submit_push", "app/views.py", Status.CONNECTED),
            _symbol("redis_key", "user:*:pushes", "app/cache.py", Status.UNRESOLVED),
            _symbol("dom_selector", ".maybe-dead", "templates/x.html", Status.UNCERTAIN),
        ], edges=[])
        with mock.patch("seamcheck.scancache.cached_scan",
                        return_value=(graph, {"cached": True, "seconds": 0.0})):
            default_out = queries.findings(".")
            uncertain_out = queries.findings(".", status="uncertain")
            connected_out = queries.findings(".", status="connected")

        default_statuses = {row["status"] for row in default_out["data"]["findings"]}
        self.assertNotIn("uncertain", default_statuses)
        self.assertNotIn("connected", default_statuses)

        self.assertEqual([row["id"] for row in uncertain_out["data"]["findings"]],
                         ["dom_selector:.maybe-dead"])
        self.assertEqual(uncertain_out["data"]["statuses"], ["uncertain"])

        self.assertEqual([row["id"] for row in connected_out["data"]["findings"]],
                         ["view:submit_push"])
        self.assertEqual(connected_out["data"]["statuses"], ["connected"])

    def test_refresh_reaches_the_scan_cache(self):
        with mock.patch("seamcheck.scancache.cached_scan",
                        return_value=(GRAPH, {"cached": False, "seconds": 1.0})) as cached_scan:
            queries.findings(".", refresh=True)

        cached_scan.assert_called_once_with(".", refresh=True)

    def test_a_triaged_finding_is_absent_by_default_and_present_with_the_flag(self):
        # `check --format sarif` could exit 0 because everything was approved, while
        # uploading every one of those approved findings to GitHub Code Scanning as an
        # error with no way to suppress them - because `findings()` never looked at triage
        # at all. "What is wrong" has to mean the same thing everywhere it is asked.
        from seamcheck.triage import TriageEntry, TriageStatus

        entry = TriageEntry(symbol_id="redis_key:user:*:pushes", fingerprint="f",
                            status=TriageStatus.APPROVED, who="t", when="2026-01-01",
                            reason="")
        with mock.patch("seamcheck.queries.load_triage", return_value=[entry]):
            default_out = queries.findings(".")
            everything_out = queries.findings(".", include_triaged=True)

        self.assertNotIn(
            "redis_key:user:*:pushes",
            [row["id"] for row in default_out["data"]["findings"]],
            "a triaged finding must not appear by default",
        )
        self.assertIn(
            "redis_key:user:*:pushes",
            [row["id"] for row in everything_out["data"]["findings"]],
            "--include-triaged / include_triaged=True must still show it",
        )

    def test_only_blocking_excludes_an_approved_finding(self):
        # only_blocking is the predicate SARIF/GitHub (api._findings_report) use - an
        # APPROVED mark silences a finding there exactly as it does for check's gate.
        from seamcheck.triage import TriageEntry, TriageStatus, fingerprint_for_symbol

        symbol = next(s for s in GRAPH.symbols if s.id == "redis_key:user:*:pushes")
        entry = TriageEntry(symbol_id=symbol.id, fingerprint=fingerprint_for_symbol(symbol),
                            status=TriageStatus.APPROVED, who="t", when="2026-01-01", reason="")
        with mock.patch("seamcheck.queries.load_triage", return_value=[entry]):
            out = queries.findings(".", only_blocking=True)

        self.assertNotIn(symbol.id, [row["id"] for row in out["data"]["findings"]])

    def test_only_blocking_includes_a_confirmed_finding_the_plain_default_excludes(self):
        # The exact bug: has_blocking_findings() (check's gate) says a CONFIRMED mark
        # still blocks, but the plain default here (judged_ids alone) excluded it same
        # as any other mark - so `check --format sarif` could fail the build over a
        # finding the rendered SARIF said nothing about. only_blocking closes the gap.
        from seamcheck.triage import TriageEntry, TriageStatus, fingerprint_for_symbol

        symbol = next(s for s in GRAPH.symbols if s.id == "redis_key:user:*:pushes")
        entry = TriageEntry(symbol_id=symbol.id, fingerprint=fingerprint_for_symbol(symbol),
                            status=TriageStatus.CONFIRMED, who="t", when="2026-01-01", reason="")
        with mock.patch("seamcheck.queries.load_triage", return_value=[entry]):
            default_out = queries.findings(".")
            blocking_out = queries.findings(".", only_blocking=True)

        self.assertNotIn(
            symbol.id, [row["id"] for row in default_out["data"]["findings"]],
            "the plain default excludes ANY judged mark, confirmed included")
        self.assertIn(
            symbol.id, [row["id"] for row in blocking_out["data"]["findings"]],
            "only_blocking must still show a CONFIRMED finding - it still blocks the gate")


class DiffTests(SimpleTestCase):
    """"What did this commit break" is the question CI and an agent both ask, and there was
    no command that answered it: only `check --since`, which mixes the answer into a gate."""

    def test_it_names_what_appeared_and_what_went(self):
        before = Graph(symbols=[_symbol("url", "api/old/", "app/urls.py")], edges=[])
        after = Graph(symbols=[_symbol("url", "api/new/", "app/urls.py")], edges=[])
        with mock.patch("seamcheck.scancache.cached_scan",
                        return_value=(after, {"cached": True, "seconds": 0.0})), \
             mock.patch("seamcheck.snapshot.load_snapshot", return_value=before), \
             mock.patch("seamcheck.queries._resolve", return_value="abc123"):
            out = queries.diff(".", since="main")

        self.assertEqual([row["id"] for row in out["data"]["appeared"]], ["url:api/new/"])
        self.assertEqual([row["id"] for row in out["data"]["vanished"]], ["url:api/old/"])
        self.assertEqual(out["data"]["baseline"], "abc123")

    def test_no_baseline_is_a_coded_failure_not_a_crash(self):
        with mock.patch("seamcheck.scancache.cached_scan",
                        return_value=(GRAPH, {"cached": True, "seconds": 0.0})), \
             mock.patch("seamcheck.snapshot.load_snapshot", return_value=None), \
             mock.patch("seamcheck.queries._resolve", return_value="abc123"):
            out = queries.diff(".", since="main")

        self.assertFalse(out["ok"])
        self.assertEqual(out["error"]["code"], "no_baseline")
        self.assertIn("seamcheck scan", out["error"]["hint"])

    def test_a_snapshot_this_version_cannot_read_is_a_coded_failure_not_a_crash(self):
        # A snapshot written by an older or foreign version of the tool: graph_from_dict
        # raises TypeError on a renamed/missing field rather than returning None, and that
        # must not reach the caller as a bare stack trace.
        with mock.patch("seamcheck.scancache.cached_scan",
                        return_value=(GRAPH, {"cached": True, "seconds": 0.0})), \
             mock.patch("seamcheck.snapshot.load_snapshot",
                        side_effect=TypeError("missing 1 required positional argument")), \
             mock.patch("seamcheck.queries._resolve", return_value="abc123"):
            out = queries.diff(".", since="main")

        self.assertFalse(out["ok"])
        self.assertEqual(out["error"]["code"], "stale_snapshot")
        self.assertIn("seamcheck scan", out["error"]["hint"])

    def test_counts_are_whole_even_when_a_category_is_empty_on_the_current_page(self):
        # Three appeared + two vanished, paged at limit=2: the page's own "vanished" list
        # is empty (the first 2 rows are all "appeared"), but the caller must still be
        # able to tell that from "nothing vanished at all" - that is what counts is for.
        before = Graph(symbols=[
            _symbol("url", "api/old1/", "app/urls.py"),
            _symbol("url", "api/old2/", "app/urls.py"),
        ], edges=[])
        after = Graph(symbols=[
            _symbol("url", "api/new1/", "app/urls.py"),
            _symbol("url", "api/new2/", "app/urls.py"),
            _symbol("url", "api/new3/", "app/urls.py"),
        ], edges=[])
        with mock.patch("seamcheck.scancache.cached_scan",
                        return_value=(after, {"cached": True, "seconds": 0.0})), \
             mock.patch("seamcheck.snapshot.load_snapshot", return_value=before), \
             mock.patch("seamcheck.queries._resolve", return_value="abc123"):
            out = queries.diff(".", since="main", limit=2)

        self.assertEqual(out["data"]["counts"], {"appeared": 3, "vanished": 2, "changed": 0})
        self.assertEqual(out["data"]["vanished"], [],
                         "nothing vanished on THIS page - counts says 2 total, not 0")

    def test_refresh_reaches_the_scan_cache(self):
        with mock.patch("seamcheck.scancache.cached_scan",
                        return_value=(GRAPH, {"cached": False, "seconds": 1.0})) as cached_scan, \
             mock.patch("seamcheck.snapshot.load_snapshot", return_value=GRAPH), \
             mock.patch("seamcheck.queries._resolve", return_value="abc123"):
            queries.diff(".", since="main", refresh=True)

        cached_scan.assert_called_once_with(".", refresh=True)


class DeterminismTests(SimpleTestCase):
    def test_two_identical_runs_of_a_machine_command_agree(self):
        with mock.patch("seamcheck.scancache.cached_scan",
                        return_value=(GRAPH, {"cached": True, "seconds": 0.0})):
            first = json.dumps(queries.findings("."))
            second = json.dumps(queries.findings("."))

        self.assertEqual(first, second, "a machine answer may not carry a clock")
