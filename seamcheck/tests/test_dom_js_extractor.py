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


class AttributeWritesAreDefinitionsTests(SimpleTestCase):
    """Code that WRITES an attribute asserts the element has it.

    `setAttribute('data-x', v)` was read as a read of `data-x`, so an attribute
    JavaScript creates and JavaScript reads had no definition anywhere and both sides
    were findings: `data-incremented-today` is set in `stats_manager.js` and read in
    `push_arena.js`, and the scan reported the reader as reaching for nothing.
    """

    def _write(self, text: str) -> str:
        import tempfile
        import textwrap

        path = Path(tempfile.mkdtemp()) / "writer.js"
        path.write_text(textwrap.dedent(text), encoding="utf-8")
        return str(path)

    def _definitions(self, text: str):
        from seamcheck.extractors.dom_js_extractor import extract_js_dom_definitions

        return [(s.sub, s.label) for s in extract_js_dom_definitions([self._write(text)])]

    def test_set_attribute_of_a_data_name_defines_it(self):
        self.assertIn(("data", "incremented-today"), self._definitions("""
            export function mark(el) {
              el.setAttribute('data-incremented-today', 'true');
            }
        """))

    def test_assigning_dataset_defines_it(self):
        self.assertIn(("data", "button-type"), self._definitions("""
            export function mark(el) {
              el.dataset.buttonType = 'quantum';
            }
        """))

    def test_a_read_is_not_a_definition(self):
        self.assertEqual(self._definitions("""
            export function read(el) {
              return el.getAttribute('data-incremented-today');
            }
        """), [])

    def test_a_string_that_names_an_attribute_is_read_as_a_reference(self):
        # The mapping-table shape: the name never appears as `dataset.x` or in a
        # getAttribute call anywhere, only as a string a loop later applies.
        found = extract_dom_selectors([self._write("""
            const MAP = [['daily_hours_active', 'data-modal-daily-hours']];
            MAP.forEach(([key, attr]) => document.querySelector('[' + attr + ']'));
        """)], [])
        rows = [(s.sub, s.label) for s in found if s.label == "modal-daily-hours"]
        self.assertEqual(rows, [("data:read", "modal-daily-hours")])

    def test_a_data_prefix_alone_is_not_a_name(self):
        found = extract_dom_selectors([self._write("""
            const partial = 'data-';
            const bare = 'data-x';
        """)], [])
        self.assertEqual([s.label for s in found if s.sub.startswith("data")], [])

    def test_one_symbol_per_name_and_line_however_many_ways_it_is_written(self):
        # `getAttribute('data-x')` is now seen twice - as an attribute call and as a
        # plain string - and two symbols under one id is two rows for one line.
        found = extract_dom_selectors([self._write("""
            export function read(el) {
              return el.getAttribute('data-incremented-today');
            }
        """)], [])
        rows = [s for s in found if s.label == "incremented-today"]
        self.assertEqual(len(rows), 1, [(s.sub, s.line) for s in rows])

    def test_a_single_word_data_attribute_is_defined_too(self):
        # F44 class 1: `_DATA_NAME_RE` requires two hyphenated segments (right for a bare
        # string found anywhere in the source), but this branch already knows it is the
        # first argument of a setAttribute call - `data-active`, one word, is a completely
        # real attribute name, and using the same two-segment check here meant a write to
        # it never defined anything: `button_manager.js:689 setAttribute('data-active',
        # 'true')` stayed reported as an unresolved READ of an attribute nothing produces.
        self.assertIn(("data", "active"), self._definitions("""
            export function select(el) {
              el.setAttribute('data-active', 'true');
            }
        """))

    def test_remove_attribute_of_a_data_name_defines_it_too(self):
        # `removeAttribute('data-state')` is just as much proof `data-state` is a real
        # attribute on this element as setting it - evidence, not a claim, works either
        # direction.
        self.assertIn(("data", "state"), self._definitions("""
            export function clear(el) {
              el.removeAttribute('data-state');
            }
        """))

    def test_toggle_attribute_of_a_data_name_defines_it_too(self):
        self.assertIn(("data", "busy"), self._definitions("""
            export function flip(el) {
              el.toggleAttribute('data-busy');
            }
        """))

    def test_a_name_held_in_a_const_is_resolved_at_the_write_site(self):
        # `const BUSY = 'data-ab-busy'` then `setAttribute(BUSY, '1')` elsewhere - the
        # call site passes an Identifier, not a Literal, so without resolving it through
        # the constant only the declaration line was ever visible as evidence and every
        # actual write through the name was invisible.
        self.assertIn(("data", "ab-busy"), self._definitions("""
            const BUSY = 'data-ab-busy';
            export function mark(el) {
              el.setAttribute(BUSY, '1');
            }
        """))

    def test_a_name_held_in_a_const_is_resolved_at_the_read_site(self):
        found = extract_dom_selectors([self._write("""
            const BUSY = 'data-ab-busy';
            export function check(el) {
              return el.hasAttribute(BUSY);
            }
        """)], [])
        rows = [(s.sub, s.label) for s in found if s.label == "ab-busy"]
        self.assertIn(("data:read", "ab-busy"), rows)


