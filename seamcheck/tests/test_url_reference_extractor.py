import pathlib
import tempfile

from django.test import SimpleTestCase

from seamcheck.extractors.url_reference_extractor import (
    _python_route_references,
    extract_url_references,
)
from seamcheck.graph import Status, Symbol


def _url(path):
    return Symbol(id=f"url:{path}", kind="url", label=path, sub="GET/POST", file="urls.py",
                  line=1, status=Status.CONNECTED, snippet="", chain=[], note="")


class PythonParsingTests(SimpleTestCase):
    """The Python side uses ast, and both false positives on the first run explain why."""

    def test_a_reverse_inside_a_comment_is_not_a_reference(self):
        # Cost a false positive: `# Use standard namespace so reverse('admin:...') works`.
        source = "name = 'admin'  # so reverse('admin:...') works\n"

        self.assertEqual(list(_python_route_references(source)), [])

    def test_a_reverse_in_a_docstring_is_not_a_reference(self):
        source = '"""Call reverse(\'profile\') to get there."""\n'

        self.assertEqual(list(_python_route_references(source)), [])

    def test_a_reverse_guarded_by_its_own_except_is_not_a_finding(self):
        # The author has already said the route may not exist and written the fallback.
        # Calling that a broken reference is telling someone their error handling is a bug.
        source = (
            "try:\n"
            "    url = reverse('sandbox_login')\n"
            "except NoReverseMatch:\n"
            "    url = '/sandbox-login/'\n"
        )

        self.assertEqual(list(_python_route_references(source)), [])

    def test_an_unguarded_reverse_is_a_reference(self):
        source = "url = reverse('profile')\n"

        self.assertEqual(list(_python_route_references(source)), [("reverse", "profile", 1)])

    def test_a_computed_route_name_is_not_guessed_at(self):
        source = "url = reverse(name_from_somewhere)\n"

        self.assertEqual(list(_python_route_references(source)), [])

    def test_unparseable_python_claims_nothing(self):
        self.assertEqual(list(_python_route_references("def (:\n")), [])


class ReferenceMatchingTests(SimpleTestCase):
    def _run(self, template="", python="", names=None, urls=None):
        with tempfile.TemporaryDirectory() as tmp:
            paths, py = [], []
            if template:
                path = pathlib.Path(tmp, "t.html")
                path.write_text(template)
                paths.append(str(path))
            if python:
                path = pathlib.Path(tmp, "m.py")
                path.write_text(python)
                py.append(str(path))
            return extract_url_references(paths, py, names or {}, urls or [])

    def test_a_url_tag_connects_its_route(self):
        symbols, edges = self._run(
            template="<a href=\"{% url 'profile' %}\">me</a>",
            names={"profile": "accounts/profile/"}, urls=[_url("accounts/profile/")],
        )

        self.assertEqual([s.label for s in symbols], ["profile"])
        self.assertEqual(edges[0].to_id, "url:accounts/profile/")
        self.assertIs(edges[0].status, Status.CONNECTED)

    def test_a_name_that_exists_in_no_route_is_unresolved(self):
        # NoReverseMatch at render time - a 500 on a live page.
        symbols, edges = self._run(
            template="{% url 'gone' %}", names={"profile": "accounts/profile/"},
            urls=[_url("accounts/profile/")],
        )

        self.assertEqual([s.label for s in symbols], ["gone"])
        self.assertIs(edges[0].status, Status.UNRESOLVED)

    def test_a_third_party_route_is_valid_and_claims_nothing(self):
        # 51 false positives on the first run: the name index covers every route while the
        # graph holds only first-party ones, so `{% url 'account_login' %}` pointing at a
        # real django-allauth route looked like a name that resolves to nothing.
        symbols, edges = self._run(
            template="{% url 'account_login' %}",
            names={"account_login": "accounts/login/"},   # exists...
            urls=[_url("mine/")],                          # ...but is not in the graph
        )

        self.assertEqual(symbols, [])
        self.assertEqual(edges, [])

    def test_a_literal_link_resolves_through_the_route_converters(self):
        symbols, edges = self._run(
            template='<a href="/team/42/">t</a>', urls=[_url("team/<int:pk>/")],
        )

        self.assertEqual(edges[0].to_id, "url:team/<int:pk>/")
        self.assertIs(edges[0].status, Status.CONNECTED)

    def test_a_link_to_something_the_project_does_not_serve_claims_nothing(self):
        symbols, edges = self._run(template='<a href="/static/x.png">i</a>', urls=[_url("mine/")])

        self.assertEqual(symbols, [])

    def test_htmx_attributes_count_as_references(self):
        _symbols, edges = self._run(template='<div hx-post="/vote/"></div>', urls=[_url("vote/")])

        self.assertEqual(edges[0].to_id, "url:vote/")

    def test_the_same_route_referenced_twice_keeps_both_sites(self):
        # Twelve templates pointing at one route is twelve places a reader may need to go.
        symbols, _edges = self._run(
            template="{% url 'p' %}\n{% url 'p' %}", names={"p": "p/"}, urls=[_url("p/")],
        )

        self.assertEqual(len({s.line for s in symbols}), 2)
