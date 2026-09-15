from django.test import SimpleTestCase

from seamcheck.graph import Edge, Graph, Status, Symbol
from seamcheck.mapdata import BUCKET_PREFIXES, UNDRAWN_PAGE, UNREACHED_PAGE, build_map
from seamcheck.pagenames import PageName


def _symbol(id_, kind, label=None, status=Status.CONNECTED, file="a.js", sub=""):
    return Symbol(
        id=id_, kind=kind, label=label or id_, sub=sub, file=file, line=1,
        status=status, snippet=id_, chain=[id_], note="",
    )


def _entries(built):
    """The pages rooted at an entry, without the buckets."""
    return [page for page in built.pages if not page.page.startswith(BUCKET_PREFIXES)]


def _bucket_ids(built, prefix):
    return {n.id for p in built.pages if p.page.startswith(prefix + ":") for n in p.nodes}


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

    def test_a_page_with_nothing_to_draw_stays_in_the_picker(self):
        # Dropping it is how every Next.js page vanished: a page that disappears is the
        # failure, not a page with an empty canvas.
        built = build_map(_graph(), {"empty": {"nothing.js"}}, git_sha="abc", now="t")

        self.assertEqual([(p.page, p.reached) for p in _entries(built)], [("empty", 0)])

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

        drawn = {n.id for p in _entries(built) for n in p.nodes}
        in_buckets = _bucket_ids(built, UNREACHED_PAGE) | _bucket_ids(built, UNDRAWN_PAGE)
        every = {s.id for s in graph.symbols}

        self.assertEqual(every - drawn, in_buckets)

    def test_nothing_is_counted_on_both_sides(self):
        built = build_map(_graph(), {"home": {"a.js"}}, git_sha="abc", now="t")

        drawn = {n.id for p in _entries(built) for n in p.nodes}
        unreached = _bucket_ids(built, UNREACHED_PAGE)
        undrawn = _bucket_ids(built, UNDRAWN_PAGE)

        self.assertEqual((drawn & unreached, drawn & undrawn, unreached & undrawn),
                         (set(), set(), set()))

    def test_the_buckets_say_why_rather_than_naming_a_verdict(self):
        # Being unreached is not a finding: Django reaches a model, a webhook reaches a
        # route. Each symbol keeps its own status, and the bucket explains itself.
        built = build_map(_graph(), {"home": {"a.js"}}, git_sha="abc", now="t")

        buckets = [p for p in built.pages if p.page.startswith(UNREACHED_PAGE + ":")]
        self.assertTrue(buckets)
        for bucket in buckets:
            self.assertEqual(bucket.title, "Not reached from any page")
            # The picker appends the count; a count in `where` showed it twice ("— 3 — 3 nodes").
            self.assertNotRegex(bucket.where, r"\d")

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

        self.assertEqual([p for p in again.pages if p.page.startswith(BUCKET_PREFIXES)], [])


class MembershipByFileTests(SimpleTestCase):
    def test_a_store_use_on_a_page_file_is_drawn(self):
        graph = Graph(symbols=[_symbol("db:orders:a.ts:1", "db_table_use", "orders", file="a.ts")], edges=[])
        built = build_map(graph, {"home": {"a.ts"}}, git_sha="abc", now="t")

        self.assertIn("db:orders:a.ts:1", {n.id for n in _entries(built)[0].nodes})

    def test_a_symbol_on_a_reached_file_that_nothing_draws_is_on_the_page_not_unreached(self):
        graph = Graph(symbols=[_symbol("apply:a.tsx:1", "dom_attr", "x", file="a.tsx", sub="class:apply")],
                      edges=[])
        built = build_map(graph, {"home": {"a.tsx"}}, git_sha="abc", now="t")

        self.assertNotIn("apply:a.tsx:1", {n.id for n in _entries(built)[0].nodes})
        self.assertEqual(_bucket_ids(built, UNREACHED_PAGE), set())
        self.assertEqual(_bucket_ids(built, UNDRAWN_PAGE), {"apply:a.tsx:1"})
        self.assertEqual(_entries(built)[0].reached, 1)

    def test_the_undrawn_bucket_sits_after_the_entries_and_before_the_not_reached_ones(self):
        graph = Graph(symbols=[
            _symbol("call:a.js:1", "js_call", file="a.js"),
            _symbol("apply:a.js:2", "dom_attr", "x", file="a.js", sub="class:apply"),
            _symbol("call:z.js:1", "js_call", file="z.js"),
        ], edges=[])
        built = build_map(graph, {"home": {"a.js"}}, git_sha="abc", now="t")

        places = [p.page.split(":", 1)[0] if p.page.startswith(BUCKET_PREFIXES) else "entry"
                  for p in built.pages]
        self.assertEqual(places, ["entry", UNDRAWN_PAGE, UNREACHED_PAGE])

    def test_the_undrawn_bucket_says_why_without_a_count(self):
        graph = Graph(symbols=[_symbol("apply:a.tsx:1", "dom_attr", "x", file="a.tsx", sub="class:apply")],
                      edges=[])
        built = build_map(graph, {"home": {"a.tsx"}}, git_sha="abc", now="t")
        [bucket] = [p for p in built.pages if p.page.startswith(UNDRAWN_PAGE + ":")]

        self.assertEqual(bucket.title, "On a page, nothing to draw")
        self.assertTrue(bucket.where)
        self.assertNotRegex(bucket.where, r"\d")

    def test_a_page_that_draws_everything_on_its_files_has_no_undrawn_bucket(self):
        built = build_map(_graph(), {"home": {"a.js"}}, git_sha="abc", now="t")

        self.assertEqual(_bucket_ids(built, UNDRAWN_PAGE), set())


