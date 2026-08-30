from pathlib import Path

from django.test import SimpleTestCase

from seamcheck.extractors.css_extractor import css_imports, extract_css

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


class VarFallbackTests(SimpleTestCase):
    """`var(--x, .08em)` is correct CSS, not a broken reference."""

    def _tokens(self, css):
        import tempfile

        from seamcheck.extractors.css_extractor import extract_css

        with tempfile.NamedTemporaryFile("w", suffix=".css", delete=False) as handle:
            handle.write(css)
        return [s for s in extract_css([handle.name]) if s.kind == "css_token_use"]

    def test_a_use_with_a_fallback_is_marked_as_carrying_its_own_value(self):
        uses = self._tokens("a { border-width: var(--fa-border-width, .08em); }")

        self.assertEqual([u.sub for u in uses], ["token-fallback"])
        self.assertIn("fallback", uses[0].note)

    def test_a_bare_use_still_demands_a_definition(self):
        uses = self._tokens("a { color: var(--text-primary); }")

        self.assertEqual([u.sub for u in uses], ["token"])

    def test_the_two_forms_of_one_token_are_two_symbols(self):
        # 53 of this project's 63 "undefined token" findings were the fallback form. If
        # both forms shared an id, one would silently replace the other.
        uses = self._tokens("a { color: var(--x); border: var(--x, 1px); }")

        self.assertEqual(sorted(u.sub for u in uses), ["token", "token-fallback"])
        self.assertEqual(len({u.id for u in uses}), 2)


class TokenMatchingTests(SimpleTestCase):
    def _symbol(self, kind, label, sub):
        from seamcheck.graph import Status, Symbol

        return Symbol(id=f"{kind}:{sub}:{label}", kind=kind, label=label, sub=sub,
                      file="a.css", line=1, status=Status.UNCERTAIN, snippet="",
                      chain=[label], note="")

    def test_an_undefined_use_with_a_fallback_is_not_a_broken_reference(self):
        from seamcheck.dom_matcher import match_css_tokens
        from seamcheck.graph import Status

        edges = match_css_tokens([], [self._symbol("css_token_use", "--x", "token-fallback")])

        self.assertEqual([e.status for e in edges], [Status.CONNECTED])

    def test_an_undefined_bare_use_is_still_reported(self):
        from seamcheck.dom_matcher import match_css_tokens
        from seamcheck.graph import Status

        edges = match_css_tokens([], [self._symbol("css_token_use", "--x", "token")])

        self.assertEqual([e.status for e in edges], [Status.UNRESOLVED])

    def test_both_forms_of_one_token_each_get_an_edge(self):
        # Keyed by label, a dict kept one of the two and the other silently lost its edge.
        from seamcheck.dom_matcher import match_css_tokens

        edges = match_css_tokens([], [
            self._symbol("css_token_use", "--x", "token"),
            self._symbol("css_token_use", "--x", "token-fallback"),
        ])

        self.assertEqual(len(edges), 2)

    def test_a_definition_used_only_with_a_fallback_is_not_reported_unused(self):
        from seamcheck.dom_matcher import match_css_tokens
        from seamcheck.graph import Status

        edges = match_css_tokens(
            [self._symbol("css_token_def", "--x", "token")],
            [self._symbol("css_token_use", "--x", "token-fallback")],
        )

        self.assertNotIn(Status.UNUSED, [e.status for e in edges])


class TemplateStyleBlockTests(SimpleTestCase):
    """A <style> block in a template is a stylesheet."""

    def _selectors(self, html):
        import tempfile

        from seamcheck.extractors.css_extractor import extract_template_css

        with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False) as handle:
            handle.write(html)
        return extract_template_css([handle.name])

    def test_a_selector_inside_a_style_block_is_found(self):
        # Reading only .css files reported every element styled this way as one nothing
        # reaches: 1,016 selectors live in this project's template <style> blocks.
        found = self._selectors("<style>.ev-inline-has-img { color: red; }</style>")

        self.assertEqual([(s.sub, s.label) for s in found], [("class", "ev-inline-has-img")])

    def test_a_line_number_points_at_the_template_not_into_the_block(self):
        found = self._selectors("<html>\n<body>\n<style>\n.deep { color: red; }\n</style>")

        self.assertEqual(found[0].line, 4)

    def test_a_class_named_only_in_a_comment_is_not_a_selector(self):
        # The check that caught this ran a raw grep and called a comment a rule.
        found = self._selectors("<style>/* .menu-icon is styled elsewhere */</style>")

        self.assertEqual(found, [])

    def test_a_template_with_no_style_block_yields_nothing(self):
        self.assertEqual(self._selectors("<div class='x'>hi</div>"), [])

    def test_an_empty_style_block_is_skipped(self):
        self.assertEqual(self._selectors("<style>\n\n</style>"), [])
