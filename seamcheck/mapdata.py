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

from seamcheck.graph import Graph, Status, Symbol
from seamcheck.pagenames import PageName

# Kinds worth carrying source context for. Every kind would cost roughly a megabyte, and
# most of it would be a DOM selector whose one line already says everything it does.
_CONTEXT_KINDS = frozenset({"module", "js_call", "fetch_target", "url", "view", "json_field"})
_CONTEXT_LINES = 3
_source_cache: dict[str, list[str]] = {}


_MAX_BLOCK = 60
_OPENERS = ("def ", "async def ", "class ", "function ", "export function ",
            "export default function ", "export async function ")


def _block_bounds(lines: list[str], index: int, path: str) -> tuple[int, int]:
    """The enclosing block around `index`, as (start, end) line offsets.

    Three lines either side showed a call without showing what it belongs to. Python is
    bounded by indentation, JavaScript and CSS by their braces; anything unrecognised
    falls back to the window. Capped, because one function in this project is 900 lines.
    """
    if path.endswith(".py"):
        for start in range(index, -1, -1):
            text = lines[start].lstrip()
            if text.startswith(("def ", "async def ", "class ")):
                indent = len(lines[start]) - len(text)
                for end in range(start + 1, min(len(lines), start + _MAX_BLOCK)):
                    body = lines[end]
                    if body.strip() and (len(body) - len(body.lstrip())) <= indent:
                        return start, end
                return start, min(len(lines), start + _MAX_BLOCK)
            if text and not lines[start].startswith((" ", "\t", "#", "@")):
                break
    elif path.endswith((".js", ".mjs", ".css")):
        # Walk back counting braces until one is left unclosed - that line opened the block
        # this line sits in. Matching on `function` or `{` instead picked the options
        # object on the fetch() line itself, which opens and closes in the same breath.
        depth = 0
        for start in range(index, max(-1, index - 160), -1):
            depth += lines[start].count("}") - lines[start].count("{")
            if depth < 0:
                forward = 0
                for end in range(start, min(len(lines), start + _MAX_BLOCK)):
                    forward += lines[end].count("{") - lines[end].count("}")
                    if forward <= 0 and end > start:
                        return start, end + 1
                return start, min(len(lines), start + _MAX_BLOCK)
    return -1, -1


def _context(path: str | None, line: int | None) -> str:
    """The block `line` sits in, numbered, or the lines around it as a fallback."""
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
    if not lines or line > len(lines):
        return ""
    start, end = _block_bounds(lines, line - 1, path)
    if start < 0:
        start = max(0, line - 1 - _CONTEXT_LINES)
        end = min(len(lines), line + _CONTEXT_LINES)
    return "\n".join(f"{n + 1:5d}  {lines[n][:160]}" for n in range(start, min(end, len(lines))))


# Symbols that belong to a JS module and start a chain outward.
_SEED_KINDS = frozenset({"js_call", "fetch_target", "dom_selector", "multi_writer_element"})
# A class application is evidence that a rule is live, not a thing to navigate to.
# Seeding from all 5,355 of them produced 82,000 nodes - as unreadable as the raw graph.
_SEED_EXCLUDED_SUBS = ("class:apply",)
# Only these expand further. Stopping elsewhere keeps DOM and CSS as leaves: walking
# INTO a css_selector pulls in every template element sharing that class, which is how
# one page reached 13,458 nodes.
_EXPANDABLE_KINDS = frozenset({"js_call", "fetch_target", "url", "view",
                               # A handler's store call is a hop, not a leaf:
                               # stopping here drew the server talking to
                               # nothing.
                               "redis_key_use", "db_table_use"})
# How far to follow edges out of a seed. Three hops reaches fetch -> url -> view -> field,
# which is the whole frontend-to-backend story; further out is noise.
# Four, not three. fetch -> url -> view was the whole story until a handler could reach
# its store; now the fourth hop is the store itself, which is the point of the second seam.
_MAX_HOPS = 4


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
    # What language this node IS, and which deployable it belongs to. Read from the file
    # rather than from the service, because they disagree and the file is the truth: a
    # Django service happily contains TypeScript, and labelling that node "Python"
    # because of the directory it sits in is the mistake this is meant to prevent.
    lang: str = ""
    service: str = ""
    # The function this line sits in. A card that names the variable and the file but not
    # the function is missing the one thing the person reading it already knows.
    owner: str = ""


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