class EntryOrderAndEvidenceTests(SimpleTestCase):
    def test_pages_come_before_server_entries_whatever_their_titles(self):
        names = {"a": PageName(title="Zebra", where="", entry="a", kind="page"),
                 "b": PageName(title="Alpha", where="", entry="b", kind="server")}
        built = build_map(_graph(), {"a": {"a.js"}, "b": {"views.py"}}, git_sha="abc", now="t",
                          names=names)

        self.assertEqual([p.page for p in _entries(built)], ["a", "b"])

    def test_the_page_node_carries_the_entrys_evidence_and_note(self):
        names = {"home": PageName(title="Home", where="/", entry="home",
                                  evidence="a page file, routed by the filesystem",
                                  note="by convention, not declared")}
        built = build_map(_graph(), {"home": {"a.js"}}, git_sha="abc", now="t", names=names)
        root = _entries(built)[0].nodes[0]

        self.assertEqual((root.kind, root.note),
                         ("page", "a page file, routed by the filesystem · by convention, not declared"))

    def test_the_page_node_reads_as_the_entrys_label_and_keeps_its_key_as_its_id(self):
        names = {"next:/pricing": PageName(title="Pricing", where="/pricing - app/pricing/page.tsx",
                                           entry="next:/pricing", label="/pricing")}
        built = build_map(_graph(), {"next:/pricing": {"a.js"}}, git_sha="abc", now="t", names=names)
        root = _entries(built)[0].nodes[0]

        self.assertEqual((root.id, root.label), ("page:next:/pricing", "/pricing"))

    def test_an_entry_without_a_label_reads_as_its_key(self):
        names = {"home": PageName(title="Home", where="/", entry="home")}
        built = build_map(_graph(), {"home": {"a.js"}}, git_sha="abc", now="t", names=names)

        self.assertEqual(_entries(built)[0].nodes[0].label, "home")


class ServerEntrySeedTests(SimpleTestCase):
    """A server entry is its routes and handlers. They start its chain even when the handler
    touches nothing - no store, no env var - that would seed a page."""

    def test_a_server_entry_draws_its_own_route_and_handler(self):
        names = {"server:views.py": PageName(title="/api/x/", where="/api/x/ - views.py",
                                             entry="server:views.py", kind="server")}
        built = build_map(_graph(), {"server:views.py": {"views.py"}}, git_sha="abc", now="t",
                          names=names)
        [entry] = _entries(built)
        own = {"url:api/x/", "view:app.views.x"}

        self.assertTrue(own <= {n.id for n in entry.nodes}, [n.id for n in entry.nodes])
        self.assertFalse(own & _bucket_ids(built, UNDRAWN_PAGE))

    def test_a_page_holding_the_same_file_still_seeds_only_from_touches(self):
        names = {"home": PageName(title="Home", where="/", entry="home")}
        built = build_map(_graph(), {"home": {"views.py"}}, git_sha="abc", now="t", names=names)
        [entry] = _entries(built)

        self.assertEqual([n.kind for n in entry.nodes], ["page"])


class BucketWordingTests(SimpleTestCase):
    def _where(self, key, graph, **kwargs):
        built = build_map(graph, {"home": {"nothing.js"}}, git_sha="abc", now="t", **kwargs)
        return next(p.where for p in built.pages if p.page == f"{UNREACHED_PAGE}:{key}")

    def test_with_no_backend_detected_the_backend_bucket_reads_as_today(self):
        self.assertEqual(self._where("backend", _graph()),
                         "Reached by the framework, never by a browser page — "
                         "routes, handlers, models, signal receivers")

    def test_nextjs_is_described_in_its_own_terms(self):
        self.assertIn("route handlers and pages no entry reaches",
                      self._where("backend", _graph(), detected_backends=frozenset({"nextjs"})))

    def test_two_backends_are_both_named(self):
        where = self._where("backend", _graph(), detected_backends=frozenset({"django", "nextjs"}))
        self.assertIn("URLs, views, admin actions, signal receivers", where)
        self.assertIn("route handlers and pages no entry reaches", where)

    def test_the_script_bucket_names_the_languages_actually_in_it(self):
        graph = Graph(symbols=[_symbol("c1", "js_call", file="a.ts"), _symbol("c2", "js_call", file="b.js")],
                      edges=[])
        self.assertEqual(self._where("js", graph), "TypeScript and JavaScript no entry imports")
