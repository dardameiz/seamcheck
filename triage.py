"""Human dispositions on findings, invalidated the moment the evidence changes."""

from __future__ import annotations

import dataclasses
import json
import pathlib
from dataclasses import dataclass
from enum import Enum

from signal_map.graph import Graph, Status, Symbol

_TRIAGE_FILE = pathlib.Path("signal_map") / "triage.json"

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


def _valid_entries(graph: Graph, entries: list[TriageEntry]) -> dict[str, TriageEntry]:
    """Entries whose stored fingerprint still matches the symbol as it is now."""
    by_id = {symbol.id: symbol for symbol in graph.symbols}
    valid = {}
    for entry in entries:
        symbol = by_id.get(entry.symbol_id)
        if symbol and fingerprint_for_symbol(symbol) == entry.fingerprint:
            valid[entry.symbol_id] = entry
    return valid


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
