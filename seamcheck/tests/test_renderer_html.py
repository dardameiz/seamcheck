import html as html_lib

from django.test import SimpleTestCase

from seamcheck.graph import Status, Symbol
from seamcheck.renderers import html
from seamcheck.report import Report, ReportGroup


def _symbol(id_, kind="url", status=Status.UNRESOLVED, label=None, file="a.py", line=7, note=""):
    return Symbol(
        id=id_, kind=kind, label=id_ if label is None else label, sub="", file=file, line=line,
        status=status, snippet=f"<{id_}>", chain=[id_], note=note,
    )


def _report(**kwargs):
    # A literal, not dict(...): the ruff C4 ruleset this plan mandates rejects C408.
    base = {
        "git_sha": "abc123def456", "generated_at": "2026-08-30T00:00:00",
        "baseline_sha": "0000111", "baseline_message": "",
        "new_findings": [], "resolved": [], "triage_invalidated": [], "groups": [],
        "counts": {"connected": 3, "unresolved": 1, "unused": 0, "uncertain": 2},
    }
    base.update(kwargs)
    return Report(**base)


class HtmlRenderTests(SimpleTestCase):
    def test_it_is_a_complete_document(self):
        out = html.render(_report())

        self.assertTrue(out.lstrip().startswith("<!doctype html>"))
        self.assertIn("</html>", out)

    def test_it_makes_no_network_requests(self):
        # This surface exists for a phone with no access to the machine that built it.
        # A single CDN, webfont, or fetch-shaped reference breaks it exactly where it
        # cannot report back - so every syntax that can trigger a fetch gets its own
        # entry, not just the two literal URL schemes.
        out = html.render(_report(new_findings=[_symbol("x")]))

        forbidden = (
            "http://", "https://",       # absolute URL, either scheme
            '"//', "url(//",             # protocol-relative URL in an attribute or CSS url()
            "url(", "@import",           # any CSS/font/image reference or external stylesheet
            "<link", "<img",             # stylesheet/preconnect/prefetch tag, image tag
            "srcset",                    # responsive image attribute (no leading space, unlike src=)
            "href=",                     # anchor/link/SVG <use href="..."> reference
            "src=",                      # any fetching attribute: script, img, iframe, audio, video
            "fetch(",                    # fetch API
            "XMLHttpRequest",            # XHR
            "import(",                   # dynamic ES module import
            "new WebSocket",             # WebSocket connection
            "EventSource",               # server-sent events
            "sendBeacon",                # navigator.sendBeacon
        )
        for pattern in forbidden:
            self.assertNotIn(pattern, out)

    def test_groups_collapse_without_javascript(self):
        group = ReportGroup("url", Status.UNRESOLVED, "URLs", [_symbol("u1")], triaged=0)

        out = html.render(_report(groups=[group]))

        self.assertIn("<details", out)
        self.assertIn("<summary", out)

    def test_a_group_shows_every_symbol(self):
        group = ReportGroup("url", Status.UNRESOLVED, "URLs",
                            [_symbol(f"u{i}") for i in range(30)], triaged=0)

        out = html.render(_report(groups=[group]))

        self.assertIn("u29", out)

    def test_it_declares_a_mobile_viewport(self):
        self.assertIn('name="viewport"', html.render(_report()))

    def test_it_defines_both_colour_schemes(self):
        out = html.render(_report())

        self.assertIn("prefers-color-scheme: dark", out)

    def test_the_counts_keep_the_reports_order_not_alphabetical(self):
        # report.py owns the order; a renderer that re-sorts is a bug. Terminal shipped
        # sorted() here and had to be fixed - this pins it so it cannot come back.
        counts = {status.value: index for index, status in enumerate(Status, start=1)}

        out = html.render(_report(counts=counts))

        positions = [out.index(name) for name in ("connected", "unused", "unresolved", "uncertain")]
        self.assertEqual(positions, sorted(positions))

    def test_symbol_text_is_escaped(self):
        # Snippets are source code and routinely contain angle brackets and quotes.
        out = html.render(_report(new_findings=[_symbol("<script>alert(1)</script>")]))

        self.assertNotIn("<script>alert(1)", out)
        self.assertIn(html_lib.escape("<script>alert(1)</script>"), out)

    def test_it_ships_a_client_side_filter(self):
        # 5,452 items live behind collapsed groups; without a filter the page is a
        # scroll, not a tool. The script is inline - no network request.
        out = html.render(_report(groups=[
            ReportGroup("url", Status.UNRESOLVED, "URLs", [_symbol("u1")], triaged=0)
        ]))

        self.assertIn("<script>", out)
        self.assertIn('id="filter"', out)

    def test_a_group_caveat_is_shown_under_the_summary(self):
        group = ReportGroup(
            "css_selector", Status.UNUSED, "Unreferenced CSS selectors",
            [_symbol("s1", kind="css_selector", status=Status.UNUSED)], triaged=0,
            caveat="JavaScript that applies classes via className is not yet scanned.",
        )

        out = html.render(_report(groups=[group]))

        summary_end = out.index("</summary>")
        caveat_pos = out.index(
            "JavaScript that applies classes via className is not yet scanned."
        )
        self.assertGreater(caveat_pos, summary_end)

    def test_a_group_with_no_caveat_shows_none(self):
        group = ReportGroup("url", Status.UNRESOLVED, "URLs", [_symbol("u1")], triaged=0)

        out = html.render(_report(groups=[group]))

        self.assertNotIn("is not yet scanned", out)

    def test_filter_script_caches_item_text_once_instead_of_per_keystroke(self):
        # The old script re-read el.textContent on every "input" event across every
        # .item node - on a real render (7,097 items) that forces layout+paint of the
        # whole page on the first character AND every keystroke after it. The fix reads
        # and lowercases each item's text exactly once, at load, outside the handler.
        out = html.render(_report(groups=[
            ReportGroup("url", Status.UNRESOLVED, "URLs", [_symbol("u1")], triaged=0)
        ]))
        script = out[out.index("<script>") : out.index("</script>")]

        self.assertEqual(script.count("textContent.toLowerCase()"), 1)
        self.assertIn('document.querySelectorAll(".item")', script)
        # The DOM query happens once, in the setup code above addEventListener - not
        # inside the per-keystroke callback.
        setup, _, handler = script.partition("addEventListener")
        self.assertIn("querySelectorAll", setup)
        self.assertNotIn("querySelectorAll", handler)

    def test_filter_script_hides_a_group_with_zero_matches(self):
        out = html.render(_report())
        script = out[out.index("<script>") : out.index("</script>")]

        self.assertIn("group.el.hidden = n === 0", script)

    def test_filter_script_restores_collapse_and_count_when_query_clears(self):
        out = html.render(_report())
        script = out[out.index("<script>") : out.index("</script>")]

        self.assertIn("group.el.open = false", script)
        self.assertIn("group.count.textContent = group.count.dataset.total", script)

    def test_group_summary_carries_the_total_for_the_script_to_restore(self):
        group = ReportGroup(
            "url", Status.UNRESOLVED, "URLs",
            [_symbol("u1"), _symbol("u2")], triaged=0,
        )

        out = html.render(_report(groups=[group]))

        self.assertIn("data-total='2'", out)

    def test_the_uncertain_gloss_sentence_is_rendered(self):
        # Not a test that uncertain symbols are excluded from groups/new_findings - that
        # guarantee is structural, in report.py's _FINDING_STATUSES, and cannot reach a
        # renderer at all. This only checks the gloss sentence text is present.
        self.assertIn("no evidence either way", html.render(_report()).lower())

    def test_no_baseline_says_so(self):
        out = html.render(_report(baseline_sha=None, baseline_message="No baseline stored yet."))

        self.assertIn("No baseline stored yet.", out)

    def test_no_baseline_falls_back_when_the_message_is_empty(self):
        # baseline_message is only populated when baseline_sha is None; the fixture's
        # default is "" - this pins the fallback sentence for that not-yet-set state.
        out = html.render(_report(baseline_sha=None, baseline_message=""))

        self.assertIn("No baseline to compare against.", out)

    def test_note_is_escaped(self):
        # note is source-adjacent free text (e.g. why a symbol was triaged) and is just
        # as attacker/typo-reachable as label - the note branch only renders when note
        # is truthy, so every prior fixture (note="") skipped it entirely.
        hostile = '<script>alert("note")</script>'
        out = html.render(_report(new_findings=[_symbol("n1", note=hostile)]))

        self.assertNotIn('<script>alert("note")', out)
        self.assertIn(html_lib.escape(hostile), out)

    def test_group_title_is_escaped(self):
        hostile = 'URLs & <b>"unsafe"</b>'
        group = ReportGroup("url", Status.UNRESOLVED, hostile, [_symbol("u1")], triaged=0)

        out = html.render(_report(groups=[group]))

        self.assertNotIn("<b>\"unsafe\"</b>", out)
        self.assertIn(html_lib.escape(hostile), out)

    def test_triage_invalidated_fields_are_escaped(self):
        # symbol_id and note come straight from a dict built off scanned project data,
        # not a Symbol - a separate interpolation site from the two above.
        out = html.render(_report(triage_invalidated=[
            {"symbol_id": "<sym & id>", "note": 'no longer <valid> & "true"'},
        ]))

        self.assertNotIn("<sym & id>", out)
        self.assertNotIn("no longer <valid>", out)
        self.assertIn(html_lib.escape("<sym & id>"), out)
        self.assertIn(html_lib.escape('no longer <valid> & "true"'), out)

    def test_where_has_no_trailing_colon_when_there_is_no_line(self):
        out = html.render(_report(new_findings=[_symbol("n1", file="b.py", line=None)]))

        self.assertIn("b.py", out)
        self.assertNotIn("b.py:", out)

    def test_where_is_blank_when_there_is_no_file(self):
        out = html.render(_report(new_findings=[_symbol("n1", file="", line=None)]))

        # The label div always renders; assert the immediately-following where div is
        # empty rather than merely absent from the whole page (git_sha etc. would still
        # make a bare assertNotIn("b.py") pass even if _where crashed instead of
        # returning "").
        self.assertIn('<div class="where"></div>', out)

    def test_group_triaged_count_is_shown(self):
        group = ReportGroup("url", Status.UNRESOLVED, "URLs", [_symbol("u1")], triaged=3)

        out = html.render(_report(groups=[group]))

        self.assertIn("3 triaged", out)

    def test_resolved_count_is_shown(self):
        out = html.render(_report(resolved=[_symbol("r1"), _symbol("r2")]))

        self.assertIn("Resolved since the baseline (2)", out)


class ReturnedHtmlTests(SimpleTestCase):
    def _returned(self, **kwargs):
        base = {"symbol_id": "url:<x>", "label": "x", "kind": "url", "status": "unresolved",
                "file": "a.py", "line": 7, "marked": "approved", "why": "js-applied",
                "when": "2026-08-20", "who": "<alice>", "reason": "", "expired": "",
                "returned": True}
        base.update(kwargs)
        return base

    def test_a_returned_finding_is_shown_escaped_with_its_count(self):
        out = html.render(_report(returned=[self._returned()]))

        self.assertIn("Returned (1)", out)
        self.assertIn("returned 1", out)
        self.assertIn(html_lib.escape("<alice>"), out)
        self.assertNotIn("<alice>", out)
        self.assertIn("js-applied", out)

    def test_no_returned_means_no_count_and_no_heading(self):
        out = html.render(_report())

        self.assertNotIn("Returned", out)
        self.assertNotIn("returned", out)
