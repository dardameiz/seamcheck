"""What changed between two scans, and which human dispositions that invalidated."""

from __future__ import annotations

from dataclasses import dataclass, field

from seamcheck.graph import Graph, Status, Symbol
from seamcheck.triage import fingerprint_for_symbol, stale_entries


@dataclass
class DiffResult:
    new_unresolved: list[Symbol]
    new_unused: list[Symbol]
    resolved: list[Symbol]
    triage_invalidated: list[dict]
    # Populated by the DOM Wiring plan, which owns the only extractor that produces the
    # kind. Declared here so that plan changes a body, not this shape.
    new_multi_writer: list[Symbol] = field(default_factory=list)


_BAD_STATUSES = frozenset({Status.UNRESOLVED, Status.UNUSED})


def diff_graphs(
    old: Graph,
    new: Graph,
    triage_entries: list | None = None,
) -> DiffResult:
    old_by_id = {symbol.id: symbol for symbol in old.symbols}
    new_by_id = {symbol.id: symbol for symbol in new.symbols}

    new_unresolved: list[Symbol] = []
    new_unused: list[Symbol] = []
    for symbol in new.symbols:
        if symbol.status not in _BAD_STATUSES:
            continue
        previous = old_by_id.get(symbol.id)
        if previous is not None and previous.status == symbol.status:
            continue
        (new_unresolved if symbol.status is Status.UNRESOLVED else new_unused).append(symbol)

    resolved = [
        symbol
        for symbol in new.symbols
        if symbol.status not in _BAD_STATUSES
        and (old_by_id.get(symbol.id) is not None)
        and old_by_id[symbol.id].status in _BAD_STATUSES
    ]

    # The predicate is triage.stale_entries' - the one that also decides what the report
    # raises as returned - so the diff and the report can never disagree about which
    # marks moved. The dict shape is the `check` JSON an agent reads.
    triage_invalidated = [
        {
            "symbol_id": entry.symbol_id,
            "stored_fingerprint": entry.fingerprint,
            "current_fingerprint": fingerprint_for_symbol(new_by_id[entry.symbol_id]),
            "note": (
                "The evidence behind this disposition changed, so the mark no longer "
                "applies. Re-triage rather than treating it as a brand-new finding."
            ),
        }
        for entry in stale_entries(new, triage_entries or [])
    ]

    return DiffResult(
        new_unresolved=new_unresolved,
        new_unused=new_unused,
        resolved=resolved,
        triage_invalidated=triage_invalidated,
    )
