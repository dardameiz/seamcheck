from django.test import SimpleTestCase

from signal_map.graph import Edge, Graph, Status, Symbol, graph_from_dict, graph_to_dict, relativise


def _make_symbol(id_="s1", status=Status.CONNECTED):
    return Symbol(
        id=id_,
        kind="view",
        label="get_thing",
        sub="pointless/views.py:10",
        file="pointless/views.py",
        line=10,
        status=status,
        snippet="def get_thing(request): ...",
        chain=["get_thing"],
        note="",
    )


class GraphSerializationTests(SimpleTestCase):
    def test_symbol_round_trips_through_dict(self):
        graph = Graph(symbols=[_make_symbol()], edges=[])
        restored = graph_from_dict(graph_to_dict(graph))

        self.assertEqual(restored.symbols[0].id, "s1")
        self.assertEqual(restored.symbols[0].status, Status.CONNECTED)
        self.assertEqual(restored.schema_version, graph.schema_version)

    def test_edge_round_trips_through_dict(self):
        edge = Edge(from_id="s1", to_id="s2", status=Status.CONNECTED)
        graph = Graph(symbols=[_make_symbol(), _make_symbol("s2")], edges=[edge])
        restored = graph_from_dict(graph_to_dict(graph))

        self.assertEqual(len(restored.edges), 1)
        self.assertEqual(restored.edges[0].from_id, "s1")
        self.assertEqual(restored.edges[0].to_id, "s2")

    def test_status_serializes_as_plain_string(self):
        data = graph_to_dict(Graph(symbols=[_make_symbol(status=Status.UNCERTAIN)], edges=[]))

        self.assertEqual(data["symbols"][0]["status"], "uncertain")
        # Not just str-equal: literally a str. asdict() leaves Status enum members in
        # place, and repr(Status.UNCERTAIN) != repr("uncertain") — which would give the
        # Triage fingerprint two different hashes for identical evidence.
        self.assertIs(type(data["symbols"][0]["status"]), str)

    def test_graph_survives_a_json_round_trip(self):
        import json

        graph = Graph(symbols=[_make_symbol(status=Status.UNRESOLVED)], edges=[])
        restored = graph_from_dict(json.loads(json.dumps(graph_to_dict(graph))))

        self.assertEqual(restored.symbols[0].status, Status.UNRESOLVED)


class RelativisePathTests(SimpleTestCase):
    """A symbol id is the primary key diffs and triage marks join on."""

    def _symbol(self, id_, file=None, sub="", chain=None):
        return Symbol(
            id=id_, kind="admin_action", label="x", sub=sub, file=file, line=1,
            status=Status.CONNECTED, snippet="", chain=chain or [id_], note="",
        )

    def test_an_id_built_from_an_absolute_path_becomes_checkout_independent(self):
        # Scanned at /Users/me/app and at /app, the same code produced two different ids,
        # so every one of those symbols read as added and removed on the next scan.
        graph = Graph([self._symbol("admin_action:/Users/me/app/pointless/admin.py:go")], [])

        out = relativise(graph, "/Users/me/app")

        self.assertEqual(out.symbols[0].id, "admin_action:pointless/admin.py:go")

    def test_edges_follow_the_symbols_they_point_at(self):
        graph = Graph(
            [self._symbol("admin_action:/r/a.py:x"), self._symbol("admin_action:/r/b.py:y")],
            [Edge("admin_action:/r/a.py:x", "admin_action:/r/b.py:y", Status.CONNECTED)],
        )

        out = relativise(graph, "/r")

        ids = {symbol.id for symbol in out.symbols}
        self.assertEqual(out.edges[0].from_id, "admin_action:a.py:x")
        self.assertIn(out.edges[0].from_id, ids)
        self.assertIn(out.edges[0].to_id, ids)

    def test_the_developers_home_directory_does_not_travel_in_a_shared_report(self):
        graph = Graph([self._symbol("x", file="/Users/me/app/a.py", sub="/Users/me/app/a.py:1")], [])

        out = relativise(graph, "/Users/me/app")

        self.assertEqual(out.symbols[0].file, "a.py")
        self.assertEqual(out.symbols[0].sub, "a.py:1")

    def test_a_path_outside_the_repository_is_left_alone(self):
        graph = Graph([self._symbol("x", file="/usr/lib/python/site.py")], [])

        self.assertEqual(relativise(graph, "/Users/me/app").symbols[0].file, "/usr/lib/python/site.py")

    def test_a_url_shaped_id_is_not_mistaken_for_a_path(self):
        graph = Graph([self._symbol("fetch:/api/get-user-stats/")], [])

        self.assertEqual(relativise(graph, "/Users/me/app").symbols[0].id, "fetch:/api/get-user-stats/")