class SelectorCompoundTests(SimpleTestCase):
    """F44 classes 2 and 4: what a compound/combinator selector string resolves to."""

    def _write(self, text: str) -> str:
        import tempfile
        import textwrap

        path = Path(tempfile.mkdtemp()) / "app.js"
        path.write_text(textwrap.dedent(text), encoding="utf-8")
        return str(path)

    def test_an_escaped_dot_in_a_class_name_is_unescaped(self):
        # Tailwind spells `p-0.5` as `.p-0\.5` in a selector - CSS's own escape for a
        # character that would otherwise end the class token early. The naive
        # `[\w-]+` this replaced stopped at the backslash and reported the write as
        # `p-0`, which the CSS side (already unescaped, via the same pattern
        # css_extractor.py uses) never defines.
        #
        # Two backslashes in this Python source: one JS-level escape (so the JS string
        # literal's COOKED value keeps a real `\.`, exactly as a developer typing
        # `.p-0\.5` in actual JS source would produce - a single `\.` here would be
        # dropped by JS's own string-cooking before this extractor ever sees it).
        found = extract_dom_selectors([self._write(r"""
            function toggle(el) {
              document.querySelector('.rounded-full.p-0\\.5').classList.add('active');
            }
        """)], [])
        self.assertIn(("class:write", "p-0.5"), [(s.sub, s.label) for s in found])
        self.assertNotIn("p-0", [s.label for s in found])

    def test_a_descendant_selector_write_targets_only_the_last_compound(self):
        # `.nav-right .pbits-amount` names an ancestor SCOPE and the element itself -
        # only the element matching `.pbits-amount` is ever returned by querySelector,
        # and only it is ever the node a following assignment mutates. Attributing the
        # write to `.nav-right` too reported an ancestor scope as written.
        found = extract_dom_selectors([self._write("""
            function updatePrice(el) {
              document.querySelector('.nav-right .pbits-amount').textContent = '5';
            }
        """)], [])
        labels = [s.label for s in found if s.sub.endswith(":write")]
        self.assertEqual(labels, ["pbits-amount"])

    def test_a_write_selectors_ancestor_compound_is_still_checked_as_a_read(self):
        # The ancestor is never the WRITE target, but querySelector cannot match
        # anything unless it exists too - dropping its token entirely (an earlier
        # version of the last-compound fix) silently lost a true finding: `.goal-bar
        # .progress` with zero `.goal-bar` producers anywhere (push_arena.js:1485).
        found = extract_dom_selectors([self._write("""
            function initProgress(el) {
              const bar = document.querySelector('.goal-bar .progress');
              bar.style.width = '50%';
            }
        """)], [])
        rows = [(s.label, s.sub) for s in found]
        self.assertIn(("progress", "class:write"), rows)
        self.assertIn(("goal-bar", "class:read"), rows)

    def test_a_descendant_selector_read_still_sees_every_compound(self):
        # Reads keep the looser, existing segment-presence behaviour - connectivity
        # matching already treats that as a stated v1 limitation, a different and
        # looser question than "which element does this write actually touch".
        found = extract_dom_selectors([self._write("""
            function checkPrice(el) {
              return document.querySelector('.nav-right .pbits-amount').textContent;
            }
        """)], [])
        labels = sorted(s.label for s in found if s.sub.endswith(":read"))
        self.assertEqual(labels, ["nav-right", "pbits-amount"])

    def test_a_single_compound_with_two_classes_is_unaffected(self):
        # No combinator here at all - both classes belong to the SAME element, and
        # narrowing to "the last compound" must not narrow this any further.
        found = extract_dom_selectors([self._write("""
            function toggle(el) {
              document.querySelector('.rounded-full.active-state').classList.add('x');
            }
        """)], [])
        labels = sorted(s.label for s in found if s.sub.endswith(":write"))
        self.assertEqual(labels, ["active-state", "rounded-full"])


