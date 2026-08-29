from pathlib import Path

from django.test import SimpleTestCase

from signal_map.attribution import attribute_by_feature, attribute_by_page
from signal_map.extractors.dom_js_extractor import extract_dom_selectors
from signal_map.graph import Edge, Graph, Status, Symbol

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _symbol(id_, label="x", kind="dom_selector"):
    return Symbol(
        id=id_, kind=kind, label=label, sub="id:write", file="a.js", line=1,
        status=Status.UNCERTAIN, snippet="", chain=[label], note="",
    )


class FeatureAttributionTests(SimpleTestCase):
    def test_a_root_labels_everything_it_reaches(self):
        root = _symbol("root", "purchase-btn", kind="dom_attr")
        graph = Graph(
            symbols=[root, _symbol("mid"), _symbol("leaf")],
            edges=[
                Edge("root", "mid", Status.CONNECTED),
                Edge("mid", "leaf", Status.CONNECTED),
            ],
        )

        labels = attribute_by_feature(graph, [root])

        self.assertEqual(labels["leaf"], ["purchase-btn"])

    def test_an_unreachable_symbol_gets_no_label(self):
        root = _symbol("root", "purchase-btn", kind="dom_attr")
        graph = Graph(symbols=[root, _symbol("island")], edges=[])

        self.assertNotIn("island", attribute_by_feature(graph, [root]))

    def test_a_symbol_reached_by_two_roots_keeps_both(self):
        a = _symbol("a", "btn-a", kind="dom_attr")
        b = _symbol("b", "btn-b", kind="dom_attr")
        graph = Graph(
            symbols=[a, b, _symbol("shared")],
            edges=[Edge("a", "shared", Status.CONNECTED), Edge("b", "shared", Status.CONNECTED)],
        )

        self.assertEqual(attribute_by_feature(graph, [a, b])["shared"], ["btn-a", "btn-b"])

    def test_a_cycle_does_not_hang_the_walk(self):
        root = _symbol("root", "r", kind="dom_attr")
        graph = Graph(
            symbols=[root, _symbol("a"), _symbol("b")],
            edges=[
                Edge("root", "a", Status.CONNECTED),
                Edge("a", "b", Status.CONNECTED),
                Edge("b", "root", Status.CONNECTED),
            ],
        )

        self.assertIn("b", attribute_by_feature(graph, [root]))


class PageAttributionTests(SimpleTestCase):
    def test_a_symbol_is_labelled_with_the_entry_that_reaches_it(self):
        symbols = extract_dom_selectors([str(FIXTURES_DIR / "fixture_dom_multiwriter_a.js")])

        pages = attribute_by_page({"entry-a": symbols})

        self.assertTrue(all(entries == ["entry-a"] for entries in pages.values()))

    def test_a_shared_symbol_lists_every_entry_that_reaches_it(self):
        # A symbol several pages reach is exactly the risky one to change, so both
        # owners are kept rather than collapsed to a single label.
        symbols = extract_dom_selectors([str(FIXTURES_DIR / "fixture_dom_multiwriter_a.js")])

        pages = attribute_by_page({"entry-a": symbols, "entry-b": symbols})

        self.assertTrue(all(entries == ["entry-a", "entry-b"] for entries in pages.values()))
