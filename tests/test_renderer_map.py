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


class TouchTests(SimpleTestCase):
    """A phone was left with no pan, no zoom, and an 8px tap target."""

    def test_the_canvas_claims_touch_gestures_from_the_browser(self):
        # Without this the browser treats every drag as a page scroll and the pan, the
        # pinch and often the tap that follows never reach the script.
        self.assertIn("touch-action:none", map_html.render(_map()))

    def test_input_is_handled_as_pointers_not_as_mouse_only(self):
        out = map_html.render(_map())

        for handler in ("pointerdown", "pointermove", "pointerup", "pointercancel"):
            self.assertIn(handler, out)
        self.assertNotIn('addEventListener("mousedown"', out)
        self.assertNotIn('addEventListener("mousemove"', out)

    def test_a_node_carries_a_tap_target_larger_than_the_box_it_draws(self):
        out = map_html.render(_map())

        # The drawn box is 20px tall and the view opens scaled down; on a phone that is
        # roughly 8px of target. The transparent rect fills the whole row pitch.
        self.assertIn('height="30" fill="transparent"', out)
        self.assertIn('pointer-events="all"', out)

    def test_zoom_is_reachable_without_a_wheel_or_two_fingers(self):
        out = map_html.render(_map())

        for control in ('id="zi"', 'id="zo"', 'id="zf"'):
            self.assertIn(control, out)


class CommitPickerTests(SimpleTestCase):
    def _with_commits(self):
        return _map(commits=[
            {"sha": "a" * 40, "subject": "fix: <script>", "date": "2026-01-01",
             "symbols": 10, "changed": {"url:x": "added"}, "baseline": "b" * 40},
        ])

    def test_a_commit_is_offered_by_its_subject_not_only_its_sha(self):
        out = map_html.render(self._with_commits())

        self.assertIn('id="cm"', out)
        self.assertIn("fix:", out)

    def test_a_commit_subject_is_escaped_before_it_reaches_innerHTML(self):
        # A subject is project text. Unescaped, one containing markup closes the option
        # early and swallows the rest of the list - the same defect the node labels had.
        out = map_html.render(self._with_commits())

        self.assertIn("${esc(c.subject)}", out)

    def test_a_commit_subject_cannot_close_the_script_tag(self):
        built = _map(commits=[
            {"sha": "a" * 40, "subject": "</script><b>hi</b>", "date": "2026-01-01",
             "symbols": 1, "changed": {}, "baseline": None},
        ])

        self.assertNotIn("</script><b>", map_html.render(built))

    def test_selecting_a_commit_narrows_the_view_to_what_it_changed(self):
        out = map_html.render(self._with_commits())

        self.assertIn("if (only) return changedIn(p)", out)

    def test_a_commit_that_changed_nothing_says_so_rather_than_drawing_a_blank(self):
        out = map_html.render(self._with_commits())

        self.assertIn("nothing the scan reads changed in this commit", out)

    def test_with_no_history_the_picker_says_how_to_build_some(self):
        out = map_html.render(_map())

        self.assertIn("--backfill", out)


class MobileLayoutTests(SimpleTestCase):
    """A phone spent 1250 of 1600 pixels on fixed chrome and 150 on the map."""

    def test_the_two_filters_are_the_only_navigation(self):
        out = map_html.render(_map())

        # Commit on the left, page on the right, side by side in one strip.
        self.assertIn('<div class="filters">', out)
        self.assertIn('<select id="cm">', out)
        self.assertIn('<select id="pg">', out)

    def test_pages_are_a_grouped_select_not_a_scrolling_rail(self):
        out = map_html.render(_map())

        self.assertIn("<optgroup", out)
        self.assertNotIn('id="pages"', out)
        self.assertNotIn('class="pg"', out)

    def test_the_document_is_exactly_one_screen_and_the_canvas_takes_the_rest(self):
        out = map_html.render(_map())

        self.assertIn("height:100dvh", out)
        self.assertIn("overflow:hidden", out)
        self.assertIn(".main { flex:1 1 auto;", out)

    def test_evidence_arrives_as_a_dismissable_sheet_not_a_permanent_panel(self):
        out = map_html.render(_map())

        self.assertIn('<aside class="sheet" id="detail" hidden>', out)
        self.assertIn('id="dx"', out)

    def test_the_colour_key_does_not_sit_on_top_of_the_nodes_it_explains(self):
        out = map_html.render(_map())

        self.assertIn('<div class="legend" id="legend" hidden>', out)
        self.assertIn('id="lg"', out)

    def test_double_tap_zooms_because_a_phone_has_no_wheel(self):
        self.assertIn("lastTap", map_html.render(_map()))


class EvidencePathTests(SimpleTestCase):
    def test_a_node_ships_the_source_line_it_was_read_from(self):
        node = MapNode("f", "fetch()", "fetch_target", "connected",
                       file="a.js", line=3, snippet='fetch("/api/x/")')
        built = _map(pages=[PageMap("p", [node], [])])

        self.assertIn('fetch(\\"/api/x/\\")', map_html.render(built))

    def test_the_sheet_reconstructs_the_walk_from_browser_to_backend(self):
        # A node alone says "this exists". The question a reader has is where the browser
        # started and where it ended up.
        out = map_html.render(_map())

        self.assertIn("Path — browser to backend", out)
        self.assertIn("function routes(id)", out)

    def test_a_node_that_reaches_this_one_is_not_listed_as_one_it_reaches(self):
        out = map_html.render(_map())

        self.assertIn("seenOnPath", out)


class CommitContextTests(SimpleTestCase):
    def _commits(self):
        return [
            {"sha": "a" * 40, "subject": "second", "date": "2026-08-30T14:29:00+02:00",
             "symbols": 2, "changed": {"url:x": "removed"}, "baseline": "b" * 40,
             "head": True, "changes": [
                 {"id": "url:x", "label": "x", "kind": "url", "file": "u.py",
                  "line": 4, "change": "removed"}], "change_total": 1},
        ]

    def test_a_commit_is_offered_with_its_time_not_only_its_date(self):
        # Two commits made the same afternoon are two different answers to "which one am
        # I looking at"; the date alone cannot separate them.
        out = map_html.render(_map(commits=self._commits()))

        self.assertIn("const when = iso =>", out)
        self.assertIn("2026-08-30T14:29:00+02:00", out)

    def test_the_scanned_head_is_named_in_the_header(self):
        self.assertIn("HEAD abc123def456", map_html.render(_map()))

    def test_it_opens_on_the_newest_commit_not_on_the_whole_graph(self):
        out = map_html.render(_map(commits=self._commits()))

        self.assertIn('picker.value = "0"; selectCommit(0);', out)

    def test_a_change_that_no_longer_exists_is_still_named(self):
        # The canvas draws today's code. A symbol this commit deleted - or added and a
        # later commit took away - is on no page, and an empty canvas under a note
        # claiming two things changed reads as a broken page.
        out = map_html.render(_map(commits=self._commits()))

        self.assertIn("What this commit changed", out)
        self.assertIn('id="gone"', out)
