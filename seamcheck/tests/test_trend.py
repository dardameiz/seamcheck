"""The series across scans, which is the question a single before-and-after cannot answer.

`Changes` compares one scan against one baseline: "what moved since that commit". The
question a codebase actually raises is "is this getting better", and that needs a series.
Two rules keep it honest, and both are tested here: a commit appears once no matter how
often it is re-scanned, and a row is never rewritten to look better than it was.
"""

from __future__ import annotations

import json
import tempfile
import unittest

from seamcheck.graph import Graph, Status, Symbol
from seamcheck.trend import Entry, load, path, record, summarise, trend


def _graph(unused=0, unresolved=0, connected=1):
    symbols = []
    for i in range(unused):
        symbols.append(Symbol(id=f"css:{i}", kind="css_selector", label=f"c{i}", sub="",
                              file="a.css", line=1, status=Status.UNUSED, snippet="",
                              chain=[], note=""))
    for i in range(unresolved):
        symbols.append(Symbol(id=f"fetch:{i}", kind="fetch_target", label=f"/api/{i}", sub="",
                              file="a.js", line=1, status=Status.UNRESOLVED, snippet="",
                              chain=[], note=""))
    for i in range(connected):
        symbols.append(Symbol(id=f"url:{i}", kind="url", label=f"/u{i}", sub="",
                              file="u.py", line=1, status=Status.CONNECTED, snippet="",
                              chain=[], note=""))
    return Graph(symbols=symbols, edges=[])


class Summarising(unittest.TestCase):
    def test_findings_are_the_two_actionable_statuses(self):
        entry = summarise(_graph(unused=3, unresolved=2, connected=5), "a" * 40)
        self.assertEqual(entry.findings, 5, "uncertain and connected are not findings")
        self.assertEqual(entry.symbols, 10)

    def test_kinds_are_tracked_for_findings_only(self):
        entry = summarise(_graph(unused=3, unresolved=2, connected=5), "a" * 40)
        self.assertEqual(entry.by_kind.get("css_selector"), 3)
        self.assertEqual(entry.by_kind.get("fetch_target"), 2)
        self.assertIsNone(entry.by_kind.get("url"), "connected routes are not findings")

    def test_status_counts_cover_everything(self):
        entry = summarise(_graph(unused=3, unresolved=2, connected=5), "a" * 40)
        self.assertEqual(sum(entry.by_status.values()), 10)


class Recording(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()

    def test_a_scan_is_appended(self):
        record(_graph(unused=4), "a" * 40, self.root)
        record(_graph(unused=2), "b" * 40, self.root)
        self.assertEqual([e.findings for e in load(self.root)], [4, 2])

    def test_rescanning_a_commit_replaces_its_row(self):
        """A rebuild must not look like progress."""
        record(_graph(unused=4), "a" * 40, self.root)
        record(_graph(unused=4), "a" * 40, self.root)
        self.assertEqual(len(load(self.root)), 1)

    def test_a_rescan_keeps_the_commit_in_place(self):
        record(_graph(unused=9), "a" * 40, self.root)
        record(_graph(unused=5), "b" * 40, self.root)
        record(_graph(unused=8), "a" * 40, self.root)
        entries = load(self.root)
        self.assertEqual([e.sha[:1] for e in entries], ["b", "a"])
        self.assertEqual(entries[-1].findings, 8, "the newer reading of that commit wins")

    def test_a_corrupt_line_does_not_cost_the_series(self):
        record(_graph(unused=4), "a" * 40, self.root)
        with path(self.root).open("a", encoding="utf-8") as handle:
            handle.write("{not json at all\n")
        record(_graph(unused=1), "b" * 40, self.root)
        self.assertEqual([e.findings for e in load(self.root)], [4, 1])

    def test_no_file_is_an_empty_series_not_an_error(self):
        self.assertEqual(load(tempfile.mkdtemp()), [])

    def test_rows_are_one_json_object_per_line(self):
        record(_graph(unused=4), "a" * 40, self.root)
        record(_graph(unused=2), "b" * 40, self.root)
        lines = path(self.root).read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(len(lines), 2)
        for line in lines:
            json.loads(line)


class Trend(unittest.TestCase):
    def _entries(self, *counts):
        return [
            Entry(sha=str(i) * 40, at=f"2026-0{i + 1}-01T00:00:00", symbols=100,
                  findings=c, by_status={"unused": c}, by_kind={"css_selector": c})
            for i, c in enumerate(counts)
        ]

    def test_delta_is_negative_when_findings_fall(self):
        self.assertEqual(trend(self._entries(40, 30, 12))["delta"], -28)

    def test_delta_is_positive_when_they_rise(self):
        """A series that can only go down is a series nobody should believe."""
        self.assertEqual(trend(self._entries(10, 25))["delta"], 15)

    def test_movers_name_the_kind_and_both_ends(self):
        mover = trend(self._entries(40, 12))["movers"][0]
        self.assertEqual(mover["kind"], "css_selector")
        self.assertEqual((mover["from"], mover["to"], mover["change"]), (40, 12, -28))

    def test_an_empty_series_is_safe(self):
        self.assertEqual(trend([])["span"], 0)

    def test_a_single_scan_has_no_delta(self):
        self.assertEqual(trend(self._entries(7))["delta"], 0)


if __name__ == "__main__":
    unittest.main()
