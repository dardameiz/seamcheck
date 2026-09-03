"""Runtime evidence, and the discipline that keeps it honest."""

import pathlib
import tempfile

from django.test import SimpleTestCase

from seamcheck.graph import Graph, Status, Symbol
from seamcheck.observe import PROBE, Observation, load, merge, save
from seamcheck.provenance import apply_observations


def _obs(page="/", selectors=None, fetches=None, classes=None):
    return Observation(page=page, selectors=selectors or {}, fetches=fetches or {},
                       classes=classes or {})


def _sym(id_, kind, label, sub, status=Status.UNCERTAIN):
    return Symbol(id=id_, kind=kind, label=label, sub=sub, file="a.js", line=1,
                  status=status, snippet="", chain=[], note="")


class ProbeTests(SimpleTestCase):
    def test_it_patches_every_way_the_page_can_ask_for_an_element(self):
        for call in ("querySelector", "querySelectorAll", "closest", "getElementById"):
            self.assertIn(call, PROBE)

    def test_it_patches_both_request_apis(self):
        self.assertIn("window.fetch", PROBE)
        self.assertIn("XMLHttpRequest.prototype.open", PROBE)

    def test_every_patch_calls_the_original_and_returns_its_result(self):
        # A probe that changed behaviour would make the thing it measures untrue. Every
        # patched function has to hand back exactly what the real one produced.
        for original in ("original.apply(this, arguments)", "byId.apply(this, arguments)",
                         "fetched.apply(this, arguments)", "open.apply(this, arguments)",
                         "add.apply(this, arguments)", "className.set.call(this, value)"):
            with self.subTest(call=original):
                self.assertIn(original, PROBE)
        # ...and returned, not swallowed.
        for returned in ("return found;", "return fetched.apply", "return open.apply",
                         "return add.apply", "return className.set.call"):
            with self.subTest(returns=returned):
                self.assertIn(returned, PROBE)

    def test_a_query_that_found_nothing_is_recorded_as_a_miss(self):
        # The distinction the whole merge rests on: a selector that ran and returned null is
        # evidence AGAINST the element, not for it.
        self.assertIn("found.length === undefined || found.length > 0", PROBE)


class StoreTests(SimpleTestCase):
    def test_a_round_trip_keeps_everything(self):
        with tempfile.TemporaryDirectory() as repo:
            save([_obs("/a", selectors={"#x": {"count": 2, "hits": 2}})], repo, "sha1")

            back = load(repo, "sha1")

            self.assertEqual(len(back), 1)
            self.assertEqual(back[0].selectors["#x"]["hits"], 2)

    def test_observations_are_keyed_by_commit(self):
        # Evidence from a different version of the code describes a different program.
        with tempfile.TemporaryDirectory() as repo:
            save([_obs("/a")], repo, "sha1")

            self.assertEqual(load(repo, "other"), [])

    def test_a_corrupt_file_yields_nothing_rather_than_raising(self):
        with tempfile.TemporaryDirectory() as repo:
            path = pathlib.Path(repo, "OTHER", "seamcheck", "observed", "s.json")
            path.parent.mkdir(parents=True)
            path.write_text("{ not json")

            self.assertEqual(load(repo, "s"), [])

    def test_pages_are_folded_because_any_page_using_it_counts(self):
        merged = merge([
            _obs("/a", selectors={"#x": {"count": 1, "hits": 1}}),
            _obs("/b", selectors={"#x": {"count": 3, "hits": 0}}),
        ])

        self.assertEqual(merged["selectors"]["#x"]["count"], 4)
        self.assertEqual(merged["selectors"]["#x"]["hits"], 1)
        self.assertEqual(merged["selectors"]["#x"]["pages"], ["/a", "/b"])


class ProvenanceTests(SimpleTestCase):
    def test_a_runtime_built_selector_is_resolved_by_observation(self):
        # 849 of one project's uncertains are exactly this, and no static reader can move
        # a single one of them.
        graph = Graph(symbols=[_sym("s1", "dom_selector", "<dynamic>", "dynamic:read")], edges=[])

        out = apply_observations(graph, merge([
            _obs("/arena", selectors={"#combo-42": {"count": 1, "hits": 1}})
        ]))

        self.assertIs(out.symbols[0].status, Status.CONNECTED)

    def test_a_selector_that_returned_nothing_becomes_a_finding(self):
        # The strongest claim the tool can make: not "we could not find it" but "the page
        # asked for it and it was not there".
        graph = Graph(symbols=[_sym("s1", "dom_selector", "gone", "id:read")], edges=[])

        out = apply_observations(graph, merge([
            _obs("/p", selectors={"#gone": {"count": 5, "hits": 0}})
        ]))

        self.assertIs(out.symbols[0].status, Status.UNRESOLVED)
        self.assertIn("live null", out.symbols[0].note)

    def test_every_promotion_says_it_was_observed_and_what_that_does_not_prove(self):
        # A coverage gap must never read as an all-clear.
        graph = Graph(symbols=[_sym("s1", "dom_selector", "here", "id:read")], edges=[])

        out = apply_observations(graph, merge([
            _obs("/page-one", selectors={"#here": {"count": 1, "hits": 1}})
        ]))

        self.assertIs(out.symbols[0].status, Status.CONNECTED)
        self.assertIn("nothing about the paths that were not", out.symbols[0].note)

    def test_a_run_that_saw_nothing_changes_nothing(self):
        graph = Graph(symbols=[_sym("s1", "dom_selector", "<dynamic>", "dynamic:read")], edges=[])

        out = apply_observations(graph, merge([_obs("/p")]))

        self.assertIs(out.symbols[0].status, Status.UNCERTAIN)

    def test_a_runtime_built_fetch_is_resolved_by_observation(self):
        graph = Graph(symbols=[_sym("f1", "fetch_target", "<dynamic>", "dynamic")], edges=[])

        out = apply_observations(graph, merge([
            _obs("/p", fetches={"/api/user/7/stats/": {"count": 1, "hits": 1}})
        ]))

        self.assertIs(out.symbols[0].status, Status.CONNECTED)

    def test_kinds_it_has_no_evidence_about_are_untouched(self):
        graph = Graph(symbols=[_sym("m1", "model", "Book", "app")], edges=[])

        out = apply_observations(graph, merge([
            _obs("/p", selectors={"#x": {"count": 1, "hits": 1}})
        ]))

        self.assertIs(out.symbols[0].status, Status.UNCERTAIN)
