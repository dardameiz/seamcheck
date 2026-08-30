"""The two CSS claims the tool must not make while their evidence is unread.

Measured on this project before these guards existed: 5,318 selectors reported unused
while 3,205 JS class-application sites went unread, and 128 undefined token references
of which exactly half were defined at runtime by JS.
"""

import tempfile

from django.test import SimpleTestCase

from seamcheck.classifier import classify
from seamcheck.dom_matcher import match_css_tokens
from seamcheck.extractors.dom_js_extractor import extract_js_css_tokens
from seamcheck.graph import Edge, Status, Symbol


def _symbol(id_, kind, label, status=Status.UNCERTAIN):
    return Symbol(
        id=id_, kind=kind, label=label, sub="token" if "token" in kind else "class",
        file="a.css", line=1, status=status, snippet=label, chain=[label], note="",
    )


class CssSelectorIsNeverClaimedUnusedTests(SimpleTestCase):
    def test_an_unreferenced_css_selector_is_uncertain_not_unused(self):
        # The matcher can only see querySelector() and template class= attributes.
        # It cannot see className=, classList.add, setAttribute('class') or class
        # literals in JS template strings, so "nothing uses this" is not a claim it
        # is entitled to make.
        selector = _symbol("css_selector:class:orphan", "css_selector", "orphan")

        orphan = "css_selector:class:orphan"
        classified = classify([selector], [Edge(orphan, orphan, Status.UNUSED)])

        self.assertEqual(classified[0].status, Status.UNCERTAIN)

    def test_the_downgraded_selector_says_what_evidence_is_missing(self):
        selector = _symbol("css_selector:class:orphan", "css_selector", "orphan")

        orphan = "css_selector:class:orphan"
        classified = classify([selector], [Edge(orphan, orphan, Status.UNUSED)])

        self.assertIn("className", classified[0].note)

    def test_a_referenced_css_selector_is_still_connected(self):
        selector = _symbol("css_selector:class:used", "css_selector", "used")

        classified = classify([selector], [Edge("dom_attr:x", "css_selector:class:used", Status.CONNECTED)])

        self.assertEqual(classified[0].status, Status.CONNECTED)

    def test_other_kinds_can_still_be_claimed_unused(self):
        # Only css_selector has this recall gap. A dead design token stays a finding.
        token = _symbol("css_token_def:token:--dead", "css_token_def", "--dead")

        dead = "css_token_def:token:--dead"
        classified = classify([token], [Edge(dead, dead, Status.UNUSED)])

        self.assertEqual(classified[0].status, Status.UNUSED)


class JsDefinedTokensTests(SimpleTestCase):
    def _extract(self, source):
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as handle:
            handle.write(source)
            path = handle.name
        return extract_js_css_tokens([path])

    def test_a_token_set_by_js_is_a_definition(self):
        symbols = self._extract("el.style.setProperty('--angle', deg + 'deg');")

        self.assertEqual([s.label for s in symbols], ["--angle"])
        self.assertEqual(symbols[0].kind, "css_token_def")

    def test_double_quoted_names_are_found_too(self):
        symbols = self._extract('el.style.setProperty("--balloon-y", y);')

        self.assertEqual([s.label for s in symbols], ["--balloon-y"])

    def test_a_non_custom_property_is_not_a_token(self):
        symbols = self._extract("el.style.setProperty('color', 'red');")

        self.assertEqual(symbols, [])

    def test_a_runtime_built_name_is_not_guessed(self):
        symbols = self._extract("el.style.setProperty(`--${name}`, v);")

        self.assertEqual(symbols, [])

    def test_a_js_defined_token_resolves_a_css_var_reference(self):
        # The measured payoff: 64 of 128 "undefined" references were JS-defined.
        use = _symbol("css_token_use:token:--angle", "css_token_use", "--angle")
        js_defs = self._extract("el.style.setProperty('--angle', x);")

        edges = match_css_tokens(js_defs, [use])

        self.assertEqual([e.status for e in edges], [Status.CONNECTED])

    def test_a_token_defined_nowhere_is_still_unresolved(self):
        use = _symbol("css_token_use:token:--ghost", "css_token_use", "--ghost")

        edges = match_css_tokens([], [use])

        self.assertEqual([e.status for e in edges], [Status.UNRESOLVED])

    def test_every_js_token_carries_its_evidence(self):
        symbols = self._extract("el.style.setProperty('--angle', x);")

        self.assertTrue(symbols[0].file)
        self.assertTrue(symbols[0].line and symbols[0].line > 0)
        self.assertIn("setProperty", symbols[0].snippet)