# Extension -> the name a person would use for it. Deliberately the language, not the
# file type: a reader scanning a map for "where is the TypeScript" is asking about a
# language boundary, and `.tsx` and `.ts` are the same side of it.
_LANGS = {
    ".py": "Python", ".pyi": "Python",
    ".ts": "TypeScript", ".tsx": "TypeScript", ".mts": "TypeScript", ".cts": "TypeScript",
    ".js": "JavaScript", ".jsx": "JavaScript", ".mjs": "JavaScript", ".cjs": "JavaScript",
    ".vue": "Vue", ".svelte": "Svelte",
    ".css": "CSS", ".scss": "Sass", ".sass": "Sass", ".less": "Less",
    ".html": "Template", ".htm": "Template", ".jinja": "Template", ".jinja2": "Template",
    ".j2": "Template", ".twig": "Template", ".erb": "Template",
    ".sql": "SQL", ".go": "Go", ".rs": "Rust", ".rb": "Ruby", ".php": "PHP",
    ".java": "Java", ".kt": "Kotlin", ".cs": "C#",
}


def language_of(path: str) -> str:
    """The language of a FILE. Empty when it is not a language this names."""
    if not path:
        return ""
    return _LANGS.get(os.path.splitext(path)[1].lower(), "")


def _node(symbol: Symbol, context: bool = True, snippet_limit: int = 400,
          services=None) -> MapNode:
    # The site root is path("", index): a genuinely empty label that must still be
    # nameable on screen.
    label = symbol.label if (symbol.label or "").strip() else "/"
    return MapNode(
        id=symbol.id, label=label, kind=symbol.kind, status=symbol.status.value,
        snippet=(symbol.snippet or "")[:snippet_limit],
        context=_context(symbol.file, symbol.line)
                if context and symbol.kind in _CONTEXT_KINDS else "",
        file=symbol.file, line=symbol.line, note=symbol.note, owner=symbol.owner,
        lang=language_of(symbol.file),
        service=services.of(symbol.file) if services and symbol.file else "",
    )


def _page_node_id(page: str) -> str:
    return f"page:{page}"


def _module_node_id(path: str) -> str:
    return f"module:{path}"


def build_page_map(page: str, files: set[str], graph: Graph, adjacency: dict[str, list], services=None) -> PageMap:
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
        nodes[module_id] = MapNode(module_id, os.path.basename(path), "module", "connected",
                                  file=path, lang=language_of(path),
                                  service=services.of(path) if services else "")
        _add_edge(_page_node_id(page), module_id, Status.CONNECTED.value)
        for symbol in seeds:
            nodes[symbol.id] = _node(symbol, services=services)
            _add_edge(module_id, symbol.id, symbol.status.value)
            frontier.append((symbol.id, symbol))

    # A multi-writer element is the one finding whose SHAPE is the defect: N files
    # writing one thing. Seeded like everything else it gets a single edge, from the file
    # the sample happened to come from - so the map drew a two-node chain and hid the
    # convergence, while the panel beside it listed four writers. Isolating the finding
    # then showed one of them, which is the least useful subset there is.
    #
    # The other writers are already known, as basenames on `chain`. Resolving them to the
    # modules on this page and drawing an edge from each is what makes the fan-in visible,
    # and it needs no new analysis - `chainOf` walks every ancestor already, so lighting
    # the finding now lights all of its writers at once.
    by_basename: dict[str, str] = {}
    for path in files:
        by_basename.setdefault(os.path.basename(path), path)
    for seeds in seeds_by_module.values():
        for symbol in seeds:
            if symbol.kind != "multi_writer_element":
                continue
            for name in symbol.chain:
                writer = by_basename.get(name)
                if not writer or writer == symbol.file:
                    continue
                writer_id = _module_node_id(writer)
                if writer_id not in nodes:
                    nodes[writer_id] = MapNode(
                        writer_id, os.path.basename(writer), "module", "connected",
                        file=writer, lang=language_of(writer),
                        service=services.of(writer) if services else "")
                    _add_edge(_page_node_id(page), writer_id, Status.CONNECTED.value)
                _add_edge(writer_id, symbol.id, symbol.status.value)

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
                    nodes[neighbour_id] = _node(neighbour, services=services)
                    if neighbour.kind in _EXPANDABLE_KINDS:
                        next_frontier.append((neighbour_id, neighbour))
        frontier = next_frontier

    return PageMap(page=page, nodes=list(nodes.values()), edges=edges)


