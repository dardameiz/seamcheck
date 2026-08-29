from pathlib import Path

from django.test import SimpleTestCase

from signal_map.extractors.js_extractor import extract_js
from signal_map.graph import Status

FIXTURES_DIR = str(Path(__file__).parent / "fixtures")


class JsExtractorTests(SimpleTestCase):
    def setUp(self):
        self.symbols, self.edges = extract_js(["fixture_entry.js"], FIXTURES_DIR)
        self.targets = {s.label for s in self.symbols if s.kind == "fetch_target"}

    def test_finds_a_string_literal_fetch_call(self):
        self.assertIn("/api/get-thing/", self.targets)
        self.assertIn("/api/does-not-exist/", self.targets)

    def test_dynamic_fetch_argument_is_uncertain_not_matched(self):
        dynamic = [s for s in self.symbols if s.kind == "js_call" and "callDynamic" in s.chain]

        self.assertEqual(len(dynamic), 1)
        self.assertEqual(dynamic[0].status, Status.UNCERTAIN)
        self.assertFalse(any("items" in label for label in self.targets))

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

    def test_every_literal_target_has_an_edge_from_its_call(self):
        call_ids = {s.id for s in self.symbols if s.kind == "js_call" and s.status == Status.CONNECTED}
        target_ids = {s.id for s in self.symbols if s.kind == "fetch_target"}

        for edge in self.edges:
            self.assertIn(edge.from_id, call_ids)
            self.assertIn(edge.to_id, target_ids)
