from pathlib import Path

from django.test import SimpleTestCase

from signal_map.extractors.django_extractor import extract_django_urls_views
from signal_map.extractors.js_extractor import extract_js
from signal_map.graph import Status
from signal_map.matcher import match_js_to_django

URLCONF = "signal_map.tests.fixtures.fixture_urls"
FIXTURES_DIR = str(Path(__file__).parent / "fixtures")


class MatcherTests(SimpleTestCase):
    def setUp(self):
        self.django_symbols, _ = extract_django_urls_views(URLCONF)
        self.js_symbols, _ = extract_js(["fixture_entry.js"], FIXTURES_DIR)
        self.edges = match_js_to_django(self.django_symbols, self.js_symbols)

    def _edges_from(self, label):
        target = next(s for s in self.js_symbols if s.kind == "fetch_target" and s.label == label)
        return [e for e in self.edges if e.from_id == target.id]

    def test_matches_a_real_endpoint(self):
        matching = self._edges_from("/api/get-thing/")

        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0].status, Status.CONNECTED)

    def test_unresolved_when_no_matching_url_exists(self):
        matching = self._edges_from("/api/does-not-exist/")

        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0].status, Status.UNRESOLVED)

    def test_every_fetch_target_gets_exactly_one_edge(self):
        targets = [s for s in self.js_symbols if s.kind == "fetch_target"]

        self.assertEqual(len(self.edges), len(targets))
