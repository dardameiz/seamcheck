import unittest

from seamcheck.entries.server import ServerEntrySource
from seamcheck.graph import Edge, Graph, Status, Symbol


def _symbol(id_, kind, label, file):
    return Symbol(id=id_, kind=kind, label=label, sub="", file=file, line=1,
                  status=Status.CONNECTED, snippet="", chain=[], note="")


def _routes(*routes):
    """(url label, view id, file, view label) -> a graph with a url->view edge for each."""
    symbols, edges = [], []
    for url, view_id, file, view_label in routes:
        symbols += [_symbol(f"url:{url}", "url", url, file), _symbol(view_id, "view", view_label, file)]
        edges.append(Edge(f"url:{url}", view_id, Status.CONNECTED))
    return Graph(symbols=symbols, edges=edges)


class ServerEntrySourceTests(unittest.TestCase):
    def test_a_route_handler_is_one_entry_titled_by_the_url_it_serves(self):
        graph = _routes(("/api/checkout", "view:app/api/checkout/route.ts", "app/api/checkout/route.ts", "route"))
        [entry] = ServerEntrySource().entries("/repo", {}, graph)
        self.assertEqual(
            (entry.key, entry.kind, entry.roots, entry.title, entry.where, entry.label),
            ("server:app/api/checkout/route.ts", "server", ("app/api/checkout/route.ts",),
             "/api/checkout", "/api/checkout - app/api/checkout/route.ts", "app/api/checkout/route.ts"))

    def test_two_route_handlers_with_the_same_view_label_get_different_titles(self):
        # The Next.js adapter labels every handler's view "route"; titled by that, the
        # picker merged all of them into one row.
        graph = _routes(("/api/a", "view:app/api/a/route.ts", "app/api/a/route.ts", "route"),
                        ("/api/b", "view:app/api/b/route.ts", "app/api/b/route.ts", "route"))
        self.assertEqual([e.title for e in ServerEntrySource().entries("/repo", {}, graph)],
                         ["/api/a", "/api/b"])

    def test_several_routes_in_one_file_are_one_entry(self):
        graph = _routes(("/users", "view:routes.users.list", "routes/users.py", "list"),
                        ("/users/{id}", "view:routes.users.get", "routes/users.py", "get"))
        [entry] = ServerEntrySource().entries("/repo", {}, graph)
        self.assertEqual((entry.title, entry.where), ("2 routes", "/users · /users/{id} - routes/users.py"))

    def test_more_routes_than_fit_say_so(self):
        graph = _routes(*[(f"/r{i}", f"view:r{i}", "routes.py", f"r{i}") for i in range(5)])
        [entry] = ServerEntrySource().entries("/repo", {}, graph)
        self.assertEqual(entry.where, "/r0 · /r1 · /r2 … - routes.py")

    def test_a_django_pattern_is_shown_with_its_leading_slash(self):
        graph = _routes(("api/x/", "view:app.views.x", "app/views.py", "x"),
                        ("", "view:app.views.home", "app/home.py", "home"))
        self.assertEqual([e.title for e in ServerEntrySource().entries("/repo", {}, graph)],
                         ["/", "/api/x/"])

    def test_a_view_no_url_reaches_is_titled_by_its_file(self):
        graph = Graph(symbols=[_symbol("view:x", "view", "x", "app/handlers.py")], edges=[])
        [entry] = ServerEntrySource().entries("/repo", {}, graph)
        self.assertEqual((entry.title, entry.where), ("handlers.py", "app/handlers.py"))

    def test_only_views_become_entries(self):
        graph = Graph(symbols=[_symbol("css:x", "css_selector", "x", "a.css")], edges=[])
        self.assertEqual(ServerEntrySource().entries("/repo", {}, graph), [])
        self.assertEqual(ServerEntrySource().entries("/repo", {}, None), [])

    def test_detect_is_a_constant_the_graph_decides(self):
        self.assertEqual(ServerEntrySource().detect("/repo", {}), 0.6)
