from pathlib import Path

from django.test import SimpleTestCase

from signal_map.extractors.css_extractor import css_imports, extract_css

FIXTURE = str(Path(__file__).parent / "fixtures" / "fixture_dom.css")


class CssExtractorTests(SimpleTestCase):
    def setUp(self):
        self.symbols = extract_css([FIXTURE])

    def _labels(self, kind):
        return {s.label for s in self.symbols if s.kind == kind}

    def test_extracts_class_and_id_selectors_without_their_markers(self):
        selectors = self._labels("css_selector")

        self.assertIn("fp-btn", selectors)
        self.assertIn("purchase-btn", selectors)
        self.assertIn("no-such-class-anywhere", selectors)

    def test_distinguishes_id_selectors_from_class_selectors(self):
        by_label = {s.label: s.sub for s in self.symbols if s.kind == "css_selector"}

        self.assertEqual(by_label["purchase-btn"], "id")
        self.assertEqual(by_label["fp-btn"], "class")

    def test_extracts_token_definitions(self):
        self.assertEqual(self._labels("css_token_def"), {"--used-token", "--dead-token"})

    def test_extracts_token_uses(self):
        self.assertEqual(self._labels("css_token_use"), {"--used-token"})

    def test_every_symbol_carries_file_and_line(self):
        for symbol in self.symbols:
            self.assertTrue(symbol.file)
            self.assertTrue(symbol.line and symbol.line > 0)

    def test_import_targets_are_reported_for_the_pipeline_to_walk(self):
        self.assertEqual(
            css_imports([FIXTURE])[FIXTURE], ["./fixture_dom_imported.css"]
        )


class EscapedSelectorTests(SimpleTestCase):
    def test_escaped_variant_selectors_unescape_to_the_template_form(self):
        # Tailwind compiles `md:flex` to `.md\:flex`. Reading only [\w-] yields "md",
        # so every variant utility in every template reads as a broken reference.
        import tempfile

        with tempfile.NamedTemporaryFile("w", suffix=".css", delete=False) as handle:
            handle.write(".md\\:flex { display: flex; }\n.w-1\\/2 { width: 50%; }")
            path = handle.name

        labels = {s.label for s in extract_css([path]) if s.kind == "css_selector"}

        self.assertIn("md:flex", labels)
        self.assertIn("w-1/2", labels)
