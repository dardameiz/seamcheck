from django.test import SimpleTestCase

from seamcheck.graph import Edge, Graph, Status, Symbol
from seamcheck.mapdata import UNREACHED_PAGE, build_map


def _symbol(id_, kind, label=None, status=Status.CONNECTED, file="a.js"):
    return Symbol(
        id=id_, kind=kind, label=label or id_, sub="", file=file, line=1,
        status=status, snippet=id_, chain=[id_], note="",
    )


def _entries(built):
    """The pages rooted at an entry point, without the not-reached buckets."""
    return [page for page in built.pages if not page.page.startswith(UNREACHED_PAGE + ":")]


def _graph():
    call = _symbol("jscall:a.js:5", "js_call", "fetch()", file="a.js")
    target = _symbol("fetch:/api/x/", "fetch_target", "/api/x/", file="a.js")
    url = _symbol("url:api/x/", "url", "api/x/", file="views.py")
    view = _symbol("view:app.views.x", "view", "x", file="views.py")
    other = _symbol("jscall:z.js:1", "js_call", "fetch()", file="z.js")
    return Graph(
        symbols=[call, target, url, view, other],
        edges=[
            Edge("jscall:a.js:5", "fetch:/api/x/", Status.CONNECTED),
            Edge("fetch:/api/x/", "url:api/x/", Status.CONNECTED),
            Edge("url:api/x/", "view:app.views.x", Status.CONNECTED),
        ],
    )


class PageScopingTests(SimpleTestCase):
    def setUp(self):
        self.map = build_map(_graph(), {"home": {"a.js"}}, git_sha="abc123", now="2026-08-30T00:00:00")

    def test_one_page_map_per_page_with_content(self):
        self.assertEqual([p.page for p in _entries(self.map)], ["home"])

    def test_the_page_is_the_root_node(self):
        kinds = {n.kind for n in self.map.pages[0].nodes}

        self.assertIn("page", kinds)
        self.assertIn("module", kinds)

    def test_a_module_is_created_for_each_file_with_symbols(self):
        modules = [n for n in self.map.pages[0].nodes if n.kind == "module"]

        self.assertEqual([n.label for n in modules], ["a.js"])

    def test_the_chain_reaches_the_backend(self):
        # fetch -> url -> view is the whole frontend-to-backend story; a map that
        # stopped at the fetch call would show none of what this tool is for.
        ids = {n.id for n in self.map.pages[0].nodes}

        self.assertIn("fetch:/api/x/", ids)
        self.assertIn("url:api/x/", ids)
        self.assertIn("view:app.views.x", ids)

    def test_symbols_from_another_page_are_excluded(self):
        ids = {n.id for n in self.map.pages[0].nodes}

        self.assertNotIn("jscall:z.js:1", ids)

    def test_a_page_with_no_symbols_is_dropped(self):
        built = build_map(_graph(), {"empty": {"nothing.js"}}, git_sha="abc", now="t")

        self.assertEqual(_entries(built), [])

    def test_edges_carry_their_status(self):
        statuses = {e.status for e in self.map.pages[0].edges}

        self.assertIn("connected", statuses)

    def test_the_map_records_the_commit_it_describes(self):
        self.assertEqual(self.map.git_sha, "abc123")
        self.assertIsNone(self.map.baseline_sha)
        self.assertEqual(self.map.changed, {})


class DiffModeTests(SimpleTestCase):
    def _built(self, baseline):
        return build_map(
            _graph(), {"home": {"a.js"}}, git_sha="new", baseline=baseline,
            baseline_sha="old", now="t",
        )

    def test_a_symbol_absent_from_the_baseline_is_added(self):
        baseline = Graph(symbols=[], edges=[])

        self.assertEqual(self._built(baseline).changed["fetch:/api/x/"], "added")

    def test_a_symbol_whose_status_changed_is_flagged(self):
        baseline = Graph(
            symbols=[_symbol("fetch:/api/x/", "fetch_target", "/api/x/", Status.UNRESOLVED)], edges=[]
        )

        self.assertEqual(self._built(baseline).changed["fetch:/api/x/"], "status")

    def test_a_symbol_gone_from_the_current_scan_is_removed(self):
        baseline = Graph(symbols=[_symbol("url:gone", "url", "gone")], edges=[])

        self.assertEqual(self._built(baseline).changed["url:gone"], "removed")

    def test_an_unchanged_symbol_is_not_flagged(self):
        baseline = Graph(
            symbols=[_symbol("fetch:/api/x/", "fetch_target", "/api/x/", Status.CONNECTED)], edges=[]
        )

        self.assertNotIn("fetch:/api/x/", self._built(baseline).changed)

    def test_the_diff_records_both_commits(self):
        built = self._built(Graph(symbols=[], edges=[]))

        self.assertEqual((built.git_sha, built.baseline_sha), ("new", "old"))


