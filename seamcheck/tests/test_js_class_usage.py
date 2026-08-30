"""Classes JavaScript applies at runtime — the largest recall gap the tool had.

Measured before this extractor existed: 3,205 class-application sites unread in
buttons/js alone, and 5,318 CSS selectors reported with no evidence either way.
"""

import tempfile

from django.test import SimpleTestCase

from seamcheck.dom_matcher import detect_multi_writers, match_css_selectors
from seamcheck.extractors.dom_js_extractor import extract_js_class_usages
from seamcheck.graph import Status, Symbol


def _extract(source):
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as handle:
        handle.write(source)
        path = handle.name
    return extract_js_class_usages([path])


def _labels(source):
    return {symbol.label for symbol in _extract(source)}


class ClassNameAssignmentTests(SimpleTestCase):
    def test_a_single_class_assignment_is_found(self):
        self.assertEqual(_labels("star.className = 'ab-star';"), {"ab-star"})

    def test_a_multi_token_assignment_yields_every_class(self):
        self.assertEqual(_labels('el.className = "fp-btn fp-btn-primary";'), {"fp-btn", "fp-btn-primary"})

    def test_this_property_assignment_is_found(self):
        self.assertEqual(_labels("this.container.className = 'aurora-container';"), {"aurora-container"})

    def test_static_parts_of_a_template_literal_are_kept(self):
        # `class-${n}` yields nothing guessable, but a static neighbour still counts.
        self.assertEqual(_labels("el.className = `ab-star ${extra}`;"), {"ab-star"})


class ClassListTests(SimpleTestCase):
    def test_class_list_add_is_found(self):
        self.assertEqual(_labels("el.classList.add('ab-star-bright');"), {"ab-star-bright"})

    def test_every_argument_is_found(self):
        self.assertEqual(_labels("el.classList.add('a', 'b');"), {"a", "b"})

    def test_remove_and_toggle_count_as_usage(self):
        # Removing a class proves the stylesheet rule is referenced by live code.
        self.assertEqual(_labels("el.classList.remove('gone'); el.classList.toggle('on');"), {"gone", "on"})


class SetAttributeTests(SimpleTestCase):
    def test_set_attribute_class_is_found(self):
        self.assertEqual(_labels("el.setAttribute('class', 'ab-ribbon wide');"), {"ab-ribbon", "wide"})

    def test_set_attribute_of_another_name_is_not_a_class(self):
        self.assertEqual(_labels("el.setAttribute('id', 'not-a-class');"), set())


class MarkupStringTests(SimpleTestCase):
    def test_a_class_attribute_inside_generated_markup_is_found(self):
        self.assertEqual(_labels("""el.innerHTML = '<div class="card glow"></div>';"""), {"card", "glow"})

    def test_a_class_attribute_inside_a_template_literal_is_found(self):
        self.assertEqual(_labels('el.innerHTML = `<div class="row ${cls}">x</div>`;'), {"row"})


class IntegrationTests(SimpleTestCase):
    def _css(self, label):
        return Symbol(
            id=f"css_selector:class:{label}", kind="css_selector", label=label, sub="class",
            file="a.css", line=1, status=Status.UNCERTAIN, snippet=f".{label}", chain=[label], note="",
        )

    def test_a_js_applied_class_resolves_its_css_rule(self):
        usages = _extract("star.className = 'ab-star';")

        edges = match_css_selectors(usages, [], [self._css("ab-star")], set())

        self.assertIn(Status.CONNECTED, {edge.status for edge in edges})

    def test_class_usages_never_become_multi_writer_findings(self):
        # Twenty modules adding `.active` is normal; only element writes are the bug.
        usages = _extract("a.classList.add('active');") + _extract("b.classList.add('active');")

        self.assertEqual(detect_multi_writers(usages), [])


class InterpolationFragmentTests(SimpleTestCase):
    def test_an_interpolated_name_yields_no_fragment(self):
        # `bb-spark-${i}` is one runtime name, not a class called "bb-spark-".
        # 319 such fragments were reported before this guard.
        self.assertEqual(_labels("el.className = `bb-spark-${i}`;"), set())

    def test_a_leading_interpolation_yields_no_fragment(self):
        self.assertEqual(_labels("el.className = `${prefix}-celebration`;"), set())

    def test_a_whole_token_beside_an_interpolation_is_still_kept(self):
        self.assertEqual(_labels("el.className = `ab-star ${extra}`;"), {"ab-star"})


class AppliedClassIsNeverAFindingTests(SimpleTestCase):
    def test_an_applied_class_with_no_css_rule_produces_no_finding(self):
        # `balance-counter` is applied in markup and then queried with
        # querySelector('.balance-counter') - a JS hook, not a broken style.
        usages = _extract("el.className = 'balance-counter';")

        edges = match_css_selectors(usages, [], [], set())

        self.assertEqual(edges, [])

    def test_a_template_element_with_no_css_rule_is_still_a_finding(self):
        # Only applied classes get the pass; a template attribute nothing reaches
        # remains reportable.
        attr = Symbol(
            id="dom_attr:class:ghost:t.html:1", kind="dom_attr", label="ghost", sub="class",
            file="t.html", line=1, status=Status.UNCERTAIN, snippet="", chain=["ghost"], note="",
        )

        edges = match_css_selectors([], [attr], [], set())

        self.assertEqual([e.status for e in edges], [Status.UNRESOLVED])


class AppliedClassNeedsNoTemplateElementTests(SimpleTestCase):
    def test_an_applied_class_produces_no_dom_match_finding(self):
        # JavaScript that creates the element also creates the class; requiring a
        # template attribute to match would report every generated element as broken.
        from seamcheck.dom_matcher import match_dom_selectors

        usages = _extract("el.className = 'made-by-js';")

        self.assertEqual(match_dom_selectors([], usages), [])


class JsInjectedCssTokenTests(SimpleTestCase):
    def test_a_var_reference_inside_injected_css_counts_as_a_use(self):
        # stats_manager.js sets --fall-duration and consumes it in a stylesheet it
        # injects. Reading only .css files reported that working token as unused.
        import tempfile

        from seamcheck.dom_matcher import match_css_tokens
        from seamcheck.extractors.dom_js_extractor import extract_js_css_tokens
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as handle:
            handle.write(
                "el.style.setProperty('--fall-duration', d);\n"
                "sheet.textContent = `.confetti { animation-duration: var(--fall-duration); }`;"
            )
            path = handle.name

        symbols = extract_js_css_tokens([path])
        defs = [s for s in symbols if s.kind == "css_token_def"]
        uses = [s for s in symbols if s.kind == "css_token_use"]
        edges = match_css_tokens(defs, uses)

        self.assertEqual([s.label for s in defs], ["--fall-duration"])
        self.assertEqual([s.label for s in uses], ["--fall-duration"])
        self.assertEqual([e.status for e in edges], [Status.CONNECTED])
