from django.test import SimpleTestCase

from seamcheck.field_matcher import (
    match_json_response_fields,
    match_json_script_bridge,
    match_template_context_fields,
)
from seamcheck.graph import Status

VIEW = 'def get_stats(request):\n    return JsonResponse({"pushes": 10, "dead": 1})\n'


def _status(symbols, label):
    return next(s for s in symbols if s.label == label).status


class JsonResponseFieldTests(SimpleTestCase):
    def test_field_read_by_js_is_connected(self):
        js = "fetch('/api/stats/').then(r => r.json()).then(data => { el.textContent = data.pushes; })"

        symbols, _ = match_json_response_fields(VIEW, js)

        self.assertEqual(_status(symbols, "pushes"), Status.CONNECTED)

    def test_field_never_read_is_unused(self):
        js = "fetch('/api/stats/').then(r => r.json()).then(data => { el.textContent = data.pushes; })"

        symbols, _ = match_json_response_fields(VIEW, js)

        self.assertEqual(_status(symbols, "dead"), Status.UNUSED)

    def test_field_read_but_never_sent_is_unresolved(self):
        js = "fetch('/api/stats/').then(r => r.json()).then(data => { el.textContent = data.ghost; })"

        symbols, _ = match_json_response_fields(VIEW, js)

        self.assertEqual(_status(symbols, "ghost"), Status.UNRESOLVED)

    def test_computed_access_makes_the_whole_response_uncertain(self):
        js = "fetch('/api/stats/').then(r => r.json()).then(data => { el.textContent = data[key]; })"

        symbols, _ = match_json_response_fields(VIEW, js)

        self.assertTrue(all(s.status == Status.UNCERTAIN for s in symbols))

    def test_spread_makes_the_whole_response_uncertain(self):
        js = "const data = await r.json(); const copy = {...data};"

        symbols, _ = match_json_response_fields(VIEW, js)

        self.assertTrue(all(s.status == Status.UNCERTAIN for s in symbols))

    def test_binding_named_anything_other_than_data_still_resolves(self):
        # `data` is under half of real bindings. Assuming that name marks every field
        # behind any other name UNUSED -- a false "dead field" claim.
        js = "const payload = await response.json(); render(payload.pushes);"

        symbols, _ = match_json_response_fields(VIEW, js)

        self.assertEqual(_status(symbols, "pushes"), Status.CONNECTED)
        self.assertEqual(_status(symbols, "dead"), Status.UNUSED)

    def test_destructured_read_counts_as_a_read(self):
        js = "const { pushes } = await response.json();"

        symbols, _ = match_json_response_fields(VIEW, js)

        self.assertEqual(_status(symbols, "pushes"), Status.CONNECTED)

    def test_a_dict_before_the_jsonresponse_call_is_not_mistaken_for_the_payload(self):
        view = (
            "def get_stats(request):\n"
            '    lookup = {"not_a_field": 1}\n'
            '    return JsonResponse({"pushes": lookup})\n'
        )

        symbols, _ = match_json_response_fields(view, "const d = await r.json(); d.pushes;")

        self.assertEqual({s.label for s in symbols}, {"pushes"})


class TemplateContextFieldTests(SimpleTestCase):
    VIEW = (
        "def show_profile(request):\n"
        '    return render(request, "t.html", {"username": "alice", "unused": 1, "obj": 2, "flag": 3})\n'
    )

    def test_rendered_key_is_connected(self):
        symbols, _ = match_template_context_fields(self.VIEW, "<p>{{ username }}</p>")

        self.assertEqual(_status(symbols, "username"), Status.CONNECTED)

    def test_key_never_rendered_is_unused(self):
        symbols, _ = match_template_context_fields(self.VIEW, "<p>{{ username }}</p>")

        self.assertEqual(_status(symbols, "unused"), Status.UNUSED)

    def test_attribute_access_and_tag_usage_count_as_rendered(self):
        template = "<p>{{ obj.name }}</p>{% if flag %}x{% endif %}"

        symbols, _ = match_template_context_fields(self.VIEW, template)

        self.assertEqual(_status(symbols, "obj"), Status.CONNECTED)
        self.assertEqual(_status(symbols, "flag"), Status.CONNECTED)


class JsonScriptBridgeTests(SimpleTestCase):
    def test_bridge_is_a_certain_connected_edge(self):
        edges = match_json_script_bridge(
            '{{ payload|json_script:"payload-data" }}',
            "JSON.parse(document.getElementById('payload-data').textContent)",
        )

        self.assertEqual(len(edges), 1)
        self.assertEqual(edges[0].status, Status.CONNECTED)

    def test_no_edge_when_the_ids_differ(self):
        edges = match_json_script_bridge(
            '{{ payload|json_script:"payload-data" }}',
            "JSON.parse(document.getElementById('other-id').textContent)",
        )

        self.assertEqual(edges, [])
