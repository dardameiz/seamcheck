"""Human dispositions on findings, invalidated the moment the evidence changes."""

from __future__ import annotations

import dataclasses
import json
import pathlib
from dataclasses import dataclass
from enum import Enum

from seamcheck.graph import Graph, Status, Symbol

_TRIAGE_FILE = pathlib.Path("seamcheck") / "triage.json"

# Statuses a human can act on. APPROVED is the only one that silences a finding; a
# CONFIRMED finding is a real bug someone has acknowledged, and must keep blocking.
_BLOCKING_STATUSES = frozenset({Status.UNRESOLVED, Status.UNUSED})


class TriageStatus(str, Enum):
    UNTRIAGED = "untriaged"
    APPROVED = "approved"
    CONFIRMED = "confirmed"
    DEFERRED = "deferred"


@dataclass
class TriageEntry:
    symbol_id: str
    fingerprint: str
    status: TriageStatus
    who: str
    when: str
    reason: str


def fingerprint_for_symbol(symbol: Symbol) -> str:
    """Content hash of the evidence a disposition was made against.

    Keyed to evidence, never to a symbol id or line number: if the snippet or the status
    changes, the mark someone made no longer describes what is on screen and must expire.
    """
    if symbol.kind == "multi_writer_element":
        # The finding IS the set of writers, so the disposition must expire when that
        # set changes -- a third writer appearing is a new problem, not the old one.
        return f"multi_writer_element:{symbol.label}:{'|'.join(sorted(symbol.chain))}"
    return f"{symbol.kind}:{symbol.snippet}:{symbol.status.value}"


def load_triage(repo_root: str) -> list[TriageEntry]:
    path = pathlib.Path(repo_root) / _TRIAGE_FILE
    if not path.is_file():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return [
        TriageEntry(**{**entry, "status": TriageStatus(entry["status"])})
        for entry in data.get("entries", [])
    ]


def save_triage(entries: list[TriageEntry], repo_root: str) -> None:
    path = pathlib.Path(repo_root) / _TRIAGE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "entries": [
            {**dataclasses.asdict(entry), "status": entry.status.value} for entry in entries
        ]
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def valid_triage_entries(graph: Graph, entries: list[TriageEntry]) -> list[TriageEntry]:
    """Entries whose stored fingerprint still matches the symbol as it is now, in order.

    The one place the fingerprint-validity predicate lives -- valid_triage_ids() and
    _valid_entries() below, plus report.py, all derive from this list instead of
    re-deriving the predicate, so "still valid" can't silently drift between callers.
    Filtering entry-by-entry (not id-by-id) matters: triage.json is a checked-in file a
    human can hand-edit, so nothing here may assume at most one entry per symbol_id --
    a set of "ids with SOME valid entry" would let a stale entry for an id ride along
    just because another entry for that same id happened to validate.
    """
    by_id = {symbol.id: symbol for symbol in graph.symbols}
    return [
        entry
        for entry in entries
        if entry.symbol_id in by_id
        and fingerprint_for_symbol(by_id[entry.symbol_id]) == entry.fingerprint
    ]


def valid_triage_ids(graph: Graph, entries: list[TriageEntry]) -> set[str]:
    """Symbol ids carrying a still-valid triage entry."""
    return {entry.symbol_id for entry in valid_triage_entries(graph, entries)}


def _valid_entries(graph: Graph, entries: list[TriageEntry]) -> dict[str, TriageEntry]:
    """Entries whose stored fingerprint still matches the symbol as it is now.

    Built only from already-valid entries, so when more than one entry names the same
    symbol_id, the later VALID one wins -- never a later entry that merely shares an id
    with a valid one, which would let a stale disposition decide has_blocking_findings().
    """
    return {entry.symbol_id: entry for entry in valid_triage_entries(graph, entries)}


def apply_triage(graph: Graph, entries: list[TriageEntry]) -> Graph:
    valid = _valid_entries(graph, entries)
    symbols = [
        dataclasses.replace(symbol, note=f"[triage:{valid[symbol.id].status.value}] {symbol.note}".strip())
        if symbol.id in valid
        else symbol
        for symbol in graph.symbols
    ]
    return Graph(symbols=symbols, edges=graph.edges, schema_version=graph.schema_version)


def has_blocking_findings(graph: Graph, entries: list[TriageEntry]) -> bool:
    valid = _valid_entries(graph, entries)
    for symbol in graph.symbols:
        if symbol.status not in _BLOCKING_STATUSES:
            continue
        entry = valid.get(symbol.id)
        if entry is None or entry.status is TriageStatus.CONFIRMED:
            return True
    return False
