"""The drill-down shape behind the visual map: page -> module -> symbol -> what it reaches.

A scan of this project produces 36k symbols. Drawing them at once is not a rendering
problem to solve with a better layout - it is unreadable at any layout. So the map is
rooted at the ~20 pages a person actually thinks in, and every level below one is small
enough to read: a page has tens of modules, a module has a handful of calls, a call
resolves to one endpoint.
"""

from __future__ import annotations

import datetime as dt
import os
import pathlib
from dataclasses import dataclass, field

from signal_map.graph import Graph, Status, Symbol
from signal_map.pagenames import PageName

# Kinds worth carrying source context for. Every kind would cost roughly a megabyte, and
# most of it would be a DOM selector whose one line already says everything it does.
_CONTEXT_KINDS = frozenset({"module", "js_call", "fetch_target", "url", "view", "json_field"})
_CONTEXT_LINES = 3
_source_cache: dict[str, list[str]] = {}


def _context(path: str | None, line: int | None) -> str:
    """The lines around `line`, numbered, or nothing if the file cannot be read."""
    if not path or not line:
        return ""
    if path not in _source_cache:
        try:
            _source_cache[path] = pathlib.Path(path).read_text(
                encoding="utf-8", errors="replace"
            ).splitlines()
        except OSError:
            _source_cache[path] = []
    lines = _source_cache[path]
    if not lines:
        return ""
    start = max(0, line - 1 - _CONTEXT_LINES)
    end = min(len(lines), line + _CONTEXT_LINES)
    return "\n".join(f"{n + 1:5d}  {lines[n][:160]}" for n in range(start, end))


# Symbols that belong to a JS module and start a chain outward.
_SEED_KINDS = frozenset({"js_call", "fetch_target", "dom_selector", "multi_writer_element"})
# A class application is evidence that a rule is live, not a thing to navigate to.
# Seeding from all 5,355 of them produced 82,000 nodes - as unreadable as the raw graph.
_SEED_EXCLUDED_SUBS = ("class:apply",)
# Only these expand further. Stopping elsewhere keeps DOM and CSS as leaves: walking
# INTO a css_selector pulls in every template element sharing that class, which is how
# one page reached 13,458 nodes.
_EXPANDABLE_KINDS = frozenset({"js_call", "fetch_target", "url", "view"})
# How far to follow edges out of a seed. Three hops reaches fetch -> url -> view -> field,
# which is the whole frontend-to-backend story; further out is noise.
_MAX_HOPS = 3


@dataclass
class MapNode:
    id: str
    label: str
    kind: str
    status: str
    file: str = ""
    line: int | None = None
    note: str = ""
    # The line of source the symbol was read from. Truncated: this ships to a browser
    # once per node, and the map already carries thousands of them.
    snippet: str = ""
    # A few lines around it, for the kinds that carry the frontend-to-backend story. One
    # line tells you a call happened; the lines around it are where you learn how.
    context: str = ""


@dataclass
class MapEdge:
    source: str
    target: str
    status: str


@dataclass
class PageMap:
    page: str
    nodes: list[MapNode]
    edges: list[MapEdge]
    # What to call this on screen. Defaults to the bundle's own filename, so a map built
    # without name resolution still renders rather than showing blanks.
    title: str = ""
    where: str = ""


@dataclass
class ConnectivityMap:
    git_sha: str
    generated_at: str
    pages: list[PageMap]
    baseline_sha: str | None = None
    # One entry per scanned commit, newest first, each with its own changed set, so a
    # reader can ask what a single commit did rather than what the branch did.
    commits: list[dict] = field(default_factory=list)
    # node id -> "added" | "removed" | "status", populated only in diff mode.
    changed: dict[str, str] = field(default_factory=dict)


def _node(symbol: Symbol) -> MapNode:
    # The site root is path("", index): a genuinely empty label that must still be
    # nameable on screen.
    label = symbol.label if (symbol.label or "").strip() else "/"
    return MapNode(
        id=symbol.id, label=label, kind=symbol.kind, status=symbol.status.value,
        snippet=(symbol.snippet or "")[:400],
        context=_context(symbol.file, symbol.line) if symbol.kind in _CONTEXT_KINDS else "",
        file=symbol.file, line=symbol.line, note=symbol.note,
    )


def _page_node_id(page: str) -> str:
    return f"page:{page}"


def _module_node_id(path: str) -> str:
    return f"module:{path}"


