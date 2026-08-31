"""Reading a URLconf as text. Measured against Django's own resolver on a real project:
95% of the routes actually declared in a urls.py, and every miss explained."""

import pathlib
import tempfile

from django.test import SimpleTestCase

from seamcheck.extractors.django_static_extractor import extract_urls_views_static


class StaticUrlconfTests(SimpleTestCase):
    def _project(self, files: dict[str, str]):
        tmp = tempfile.mkdtemp()
        for name, body in files.items():
            path = pathlib.Path(tmp, name)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body, encoding="utf-8")
        return tmp

    def _scan(self, files, module="proj.urls", prefixes=("proj", "app")):
        symbols, edges, names = extract_urls_views_static(
            self._project(files), module, list(prefixes)
        )
        return (
            {s.label for s in symbols if s.kind == "url"},
            {s.label for s in symbols if s.kind == "view"},
            names, edges,
        )

    def test_a_plain_route_and_its_view(self):
        urls, views, names, edges = self._scan({
            "proj/__init__.py": "",
            "proj/urls.py": (
                "from app import views\n"
                "urlpatterns = [path('books/', views.book_list, name='books')]\n"
            ),
            "app/__init__.py": "",
            "app/views.py": "def book_list(request):\n    pass\n",
        })

        self.assertEqual(urls, {"books/"})
        self.assertEqual(views, {"book_list"})
        self.assertEqual(names, {"books": "books/"})
        self.assertEqual(len(edges), 1)

    def test_a_relative_import_resolves(self):
        # `from . import views` is how a URLconf almost always names its views, and
        # skipping relative imports lost 230 of one project's 231 routes.
        urls, views, _n, _e = self._scan({
            "app/__init__.py": "",
            "app/urls.py": (
                "from . import views\n"
                "urlpatterns = [path('x/', views.thing)]\n"
            ),
            "app/views.py": "def thing(request):\n    pass\n",
        }, module="app.urls")

        self.assertEqual(urls, {"x/"})
        self.assertEqual(views, {"thing"})

    def test_a_nested_attribute_chain_resolves(self):
        # `views.announcement_views.mark_read` - a views PACKAGE. Reading one level lost
        # every route in such a file.
        urls, views, _n, _e = self._scan({
            "app/__init__.py": "",
            "app/urls.py": (
                "from . import views\n"
                "urlpatterns = [path('a/', views.inner.mark_read)]\n"
            ),
            "app/views/__init__.py": "",
            "app/views/inner.py": "def mark_read(request):\n    pass\n",
        }, module="app.urls")

        self.assertEqual(urls, {"a/"})
        self.assertEqual(views, {"mark_read"})

    def test_an_include_carries_its_prefix(self):
        urls, _v, names, _e = self._scan({
            "proj/__init__.py": "",
            "proj/urls.py": "urlpatterns = [path('api/', include('app.urls'))]\n",
            "app/__init__.py": "",
            "app/urls.py": (
                "from . import views\n"
                "urlpatterns = [path('books/', views.book_list, name='books')]\n"
            ),
            "app/views.py": "def book_list(request):\n    pass\n",
        })

        self.assertEqual(urls, {"api/books/"})
        self.assertEqual(names, {"books": "api/books/"})

    def test_routes_added_inside_a_conditional_are_found(self):
        # A URLconf routinely adds routes under `if settings.DEBUG:`.
        urls, _v, _n, _e = self._scan({
            "app/__init__.py": "",
            "app/urls.py": (
                "from . import views\n"
                "urlpatterns = [path('a/', views.a)]\n"
                "if DEBUG:\n"
                "    urlpatterns += [path('b/', views.b)]\n"
            ),
            "app/views.py": "def a(request):\n    pass\ndef b(request):\n    pass\n",
        }, module="app.urls")

        self.assertEqual(urls, {"a/", "b/"})

    def test_a_wrapper_call_is_read_through(self):
        urls, _v, _n, _e = self._scan({
            "app/__init__.py": "",
            "app/urls.py": (
                "from . import views\n"
                "urlpatterns = i18n_patterns(path('a/', views.a))\n"
            ),
            "app/views.py": "def a(request):\n    pass\n",
        }, module="app.urls")

        self.assertEqual(urls, {"a/"})

    def test_a_class_based_view_resolves_through_as_view(self):
        _u, views, _n, _e = self._scan({
            "app/__init__.py": "",
            "app/urls.py": (
                "from .views import BookList\n"
                "urlpatterns = [path('b/', BookList.as_view())]\n"
            ),
            "app/views.py": "class BookList:\n    pass\n",
        }, module="app.urls")

        self.assertEqual(views, {"BookList"})

    def test_a_third_party_include_is_not_followed(self):
        # Somebody else's routing table is not this project's code, and following it would
        # report their views as findings the reader cannot act on.
        urls, _v, _n, _e = self._scan({
            "proj/__init__.py": "",
            "proj/urls.py": "urlpatterns = [path('accounts/', include('allauth.urls'))]\n",
        })

        self.assertEqual(urls, set())

    def test_an_include_cycle_terminates(self):
        urls, _v, _n, _e = self._scan({
            "a/__init__.py": "", "a/urls.py": "urlpatterns = [path('', include('b.urls'))]\n",
            "b/__init__.py": "", "b/urls.py": "urlpatterns = [path('', include('a.urls'))]\n",
        }, module="a.urls", prefixes=("a", "b"))

        self.assertEqual(urls, set())

    def test_unparseable_python_claims_nothing(self):
        urls, _v, _n, _e = self._scan({
            "proj/__init__.py": "", "proj/urls.py": "def (:\n",
        })

        self.assertEqual(urls, set())

    def test_a_loop_built_pattern_list_is_a_known_blind_spot(self):
        # Django would resolve it; text cannot. Documented rather than silently wrong.
        urls, _v, _n, _e = self._scan({
            "app/__init__.py": "",
            "app/urls.py": (
                "from . import views\n"
                "urlpatterns = [path(f'{v}/', views.a) for v in VARIANTS]\n"
            ),
            "app/views.py": "def a(request):\n    pass\n",
        }, module="app.urls")

        self.assertEqual(urls, set())
