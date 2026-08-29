from pathlib import Path

from django.test import SimpleTestCase

from signal_map.extractors.template_scanner import scan_templates

FIXTURE = str(Path(__file__).parent / "fixtures" / "fixture_dom_template.html")


class TemplateScannerTests(SimpleTestCase):
    def setUp(self):
        self.symbols = scan_templates([FIXTURE])

    def _labels(self, sub):
        return {s.label for s in self.symbols if s.sub == sub}

    def test_finds_every_id_attribute(self):
        self.assertEqual(
            self._labels("id"), {"purchase-btn", "gift-btn", "shared-counter", "single-quoted"}
        )

    def test_splits_multi_token_class_attributes_into_separate_symbols(self):
        self.assertTrue(
            {"fp-btn", "fp-btn-primary", "fp-btn-secondary", "stat-value"}.issubset(
                self._labels("class")
            )
        )

    def test_finds_data_attributes_with_their_full_dashed_name(self):
        self.assertIn("item-id", self._labels("data"))

    def test_handles_single_quoted_attributes(self):
        self.assertIn("sq-class", self._labels("class"))

    def test_keeps_static_classes_around_a_template_expression(self):
        # class="static-one {{ dynamic_class }} static-two" still pins two real classes;
        # dropping the whole attribute because part of it is dynamic loses both.
        self.assertIn("static-one", self._labels("class"))
        self.assertIn("static-two", self._labels("class"))

    def test_never_emits_a_template_expression_as_a_class_name(self):
        self.assertFalse(any("{{" in s.label or "{%" in s.label for s in self.symbols))

    def test_every_symbol_carries_file_and_line(self):
        for symbol in self.symbols:
            self.assertTrue(symbol.file)
            self.assertTrue(symbol.line and symbol.line > 0)