class NamedInAConstantTests(SimpleTestCase):
    """`var COUNTDOWN_ID = 'arena-next-season-countdown'` then `getElementById(ID)`.

    The lookup names a variable, so the reader recorded `getElementById(<runtime value>)`
    and the element - plainly rendered, plainly used - was reported as one nothing
    reaches. Following the variable is data-flow analysis this tool does not do;
    recognising the string is not.
    """

    def _selectors(self, source: str, declared: dict[str, str]):
        import tempfile
        import textwrap

        path = Path(tempfile.mkdtemp()) / "app.js"
        path.write_text(textwrap.dedent(source), encoding="utf-8")
        return extract_dom_selectors([str(path)], [], declared)

    def test_a_string_that_spells_a_rendered_element_is_evidence(self):
        found = self._selectors("""
            var COUNTDOWN_ID = 'arena-next-season-countdown';
            export function tick() { return document.getElementById(COUNTDOWN_ID); }
        """, {"arena-next-season-countdown": "id"})
        rows = [(s.sub, s.label) for s in found if s.label == "arena-next-season-countdown"]
        self.assertEqual(rows, [("id:string:evidence", "arena-next-season-countdown")])

    def test_it_can_never_invent_an_element(self):
        # Bounded by what the markup declares: a project's strings outnumber its symbols
        # by orders of magnitude, and a rule that emitted one symbol per string would
        # double the graph to say nothing.
        found = self._selectors("""
            const label = 'not-in-any-markup';
        """, {"something-else": "id"})
        self.assertEqual([s.label for s in found], [])

    def test_it_is_evidence_and_never_a_claim(self):
        # A string is not proof that a lookup happened. The sub ends in `:evidence`, which
        # is how the matcher knows never to raise it as a finding of its own.
        found = self._selectors("""
            const NAME = 'known-thing';
        """, {"known-thing": "class"})
        self.assertTrue(all(s.sub.endswith(":evidence") for s in found), found)
        self.assertEqual([s.sub for s in found], ["class:string:evidence"])

    def test_one_symbol_per_name_per_file(self):
        found = self._selectors("""
            const A = 'known-thing';
            const B = 'known-thing';
            const C = ['known-thing', 'known-thing'];
        """, {"known-thing": "id"})
        self.assertEqual(len(found), 1, [(s.sub, s.line) for s in found])


class JsxIsMarkupTests(SimpleTestCase):
    """In a React codebase the component file IS the markup, and it was never read.

    saleor-dashboard's CSS modules query `[data-test-id="swatch-preview"]`,
    `[data-state]` and `[data-highlighted]`; every one of those attributes is written in
    a sibling `.tsx`, and the scan - which read only Django templates as markup - called
    all twelve of them elements nothing renders.
    """

    def _definitions(self, source: str, name: str = "Widget.tsx"):
        import tempfile
        import textwrap

        from seamcheck.extractors.dom_js_extractor import extract_js_dom_definitions

        path = Path(tempfile.mkdtemp()) / name
        path.write_text(textwrap.dedent(source), encoding="utf-8")
        return sorted((s.sub, s.label) for s in extract_js_dom_definitions([str(path)]))

    def test_an_id_written_in_jsx_declares_the_element(self):
        self.assertIn(("id", "swatch-root"), self._definitions("""
            export const Swatch = () => <div id="swatch-root" />;
        """))

    def test_a_data_attribute_written_in_jsx_declares_it(self):
        found = self._definitions("""
            export const Row = ({ hot }) => (
              <div data-test-id="swatch-preview" data-highlighted={hot} />
            );
        """)
        self.assertIn(("data", "test-id"), found)
        # The NAME is the declaration even when the value is a variable: a selector for
        # the attribute is not reaching for something nobody writes.
        self.assertIn(("data", "highlighted"), found)

    def test_a_class_written_in_jsx_declares_it(self):
        self.assertIn(("class", "swatch"), self._definitions("""
            export const Swatch = () => <div className="swatch tall" />;
        """))

    def test_a_selector_in_a_string_does_not_declare_an_element(self):
        # `\b` matches between the `-` and the `id` of `data-test-id`, so this SELECTOR
        # was read as markup declaring `id="swatch-preview"`. Inventing an element is
        # worse than missing one: it silences a true finding about a missing element.
        self.assertEqual(self._definitions("""
            export function find() {
              return document.querySelector('[data-test-id="swatch-preview"]');
            }
        """), [])

    def test_a_data_class_attribute_declares_the_attribute_not_the_class(self):
        # `data-class="x"` is an attribute named `data-class`. It declares that attribute
        # and it does NOT declare a CSS class - which is the half `\b` used to get wrong.
        found = self._definitions("""
            export const html = '<div data-class="not-a-class"></div>';
        """)
        self.assertIn(("data", "class"), found)
        self.assertEqual([f for f in found if f[0] == "class"], [])

    def test_a_data_word_in_prose_declares_nothing(self):
        # Only inside something that is actually markup. Inventing an element is worse
        # than missing one: it silences a true finding about an element that is absent.
        self.assertEqual(self._definitions("""
            export const help = 'see the data-migration guide for details';
        """), [])

    def test_real_generated_markup_still_declares(self):
        # The rule that had to survive the boundary fix: markup built as a string is how
        # most JS-created elements arrive.
        found = self._definitions("""
            export function render(el) {
              el.innerHTML = '<div id="live-one" class="live-two"></div>';
            }
        """)
        self.assertIn(("id", "live-one"), found)
        self.assertIn(("class", "live-two"), found)
