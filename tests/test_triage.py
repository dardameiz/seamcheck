import tempfile

from django.test import SimpleTestCase

from signal_map.graph import Graph, Status, Symbol
from signal_map.triage import (
    TriageEntry,
    TriageStatus,
    apply_triage,
    fingerprint_for_symbol,
    has_blocking_findings,
    load_triage,
    save_triage,
)


def _symbol(id_="a", status=Status.UNUSED, snippet="s"):
    return Symbol(
        id=id_, kind="view", label=id_, sub="", file="", line=None,
        status=status, snippet=snippet, chain=[], note="",
    )


def _entry(symbol, status=TriageStatus.APPROVED, fingerprint=None):
    return TriageEntry(
        symbol_id=symbol.id, fingerprint=fingerprint or fingerprint_for_symbol(symbol),
        status=status, who="alice", when="2026-08-29", reason="known false positive",
    )


class FingerprintTests(SimpleTestCase):
    def test_stable_for_the_same_evidence(self):
        self.assertEqual(fingerprint_for_symbol(_symbol()), fingerprint_for_symbol(_symbol()))

    def test_changes_when_the_snippet_changes(self):
        self.assertNotEqual(
            fingerprint_for_symbol(_symbol(snippet="a")), fingerprint_for_symbol(_symbol(snippet="b"))
        )

    def test_changes_when_the_status_changes(self):
        self.assertNotEqual(
            fingerprint_for_symbol(_symbol(status=Status.UNUSED)),
            fingerprint_for_symbol(_symbol(status=Status.UNRESOLVED)),
        )


class TriagePersistenceTests(SimpleTestCase):
    def test_save_then_load_round_trips(self):
        with tempfile.TemporaryDirectory() as tmp:
            save_triage([_entry(_symbol())], tmp)

            loaded = load_triage(tmp)

            self.assertEqual(len(loaded), 1)
            self.assertEqual(loaded[0].symbol_id, "a")
            self.assertEqual(loaded[0].status, TriageStatus.APPROVED)

    def test_missing_file_loads_as_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(load_triage(tmp), [])


class BlockingTests(SimpleTestCase):
    def test_an_untriaged_finding_blocks(self):
        graph = Graph(symbols=[_symbol()], edges=[])

        self.assertTrue(has_blocking_findings(graph, []))

    def test_an_approved_finding_does_not_block(self):
        symbol = _symbol()
        graph = Graph(symbols=[symbol], edges=[])

        self.assertFalse(has_blocking_findings(graph, [_entry(symbol)]))

    def test_a_confirmed_finding_still_blocks(self):
        symbol = _symbol()
        graph = Graph(symbols=[symbol], edges=[])

        self.assertTrue(has_blocking_findings(graph, [_entry(symbol, TriageStatus.CONFIRMED)]))

    def test_approval_expires_when_the_evidence_changes(self):
        # The whole point of fingerprinting: an approval describes evidence someone
        # looked at. Different evidence, same symbol id, is not the thing they approved.
        approved_earlier = _entry(_symbol(snippet="old code"))
        graph = Graph(symbols=[_symbol(snippet="new code")], edges=[])

        self.assertTrue(has_blocking_findings(graph, [approved_earlier]))

    def test_apply_triage_annotates_only_still_valid_entries(self):
        symbol = _symbol()
        graph = Graph(symbols=[symbol], edges=[])

        annotated = apply_triage(graph, [_entry(symbol)])
        stale = apply_triage(graph, [_entry(_symbol(snippet="other"))])

        self.assertIn("[triage:approved]", annotated.symbols[0].note)
        self.assertEqual(stale.symbols[0].note, "")
