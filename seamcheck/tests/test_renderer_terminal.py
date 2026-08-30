from django.test import SimpleTestCase

from seamcheck.graph import Status, Symbol
from seamcheck.renderers import terminal
from seamcheck.report import Report, ReportGroup


def _symbol(id_, kind="url", status=Status.UNRESOLVED):
    return Symbol(
        id=id_, kind=kind, label=id_, sub="", file="a.py", line=7,
        status=status, snippet=f"<{id_}>", chain=[id_], note="",
    )


def _report(**kwargs):
    base = {
        "git_sha": "abc123def456", "generated_at": "2026-08-30T00:00:00",
        "baseline_sha": "0000111", "baseline_message": "",
        "new_findings": [], "resolved": [], "triage_invalidated": [], "groups": [],
        "counts": {"connected": 3, "unresolved": 1, "unused": 0, "uncertain": 2},
    }
    base.update(kwargs)
    return Report(**base)


class TerminalRenderTests(SimpleTestCase):
    def test_a_new_finding_shows_its_label_and_location(self):
        out = terminal.render(_report(new_findings=[_symbol("api/ghost/")]))

        self.assertIn("api/ghost/", out)
        self.assertIn("a.py:7", out)

    def test_groups_show_a_title_and_a_count(self):
        group = ReportGroup("css_token_def", Status.UNUSED, "Unused design tokens",
                            [_symbol(f"t{i}") for i in range(12)], triaged=2)

        out = terminal.render(_report(groups=[group]))

        self.assertIn("Unused design tokens", out)
        # Bare "12" also matches the header's git_sha[:12] ("abc123def456" contains "12"),
        # so it would pass even if the count were dropped or hard-coded. Anchor on the
        # parenthesized form so this actually pins the group's rendered symbol count.
        self.assertIn("(12", out)

    def test_a_group_is_capped_at_five_with_a_more_marker(self):
        group = ReportGroup("url", Status.UNRESOLVED, "URLs",
                            [_symbol(f"u{i}") for i in range(9)], triaged=0)

        out = terminal.render(_report(groups=[group]))

        self.assertIn("+4 more", out)
        self.assertNotIn("u8", out)

    def test_a_group_caveat_is_shown_next_to_the_title(self):
        group = ReportGroup(
            "css_selector", Status.UNUSED, "Unreferenced CSS selectors",
            [_symbol("s1", kind="css_selector", status=Status.UNUSED)], triaged=0,
            caveat="JavaScript that applies classes via className is not yet scanned.",
        )

        out = terminal.render(_report(groups=[group]))

        self.assertIn("JavaScript that applies classes via className is not yet scanned.", out)

    def test_a_group_with_no_caveat_shows_none(self):
        group = ReportGroup("url", Status.UNRESOLVED, "URLs", [_symbol("u1")], triaged=0)

        out = terminal.render(_report(groups=[group]))

        self.assertNotIn("is not yet scanned", out)

    def test_the_baseline_message_is_shown_when_there_is_no_baseline(self):
        out = terminal.render(
            _report(baseline_sha=None, baseline_message="No baseline snapshot stored yet.")
        )

        self.assertIn("No baseline snapshot stored yet.", out)

    def test_the_uncertain_count_label_and_gloss_sentence_are_rendered(self):
        # Not a test that uncertain symbols are excluded from groups/new_findings - that
        # guarantee is structural, in report.py's _FINDING_STATUSES, and cannot reach a
        # renderer at all. This only checks the count label and gloss sentence text.
        out = terminal.render(_report())

        self.assertIn("uncertain", out)
        self.assertIn("no evidence either way", out.lower())

    def test_an_empty_report_still_says_something_useful(self):
        out = terminal.render(_report())

        self.assertIn("nothing new", out.lower())

    def test_output_carries_no_ansi_escape_codes(self):
        # CI logs and piped output must stay readable.
        out = terminal.render(_report(new_findings=[_symbol("x")]))

        self.assertNotIn("\x1b[", out)

    def test_a_triage_invalidation_is_reported(self):
        out = terminal.render(_report(triage_invalidated=[
            {"symbol_id": "x", "note": "evidence changed, re-triage"}
        ]))

        self.assertIn("x", out)
        self.assertIn("re-triage", out)

    def test_the_counts_line_keeps_the_reports_order_not_alphabetical(self):
        # report.py emits counts in Status declaration order (connected, unused,
        # unresolved, uncertain) - not alphabetical. Pin that order here so the renderer
        # can never quietly re-sort it out from under the other two surfaces.
        counts = {status.value: i for i, status in enumerate(Status, start=1)}

        out = terminal.render(_report(counts=counts))

        self.assertIn("connected 1  unused 2  unresolved 3  uncertain 4", out)
