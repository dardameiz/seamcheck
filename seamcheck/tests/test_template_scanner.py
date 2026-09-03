import pathlib
import tempfile
import textwrap
from pathlib import Path

from django.test import SimpleTestCase

from seamcheck.extractors.template_scanner import scan_templates

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


class ScriptNoiseTests(SimpleTestCase):
    def test_a_js_template_literal_is_not_a_class_name(self):
        # Templates carry <script> blocks; class="${statusClass}" is JS, not markup.
        import tempfile

        with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False) as handle:
            handle.write('<div class="real-class ${statusClass} other"></div>')
            path = handle.name

        labels = {s.label for s in scan_templates([path]) if s.sub == "class"}

        self.assertEqual(labels, {"real-class", "other"})


class BlockTagVersusInterpolationTests(SimpleTestCase):
    """Two template constructs that must not be treated alike."""

    def _classes(self, markup):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp, "t.html")
            path.write_text(markup, encoding="utf-8")
            return {s.label for s in scan_templates([str(path)]) if s.sub == "class"}

    def test_a_block_tag_separates_names_rather_than_joining_them(self):
        # `{% if %}` DELIMITS: this renders as "ev-watch" or "ev-watch ev-watch-wide", and
        # ev-watch is a whole name either way. Treating it as an interpolation lost 41
        # classes that are plainly in the markup.
        found = self._classes('<span class="ev-watch{% if label %} ev-watch-wide{% endif %}">')

        self.assertEqual(found, {"ev-watch", "ev-watch-wide"})

    def test_an_interpolation_still_makes_the_touching_token_a_fragment(self):
        # `{{ }}` INTERPOLATES: flag-icon-us is the real class; flag-icon- is a fragment.
        found = self._classes('<i class="flag-icon flag-icon-{{ code }}">')

        self.assertEqual(found, {"flag-icon"})

    def test_a_value_that_is_only_an_interpolation_yields_nothing(self):
        self.assertEqual(self._classes('<div class="{{ css_class }}">'), set())

    def test_names_either_side_of_a_block_tag_both_survive(self):
        found = self._classes('<div class="a {% if x %}b{% endif %} c">')

        self.assertEqual(found, {"a", "b", "c"})


class ScriptBlocksAreNotMarkupTests(SimpleTestCase):
    """A string inside `<script>` names an attribute; it does not declare one.

    `[['daily_hours_active', 'data-modal-daily-hours']]` is a mapping table. Read as
    markup it invented an attribute at that line and then reported it unused - a finding
    about an element that does not exist, sitting one screen from the real one.
    """

    def _scan(self, text: str):
        path = pathlib.Path(tempfile.mkdtemp()) / "page.html"
        path.write_text(textwrap.dedent(text), encoding="utf-8")
        return scan_templates([str(path)])

    def test_an_attribute_named_in_a_script_string_is_not_an_element(self):
        found = self._scan("""
            <div data-modal-daily-hours>0</div>
            <script>
              const MAP = [['daily_hours_active', 'data-modal-daily-hours']];
            </script>
        """)
        rows = [(s.label, s.line) for s in found if s.kind == "dom_attr"]
        self.assertEqual(rows, [("modal-daily-hours", 2)])

    def test_a_class_named_in_a_style_block_is_not_an_element(self):
        found = self._scan("""
            <style>.only-in-css { color: red }</style>
            <div class="real-one"></div>
        """)
        self.assertEqual([s.label for s in found if s.kind == "dom_attr"], ["real-one"])

    def test_the_lines_after_a_script_still_report_their_own_line(self):
        # Blanked, not removed: a shorter replacement would move every attribute after
        # the first script block and every finding would point at the wrong line.
        found = self._scan("""
            <script>
              let a = 1;
              let b = 2;
            </script>
            <div id="after-the-script"></div>
        """)
        rows = {s.label: s.line for s in found if s.kind == "dom_attr"}
        self.assertEqual(rows["after-the-script"], 6)
