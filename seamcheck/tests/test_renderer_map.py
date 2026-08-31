from django.test import SimpleTestCase

from seamcheck.mapdata import ConnectivityMap, MapEdge, MapNode, PageMap
from seamcheck.renderers import map_html


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

    def test_it_loads_nothing_from_the_network(self):
        # The promise is that opening the file fetches nothing: no stylesheet, no script,
        # no font, no image, no matter where it is opened or who is watching. Anchors are
        # excluded deliberately - a link the reader may click is not a request the page
        # makes - and the next test pins that they are the ONLY external references.
        out = map_html.render(_map())

        for forbidden in ("<link", "<img", "<iframe", "url(", "@import", "srcset",
                          "XMLHttpRequest", "importScripts", "<script src"):
            self.assertNotIn(forbidden, out)

    def test_the_only_request_it_can_make_is_same_origin_and_asked_for(self):
        """The code viewer reads a file from the server that served this page.

        `fetch(` was forbidden outright, which was the right guard while the page made no
        requests at all. The promise it protects is that opening the page phones nobody -
        not that a reader clicking "code" cannot be shown the file they clicked on. So the
        letter moves and the promise does not: every fetch must be built from
        location.pathname, so it can only ever reach the origin the page came from, and
        the viewer must refuse to try at all under file://.
        """
        import re

        out = map_html.render(_map())

        calls = re.findall(r"fetch\(([^\n]*)", out)
        self.assertTrue(calls, "the viewer should make exactly one kind of request")
        for call in calls:
            self.assertIn("location.pathname", call,
                          "a fetch that is not built from this page's own path")
            self.assertNotIn("http", call, "a fetch naming an absolute URL")
        self.assertIn('location.protocol === "file:"', out,
                      "the viewer must fall back rather than request under file://")

    def test_every_external_url_is_an_anchor_and_opens_away_from_the_report(self):
        import re

        out = map_html.render(_map())

        for match in re.finditer(r"https?://", out):
            before = out[max(0, match.start() - 120):match.start()]
            self.assertRegex(before, r'<a href="$', "an external URL that is not an anchor")
        for chunk in out.split('<a href="http')[1:]:
            self.assertIn('target="_blank"', chunk[:220])
            self.assertIn('rel="noreferrer"', chunk[:220])

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

    def test_the_newest_commit_is_first_and_marked_but_is_not_the_opening_view(self):
        # It used to open there. A commit whose only change was a deletion has nothing
        # left to draw, so the page opened on a canvas holding one node.
        out = map_html.render(_map(commits=self._commits()))

        self.assertIn("HEAD · ", out)
        self.assertNotIn("selectCommit(0);", out)

    def test_a_change_that_no_longer_exists_is_still_named(self):
        # The canvas draws today's code. A symbol this commit deleted - or added and a
        # later commit took away - is on no page, and an empty canvas under a note
        # claiming two things changed reads as a broken page.
        out = map_html.render(_map(commits=self._commits()))

        self.assertIn("What this commit changed", out)
        self.assertIn('id="gone"', out)


