from django.test import SimpleTestCase

from signal_map.graph import Edge, Graph, Status, Symbol, graph_from_dict, graph_to_dict


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