class PageNamingTests(SimpleTestCase):
    def test_a_page_carries_the_name_a_reader_recognises(self):
        from seamcheck.pagenames import PageName

        built = build_map(
            _graph(),
            {"push-arena-main": {"a.js"}},
            git_sha="sha",
            names={
                "push-arena-main": PageName(
                    title="Push Arena", where="/push_arena/ - push_arena.html",
                    entry="push-arena-main",
                )
            },
        )
        self.assertEqual(built.pages[0].title, "Push Arena")
        self.assertEqual(built.pages[0].where, "/push_arena/ - push_arena.html")

    def test_pages_are_ordered_by_that_name_not_by_bundle_filename(self):
        from seamcheck.pagenames import PageName

        built = build_map(
            _graph(),
            {"zz-main": {"a.js"}, "aa-main": {"a.js"}},
            git_sha="sha",
            names={
                "zz-main": PageName(title="Arena", where="", entry="zz-main"),
                "aa-main": PageName(title="Store", where="", entry="aa-main"),
            },
        )
        self.assertEqual([page.title for page in _entries(built)], ["Arena", "Store"])

    def test_without_names_a_page_still_renders_under_its_bundle_filename(self):
        built = build_map(_graph(), {"push-arena-main": {"a.js"}}, git_sha="sha")
        self.assertEqual(built.pages[0].title, "push-arena-main")


class UnreachedTests(SimpleTestCase):
    """90% of a real scan is reached by no page entry, and the map simply omitted it."""

    def test_symbols_no_page_reaches_still_get_a_page(self):
        # Clicking `models.py` in the Files view drew an empty canvas, which is
        # indistinguishable from "this file is dead" - and models are reached by Django,
        # never by a browser page, so an empty canvas was the wrong answer to a fair
        # question.
        graph = _graph()
        built = build_map(graph, {"home": {"a.js"}}, git_sha="abc", now="t")

        covered = {n.id for p in _entries(built) for n in p.nodes}
        unreached = {n.id for p in built.pages
                     if p.page.startswith(UNREACHED_PAGE + ":") for n in p.nodes}
        every = {s.id for s in graph.symbols}

        self.assertEqual(every - covered, unreached)

    def test_nothing_is_counted_on_both_sides(self):
        built = build_map(_graph(), {"home": {"a.js"}}, git_sha="abc", now="t")

        covered = {n.id for p in _entries(built) for n in p.nodes}
        unreached = {n.id for p in built.pages
                     if p.page.startswith(UNREACHED_PAGE + ":") for n in p.nodes}

        self.assertEqual(covered & unreached, set())

    def test_the_buckets_say_why_rather_than_naming_a_verdict(self):
        # Being unreached is not a finding: Django reaches a model, a webhook reaches a
        # route. Each symbol keeps its own status, and the bucket explains itself.
        built = build_map(_graph(), {"home": {"a.js"}}, git_sha="abc", now="t")

        buckets = [p for p in built.pages if p.page.startswith(UNREACHED_PAGE + ":")]
        self.assertTrue(buckets)
        for bucket in buckets:
            self.assertEqual(bucket.title, "Not reached from any page")
            self.assertIn("—", bucket.where)

    def test_a_fully_reached_graph_produces_no_buckets(self):
        graph = _graph()
        built = build_map(graph, {"home": {"a.js"}}, git_sha="abc", now="t")
        reached = {n.id for p in _entries(built) for n in p.nodes}

        # Everything the entry page did not reach, removed - so nothing is left over.
        trimmed = Graph(
            symbols=[s for s in graph.symbols if s.id in reached],
            edges=[e for e in graph.edges if e.from_id in reached and e.to_id in reached],
        )
        again = build_map(trimmed, {"home": {"a.js"}}, git_sha="abc", now="t")

        self.assertEqual(
            [p for p in again.pages if p.page.startswith(UNREACHED_PAGE + ":")], []
        )