class MergedReviewViewTests(SimpleTestCase):
    """The console's sections live in the map's shell: one document, one link."""

    def _console(self):
        from seamcheck.console import Console, Row, Section

        return Console(
            git_sha="abc123def456", generated_at="2026-08-30T00:00:00", baseline_sha=None,
            backend={"connected": 2}, frontend={"unresolved": 1},
            counts={"connected": 2}, groups=[("Unused design tokens", 3, "")],
            sections=[Section("dom", "DOM Wiring", "blurb", rows=[
                Row(id="dom_attr:x", label="<script>bad</script>", kind="dom_attr",
                    status="unresolved", file="t.html", line=9, note="n", snippet="s")
            ])],
        )

    def test_the_review_sections_are_offered_beside_the_map(self):
        out = map_html.render(_map(), console=self._console())

        self.assertIn('<select id="vw">', out)
        self.assertIn("DOM Wiring", out)

    def test_row_text_is_escaped_before_it_reaches_innerHTML(self):
        out = map_html.render(_map(), console=self._console())

        self.assertIn("${esc(r.label)}", out)
        self.assertNotIn("<script>bad</script>", out)

    def test_a_row_snippet_is_not_shipped_because_nothing_draws_it(self):
        # A section can hold well over a thousand rows, and a field nothing draws is a
        # megabyte and a half on a page meant to open on a phone.
        out = map_html.render(_map(), console=self._console())

        self.assertNotIn('"snippet": "s"', out)
        self.assertNotIn('"snippet":"s"', out)

    def test_a_section_longer_than_what_was_sent_says_so(self):
        out = map_html.render(_map(), console=self._console())

        self.assertIn("Showing the first", out)
        self.assertIn('"total"', out)

    def test_the_page_renders_without_a_console_at_all(self):
        out = map_html.render(_map())

        self.assertIn('"sections": []', out)

    def test_it_opens_on_the_overview(self):
        # "How is this project doing" is the question someone has when they open the
        # file, and it is one screen rather than 30,000 nodes. A separate "Start here"
        # page explaining the other two was a third thing to read first; its content is
        # in Overview now, beside the numbers it explains.
        out = map_html.render(_map(), console=self._console())

        self.assertIn('const OPENS_ON = "overview";', out)
        self.assertNotIn('key: "start"', out)
        self.assertNotIn("seenBefore", out)

    def test_the_map_is_called_the_map(self):
        out = map_html.render(_map(), console=self._console())

        self.assertIn('{key: "map", title: "Map", count: null}', out)
        self.assertNotIn("Map — what reaches what", out)

    def test_overview_reports_shares_not_only_counts(self):
        # "1,319 unresolved" reads as a catastrophe or as nothing at all depending on
        # whether the project has two thousand symbols or forty thousand.
        out = map_html.render(_map(), console=self._console())

        self.assertIn("function pct(n, total)", out)
        self.assertIn("To look at", out)

    def test_the_findings_bar_is_scaled_to_the_findings(self):
        # Drawn against the whole codebase it was 96% connected-and-uncertain - the two
        # statuses nobody acts on - with the part you came for a sliver at the far edge.
        out = map_html.render(_map(), console=self._console())

        self.assertIn("(all[k] / looking) * 100", out)
        self.assertIn('class="split"', out)

    def test_backend_and_frontend_share_one_scale(self):
        # Two cards each drawn to their own width made 854 and 35,890 look like comparable
        # quantities sitting side by side.
        out = map_html.render(_map(), console=self._console())

        self.assertIn("const widest = Math.max(...rows.map(r => r.total), 1);", out)
        self.assertIn("(r.total / widest) * 100", out)

    def test_each_side_is_rated_against_itself_not_the_project(self):
        # A share of the total says only which half is bigger - a fact about the project,
        # not about its health.
        out = map_html.render(_map(), console=self._console())

        self.assertIn("pct(r.finds, r.total)", out)

    def test_an_empty_to_do_list_says_so_rather_than_drawing_a_bar_of_nothing(self):
        out = map_html.render(_map(), console=self._console())

        self.assertIn("That is the whole to-do list.", out)

    def test_the_header_row_draws_no_bar_track(self):
        # With the track background on it, the header read as an empty third row of data.
        out = map_html.render(_map(), console=self._console())

        self.assertIn(".whead .wbar { background:none; }", out)

    def test_the_map_list_toggle_is_not_a_grey_chip(self):
        # The one control that changes what kind of thing you are looking at.
        out = map_html.render(_map(), console=self._console())

        self.assertIn("#aslist { font-weight:600; color:var(--sig); border-color:var(--sig);", out)

    def test_a_desktop_gets_a_rail_and_a_phone_gets_the_select(self):
        # Rebuilding for the phone deleted the rail entirely; both drive one switch, and
        # both are built from one list so they cannot disagree.
        out = map_html.render(_map(), console=self._console())

        self.assertIn('<div class="nav" id="nav">', out)
        self.assertIn("const VIEWS = ", out)
        self.assertIn('class="filters onlymob"', out)
        self.assertIn(".onlymob { display:none; }", out)


class HiddenElementTests(SimpleTestCase):
    def test_hidden_beats_an_author_display_rule(self):
        # .zoom and .crumbrow set display:flex, which beats the UA rule [hidden] relies
        # on: el.hidden read back true while both stayed on screen over the panel.
        out = map_html.render(_map())

        self.assertIn("[hidden] { display:none !important; }", out)


