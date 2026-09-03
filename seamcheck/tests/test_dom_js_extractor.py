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

    def test_a_data_class_attribute_does_not_declare_a_class(self):
        self.assertEqual(self._definitions("""
            export const html = '<div data-class="not-a-class"></div>';
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
