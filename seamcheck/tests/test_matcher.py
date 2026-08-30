from pathlib import Path

from django.test import SimpleTestCase

from seamcheck.extractors.django_extractor import extract_django_urls_views
from seamcheck.extractors.js_extractor import extract_js
from seamcheck.graph import Status, Symbol
from seamcheck.matcher import match_js_to_django

URLCONF = "seamcheck.tests.fixtures.fixture_urls"
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


class ParameterisedRouteTests(SimpleTestCase):
    def _url(self, path):
        return Symbol(id=f"url:{path}", kind="url", label=path, sub="GET/POST",
                      file="urls.py", line=1, status=Status.UNCERTAIN, snippet="",
                      chain=[path], note="")

    def _fetch(self, target):
        return Symbol(id=f"fetch:{target}", kind="fetch_target", label=target, sub="",
                      file="a.js", line=1, status=Status.CONNECTED, snippet="",
                      chain=[target], note="")

    def test_a_concrete_url_matches_the_route_that_would_serve_it(self):
        # /api/season/division/all/ is served by <str:division_id> - 'all' is a deliberate
        # sentinel in this project. Exact string matching called it a missing endpoint.
        edges = match_js_to_django(
            [self._url("api/season/division/<str:division_id>/")],
            [self._fetch("/api/season/division/all/")],
        )

        self.assertEqual(edges[0].status, Status.CONNECTED)
        self.assertEqual(edges[0].to_id, "url:api/season/division/<str:division_id>/")

    def test_an_int_converter_does_not_swallow_a_word(self):
        edges = match_js_to_django(
            [self._url("api/user/<int:user_id>/")], [self._fetch("/api/user/profile/")]
        )

        self.assertEqual(edges[0].status, Status.UNRESOLVED)

    def test_an_exact_route_wins_over_a_pattern_that_also_fits(self):
        edges = match_js_to_django(
            [self._url("api/user/<str:name>/"), self._url("api/user/me/")],
            [self._fetch("/api/user/me/")],
        )

        self.assertEqual(edges[0].to_id, "url:api/user/me/")

    def test_a_relative_fetch_is_matched_by_the_one_route_it_can_mean(self):
        # An admin page calls `api/add-user/`; the route is under the page's own prefix.
        edges = match_js_to_django(
            [self._url("asd/pointless/challengesdashboard/api/add-user/")],
            [self._fetch("api/add-user/")],
        )

        self.assertEqual(edges[0].status, Status.CONNECTED)

    def test_a_relative_fetch_two_routes_could_mean_is_not_guessed(self):
        edges = match_js_to_django(
            [self._url("admin/api/add-user/"), self._url("staff/api/add-user/")],
            [self._fetch("api/add-user/")],
        )

        self.assertEqual(edges[0].status, Status.UNCERTAIN)

    def test_an_absolute_fetch_is_never_matched_by_suffix(self):
        # /api/add-user/ is a claim about the site root, not "somewhere ending in this".
        edges = match_js_to_django(
            [self._url("admin/api/add-user/")], [self._fetch("/api/add-user/")]
        )

        self.assertEqual(edges[0].status, Status.UNRESOLVED)