class BigCanvasTests(SimpleTestCase):
    """Everything a page touches, in one canvas."""

    def test_a_page_draws_all_its_symbols_not_only_its_modules(self):
        # It used to show page and module nodes until a reader drilled in, which hid the
        # thing the canvas is for: which symbols connect and which do not.
        out = map_html.render(_map())

        self.assertNotIn('n.kind === "page" || n.kind === "module"', out)
        self.assertIn("const kinds = SECTION_KINDS[mode]", out)

    def test_a_column_wraps_into_lanes_instead_of_running_off_the_page(self):
        # One page holds 839 selectors: in a single file that column stood 25,000px tall.
        out = map_html.render(_map())

        self.assertIn("function place(buckets, used, rows)", out)
        self.assertIn("ROW_CHOICES", out)

    def test_the_wrap_is_chosen_by_measuring_not_by_a_constant(self):
        # A fixed 42 laid the widest page out 6,600px wide; a formula off the tallest
        # column alone then made the small pages worse.
        out = map_html.render(_map())

        self.assertIn("if (fit > bestFit)", out)

    def test_a_section_is_a_lens_on_the_canvas_and_a_list_only_on_request(self):
        out = map_html.render(_map())

        self.assertIn("SECTION_KINDS = {", out)
        self.assertIn('id="aslist"', out)

    def test_the_switch_back_to_the_map_survives_list_mode(self):
        # The toggle lived in a row hidden whenever the canvas was, so the list was a
        # dead end.
        out = map_html.render(_map())

        self.assertIn("listToggle.hidden = !hasLens", out)

    def test_unresolved_and_unused_are_filled_not_just_outlined(self):
        # At the zoom where a whole page fits, labels are gone and an outline among 1,366
        # outlines carries nothing. The fill is a tuned token per status, not a tint mixed
        # against the panel - which is nearly black in dark mode and mixed to grey mush.
        out = map_html.render(_map())

        self.assertIn('F[n.status] || "var(--panel)"', out)
        self.assertIn("--crit-fill", out)

    def test_labels_are_hidden_by_class_not_by_rebuilding_the_page(self):
        # Below this zoom the text is sub-pixel anyway. Leaving it out of the markup meant
        # crossing the threshold re-parsed every node on the page, so a wheel tick on a
        # large page stalled; the threshold is one class on the svg now.
        out = map_html.render(_map())

        self.assertIn('svg.classList.toggle("nolabels", view.k < 0.34)', out)
        self.assertIn("svg.nolabels .nd text { display:none; }", out)

    def test_panning_moves_one_transform_instead_of_redrawing(self):
        # The whole reason a 1,450-node page was unusable: draw() rebuilt the markup on
        # every pointermove frame. Pan and zoom write one attribute on one group.
        out = map_html.render(_map())

        self.assertIn("view.x = e.clientX - drag.x; view.y = e.clientY - drag.y; applyView();", out)
        self.assertIn("const zoomTo = k => { view.k = Math.min(3, Math.max(0.2, k)); applyView(); }", out)
        self.assertIn('g.setAttribute("transform"', out)

    def test_the_layout_is_cached_against_what_actually_changes_it(self):
        # layout() is nine trial placements over every node. Recomputing it for a pan is
        # the difference between a smooth map and a slideshow.
        out = map_html.render(_map())

        self.assertIn("function layoutKey()", out)
        self.assertIn("if (_layout.key !== key)", out)


