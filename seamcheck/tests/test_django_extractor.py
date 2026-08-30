from django.test import SimpleTestCase

from seamcheck.extractors.django_extractor import extract_django_urls_views
from seamcheck.graph import Status

URLCONF = "seamcheck.tests.fixtures.fixture_urls"


class DjangoExtractorTests(SimpleTestCase):
    def setUp(self):
        self.symbols, self.edges = extract_django_urls_views(URLCONF)

    def _url(self, label):
        return next(s for s in self.symbols if s.kind == "url" and s.label == label)

    def _view(self, label):
        return next(s for s in self.symbols if s.kind == "view" and s.label == label)

    def test_extracts_a_symbol_per_flat_url_and_view(self):
        url_labels = {s.label for s in self.symbols if s.kind == "url"}
        view_labels = {s.label for s in self.symbols if s.kind == "view"}

        self.assertIn("api/get-thing/", url_labels)
        self.assertIn("api/orphan/", url_labels)
        self.assertIn("get_thing", view_labels)
        self.assertIn("orphan_view", view_labels)

    def test_every_flat_url_has_a_certain_edge_to_its_view(self):
        matching = [
            e for e in self.edges
            if e.from_id == self._url("api/get-thing/").id
            and e.to_id == self._view("get_thing").id
        ]

        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0].status, Status.CONNECTED)

    def test_walks_into_nested_include_and_joins_the_full_path(self):
        url_labels = {s.label for s in self.symbols if s.kind == "url"}

        self.assertIn("sub/nested/", url_labels)
        matching = [
            e for e in self.edges
            if e.from_id == self._url("sub/nested/").id
            and e.to_id == self._view("nested_thing").id
        ]
        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0].status, Status.CONNECTED)

    def test_view_symbol_is_not_duplicated_across_multiple_urls(self):
        view_ids = [s.id for s in self.symbols if s.kind == "view"]

        self.assertEqual(len(view_ids), len(set(view_ids)))

    def test_a_view_behind_two_urls_still_gets_two_edges(self):
        view_id = self._view("get_thing").id
        edges_in = [e for e in self.edges if e.to_id == view_id]

        self.assertEqual(len(edges_in), 2)

    def test_every_symbol_carries_its_evidence(self):
        for symbol in self.symbols:
            self.assertTrue(symbol.id, symbol)
            self.assertTrue(symbol.snippet, symbol)
            if symbol.kind == "view":
                self.assertTrue(symbol.file.endswith("fixture_views.py"), symbol)
                self.assertIsNotNone(symbol.line, symbol)


class FirstPartyFilterTests(SimpleTestCase):
    def test_without_prefixes_every_route_is_emitted(self):
        symbols, _ = extract_django_urls_views(URLCONF)

        self.assertTrue(any(s.kind == "view" for s in symbols))

    def test_prefixes_exclude_routes_owned_by_other_packages(self):
        # Django's own admin is 88% of this project's URL table; without the filter the
        # map is mostly third-party routes the reader cannot act on.
        symbols, _ = extract_django_urls_views(URLCONF, first_party_prefixes=["nothing_matches"])

        self.assertEqual(symbols, [])

    def test_prefixes_keep_matching_routes(self):
        symbols, _ = extract_django_urls_views(URLCONF, first_party_prefixes=["seamcheck"])

        self.assertIn("get_thing", {s.label for s in symbols if s.kind == "view"})
