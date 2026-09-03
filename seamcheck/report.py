"""What to show, and in what order. Renderers decide only how it looks."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from seamcheck.coverage import CoverageResult
from seamcheck.diff import DiffResult
from seamcheck.graph import Graph, Status, Symbol
from seamcheck.triage import TriageEntry, returned, valid_triage_entries, valid_triage_ids

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
    # The data layer. Named for what a reader is looking at rather than for the symbol
    # kind: "Db table use" is an enum with a space in it, not a heading.
    "db_table": "Tables nothing reads",
    "db_table_use": "Tables the code reads that the schema has not got",
    "db_column": "Columns no select() names",
    "db_column_use": "Columns the code selects that the table has not got",
    "db_function": "Database functions nothing calls",
    "db_function_use": "rpc() calls with no such function",
    "db_policy": "Row level security",
    "edge_function": "Edge functions nothing invokes",
    "edge_function_use": "Edge functions that do not exist",
    "storage_bucket": "Storage buckets",
    "cloud_function": "Cloud functions nothing calls",
    "cloud_function_use": "Callables with no such function",
    "firestore_collection": "Collections and the rules that cover them",
    "firestore_rule": "Rules guarding nothing",
    "redis_key": "Redis keys",
    "redis_ttl": "Cache keys written with no expiry",
    # Background work, outside Celery. Named for the consequence, not the library.
    "job": "Background jobs",
    "job_enqueue": "Work queued for a job that does not exist",
    "job_schedule": "Schedules",
    "env_var": "Configuration keys",
    "env_read": "Configuration the code reads",
}

# A caveat for a kind with a known recall gap in the extractor that produces it - shown
# next to the group title in every renderer so a reader does not take the finding at
# face value. Only kinds with a genuine, logged gap get one; do not use this to soften
# a group that is simply large.
_GROUP_CAVEATS = {
    "css_selector": (
        "JavaScript that applies classes via className, classList.add, or setAttribute "
        "is not yet scanned, so many of these are false positives."
    ),
}


@dataclass
class ReportGroup:
    kind: str
    status: Status
    title: str
    symbols: list[Symbol]
    triaged: int
    caveat: str = ""


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
    # Findings that came back: marked fine once, and the evidence has since changed.
    # Independent of the baseline - a mark is its own baseline - so a report with no
    # snapshot to diff against still says so. Each is a `mark_dict()`.
    returned: list[dict] = field(default_factory=list)
    # Every mark whose symbol is in this scan, keyed by symbol id: the ones that hold
    # and the ones that came back, so a card can say which it is wearing.
    marks: dict[str, dict] = field(default_factory=dict)


def _severity(symbol: Symbol) -> int:
    if symbol.kind == "multi_writer_element":
        return 0
    return 1 if symbol.status is Status.UNRESOLVED else 2


def _sort_key(symbol: Symbol) -> tuple:
    return (_severity(symbol), symbol.file or "", symbol.line if symbol.line is not None else -1)


def _title_for(kind: str) -> str:
    return _GROUP_TITLES.get(kind, kind.replace("_", " ").capitalize())


def _dedupe_by_id(symbols: list[Symbol]) -> list[Symbol]:
    """First occurrence wins, order otherwise preserved.

    new_multi_writer, new_unresolved, and new_unused are concatenated below with no
    guarantee they are disjoint - a multi-writer symbol carries Status.UNRESOLVED, so it
    already arrives via new_unresolved too, and the day new_multi_writer is actually
    populated (it isn't yet - see diff.py) every such symbol would otherwise be listed
    twice, breaking "a finding appears exactly once".
    """
    seen: set[str] = set()
    deduped: list[Symbol] = []
    for symbol in symbols:
        if symbol.id in seen:
            continue
        seen.add(symbol.id)
        deduped.append(symbol)
    return deduped


def _group_status(symbols: list[Symbol]) -> Status:
    """The worst status present, not whichever symbol happens to sort first.

    A group can mix UNRESOLVED and UNUSED symbols under one kind (json_field is the real
    example: field_matcher.py emits both into the same bucket). Showing UNUSED on a group
    header when an UNRESOLVED symbol is inside it hides the worse problem -- the same
    class of mistake as calling something unused without evidence.
    """
    return Status.UNRESOLVED if any(s.status is Status.UNRESOLVED for s in symbols) else Status.UNUSED


def mark_dict(symbol: Symbol, entry: TriageEntry, came_back: bool) -> dict:
    """One mark as the renderers see it: what was said, by whom, when - and whether the
    code has since moved out from under it."""
    return {
        "symbol_id": symbol.id, "label": symbol.label, "kind": symbol.kind,
        "status": symbol.status.value, "file": symbol.file, "line": symbol.line,
        "marked": entry.status.value, "why": entry.why, "when": entry.when,
        "who": entry.who, "reason": entry.reason, "expired": entry.expired,
        "returned": came_back,
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
        combined = diff.new_multi_writer + diff.new_unresolved + diff.new_unused
        new_findings = sorted(_dedupe_by_id(combined), key=_sort_key)
        resolved = list(diff.resolved)
        invalidated = list(diff.triage_invalidated)

    already_reported = {symbol.id for symbol in new_findings}
    triaged_ids = valid_triage_ids(graph, entries)
    by_id = {symbol.id: symbol for symbol in graph.symbols}
    # Later valid entry per id wins, the same rule triage.py applies everywhere.
    marks = {
        entry.symbol_id: mark_dict(by_id[entry.symbol_id], entry, False)
        for entry in valid_triage_entries(graph, entries)
    }
    came_back = sorted(returned(graph, entries), key=lambda pair: _sort_key(pair[0]))
    for symbol, entry in came_back:
        marks[symbol.id] = mark_dict(symbol, entry, True)

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
                caveat=_GROUP_CAVEATS.get(kind, ""),
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
        returned=[marks[symbol.id] for symbol, _ in came_back],
        marks=marks,
    )