class ReadabilityTests(SimpleTestCase):
    def test_a_trackpad_pans_and_only_a_pinch_zooms(self):
        # A Mac trackpad streams high-resolution wheel events; a fixed 1.1x per event
        # crossed the whole zoom range on one flick. macOS marks a pinch as ctrlKey.
        out = map_html.render(_map())

        self.assertIn("if (e.ctrlKey || e.metaKey)", out)
        self.assertIn("Math.exp(-e.deltaY * 0.01)", out)
        self.assertNotIn("e.deltaY < 0 ? 1.1 : 0.9", out)

    def test_the_colour_key_is_on_screen_not_behind_a_button(self):
        out = map_html.render(_map())

        self.assertIn('id="colourkey"', out)
        for status in ("connected", "unresolved", "unused", "uncertain"):
            self.assertIn(f'class="k {status}"', out)
        self.assertIn("not a claim it is dead", out)

    def test_the_legend_does_not_borrow_the_key_buttons_class(self):
        # Both were called .key, so the strip inherited the button's absolute position and
        # rendered as a 40px square in the bottom corner.
        out = map_html.render(_map())

        self.assertIn('class="legendbar"', out)

    def test_clicking_lights_the_line_through_a_node_not_its_whole_island(self):
        # Walking edges undirected reaches the page node, and from there everything:
        # clicking one endpoint lit all 327 symbols and said nothing.
        out = map_html.render(_map())

        self.assertIn("walk(back, id);", out)
        self.assertIn("walk(fwd, id);", out)

    def test_a_chain_can_be_isolated_onto_the_canvas(self):
        out = map_html.render(_map())

        self.assertIn("if (isolate && lit) return chainOf(p, lit)", out)
        self.assertIn("Show only this chain", out)

    def test_source_is_offered_on_request_not_poured_into_the_panel(self):
        # Inline, one chain filled the panel with six listings nobody asked for, which had
        # to be scrolled past to see the shape of the path.
        node = MapNode("v", "view", "view", "connected", file="v.py", line=3,
                       snippet="def x(): ...", context="    2  # above\n    3  def x():")
        out = map_html.render(_map(pages=[PageMap("p", [node], [])]))

        self.assertIn("# above", out)
        self.assertIn('<button class="code" data-code=', out)
        self.assertIn('id="codebox"', out)

    def test_the_same_line_of_source_is_not_listed_twice(self):
        # A fetch and the call that makes it share a line, and both were printed in full
        # under two different headings.
        self.assertIn("function deduper()", map_html.render(_map()))


class FileTreeViewTests(SimpleTestCase):
    def _files(self):
        return [
            {"path": "pointless/views/push_views.py",
             "counts": {"connected": 12}, "declarations": 41, "known": 12},
            {"path": "pointless/static/js/a.js",
             "counts": {"unresolved": 1}, "declarations": 0, "known": 0},
        ]

    def test_the_tree_is_offered_beside_the_map(self):
        out = map_html.render(_map(), files=self._files())

        self.assertIn('title: "Files"', out)
        self.assertIn("function treeHtml()", out)

    def test_a_file_carries_how_much_of_it_the_graph_knows(self):
        out = map_html.render(_map(), files=self._files())

        self.assertIn('"declarations": 41', out)
        self.assertIn('"known": 12', out)

    def test_coverage_is_named_as_coverage_not_as_a_finding(self):
        # A helper that makes no request and touches no element produces no symbol
        # because there is nothing to model, not because it is dead.
        out = map_html.render(_map(), files=self._files())

        self.assertIn("that is coverage, not a finding", out)

    def test_a_file_with_no_declarations_says_so_rather_than_showing_an_empty_bar(self):
        self.assertIn("no declarations", map_html.render(_map(), files=self._files()))

    def test_choosing_a_file_draws_only_that_file(self):
        out = map_html.render(_map(), files=self._files())

        self.assertIn("if (fileFilter) return new Set(p.nodes.filter(n => n.file === fileFilter)", out)

    def test_a_filtered_tree_opens_itself(self):
        # 52 folders collapsed by default meant a match sat inside a folder nobody opened.
        out = map_html.render(_map(), files=self._files())

        self.assertIn('depth < 2 || needle ? " open" : ""', out)


class PaletteTests(SimpleTestCase):
    def test_both_themes_define_every_colour_they_use(self):
        # A colour defined only inside the dark block renders as nothing in light, and
        # the other way round - the classic unreadable-page bug.
        out = map_html.render(_map())
        tokens = ("--bg", "--panel", "--sunk", "--ink", "--muted", "--line", "--sig",
                  "--ok", "--crit", "--warn", "--dim",
                  "--ok-fill", "--crit-fill", "--warn-fill", "--dim-fill")
        light = out[out.index(":root {"):out.index("@media (prefers-color-scheme: dark)")]
        dark = out[out.index("@media (prefers-color-scheme: dark)"):out.index("* { box-sizing")]

        for token in tokens:
            self.assertIn(f"{token}:", light, f"{token} missing from the light palette")
            self.assertIn(f"{token}:", dark, f"{token} missing from the dark palette")

    def test_no_colour_is_mixed_against_the_panel_at_runtime(self):
        # A nearly-black dark panel mixed to grey mush; each status carries its own fill
        # token instead. Matching the bare word also matched the comment explaining it -
        # the call is what matters, so the call is what is asserted.
        self.assertNotIn("color-mix(", map_html.render(_map()))


