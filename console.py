"""The console's information architecture: the sections a person browses.

The spec's eight pages, as data. Sections whose extractor does not exist yet are still
present and say so — a blind spot you can see is worth more than a nav item that quietly
went missing, which is the same reason Scan Coverage exists.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from signal_map.graph import Graph, Status, Symbol
from signal_map.report import Report

# Which side of the wire a kind lives on. The Overview leads with these two totals.
BACKEND_KINDS = ("url", "view", "model", "signal_receiver", "admin_action", "template_tag")
FRONTEND_KINDS = (
    "js_call", "fetch_target", "dom_selector", "dom_attr",
    "css_selector", "css_token_def", "css_token_use", "multi_writer_element",
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
            "django", "Django Internals",
            "URLs, views, models, and the entry points Django calls without an HTTP "
            "request: signals, admin actions, template tags.",
            rows=_rows(graph, ("url", "view", "model", "signal_receiver", "admin_action", "template_tag")),
        ),
        Section(
            "css", "CSS & Tokens",
            "Stylesheet rules and design tokens, against what actually references them.",
            rows=_rows(graph, ("css_selector", "css_token_def", "css_token_use"), findings_only=True),
        ),
        Section(
            "integrations", "Integrations",
            "Celery tasks, Redis keys, WebSocket handlers and Stripe hooks.",
            unavailable=(
                "No extractor traces these yet, so anything reached only through Celery, "
                "Redis, a WebSocket or Stripe is invisible to every other section too. "
                "Treat this as a known blind spot, not as an absence of problems."
            ),
        ),
        Section(
            "health", "File Health",
            "Per-file size and cohesion — files that have grown too large or do too many things.",
            unavailable="Not implemented yet. Nothing here is a judgement about your files.",
        ),
        Section(
            "findings", "Findings",
            "Everything the scan is willing to claim, grouped and worst first.",
            rows=_rows(graph, tuple(BACKEND_KINDS + FRONTEND_KINDS), findings_only=True),
        ),
    ]

    return Console(
        git_sha=report.git_sha,
        generated_at=report.generated_at,
        baseline_sha=report.baseline_sha,
        backend=_side_counts(graph, BACKEND_KINDS),
        frontend=_side_counts(graph, FRONTEND_KINDS),
        counts=report.counts,
        sections=sections,
        groups=[(g.title, len(g.symbols), g.kind) for g in report.groups],
    )
