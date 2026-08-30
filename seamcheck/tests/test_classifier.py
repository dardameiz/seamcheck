from pathlib import Path

from django.test import SimpleTestCase

from seamcheck.classifier import classify
from seamcheck.extractors.django_extractor import extract_django_urls_views
from seamcheck.extractors.entry_points_extractor import extract_entry_points
from seamcheck.extractors.js_extractor import extract_js
from seamcheck.graph import Status
from seamcheck.matcher import match_js_to_django

URLCONF = "seamcheck.tests.fixtures.fixture_urls"
FIXTURES_DIR = str(Path(__file__).parent / "fixtures")


class ClassifierTests(SimpleTestCase):
    def setUp(self):
        django_symbols, django_edges = extract_django_urls_views(URLCONF)
        js_symbols, js_edges = extract_js(["fixture_entry.js"], FIXTURES_DIR)
        match_edges = match_js_to_django(django_symbols, js_symbols)
        self.classified = classify(django_symbols + js_symbols, django_edges + js_edges + match_edges)

    def _find(self, label, kind=None):
        return next(
            s for s in self.classified if s.label == label and (kind is None or s.kind == kind)
        )

    def test_view_reached_by_js_is_connected(self):
        self.assertEqual(self._find("get_thing", "view").status, Status.CONNECTED)

    def test_view_with_no_js_caller_is_not_claimed_connected(self):
        # A url -> view edge means "this URL routes here", not "something calls it".
        # Treating routing as usage makes every view CONNECTED and the tool useless.
        self.assertNotEqual(self._find("orphan_view", "view").status, Status.CONNECTED)

    def test_fetch_to_nonexistent_url_is_unresolved(self):
        self.assertEqual(self._find("/api/does-not-exist/", "fetch_target").status, Status.UNRESOLVED)

    def test_uncertain_status_is_never_overwritten(self):
        dynamic = [s for s in self.classified if s.kind == "js_call" and s.status == Status.UNCERTAIN]

        self.assertTrue(dynamic)

    def test_signal_receiver_stays_connected_untouched_by_classifier(self):
        entry_symbols = extract_entry_points({str(Path(FIXTURES_DIR) / "fixture_signals.py")})

        classified = classify(entry_symbols, [])

        self.assertTrue(all(s.status == Status.CONNECTED for s in classified))
