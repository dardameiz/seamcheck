from pathlib import Path

from django.test import SimpleTestCase

from seamcheck.extractors.dom_js_extractor import extract_dom_selectors
from seamcheck.graph import Status

FIXTURES_DIR = Path(__file__).parent / "fixtures"
FILES = [
    str(FIXTURES_DIR / "fixture_dom_multiwriter_a.js"),
    str(FIXTURES_DIR / "fixture_dom_multiwriter_b.js"),
    str(FIXTURES_DIR / "fixture_dom_single_writer.js"),
]


class DomSelectorExtractionTests(SimpleTestCase):
    def setUp(self):
        self.symbols = extract_dom_selectors(FILES)

    def _for(self, label):
        return [s for s in self.symbols if s.label == label]

    def test_finds_get_element_by_id_selectors(self):
        self.assertTrue(self._for("shared-counter"))
        self.assertTrue(all(s.sub.startswith("id:") for s in self._for("shared-counter")))

    def test_parses_class_and_style_selectors_from_query_selector(self):
        self.assertTrue(self._for("stat-value"))

    def test_text_content_assignment_is_a_write(self):
        writes = [s for s in self._for("shared-counter") if s.sub.endswith(":write")]

        self.assertEqual(len(writes), 2)

    def test_reading_text_content_is_not_a_write(self):
        reads = [s for s in self._for("shared-counter") if s.sub.endswith(":read")]

        self.assertEqual(len(reads), 1)

    def test_style_assignment_counts_as_a_write(self):
        self.assertTrue(all(s.sub.endswith(":write") for s in self._for("stat-value")))

    def test_class_list_toggle_counts_as_a_write(self):
        self.assertTrue(all(s.sub.endswith(":write") for s in self._for("gift-btn")))

    def test_a_runtime_built_selector_is_uncertain_and_unnamed(self):
        dynamic = [s for s in self.symbols if s.label == "<dynamic>"]

        self.assertEqual(len(dynamic), 1)
        self.assertEqual(dynamic[0].status, Status.UNCERTAIN)

    def test_every_symbol_names_its_enclosing_function(self):
        for symbol in self.symbols:
            self.assertGreaterEqual(len(symbol.chain), 2, symbol.id)


class BoundElementWriteTests(SimpleTestCase):
    def setUp(self):
        self.symbols = extract_dom_selectors(FILES)

    def test_a_write_through_a_local_binding_counts_as_a_write(self):
        # const el = getElementById(...); el.textContent = v  -- the dominant real
        # pattern. Same-statement-only matching found 70 writes in 2,300 selectors.
        bound = [s for s in self.symbols if s.label == "bound-counter"]

        self.assertTrue(bound)
        self.assertTrue(any(s.sub.endswith(":write") for s in bound))

    def test_a_write_through_a_this_property_counts_as_a_write(self):
        writes = [
            s for s in self.symbols
            if s.label == "bound-counter" and s.sub.endswith(":write")
        ]

        self.assertGreaterEqual(len(writes), 2)