# Symbols no page entry reaches, grouped so each bucket is navigable on its own. 90% of a
# real scan lands here - 32,301 of 35,767 on the project this was built against - and the
# map used to simply not contain them: clicking `models.py` in the Files view drew an
# empty canvas, which is indistinguishable from "this file is dead".
#
# Being unreached is not itself a finding. A model, a signal receiver or an admin action
# is reached by Django, never by a browser page; a route may be hit by a webhook or typed
# in. What the bucket answers is "where did the rest go", and each symbol keeps its own
# status so the reds inside it are still the reds.
UNREACHED_PAGE = "unreached"
UNREACHED_GROUPS: tuple[tuple[str, str, frozenset[str]], ...] = (
    ("backend", "Reached by the framework, never by a browser page — routes, handlers, "
                "models, signal receivers",
     frozenset({"url", "view", "model", "admin_action", "signal_receiver",
                "template_tag", "management_command"})),
    ("js", "JavaScript that no page's bundle imports",
     frozenset({"js_call", "fetch_target", "dom_selector", "multi_writer_element", "module"})),
    ("css", "Stylesheet rules and design tokens that nothing on a page matched",
     frozenset({"css_selector", "css_token_def", "css_token_use"})),
    ("template", "Template elements that no page's JavaScript selects",
     frozenset({"dom_attr"})),
)
UNREACHED_OTHER = ("other", "Everything else the scan found off the page graph")


def _unreached_group(kind: str) -> tuple[str, str]:
    for key, blurb, kinds in UNREACHED_GROUPS:
        if kind in kinds:
            return key, blurb
    return UNREACHED_OTHER


def build_unreached_pages(graph: Graph, covered: set[str], services=None) -> list[PageMap]:
    """One page per family of symbols that no page entry reaches."""
    buckets: dict[str, list[Symbol]] = {}
    blurbs: dict[str, str] = {}
    for symbol in graph.symbols:
        if symbol.id in covered:
            continue
        key, blurb = _unreached_group(symbol.kind)
        buckets.setdefault(key, []).append(symbol)
        blurbs[key] = blurb

    order = [key for key, _, _ in UNREACHED_GROUPS] + [UNREACHED_OTHER[0]]
    pages = []
    for key in order:
        symbols = buckets.get(key)
        if not symbols:
            continue
        ids = {symbol.id for symbol in symbols}
        # No source context and a short snippet: these buckets are large by nature, they
        # sit on no chain (there is no path to read them along), and the full text of
        # 32,301 source lines is megabytes nobody opens. The label, the file and the line
        # are what a reader needs here, and file:line opens the real thing.
        nodes = [_node(symbol, context=False, snippet_limit=120, services=services)
                 for symbol in symbols]
        edges = [
            MapEdge(edge.from_id, edge.to_id, edge.status.value)
            for edge in graph.edges
            if edge.from_id in ids and edge.to_id in ids
        ]
        pages.append(PageMap(
            page=f"{UNREACHED_PAGE}:{key}", nodes=nodes, edges=edges,
            title="Not reached from any page",
            where=f"{blurbs[key]} — {len(nodes):,}",
        ))
    return pages


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
    services=None,
) -> ConnectivityMap:
    adjacency = build_adjacency(graph)
    page_maps = []
    for page, files in sorted(pages.items()):
        page_map = build_page_map(page, files, graph, adjacency, services=services)
        name = (names or {}).get(page)
        page_map.title = name.title if name else page
        page_map.where = name.where if name else ""
        page_maps.append(page_map)
    page_maps = [page_map for page_map in page_maps if len(page_map.nodes) > 1]
    # Grouped by the page a reader recognises, then by bundle inside it, so the sidebar
    # reads as a site rather than as a build manifest.
    page_maps.sort(key=lambda page_map: (page_map.title.lower(), page_map.page))
    # ...and last, everything the page graph does not reach, so "where did the other 90%
    # go" has an answer on the same picker rather than being absent from the map.
    covered = {node.id for page_map in page_maps for node in page_map.nodes}
    page_maps += build_unreached_pages(graph, covered, services=services)

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
