from pathlib import Path

from django.test import SimpleTestCase

from seamcheck.extractors.django_extractor import extract_django_urls_views
from seamcheck.extractors.js_extractor import extract_js
from seamcheck.graph import Status, Symbol
from seamcheck.matcher import match_js_to_django, match_static_assets

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


class StaticAssetTests(SimpleTestCase):
    """A `/static/…` reference is a file question, not a route question.

    Asked of the route table it can only come back uncertain, which is what happened to
    all eleven such references on one surface of the reference project - every one of them
    a file sitting on disk. Asked of the filesystem it comes back yes or no, and "no" is a
    404 with its own source line attached.
    """

    def _target(self, label):
        return Symbol(id=f"fetch:{label}", kind="fetch_target", label=label, sub="",
                      file="app.js", line=3, status=Status.UNCERTAIN, snippet=label,
                      chain=[label], note="")

    def setUp(self):
        import pathlib
        import tempfile

        self.root = pathlib.Path(tempfile.mkdtemp())
        (self.root / "img").mkdir()
        (self.root / "img" / "avatar.svg").write_text("<svg/>", encoding="utf-8")

    def test_a_file_that_exists_is_connected_and_named(self):
        edges = match_static_assets([self._target("/static/img/avatar.svg")],
                                    [str(self.root)])
        self.assertEqual([e.status for e in edges], [Status.CONNECTED])
        self.assertIn("avatar.svg", edges[0].note)

    def test_a_file_that_does_not_exist_is_a_404_waiting_to_happen(self):
        edges = match_static_assets([self._target("/static/img/gone.svg")],
                                    [str(self.root)])
        self.assertEqual([e.status for e in edges], [Status.UNRESOLVED])
        self.assertIn("NOT on disk", edges[0].note)

    def test_a_query_string_does_not_stop_it_resolving(self):
        edges = match_static_assets([self._target("/static/img/avatar.svg?v=3")],
                                    [str(self.root)])
        self.assertEqual([e.status for e in edges], [Status.CONNECTED])

    def test_a_route_is_left_to_the_route_table(self):
        # No file extension, so it is not an asset question and this pass says nothing.
        self.assertEqual(match_static_assets([self._target("/api/orders/")],
                                             [str(self.root)]), [])

    def test_a_collected_copy_is_not_proof(self):
        # `staticfiles/` holds what collectstatic duplicated. A file that exists only
        # there exists only as a build artefact, and counting one as evidence is what
        # made a first sample of these findings score 10 out of 10 wrong.
        collected = self.root / "staticfiles"
        (collected / "img").mkdir(parents=True)
        (collected / "img" / "only-collected.svg").write_text("<svg/>", encoding="utf-8")
        edges = match_static_assets([self._target("/static/img/only-collected.svg")],
                                    [str(collected)])
        self.assertEqual(edges, [])
