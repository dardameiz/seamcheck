"""What to show, and in what order. Renderers decide only how it looks."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from signal_map.coverage import CoverageResult
from signal_map.diff import DiffResult
from signal_map.graph import Graph, Status, Symbol
from signal_map.triage import TriageEntry, fingerprint_for_symbol

# Only these two statuses are findings. CONNECTED needs no action, and UNCERTAIN is the
# scan saying it has no evidence either way - presenting it as actionable would undo the
# guardrail the whole pipeline is built on.
_FINDING_STATUSES = frozenset({Status.UNRESOLVED, Status.UNUSED})

_GROUP_TITLES = {
    "multi_writer_element": "Multi-writer elements",
    "css_token_def": "Unused design tokens",
    "css_token_use": "Undefined token references",
    "css_selector": "Unreferenced CSS selectors",
    "dom_attr": "Template elements nothing reaches",
    "dom_selector": "Selectors with no matching element",
    "json_field": "Response fields",
    "fetch_target": "Fetch targets",
    "js_call": "JavaScript calls",
    "url": "URLs",
    "view": "Views",
}


@dataclass
class ReportGroup:
    kind: str
    status: Status
    title: str
    symbols: list[Symbol]
    triaged: int


@dataclass
class Report:
    git_sha: str
    generated_at: str
    baseline_sha: str | None
    baseline_message: str
    new_findings: list[Symbol]
    resolved: list[Symbol]
    triage_invalidated: list[dict]
    groups: list[ReportGroup]
    counts: dict[str, int]
    coverage: CoverageResult | None = field(default=None)


def _severity(symbol: Symbol) -> int:
    if symbol.kind == "multi_writer_element":
        return 0
    return 1 if symbol.status is Status.UNRESOLVED else 2


def _sort_key(symbol: Symbol) -> tuple:
    return (_severity(symbol), symbol.file or "", symbol.line if symbol.line is not None else -1)


def _title_for(kind: str) -> str:
    return _GROUP_TITLES.get(kind, kind.replace("_", " ").capitalize())


def _valid_triaged_ids(graph: Graph, entries: list[TriageEntry]) -> set[str]:
    by_id = {symbol.id: symbol for symbol in graph.symbols}
    return {
        entry.symbol_id
        for entry in entries
        if entry.symbol_id in by_id
        and fingerprint_for_symbol(by_id[entry.symbol_id]) == entry.fingerprint
    }


def build_report(
    graph: Graph,
    diff: DiffResult | None,
    entries: list[TriageEntry],
    git_sha: str,
    baseline_sha: str | None = None,
    baseline_message: str = "",
    coverage: CoverageResult | None = None,
    now: str | None = None,
) -> Report:
    new_findings: list[Symbol] = []
    resolved: list[Symbol] = []
    invalidated: list[dict] = []
    if diff is not None:
        new_findings = sorted(
            diff.new_multi_writer + diff.new_unresolved + diff.new_unused, key=_sort_key
        )
        resolved = list(diff.resolved)
        invalidated = list(diff.triage_invalidated)

    already_reported = {symbol.id for symbol in new_findings}
    triaged_ids = _valid_triaged_ids(graph, entries)

    by_kind: dict[str, list[Symbol]] = {}
    counts: dict[str, int] = {status.value: 0 for status in Status}
    for symbol in graph.symbols:
        counts[symbol.status.value] += 1
        if symbol.status in _FINDING_STATUSES and symbol.id not in already_reported:
            by_kind.setdefault(symbol.kind, []).append(symbol)

    groups = [
        ReportGroup(
            kind=kind,
            status=symbols[0].status,
            title=_title_for(kind),
            symbols=sorted(symbols, key=_sort_key),
            triaged=sum(1 for symbol in symbols if symbol.id in triaged_ids),
        )
        for kind, symbols in by_kind.items()
    ]
    groups.sort(key=lambda group: (-len(group.symbols), group.kind))

    return Report(
        git_sha=git_sha,
        generated_at=now or dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        baseline_sha=baseline_sha,
        baseline_message=baseline_message,
        new_findings=new_findings,
        resolved=resolved,
        triage_invalidated=invalidated,
        groups=groups,
        counts=counts,
        coverage=coverage,
    )
