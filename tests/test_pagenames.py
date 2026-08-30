import pathlib
import tempfile

from django.test import SimpleTestCase

from signal_map.graph import Edge, Graph, Status, Symbol
from signal_map.pagenames import PageName, _best_url, page_names, urls_by_template


def _symbol(id_, kind, label, file=None, line=None):
    return Symbol(
        id=id_, kind=kind, label=label, sub="", file=file, line=line,
        status=Status.CONNECTED, snippet="", chain=[id_], note="",
    )


class BestUrlTests(SimpleTestCase):
    def test_site_root_wins_outright(self):
        self.assertEqual(_best_url({"", "index.html"}, "index.html"), "/")

    def test_prefers_the_route_that_shares_words_with_the_template(self):
        # /halloffame/ is shorter, so length alone would pick the alias a reader does
        # not recognise as the leaderboard.
        self.assertEqual(
            _best_url({"halloffame/", "leaderboard/"}, "leaderboard_arena.html"),
            "/leaderboard/",
        )

    def test_adds_the_leading_slash_django_omits(self):
        self.assertEqual(_best_url({"push_arena/"}, "push_arena.html"), "/push_arena/")


class UrlsByTemplateTests(SimpleTestCase):
    def test_reads_the_template_out_of_the_view_that_routes_to_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            views = pathlib.Path(tmp) / "views.py"
            views.write_text(
                "def store(request):\n"
                "    return render(request, 'store.html', {})\n",
                encoding="utf-8",
            )
            graph = Graph(
                symbols=[
                    _symbol("url:store/", "url", "store/"),
                    _symbol("view:store", "view", "store", file=str(views), line=1),
                ],
                edges=[Edge("url:store/", "view:store", Status.CONNECTED)],
            )
            self.assertEqual(urls_by_template(graph), {"store.html": {"store/"}})

    def test_a_view_choosing_between_templates_reports_both(self):
        with tempfile.TemporaryDirectory() as tmp:
            views = pathlib.Path(tmp) / "views.py"
            views.write_text(
                "def page(request):\n"
                "    name = 'a.html' if request.user.is_staff else 'b.html'\n"
                "    return render(request, name)\n",
                encoding="utf-8",
            )
            graph = Graph(
                symbols=[
                    _symbol("url:p/", "url", "p/"),
                    _symbol("view:page", "view", "page", file=str(views), line=1),
                ],
                edges=[Edge("url:p/", "view:page", Status.CONNECTED)],
            )
            self.assertEqual(sorted(urls_by_template(graph)), ["a.html", "b.html"])


class PageNamesTests(SimpleTestCase):
    def _project(self, tmp, vite, templates):
        root = pathlib.Path(tmp)
        (root / "vite.config.js").write_text(vite, encoding="utf-8")
        for name, body in templates.items():
            path = root / "templates" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body, encoding="utf-8")
        return root

    def test_names_a_bundle_after_the_page_that_loads_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._project(
                tmp,
                "input: {'push-arena': resolve(__dirname, 'js/push-arena-main.js')}",
                {"push_arena.html": "{% vite_asset 'push-arena' %}"},
            )
            names = page_names(
                tmp, {"templates_root": "templates"}, Graph(symbols=[], edges=[])
            )
            self.assertEqual(
                names["push-arena-main"],
                PageName(title="Push Arena", where="push_arena.html", entry="push-arena-main"),
            )

    def test_shows_the_url_when_a_view_renders_that_template(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(
                tmp,
                "input: {'store': resolve(__dirname, 'js/store-main.js')}",
                {"store.html": "{% vite_asset 'store' %}"},
            )
            views = root / "views.py"
            views.write_text("def store(request):\n    return render(request, 'store.html')\n")
            graph = Graph(
                symbols=[
                    _symbol("url:store/", "url", "store/"),
                    _symbol("view:store", "view", "store", file=str(views), line=1),
                ],
                edges=[Edge("url:store/", "view:store", Status.CONNECTED)],
            )
            self.assertEqual(
                page_names(tmp, {"templates_root": "templates"}, graph)["store-main"].where,
                "/store/ - store.html",
            )

    def test_a_bundle_no_template_loads_says_so_instead_of_inventing_a_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._project(tmp, "input: {'orphan': resolve(__dirname, 'js/orphan-main.js')}", {})
            name = page_names(tmp, {"templates_root": "templates"}, Graph(symbols=[], edges=[]))
            self.assertEqual(name["orphan-main"].title, "orphan-main")
            self.assertEqual(name["orphan-main"].where, "no template loads this")

    def test_a_bundle_several_pages_share_is_counted_not_mislabelled(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._project(
                tmp,
                "input: {'legal': resolve(__dirname, 'js/legal-main.js')}",
                {
                    "legal/terms.html": "{% vite_asset 'legal' %}",
                    "legal/privacy.html": "{% vite_asset 'legal' %}",
                },
            )
            name = page_names(tmp, {"templates_root": "templates"}, Graph(symbols=[], edges=[]))
            self.assertEqual(name["legal-main"].title, "Legal")
            self.assertEqual(name["legal-main"].where, "2 templates")

    def test_a_script_a_template_loads_directly_is_named_too(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._project(
                tmp, "input: {}", {"store.html": "{% static_js 'pointless/js/push_store.js' %}"}
            )
            name = page_names(tmp, {"templates_root": "templates"}, Graph(symbols=[], edges=[]))
            self.assertEqual(name["push_store"].title, "Store")
