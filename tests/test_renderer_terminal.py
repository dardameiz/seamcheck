from django.test import SimpleTestCase

from signal_map.graph import Status, Symbol
from signal_map.renderers import terminal
from signal_map.report import Report, ReportGroup


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
        self.assertIn("12", out)

    def test_a_group_is_capped_at_five_with_a_more_marker(self):
        group = ReportGroup("url", Status.UNRESOLVED, "URLs",
                            [_symbol(f"u{i}") for i in range(9)], triaged=0)

        out = terminal.render(_report(groups=[group]))

        self.assertIn("+4 more", out)
        self.assertNotIn("u8", out)

    def test_the_baseline_message_is_shown_when_there_is_no_baseline(self):
        out = terminal.render(
            _report(baseline_sha=None, baseline_message="No baseline snapshot stored yet.")
        )

        self.assertIn("No baseline snapshot stored yet.", out)

    def test_uncertain_is_counted_but_never_presented_as_an_action(self):
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