class FilterFeedbackTests(SimpleTestCase):
    """A filter that matched nothing greyed all 1,450 nodes and said nothing at all."""

    def test_it_says_how_many_matched(self):
        out = map_html.render(_map())

        self.assertIn("function reportMatches(drawn, matched)", out)
        self.assertIn("`no match in ${drawn}`", out)
        self.assertIn("`${matched} of ${drawn}`", out)

    def test_nothing_matching_is_not_drawn_as_everything_dimmed(self):
        # Fading is only meaningful when something survives it. With no matches every
        # node dimmed at once, leaving a canvas of ghosts under full-strength edges -
        # which reads as a broken map, not as "nothing here is called that".
        out = map_html.render(_map())

        self.assertIn("const fading = !query || matched > 0;", out)
        self.assertIn("const shown = (!fading || hit(n))", out)

    def test_edges_follow_the_filter_too(self):
        # Dimming only the nodes left the lines at full strength over the ghosts.
        out = map_html.render(_map())

        self.assertIn("|| (fading && query && !ends.every(n => n && hit(n)))", out)


class LabelFittingTests(SimpleTestCase):
    def test_both_ends_of_a_long_label_survive(self):
        # `api/announcements/m…` and `api/announcements/p…` were the same string on
        # screen, and the end is the half that identifies a route or a view.
        out = map_html.render(_map())

        self.assertIn("function fit(text, max)", out)
        self.assertIn('return value.slice(0, max - 1 - tail) + "…" + value.slice(-tail);', out)

    def test_the_full_value_is_on_the_node_itself(self):
        # Truncation is a display choice; hovering must still answer what it really is.
        out = map_html.render(_map())

        self.assertIn("<title>${esc(n.label)}", out)


class FilesViewTests(SimpleTestCase):
    def test_the_row_advertises_the_map_not_the_editor(self):
        # The row's own action is "draw this file on the map" - the thing the view exists
        # for - and it was invisible next to an `open` link that left for VS Code.
        out = map_html.render(_map(), console=None, files=[
            {"path": "a/b.js", "counts": {"connected": 1}, "declarations": 2, "known": 1},
        ], repo_root="/repo", editor="vscode")

        self.assertIn('<span class="go">on map \\u2192</span>', out)
        self.assertIn(">edit</a>", out)
        self.assertNotIn(">open</a>", out)
    def test_clicking_a_file_goes_to_the_page_that_holds_it(self):
        # Keeping whatever page was selected answered "what of this file is on the page
        # you happened to be looking at" - 3 symbols of 674 for push_arena.js, a canvas
        # that reads as the file being unwired.
        out = map_html.render(_map())

        self.assertIn("function bestPageFor(path)", out)
        self.assertIn("current = bestPageFor(fileFilter);", out)

    def test_the_breadcrumb_says_how_much_of_the_file_is_on_screen(self):
        # And counts the total from FILES, not from the drawn page: the canvas can only
        # show what a page entry reaches, so counting what it drew always said "210 of
        # 210" and hid the 464 symbols in that file no page reaches - the interesting ones.
        out = map_html.render(_map())

        self.assertIn("symbols`", out)
        self.assertIn("the rest are not reached from any page", out)
        self.assertIn("const FILE_TOTALS = new Map(\n  FILES.map(", out)


class PathNumberingTests(SimpleTestCase):
    def test_each_hop_is_numbered_and_the_last_one_says_so(self):
        # Five unlabelled boxes down a rule do not say which end is the browser and which
        # is the database, and a reader tracing a bug needs to know which way round.
        out = map_html.render(_map())

        self.assertIn('<span class="hn">${step}</span>', out)
        self.assertIn('<span class="hs">last</span>', out)
        self.assertIn("Path — browser to backend · ${path.length} hop", out)


class ColophonTests(SimpleTestCase):
    def test_it_asks_once_at_the_bottom_of_the_panel_people_read(self):
        # A tool that asks on every screen is an advert; one that never asks does not get
        # maintained. One line, in Overview, under everything else.
        out = map_html.render(_map())

        self.assertEqual(out.count("github.com/sponsors"), 1)
        self.assertIn("Got a finding wrong?", out)

    def test_the_links_cannot_navigate_the_report_away(self):
        out = map_html.render(_map())

        for chunk in out.split('<a href="https://github.com')[1:]:
            self.assertIn('target="_blank"', chunk[:200])
            self.assertIn('rel="noreferrer"', chunk[:200])
