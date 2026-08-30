from django.test import SimpleTestCase

from seamcheck.dom_matcher import (
    detect_multi_writers,
    match_css_selectors,
    match_css_tokens,
    match_dom_selectors,
)
from seamcheck.graph import Status, Symbol
from seamcheck.triage import fingerprint_for_symbol


def _attr(value, sub="id"):
    return Symbol(
        id=f"dom_attr:{sub}:{value}:t.html:1", kind="dom_attr", label=value, sub=sub,
        file="t.html", line=1, status=Status.UNCERTAIN, snippet="", chain=[value], note="",
    )


def _selector(value, sub="id:write", file="a.js", line=1):
    return Symbol(
        id=f"dom_selector:{value}:{file}:{line}", kind="dom_selector", label=value, sub=sub,
        file=file, line=line, status=Status.UNCERTAIN, snippet="", chain=[value], note="",
    )


class DomMatchTests(SimpleTestCase):
    def test_a_selector_with_a_matching_template_element_is_connected(self):
        edges = match_dom_selectors([_attr("shared-counter")], [_selector("shared-counter")])

        self.assertEqual(len(edges), 1)
        self.assertEqual(edges[0].status, Status.CONNECTED)

    def test_a_selector_with_no_template_element_is_unresolved(self):
        edges = match_dom_selectors([_attr("other")], [_selector("ghost")])

        self.assertEqual(edges[0].status, Status.UNRESOLVED)

    def test_class_selectors_match_class_attributes_not_ids(self):
        edges = match_dom_selectors(
            [_attr("stat-value", sub="class")], [_selector("stat-value", sub="class:read")]
        )

        self.assertEqual(edges[0].status, Status.CONNECTED)

    def test_an_id_selector_does_not_match_a_same_named_class(self):
        edges = match_dom_selectors(
            [_attr("thing", sub="class")], [_selector("thing", sub="id:read")]
        )

        self.assertEqual(edges[0].status, Status.UNRESOLVED)

    def test_a_runtime_selector_produces_no_edge_at_all(self):
        self.assertEqual(match_dom_selectors([_attr("x")], [_selector("<dynamic>")]), [])


class MultiWriterTests(SimpleTestCase):
    def test_two_files_writing_the_same_id_are_flagged(self):
        flagged = detect_multi_writers(
            [_selector("shared-counter", file="a.js"), _selector("shared-counter", file="b.js")]
        )

        self.assertEqual(len(flagged), 1)
        self.assertEqual(flagged[0].kind, "multi_writer_element")
        self.assertEqual(set(flagged[0].chain), {"a.js", "b.js"})

    def test_one_writer_and_one_reader_is_not_flagged(self):
        flagged = detect_multi_writers(
            [
                _selector("shared-counter", file="a.js"),
                _selector("shared-counter", sub="id:read", file="b.js"),
            ]
        )

        self.assertEqual(flagged, [])

    def test_two_writes_from_the_same_file_are_not_flagged(self):
        # One module writing an element twice is normal; the bug is two owners.
        flagged = detect_multi_writers(
            [_selector("c", file="a.js", line=1), _selector("c", file="a.js", line=9)]
        )

        self.assertEqual(flagged, [])

    def test_the_note_names_the_writers(self):
        flagged = detect_multi_writers(
            [_selector("counter", file="a.js"), _selector("counter", file="b.js")]
        )

        self.assertIn("a.js", flagged[0].note)
        self.assertIn("b.js", flagged[0].note)


class MultiWriterFingerprintTests(SimpleTestCase):
    def _flagged(self, files):
        return detect_multi_writers([_selector("counter", file=f) for f in files])[0]

    def test_fingerprint_covers_the_writer_set(self):
        two = self._flagged(["a.js", "b.js"])
        three = self._flagged(["a.js", "b.js", "c.js"])

        self.assertNotEqual(fingerprint_for_symbol(two), fingerprint_for_symbol(three))

    def test_fingerprint_is_stable_for_the_same_writer_set(self):
        self.assertEqual(
            fingerprint_for_symbol(self._flagged(["a.js", "b.js"])),
            fingerprint_for_symbol(self._flagged(["b.js", "a.js"])),
        )


def _css(label, sub="class"):
    return Symbol(
        id=f"css_selector:{sub}:{label}", kind="css_selector", label=label, sub=sub,
        file="a.css", line=1, status=Status.UNCERTAIN, snippet=f".{label}", chain=[label], note="",
    )


def _token(label, kind="css_token_def"):
    return Symbol(
        id=f"{kind}:token:{label}", kind=kind, label=label, sub="token", file="a.css", line=1,
        status=Status.UNCERTAIN, snippet=label, chain=[label], note="",
    )


class CssSelectorMatchTests(SimpleTestCase):
    def test_a_used_class_with_a_css_rule_is_connected(self):
        edges = match_css_selectors([], [_attr("fp-btn", sub="class")], [_css("fp-btn")], set())

        self.assertEqual([e.status for e in edges], [Status.CONNECTED])

    def test_a_css_rule_nothing_uses_is_unused(self):
        edges = match_css_selectors([], [], [_css("orphan-rule")], set())

        self.assertEqual([e.status for e in edges], [Status.UNUSED])

    def test_a_used_class_with_no_rule_anywhere_is_unresolved(self):
        edges = match_css_selectors([], [_attr("ghost", sub="class")], [], set())

        self.assertEqual([e.status for e in edges], [Status.UNRESOLVED])

    def test_a_tailwind_generated_class_is_not_reported_unresolved(self):
        # Without the compiled-utility set, every Tailwind class in every template reads
        # as a broken reference.
        edges = match_css_selectors(
            [], [_attr("bg-slate-900", sub="class")], [], {"bg-slate-900"}
        )

        self.assertEqual([e.status for e in edges], [Status.CONNECTED])

    def test_tailwind_rescue_never_applies_to_an_id(self):
        edges = match_css_selectors([], [_attr("bg-slate-900", sub="id")], [], {"bg-slate-900"})

        self.assertEqual([e.status for e in edges], [Status.UNRESOLVED])


class CssTokenMatchTests(SimpleTestCase):
    def test_a_defined_and_consumed_token_is_connected(self):
        edges = match_css_tokens([_token("--x")], [_token("--x", "css_token_use")])

        self.assertEqual([e.status for e in edges], [Status.CONNECTED])

    def test_a_token_defined_but_never_consumed_is_unused(self):
        edges = match_css_tokens([_token("--dead")], [])

        self.assertEqual([e.status for e in edges], [Status.UNUSED])

    def test_a_token_consumed_but_never_defined_is_unresolved(self):
        edges = match_css_tokens([], [_token("--ghost", "css_token_use")])

        self.assertEqual([e.status for e in edges], [Status.UNRESOLVED])