def build_page_map(page: str, files: set[str], graph: Graph, adjacency: dict[str, list]) -> PageMap:
    by_id = {symbol.id: symbol for symbol in graph.symbols}
    nodes: dict[str, MapNode] = {_page_node_id(page): MapNode(_page_node_id(page), page, "page", "connected")}
    edges: list[MapEdge] = []
    seen_edges: set[tuple[str, str]] = set()

    def _add_edge(source: str, target: str, status: str) -> None:
        if (source, target) in seen_edges:
            return
        seen_edges.add((source, target))
        edges.append(MapEdge(source, target, status))

    seeds_by_module: dict[str, list[Symbol]] = {}
    for symbol in graph.symbols:
        if (
            symbol.kind in _SEED_KINDS
            and symbol.file in files
            and not symbol.sub.startswith(_SEED_EXCLUDED_SUBS)
        ):
            seeds_by_module.setdefault(symbol.file, []).append(symbol)

    frontier: list[tuple[str, Symbol]] = []
    for path, seeds in sorted(seeds_by_module.items()):
        module_id = _module_node_id(path)
        nodes[module_id] = MapNode(module_id, os.path.basename(path), "module", "connected", file=path)
        _add_edge(_page_node_id(page), module_id, Status.CONNECTED.value)
        for symbol in seeds:
            nodes[symbol.id] = _node(symbol)
            _add_edge(module_id, symbol.id, symbol.status.value)
            frontier.append((symbol.id, symbol))

    # Walk outward from every seed: fetch -> url -> view -> response field.
    for _ in range(_MAX_HOPS):
        next_frontier: list[tuple[str, Symbol]] = []
        # Seeds always get their one hop out; only expandable kinds re-enter the
        # frontier, so nothing further is needed here.
        for symbol_id, _symbol in frontier:
            for neighbour_id, status in adjacency.get(symbol_id, []):
                if neighbour_id == symbol_id:
                    continue
                neighbour = by_id.get(neighbour_id)
                if neighbour is None:
                    continue
                _add_edge(symbol_id, neighbour_id, status)
                if neighbour_id not in nodes:
                    nodes[neighbour_id] = _node(neighbour)
                    if neighbour.kind in _EXPANDABLE_KINDS:
                        next_frontier.append((neighbour_id, neighbour))
        frontier = next_frontier

    return PageMap(page=page, nodes=list(nodes.values()), edges=edges)


def build_adjacency(graph: Graph) -> dict[str, list]:
    adjacency: dict[str, list] = {}
    for edge in graph.edges:
        adjacency.setdefault(edge.from_id, []).append((edge.to_id, edge.status.value))
        adjacency.setdefault(edge.to_id, []).append((edge.from_id, edge.status.value))
    return adjacency


def build_map(
    graph: Graph,
    pages: dict[str, set[str]],
    git_sha: str,
    baseline: Graph | None = None,
    baseline_sha: str | None = None,
    now: str | None = None,
    names: dict[str, PageName] | None = None,
    commits: list[dict] | None = None,
) -> ConnectivityMap:
    adjacency = build_adjacency(graph)
    page_maps = []
    for page, files in sorted(pages.items()):
        page_map = build_page_map(page, files, graph, adjacency)
        name = (names or {}).get(page)
        page_map.title = name.title if name else page
        page_map.where = name.where if name else ""
        page_maps.append(page_map)
    page_maps = [page_map for page_map in page_maps if len(page_map.nodes) > 1]
    # Grouped by the page a reader recognises, then by bundle inside it, so the sidebar
    # reads as a site rather than as a build manifest.
    page_maps.sort(key=lambda page_map: (page_map.title.lower(), page_map.page))

    changed: dict[str, str] = {}
    if baseline is not None:
        before = {symbol.id: symbol.status for symbol in baseline.symbols}
        after = {symbol.id: symbol.status for symbol in graph.symbols}
        for symbol_id, status in after.items():
            if symbol_id not in before:
                changed[symbol_id] = "added"
            elif before[symbol_id] is not status:
                changed[symbol_id] = "status"
        for symbol_id in before.keys() - after.keys():
            changed[symbol_id] = "removed"

    return ConnectivityMap(
        git_sha=git_sha,
        generated_at=now or dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        pages=page_maps,
        baseline_sha=baseline_sha,
        commits=commits or [],
        changed=changed,
    )
