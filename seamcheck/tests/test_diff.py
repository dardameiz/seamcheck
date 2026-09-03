from django.test import SimpleTestCase

from seamcheck.diff import diff_graphs
from seamcheck.graph import Graph, Status, Symbol
from seamcheck.triage import TriageEntry, TriageStatus, fingerprint_for_symbol


def _symbol(id_, status, snippet="s"):
    return Symbol(
        id=id_, kind="view", label=id_, sub="", file="", line=None,
        status=status, snippet=snippet, chain=[], note="",
    )


class DiffTests(SimpleTestCase):
    def test_detects_a_newly_unresolved_symbol(self):
        result = diff_graphs(
            Graph([_symbol("a", Status.CONNECTED)], []),
            Graph([_symbol("a", Status.UNRESOLVED)], []),
        )

        self.assertEqual([s.id for s in result.new_unresolved], ["a"])

    def test_detects_a_newly_unused_symbol(self):
        result = diff_graphs(
            Graph([_symbol("b", Status.CONNECTED)], []),
            Graph([_symbol("b", Status.UNUSED)], []),
        )

        self.assertEqual(len(result.new_unused), 1)

    def test_detects_a_resolved_symbol(self):
        result = diff_graphs(
            Graph([_symbol("c", Status.UNRESOLVED)], []),
            Graph([_symbol("c", Status.CONNECTED)], []),
        )

        self.assertEqual([s.id for s in result.resolved], ["c"])

    def test_a_brand_new_bad_symbol_counts_as_new(self):
        result = diff_graphs(Graph([], []), Graph([_symbol("d", Status.UNUSED)], []))

        self.assertEqual(len(result.new_unused), 1)
        self.assertEqual(result.resolved, [])

    def test_an_unchanged_bad_symbol_is_not_reported_again(self):
        # Otherwise every scan re-reports the entire backlog and nobody reads it.
        old = Graph([_symbol("e", Status.UNUSED)], [])

        result = diff_graphs(old, Graph([_symbol("e", Status.UNUSED)], []))

        self.assertEqual(result.new_unused, [])

    def test_multi_writer_defaults_to_empty_for_this_plan(self):
        result = diff_graphs(Graph([], []), Graph([], []))

        self.assertEqual(result.new_multi_writer, [])


class TriageInvalidationTests(SimpleTestCase):
    def test_a_changed_fingerprint_is_reported_as_invalidated(self):
        approved = _symbol("f", Status.UNUSED, snippet="old code")
        entry = TriageEntry(
            symbol_id="f", fingerprint=fingerprint_for_symbol(approved),
            status=TriageStatus.APPROVED, who="alice", when="2026-08-29", reason="ok",
        )

        result = diff_graphs(
            Graph([approved], []),
            Graph([_symbol("f", Status.UNUSED, snippet="new code")], []),
            triage_entries=[entry],
        )

        self.assertEqual(len(result.triage_invalidated), 1)
        self.assertIn("re-triage", result.triage_invalidated[0]["note"].lower())

    def test_an_unchanged_fingerprint_is_not_invalidated(self):
        symbol = _symbol("g", Status.UNUSED)
        entry = TriageEntry(
            symbol_id="g", fingerprint=fingerprint_for_symbol(symbol),
            status=TriageStatus.APPROVED, who="alice", when="2026-08-29", reason="ok",
        )

        result = diff_graphs(
            Graph([symbol], []), Graph([symbol], []),
            triage_entries=[entry],
        )

        self.assertEqual(result.triage_invalidated, [])


class FingerprintAgreementTests(SimpleTestCase):
    def test_diff_invalidates_exactly_what_triage_calls_stale(self):
        # Two copies of the formula drift apart and reappearance detection fails open,
        # silently keeping approvals alive after their evidence changed. The diff now
        # asks triage which marks are stale rather than deciding for itself.
        from seamcheck.triage import stale_entries

        old = _symbol("f", Status.UNUSED, snippet="old code")
        new = _symbol("f", Status.UNUSED, snippet="new code")
        same = _symbol("g", Status.UNUSED)
        entries = [
            TriageEntry(symbol_id="f", fingerprint=fingerprint_for_symbol(old),
                        status=TriageStatus.APPROVED, who="a", when="2026-08-29", reason=""),
            TriageEntry(symbol_id="g", fingerprint=fingerprint_for_symbol(same),
                        status=TriageStatus.APPROVED, who="a", when="2026-08-29", reason=""),
        ]
        after = Graph([new, same], [])

        result = diff_graphs(Graph([old, same], []), after, triage_entries=entries)

        self.assertEqual([item["symbol_id"] for item in result.triage_invalidated],
                         [entry.symbol_id for entry in stale_entries(after, entries)])
        self.assertEqual([item["symbol_id"] for item in result.triage_invalidated], ["f"])
