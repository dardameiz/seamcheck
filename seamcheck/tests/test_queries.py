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
