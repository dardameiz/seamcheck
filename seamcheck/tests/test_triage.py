import json
import pathlib
import tempfile

from django.test import SimpleTestCase

from seamcheck.graph import Graph, Status, Symbol
from seamcheck.triage import (
    TriageEntry,
    TriageStatus,
    apply_triage,
    fingerprint_for_symbol,
    has_blocking_findings,
    load_triage,
    note_expired,
    remove_mark,
    returned,
    save_triage,
    stale_entries,
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

    def test_the_expiry_stamp_round_trips_and_an_older_file_loads_without_one(self):
        # triage.json files written before the stamp existed have no `expired`; they
        # must load as "never noticed to change", not fail.
        symbol = _symbol()
        entry = _entry(symbol)
        entry.expired = "2026-09-01"
        with tempfile.TemporaryDirectory() as tmp:
            save_triage([entry], tmp)
            self.assertEqual(load_triage(tmp)[0].expired, "2026-09-01")

            path = pathlib.Path(tmp) / "seamcheck" / "triage.json"
            data = json.loads(path.read_text())
            for item in data["entries"]:
                item.pop("expired", None)
            path.write_text(json.dumps(data))
            self.assertEqual(load_triage(tmp)[0].expired, "")


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

    def test_a_later_stale_entry_does_not_win_over_an_earlier_valid_one(self):
        # A stale disposition silently winning would let it decide whether
        # has_blocking_findings() -- api.check()["passed"], the CI gate -- passes.
        # CONFIRMED always blocks, so if the stale entry wins here this goes red.
        symbol = _symbol()
        graph = Graph(symbols=[symbol], edges=[])
        valid = _entry(symbol, status=TriageStatus.APPROVED)
        stale = _entry(symbol, status=TriageStatus.CONFIRMED, fingerprint="stale")

        self.assertFalse(has_blocking_findings(graph, [valid, stale]))

        annotated = apply_triage(graph, [valid, stale])
        self.assertIn("[triage:approved]", annotated.symbols[0].note)

    def test_an_earlier_stale_entry_does_not_win_over_a_later_valid_one(self):
        # Same assertion, reversed order -- list position must not decide the winner.
        symbol = _symbol()
        graph = Graph(symbols=[symbol], edges=[])
        valid = _entry(symbol, status=TriageStatus.APPROVED)
        stale = _entry(symbol, status=TriageStatus.CONFIRMED, fingerprint="stale")

        self.assertFalse(has_blocking_findings(graph, [stale, valid]))

        annotated = apply_triage(graph, [stale, valid])
        self.assertIn("[triage:approved]", annotated.symbols[0].note)


class ReturnedTests(SimpleTestCase):
    """A mark the code moved out from under is kept, stamped, and raised as returned."""

    def test_a_mark_is_stale_when_the_symbol_is_here_and_the_evidence_differs(self):
        symbol = _symbol(snippet="new")
        graph = Graph(symbols=[symbol], edges=[])
        stale = _entry(_symbol(snippet="old"))
        gone = _entry(_symbol(id_="elsewhere"))

        self.assertEqual(stale_entries(graph, [_entry(symbol), stale, gone]), [stale])

    def test_the_expiry_is_stamped_once_by_whichever_scan_first_notices(self):
        stale = _entry(_symbol(snippet="old"))

        changed = note_expired([stale], [stale], "2026-09-01")
        again = note_expired([stale], [stale], "2026-09-08")

        self.assertTrue(changed)
        self.assertFalse(again)
        self.assertEqual(stale.expired, "2026-09-01")

    def test_a_stale_mark_on_a_finding_is_returned(self):
        symbol = _symbol(snippet="new", status=Status.UNRESOLVED)
        graph = Graph(symbols=[symbol], edges=[])
        stale = _entry(_symbol(snippet="old"))

        back = returned(graph, [stale])

        self.assertEqual([(s.id, e) for s, e in back], [("a", stale)])

    def test_a_stale_mark_on_a_now_connected_symbol_is_not_returned(self):
        # The finding is gone; the mark just outlived it. Nothing to look at again.
        graph = Graph(symbols=[_symbol(snippet="new", status=Status.CONNECTED)], edges=[])

        self.assertEqual(returned(graph, [_entry(_symbol(snippet="old"))]), [])

    def test_a_later_valid_mark_settles_an_earlier_stale_one(self):
        # Re-marking IS the answer to "returned"; the old entry must not keep raising it.
        symbol = _symbol()
        graph = Graph(symbols=[symbol], edges=[])
        stale = _entry(_symbol(snippet="old"))
        fresh = _entry(symbol)

        self.assertEqual(returned(graph, [stale, fresh]), [])
        self.assertEqual(returned(graph, [fresh, stale]), [])

    def test_the_latest_stale_entry_is_the_one_raised(self):
        symbol = _symbol(snippet="v3")
        graph = Graph(symbols=[symbol], edges=[])
        first = _entry(_symbol(snippet="v1"))
        second = _entry(_symbol(snippet="v2"), status=TriageStatus.CONFIRMED)

        self.assertEqual(returned(graph, [first, second]), [(symbol, second)])

    def test_undo_removes_every_entry_on_the_symbol_and_says_how_many(self):
        keep = _entry(_symbol(id_="other"))
        entries = [_entry(_symbol()), _entry(_symbol(snippet="older")), keep]

        left, removed = remove_mark(entries, "a")

        self.assertEqual(left, [keep])
        self.assertEqual(removed, 2)
        self.assertEqual(remove_mark([keep], "a"), ([keep], 0))
