from pathlib import Path

from django.test import SimpleTestCase

from seamcheck.extractors.js_extractor import extract_js
from seamcheck.graph import Status

FIXTURES_DIR = str(Path(__file__).parent / "fixtures")


class JsExtractorTests(SimpleTestCase):
    def setUp(self):
        self.symbols, self.edges = extract_js(["fixture_entry.js"], FIXTURES_DIR)
        self.targets = {s.label for s in self.symbols if s.kind == "fetch_target"}

    def test_finds_a_string_literal_fetch_call(self):
        self.assertIn("/api/get-thing/", self.targets)
        self.assertIn("/api/does-not-exist/", self.targets)

    def test_dynamic_fetch_keeps_the_known_prefix_and_claims_nothing_more(self):
        # `fetch(`/api/items/${id}/`)` used to produce no target at all, so the endpoint
        # read as one nothing calls. The prefix is real evidence and is recorded; which
        # route it reaches is not, so the symbol stays uncertain and says why.
        dynamic = [s for s in self.symbols if s.kind == "js_call" and "callDynamic" in s.chain]

        self.assertEqual(len(dynamic), 1)
        self.assertEqual(dynamic[0].status, Status.UNCERTAIN)
        self.assertIn("<runtime value>", dynamic[0].snippet)

        prefix = [s for s in self.symbols if s.kind == "fetch_target" and s.label == "/api/items/"]
        self.assertEqual([s.status for s in prefix], [Status.UNCERTAIN])
        self.assertIn("not proven", prefix[0].note)

    def test_follows_static_imports_from_the_entry(self):
        files_seen = {s.file for s in self.symbols if s.kind == "js_call"}

        self.assertTrue(any("fixture_module.js" in f for f in files_seen))

    def test_names_the_enclosing_arrow_function(self):
        # Arrow functions have no `id`; the name lives on the VariableDeclarator that
        # holds them. Without that, evidence for most modern JS is an empty chain.
        arrow = [s for s in self.symbols if s.kind == "js_call" and "arrowCaller" in s.chain]

        self.assertEqual(len(arrow), 1)

    def test_names_the_enclosing_class_method(self):
        method = [s for s in self.symbols if s.kind == "js_call" and "report" in s.chain]

        self.assertEqual(len(method), 1)

    def test_finds_send_beacon_targets(self):
        self.assertIn("/api/log/", self.targets)

    def test_ignores_bare_specifiers_and_non_js_imports(self):
        # `import gsap from 'gsap'` and a .css import must not be walked into or crash.
        self.assertTrue(all(f.endswith(".js") for f in {s.file for s in self.symbols}))

    def test_every_target_has_an_edge_from_its_call(self):
        # Includes the uncertain ones: a prefix-only target is still reached from a call,
        # and dropping its edge left the call looking like it went nowhere.
        call_ids = {s.id for s in self.symbols if s.kind == "js_call"}
        target_ids = {s.id for s in self.symbols if s.kind == "fetch_target"}

        for edge in self.edges:
            self.assertIn(edge.from_id, call_ids)
            self.assertIn(edge.to_id, target_ids)


class JsFileDiscoveryTests(SimpleTestCase):
    def test_discovery_returns_the_transitive_module_set(self):
        # The DOM extractor takes a flat file list; handing it only entry points hides
        # every DOM write made by an imported module.
        from seamcheck.extractors.js_extractor import discover_js_files

        files = discover_js_files(["fixture_entry.js"], FIXTURES_DIR)

        self.assertTrue(any(f.endswith("fixture_entry.js") for f in files))
        self.assertTrue(any(f.endswith("fixture_module.js") for f in files))


class DisplayStringsAreNotEndpointsTests(SimpleTestCase):
    """`textContent = '/24'` is the "/24" in "period 3/24", not a route.

    Both shapes shipped as fetch targets on the reference project. A URL-shaped string
    is only a sighting to begin with - never evidence either way - so this is noise
    rather than a false claim, and noise in this list is what stops people reading it.
    """

    def _targets(self, text: str):
        import tempfile
        import textwrap

        path = Path(tempfile.mkdtemp()) / "app.js"
        path.write_text(textwrap.dedent(text), encoding="utf-8")
        symbols, _ = extract_js([str(path)], str(path.parent))
        return sorted(s.label for s in symbols if s.kind == "fetch_target")

    def test_a_path_written_into_an_element_is_not_an_endpoint(self):
        self.assertEqual(self._targets("""
            export function show(el) {
              el.textContent = '/api/periods/';
            }
        """), [])

    def test_a_slash_and_digits_is_not_an_endpoint(self):
        self.assertEqual(self._targets("""
            export function show(el) {
              const label = '/24';
              return label;
            }
        """), [])

    def test_a_real_path_in_a_variable_is_still_seen(self):
        # The sighting this pass exists for: an endpoint named by a literal that some
        # helper later fetches. Losing these was the risk of both rules above.
        self.assertEqual(self._targets("""
            const ENDPOINT = '/api/orders/';
            export function load() { return call(ENDPOINT); }
        """), ["/api/orders/"])
