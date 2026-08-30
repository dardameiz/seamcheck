from django.test import SimpleTestCase

from signal_map.diff import DiffResult
from signal_map.graph import Graph, Status, Symbol
from signal_map.report import build_report
from signal_map.triage import TriageEntry, TriageStatus, fingerprint_for_symbol


def _symbol(id_, kind="url", status=Status.UNRESOLVED, file="a.py", line=1):
    return Symbol(
        id=id_, kind=kind, label=id_, sub="", file=file, line=line,
        status=status, snippet=f"<{id_}>", chain=[id_], note="",
    )


def _report(symbols, diff=None, entries=None, **kwargs):
    return build_report(
        graph=Graph(symbols=symbols, edges=[]),
        diff=diff,
        entries=entries or [],
        git_sha="abc123",
        now="2026-08-30T00:00:00",
        **kwargs,
    )


class OrderingTests(SimpleTestCase):
    def test_new_findings_are_ordered_multi_writer_then_unresolved_then_unused(self):
        mw = _symbol("mw", kind="multi_writer_element")
        un = _symbol("un", status=Status.UNUSED)
        ur = _symbol("ur", status=Status.UNRESOLVED)
        diff = DiffResult(
            new_unresolved=[ur], new_unused=[un], resolved=[],
            triage_invalidated=[], new_multi_writer=[mw],
        )

        report = _report([mw, un, ur], diff=diff)

        self.assertEqual([s.id for s in report.new_findings], ["mw", "ur", "un"])

    def test_ties_break_on_file_then_line(self):
        a = _symbol("a", file="b.py", line=2)
        b = _symbol("b", file="b.py", line=1)
        c = _symbol("c", file="a.py", line=9)
        diff = DiffResult([a, b, c], [], [], [], [])

        report = _report([a, b, c], diff=diff)

        self.assertEqual([s.id for s in report.new_findings], ["c", "b", "a"])

    def test_groups_are_ordered_by_size_descending(self):
        symbols = [_symbol(f"t{i}", kind="css_token_def", status=Status.UNUSED) for i in range(3)]
        symbols += [_symbol("u1", kind="url")]

        report = _report(symbols)

        self.assertEqual([g.kind for g in report.groups], ["css_token_def", "url"])


class ContentTests(SimpleTestCase):
    def test_uncertain_is_counted_but_never_a_finding_or_a_group(self):
        report = _report([_symbol("u", status=Status.UNCERTAIN)])

        self.assertEqual(report.counts["uncertain"], 1)
        self.assertEqual(report.new_findings, [])
        self.assertEqual(report.groups, [])

    def test_connected_symbols_are_counted_but_never_grouped(self):
        report = _report([_symbol("c", status=Status.CONNECTED)])

        self.assertEqual(report.counts["connected"], 1)
        self.assertEqual(report.groups, [])

    def test_a_new_finding_is_not_repeated_in_a_group(self):
        # A reader who sees the same item twice stops trusting the counts.
        new = _symbol("dup")
        old = _symbol("old")
        report = _report([new, old], diff=DiffResult([new], [], [], [], []))

        grouped = [s.id for group in report.groups for s in group.symbols]
        self.assertEqual(grouped, ["old"])

    def test_group_titles_are_human_readable(self):
        report = _report([_symbol("t", kind="css_token_def", status=Status.UNUSED)])

        self.assertEqual(report.groups[0].title, "Unused design tokens")

    def test_an_unknown_kind_still_gets_a_readable_title(self):
        report = _report([_symbol("x", kind="weird_new_kind")])

        self.assertEqual(report.groups[0].title, "Weird new kind")

    def test_group_counts_how_many_are_already_triaged(self):
        symbol = _symbol("t")
        entry = TriageEntry(
            symbol_id="t", fingerprint=fingerprint_for_symbol(symbol),
            status=TriageStatus.APPROVED, who="a", when="2026-08-30", reason="",
        )

        report = _report([symbol], entries=[entry])

        self.assertEqual(report.groups[0].triaged, 1)

    def test_a_stale_triage_mark_does_not_count_as_triaged(self):
        # The fingerprint covers the evidence; different evidence is not what was approved.
        entry = TriageEntry(
            symbol_id="t", fingerprint="stale", status=TriageStatus.APPROVED,
            who="a", when="2026-08-30", reason="",
        )

        report = _report([_symbol("t")], entries=[entry])

        self.assertEqual(report.groups[0].triaged, 0)

    def test_css_selector_group_carries_a_caveat_about_js_applied_classes(self):
        # The extractor never reads className/classList.add/setAttribute('class', ...),
        # so a class applied by JS looks unreferenced - this group is 98% of a real
        # scan's `unused` count and sorts first, so the caveat has to travel with the
        # group rather than live only in the README nobody reads before triaging.
        report = _report([_symbol("s", kind="css_selector", status=Status.UNUSED)])

        self.assertIn("className", report.groups[0].caveat)
        self.assertIn("classList.add", report.groups[0].caveat)

    def test_a_kind_with_no_known_recall_gap_carries_no_caveat(self):
        report = _report([_symbol("u", kind="url")])

        self.assertEqual(report.groups[0].caveat, "")

    def test_group_status_is_the_most_severe_present_regardless_of_insertion_order(self):
        # json_field genuinely mixes statuses in one scan (field_matcher.py emits both
        # CONNECTED/UNUSED and UNRESOLVED into the same kind) -- the header must never
        # hide the worse finding behind whichever symbol happened to be appended first.
        unused_first = _report([
            _symbol("f1", kind="json_field", status=Status.UNUSED),
            _symbol("f2", kind="json_field", status=Status.UNRESOLVED),
        ])
        unresolved_first = _report([
            _symbol("f3", kind="json_field", status=Status.UNRESOLVED),
            _symbol("f4", kind="json_field", status=Status.UNUSED),
        ])

        self.assertIs(unused_first.groups[0].status, Status.UNRESOLVED)
        self.assertIs(unresolved_first.groups[0].status, Status.UNRESOLVED)


class BaselineTests(SimpleTestCase):
    def test_no_diff_means_no_new_findings_and_the_message_is_kept(self):
        report = _report(
            [_symbol("a")], diff=None, baseline_message="No baseline snapshot stored yet.",
        )

        self.assertEqual(report.new_findings, [])
        self.assertIsNone(report.baseline_sha)
        self.assertIn("No baseline", report.baseline_message)

    def test_the_report_records_the_commit_it_describes(self):
        self.assertEqual(_report([]).git_sha, "abc123")
