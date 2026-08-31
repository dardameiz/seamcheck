"""The two CSS claims the tool must not make while their evidence is unread.

Measured on this project before these guards existed: 5,318 selectors reported unused
while 3,205 JS class-application sites went unread, and 128 undefined token references
of which exactly half were defined at runtime by JS.
"""

import tempfile

from django.test import SimpleTestCase

from seamcheck.dom_matcher import match_css_selectors, match_css_tokens
from seamcheck.extractors.dom_js_extractor import extract_js_css_tokens
from seamcheck.graph import Status, Symbol


def _symbol(id_, kind, label, status=Status.UNCERTAIN):
    return Symbol(
        id=id_, kind=kind, label=label, sub="token" if "token" in kind else "class",
        file="a.css", line=1, status=status, snippet=label, chain=[label], note="",
    )


class CssSelectorUnusedIsEarnedNotAssumedTests(SimpleTestCase):
    """This used to be a blanket downgrade, and the reason it no longer is.

    Every unreferenced CSS rule was forced to `uncertain`, because the matcher could not
    see className=, classList.add, setAttribute('class') or class literals in JS template
    strings - 3,205 unread sites, so "nothing uses this" was unearned.

    All four are read now. The downgrade was therefore no longer caution: it was holding
    3,878 rules whose names appear in NO source file inside the status that means
    "unmeasured". The one real hole left is a name assembled at runtime, and that is
    detectable, so it is detected rather than assumed everywhere.
    """

    def _css(self, label):
        return Symbol(id=f"css_selector:class:{label}", kind="css_selector", label=label,
                      sub="class", file="a.css", line=1, status=Status.UNCERTAIN,
                      snippet=label, chain=[], note="")

    def test_a_rule_whose_name_appears_nowhere_is_unused(self):
        edges = match_css_selectors([], [], [self._css("hint-warm")], set())

        self.assertEqual([e.status for e in edges], [Status.UNUSED])

    def test_a_rule_whose_name_could_be_assembled_stays_uncertain(self):
        # `'pb-badge-' + kind` produces pb-badge-success, and only the stem is in the
        # source. The rule is live and unprovable at once, so it must not be called dead.
        applied = Symbol(id="dom_selector:class:pb-badge-:a.js:1", kind="dom_selector",
                         label="pb-badge-", sub="class:stem", file="a.js", line=1,
                         status=Status.UNCERTAIN, snippet="", chain=[], note="")

        edges = match_css_selectors([applied], [], [self._css("pb-badge-success")], set())

        self.assertIn(Status.UNCERTAIN, [e.status for e in edges])

    def test_a_shared_opening_that_is_not_a_separator_does_not_excuse_a_rule(self):
        # Requiring the stem to end in - or _ is what stops this matching every class that
        # happens to share three letters with another.
        applied = Symbol(id="dom_selector:class:hint:a.js:1", kind="dom_selector",
                         label="hint", sub="class:stem", file="a.js", line=1,
                         status=Status.UNCERTAIN, snippet="", chain=[], note="")

        edges = match_css_selectors([applied], [], [self._css("hintwarm")], set())

        self.assertEqual([e.status for e in edges if e.to_id.endswith("hintwarm")],
                         [Status.UNUSED])

    def test_an_id_rule_is_never_excused_by_a_prefix(self):
        # Ids are not assembled from families the way utility classes are.
        css = Symbol(id="css_selector:id:thing-x", kind="css_selector", label="thing-x",
                     sub="id", file="a.css", line=1, status=Status.UNCERTAIN,
                     snippet="", chain=[], note="")
        applied = Symbol(id="dom_selector:class:thing-:a.js:1", kind="dom_selector",
                         label="thing-", sub="class:stem", file="a.js", line=1,
                         status=Status.UNCERTAIN, snippet="", chain=[], note="")

        edges = match_css_selectors([applied], [], [css], set())

        self.assertEqual([e.status for e in edges], [Status.UNUSED])


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
