from django.test import SimpleTestCase

from signal_map.mapdata import ConnectivityMap, MapEdge, MapNode, PageMap
from signal_map.renderers import map_html


def _map(**kwargs):
    node = MapNode("url:x", "asd/<path:object_id>/", "url", "connected", file="v.py", line=3)
    page = PageMap("home", [MapNode("page:home", "home", "page", "connected"), node],
                   [MapEdge("page:home", "url:x", "connected")])
    base = {"git_sha": "abc123def456", "generated_at": "2026-08-30T00:00:00", "pages": [page]}
    base.update(kwargs)
    return ConnectivityMap(**base)


class MapRenderTests(SimpleTestCase):
    def test_it_is_a_complete_document(self):
        out = map_html.render(_map())

        self.assertTrue(out.lstrip().startswith("<!doctype html>"))

    def test_it_makes_no_network_requests(self):
        out = map_html.render(_map())

        for forbidden in ("http://", "https://", "<link", "<img", "url(", "@import", "srcset"):
            self.assertNotIn(forbidden, out)

    def test_the_script_escapes_labels_before_inserting_them(self):
        # 163 of this project's URL labels contain <path:object_id>; unescaped, the
        # parser eats them and the node renders as a blank box.
        out = map_html.render(_map())

        self.assertIn("const esc =", out)
        self.assertIn("${esc(label)}", out)

    def test_the_payload_cannot_close_the_script_tag(self):
        node = MapNode("x", "</script><b>hi</b>", "url", "connected")
        built = _map(pages=[PageMap("p", [node], [])])

        self.assertNotIn("</script><b>", map_html.render(built))

    def test_it_records_the_commit_and_the_mode(self):
        self.assertIn("current", map_html.render(_map()))
        self.assertIn("diff vs", map_html.render(_map(baseline_sha="0000111222333")))

    def test_diff_changes_reach_the_payload(self):
        out = map_html.render(_map(baseline_sha="0000111222333", changed={"url:x": "added"}))

        self.assertIn('"url:x": "added"', out.replace('"url:x":"added"', '"url:x": "added"'))
