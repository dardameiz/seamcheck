from django.test import SimpleTestCase

from signal_map.graph import Status, Symbol
from signal_map.renderers import markdown
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


class MarkdownRenderTests(SimpleTestCase):
    def test_it_opens_with_a_heading(self):
        self.assertTrue(markdown.render(_report()).startswith("## "))

    def test_a_new_finding_appears_as_a_list_item_not_only_in_a_table(self):
        # Plenty of chat clients render no tables at all.
        out = markdown.render(_report(new_findings=[_symbol("api/ghost/")]))

        self.assertIn("- ", out)
        self.assertIn("api/ghost/", out)

    def test_evidence_locations_are_code_spans(self):
        out = markdown.render(_report(new_findings=[_symbol("x")]))

        self.assertIn("`a.py:7`", out)

    def test_the_label_is_a_code_span_not_interpolated_raw(self):
        # 45 real labels contain "[" / "]" (Tailwind arbitrary values like
        # "text-[9px]") - unescaped markdown, "[" opens link/reference syntax. A code
        # span neutralises it without a separate HTML-escaping pass.
        out = markdown.render(_report(new_findings=[_symbol("text-[9px]")]))

        self.assertIn("`text-[9px]`", out)

    def test_a_group_is_capped_at_ten_with_a_more_marker(self):
        group = ReportGroup("url", Status.UNRESOLVED, "URLs",
                            [_symbol(f"u{i}") for i in range(15)], triaged=0)

        out = markdown.render(_report(groups=[group]))

        self.assertIn("+5 more", out)
        self.assertNotIn("u14", out)

    def test_a_group_caveat_is_rendered_as_an_italic_line(self):
        group = ReportGroup(
            "css_selector", Status.UNUSED, "Unreferenced CSS selectors",
            [_symbol("s1", kind="css_selector", status=Status.UNUSED)], triaged=0,
            caveat="JavaScript that applies classes via className is not yet scanned.",
        )

        out = markdown.render(_report(groups=[group]))

        self.assertIn("_JavaScript that applies classes via className is not yet scanned._", out)

    def test_a_group_with_no_caveat_shows_none(self):
        group = ReportGroup("url", Status.UNRESOLVED, "URLs", [_symbol("u1")], triaged=0)

        out = markdown.render(_report(groups=[group]))

        self.assertNotIn("is not yet scanned", out)

    def test_the_uncertain_gloss_sentence_is_rendered(self):
        # Not a test that uncertain symbols are excluded from groups/new_findings - that
        # guarantee is structural, in report.py's _FINDING_STATUSES, and cannot reach a
        # renderer at all. This only checks the gloss sentence text is present.
        out = markdown.render(_report())

        self.assertIn("no evidence either way", out.lower())

    def test_no_baseline_says_so(self):
        out = markdown.render(
            _report(baseline_sha=None, baseline_message="No baseline snapshot stored yet.")
        )

        self.assertIn("No baseline snapshot stored yet.", out)

    def test_an_empty_report_still_says_something_useful(self):
        self.assertIn("nothing new", markdown.render(_report()).lower())

    def test_the_counts_line_keeps_the_reports_order_not_alphabetical(self):
        # report.py emits counts in Status declaration order (connected, unused,
        # unresolved, uncertain) - not alphabetical. Pin that order here so the renderer
        # can never quietly re-sort it out from under the other two surfaces.
        counts = {status.value: i for i, status in enumerate(Status, start=1)}

        out = markdown.render(_report(counts=counts))

        positions = [out.index(status.value) for status in Status]
        self.assertEqual(positions, sorted(positions))
