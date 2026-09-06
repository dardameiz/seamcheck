"""The questions an agent actually asks, answered without handing over the graph.

`seamcheck json` is 72.6 MB on the reference project - about 18 million tokens - and it is
the command the code names as the agent interface. These functions exist so that "what is
this called", "what is wrong in this file" and "what changed" each cost a few hundred tokens.
"""
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
