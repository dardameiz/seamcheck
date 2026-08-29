from pathlib import Path

from django.test import SimpleTestCase

from signal_map.extractors.asgi_extractor import extract_asgi_routes
from signal_map.graph import Status

FIXTURE = str(Path(__file__).parent / "fixtures" / "fixture_asgi.py")


class AsgiExtractorTests(SimpleTestCase):
    def setUp(self):
        self.symbols = extract_asgi_routes(FIXTURE)
        self.labels = {s.label for s in self.symbols}

    def test_finds_routes_compared_with_in(self):
        # Labels drop the leading slash to match the Django url-symbol convention;
        # keeping it would guarantee the matcher never resolves them.
        self.assertIn("submit_push/", self.labels)
        self.assertNotIn("submit_push", self.labels)  # slash variants collapse

    def test_finds_routes_compared_with_equality(self):
        self.assertIn("health/", self.labels)

    def test_ignores_strings_that_are_not_path_comparisons(self):
        self.assertNotIn("not a path", self.labels)

    def test_routes_are_connected_with_evidence(self):
        for symbol in self.symbols:
            self.assertEqual(symbol.status, Status.CONNECTED)
            self.assertEqual(symbol.kind, "url")
            self.assertTrue(symbol.line and symbol.line > 0)
            self.assertIn("asgi", symbol.note.lower())


class RouteDeduplicationTests(SimpleTestCase):
    def test_slash_variants_of_one_route_collapse_to_one_symbol(self):
        # ("/submit_push/", "/submit_push") is one endpoint written two ways. Emitting
        # both leaves a twin the matcher can never resolve.
        symbols = extract_asgi_routes(FIXTURE)

        labels = [s.label for s in symbols]
        self.assertEqual(len(labels), len(set(labels)))
        self.assertIn("submit_push/", labels)
        self.assertNotIn("submit_push", labels)
