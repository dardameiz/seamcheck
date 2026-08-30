import html as html_lib

from django.test import SimpleTestCase

from signal_map.graph import Status, Symbol
from signal_map.renderers import html
from signal_map.report import Report, ReportGroup


def _symbol(id_, kind="url", status=Status.UNRESOLVED):
    return Symbol(
        id=id_, kind=kind, label=id_, sub="", file="a.py", line=7,
        status=status, snippet=f"<{id_}>", chain=[id_], note="",
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
        # A single CDN or webfont reference breaks it exactly where it cannot report back.
        out = html.render(_report(new_findings=[_symbol("x")]))

        for forbidden in ("http://", "https://", "<link", "<img", " src="):
            self.assertNotIn(forbidden, out)

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

    def test_uncertain_is_glossed_not_listed(self):
        self.assertIn("no evidence either way", html.render(_report()).lower())

    def test_no_baseline_says_so(self):
        out = html.render(_report(baseline_sha=None, baseline_message="No baseline stored yet."))

        self.assertIn("No baseline stored yet.", out)
