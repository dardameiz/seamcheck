"""What to show, and in what order. Renderers decide only how it looks."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from signal_map.coverage import CoverageResult
from signal_map.diff import DiffResult
from signal_map.graph import Graph, Status, Symbol
from signal_map.triage import TriageEntry, valid_triage_ids

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


def _group_status(symbols: list[Symbol]) -> Status:
    """The worst status present, not whichever symbol happens to sort first.

    A group can mix UNRESOLVED and UNUSED symbols under one kind (json_field is the real
    example: field_matcher.py emits both into the same bucket). Showing UNUSED on a group
    header when an UNRESOLVED symbol is inside it hides the worse problem -- the same
    class of mistake as calling something unused without evidence.
    """
    return Status.UNRESOLVED if any(s.status is Status.UNRESOLVED for s in symbols) else Status.UNUSED


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
    triaged_ids = valid_triage_ids(graph, entries)

    by_kind: dict[str, list[Symbol]] = {}
    counts: dict[str, int] = {status.value: 0 for status in Status}
    for symbol in graph.symbols:
        counts[symbol.status.value] += 1
        if symbol.status in _FINDING_STATUSES and symbol.id not in already_reported:
            by_kind.setdefault(symbol.kind, []).append(symbol)

    groups = []
    for kind, symbols in by_kind.items():
        sorted_symbols = sorted(symbols, key=_sort_key)
        groups.append(
            ReportGroup(
                kind=kind,
                status=_group_status(sorted_symbols),
                title=_title_for(kind),
                symbols=sorted_symbols,
                triaged=sum(1 for symbol in sorted_symbols if symbol.id in triaged_ids),
            )
        )
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
