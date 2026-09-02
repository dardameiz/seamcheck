"""The console's information architecture: the sections a person browses.

Only sections an extractor actually feeds. Two placeholders used to sit in this list -
Integrations and File Health - on the reasoning that a blind spot you can see beats one
that quietly went missing. In practice a nav item that opens onto "Not implemented yet"
reads as a broken tool, and a reader who clicks it twice stops trusting the ones that do
work.

The blind spot itself is not lost: it is stated on the opening panel, next to the counts
it distorts, which is where it changes how a number is read. See `BLIND_SPOTS` in
seamcheck.meaning - one sentence, one place, shown where it matters.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from seamcheck.graph import Graph, Status, Symbol
from seamcheck.report import Report

# Which side of the wire a kind lives on. The Overview leads with these two totals.
# The four regions the map draws, so the summary and the canvas use ONE vocabulary. These
# used to be two tuples covering 14 kinds, and everything else in the scan was counted
# nowhere at all: a Supabase project opened on "0% of 0 symbols" while its own backlog
# listed four findings underneath, because every db_* kind fell outside both sets. Any
# kind added to a band belongs here too.
BACKEND_KINDS = (
    "url", "view", "model", "signal_receiver", "admin_action", "template_tag",
    "url_reference", "management_command",
)
FRONTEND_KINDS = (
    "js_call", "fetch_target", "dom_selector", "dom_attr", "json_field",
    "css_selector", "css_token_def", "css_token_use", "multi_writer_element",
)
STORE_KINDS = (
    "db_table", "db_column", "db_function", "db_policy",
    "db_table_use", "db_column_use", "db_function_use",
    "redis_key", "redis_ttl", "firestore_rule", "firestore_collection",
    "cloud_function", "cloud_function_use",
    "storage_bucket", "edge_function", "edge_function_use",
)
OFFSCREEN_KINDS = (
    "celery_task", "celery_schedule", "job", "job_enqueue", "job_schedule",
    "stripe_webhook", "stripe_event", "graphql_field", "graphql_selection",
    "env_var", "env_read",
)


@dataclass
class Row:
    id: str
    label: str
    kind: str
    status: str
    file: str
    line: int | None
    note: str
    snippet: str


@dataclass
class Section:
    key: str
    title: str
    blurb: str
    rows: list[Row] = field(default_factory=list)
    # Set when no extractor feeds this section yet.
    unavailable: str = ""


@dataclass
class Console:
    git_sha: str
    generated_at: str
    baseline_sha: str | None
    backend: dict[str, int]
    frontend: dict[str, int]
    counts: dict[str, int]
    sections: list[Section]
    groups: list[tuple[str, int, str]]
    # Defaults, and last: a dataclass cannot take a defaulted field before a bare one.
    store: dict[str, int] = field(default_factory=dict)
    offscreen: dict[str, int] = field(default_factory=dict)


def _row(symbol: Symbol) -> Row:
    return Row(
        id=symbol.id, label=symbol.label or "/", kind=symbol.kind, status=symbol.status.value,
        file=symbol.file, line=symbol.line, note=symbol.note, snippet=symbol.snippet,
    )


def _rows(graph: Graph, kinds: tuple[str, ...], *, findings_only: bool = False) -> list[Row]:
    wanted = set(kinds)
    rows = [
        _row(symbol) for symbol in graph.symbols
        if symbol.kind in wanted
        and (not findings_only or symbol.status in (Status.UNRESOLVED, Status.UNUSED))
    ]
    # Worst first, then by location, so the top of a long list is the part worth reading.
    severity = {"unresolved": 0, "unused": 1, "uncertain": 2, "connected": 3}
    rows.sort(key=lambda r: (severity.get(r.status, 4), r.file or "", r.line or -1, r.label))
    return rows


# Kinds only Django produces. Their presence is evidence of the framework, which is a
# better answer than a hardcoded name now that five backends can fill this section - a
# FastAPI project being told about its "Django Internals" is the tool not reading its own
# output.
_DJANGO_ONLY = frozenset({"admin_action", "signal_receiver", "template_tag", "management_command"})


def _backend_title(graph) -> str:
    kinds = {symbol.kind for symbol in graph.symbols}
    return "Django Internals" if kinds & _DJANGO_ONLY else "Backend Internals"


def _side_counts(graph: Graph, kinds: tuple[str, ...]) -> dict[str, int]:
    wanted = set(kinds)
    counts = {status.value: 0 for status in Status}
    for symbol in graph.symbols:
        if symbol.kind in wanted:
            counts[symbol.status.value] += 1
    return counts


def build_console(graph: Graph, report: Report) -> Console:
    changed_rows = [
        _row(symbol) for symbol in report.new_findings + report.resolved
    ]

    sections = [
        Section(
            "changes", "Changes",
            "What moved since the baseline snapshot. Empty until a baseline exists.",
            rows=changed_rows,
        ),
        Section(
            "boundary", "Frontend ↔ Backend",
            "Every fetch() the frontend makes, the endpoint it resolves to, and the "
            "response fields the consumer reads.",
            rows=_rows(graph, ("fetch_target", "js_call", "json_field")),
        ),
        Section(
            "dom", "DOM Wiring",
            "Template elements, the selectors JavaScript uses against them, and elements "
            "more than one file writes.",
            rows=_rows(graph, ("multi_writer_element", "dom_selector", "dom_attr"), findings_only=True),
        ),
        Section(
            "backend", _backend_title(graph),
            "Routes, handlers, models, and the entry points the framework calls without "
            "an HTTP request: signals, admin actions, template tags.",
            rows=_rows(graph, ("url", "view", "model", "signal_receiver", "admin_action", "template_tag")),
        ),
        Section(
            "css", "CSS & Tokens",
            "Stylesheet rules and design tokens, against what actually references them.",
            rows=_rows(graph, ("css_selector", "css_token_def", "css_token_use"), findings_only=True),
        ),
        Section(
            "integrations", "Integrations",
            "Services this project talks to, and the names it talks to them by. None of "
            "these are reached by an HTTP request from the browser, so nothing else in "
            "this report can see them.",
            rows=_rows(graph, ("stripe_webhook", "stripe_event", "celery_task",
                               "celery_schedule", "graphql_field", "graphql_selection")),
        ),
        Section(
            "findings", "Findings",
            "Everything the scan is willing to claim, grouped and worst first.",
            rows=_rows(graph, tuple(BACKEND_KINDS + FRONTEND_KINDS
                                    + STORE_KINDS + OFFSCREEN_KINDS), findings_only=True),
        ),
    ]

    return Console(
        git_sha=report.git_sha,
        generated_at=report.generated_at,
        baseline_sha=report.baseline_sha,
        backend=_side_counts(graph, BACKEND_KINDS),
        frontend=_side_counts(graph, FRONTEND_KINDS),
        store=_side_counts(graph, STORE_KINDS),
        offscreen=_side_counts(graph, OFFSCREEN_KINDS),
        counts=report.counts,
        sections=sections,
        groups=[(g.title, len(g.symbols), g.kind) for g in report.groups],
    )
