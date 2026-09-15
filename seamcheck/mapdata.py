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


# Symbols that belong to a module and start a chain outward: the calls and queries a page
# makes, the elements it selects - every "touch". A page that talks through a data client
# or a <Link> held none of the first four, so it had nothing to draw and was dropped.
_SEED_KINDS = frozenset({
    "js_call", "fetch_target", "dom_selector", "multi_writer_element",
    "db_table_use", "db_column_use", "db_function_use", "url_reference", "env_read",
    "stripe_event", "stripe_webhook", "storage_bucket", "firestore_collection",
    "graphql_selection", "job_enqueue", "redis_key_use", "edge_function_use", "dom_attr",
})
# Pages, then server entries, then scripts no framework claims: a reader looks for the
# page first.
_KIND_ORDER = {"page": 0, "server": 1, "static_page": 2, "entry_file": 3}
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
    # Entries sharing a group are sections of one page; see map_html._grouped.
    group: str = ""
    kind: str = "page"
    # Symbols in the files this entry reaches, drawn or not - what an empty canvas says
    # instead of saying nothing.
    reached: int = 0


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
    # Which function calls which, by owner name. Not part of the graph: no symbol, no
    # status, no finding depends on it - it exists so the map can follow a delegating
    # handler into the helpers that do its work.
    calls: dict[str, list[str]] = field(default_factory=dict)
    # Every function the scan saw defined, and the file it is written in - including the
    # ones that own no symbol at all, which are precisely the helpers a reader looks up.
    defined: dict[str, str] = field(default_factory=dict)


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


# A server entry IS its routes and handlers, and neither is a touch: nothing selects or
# calls them. Seeded by touches alone, a handler that queried no store and read no env var
# drew only itself, and its routes went to the bucket for what a page's files hold - on a
# FastAPI project with no page at all.
_SERVER_SEED_KINDS = _SEED_KINDS | {"url", "view"}


def build_page_map(page: str, files: set[str], graph: Graph, adjacency: dict[str, list], services=None,
                   seed_kinds: frozenset[str] = _SEED_KINDS) -> PageMap:
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
            symbol.kind in seed_kinds
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
# Symbols in files a page reaches that nothing on it draws: class applications, rules in
# an imported stylesheet, declarations. They are on a page, so they are not unreached;
# they start no chain, so no canvas shows them; and the map's search is built from what
# pages hold, so without a bucket of their own they could be found nowhere.
UNDRAWN_PAGE = "undrawn"
BUCKET_PREFIXES = (f"{UNREACHED_PAGE}:", f"{UNDRAWN_PAGE}:")
_GROUP_KINDS: tuple[tuple[str, frozenset[str]], ...] = (
    ("backend", frozenset({"url", "view", "admin_action", "signal_receiver",
                           "template_tag", "management_command"})),
    ("js", frozenset({"js_call", "fetch_target", "dom_selector", "multi_writer_element", "module"})),
    ("css", frozenset({"css_selector", "css_token_def", "css_token_use"})),
    ("template", frozenset({"dom_attr"})),
)
UNREACHED_OTHER = ("other", "Everything else the scan found off the page graph")

# What each backend calls the things a browser never reaches, in its own vocabulary: a
# message phrased in one framework's words was shown to people using another.
_BACKEND_WORDS = {
    "django": "URLs, views, admin actions, signal receivers",
    "nextjs": "route handlers and pages no entry reaches",
    "express": "routes and middleware",
    "fastapi": "routes and their handlers",
}
_BACKEND_DEFAULT = "routes, handlers, models, signal receivers"
_TEMPLATE_BACKENDS = frozenset({"django", "flask"})


def unreached_groups(detected: frozenset[str] = frozenset(),
                     script_symbols=()) -> tuple[tuple[str, str, frozenset[str]], ...]:
    """The not-reached buckets as (key, blurb, kinds), worded from what the scan found."""
    words = "; ".join(_BACKEND_WORDS[name] for name in sorted(detected) if name in _BACKEND_WORDS)
    languages = sorted({language_of(s.file) for s in script_symbols} & {"TypeScript", "JavaScript"},
                       reverse=True)
    markup = "Template elements" if detected & _TEMPLATE_BACKENDS else "HTML elements"
    blurbs = {
        "backend": f"Reached by the framework, never by a browser page — {words or _BACKEND_DEFAULT}",
        "js": f"{' and '.join(languages) or 'JavaScript'} no entry imports",
        "css": "Stylesheet rules and design tokens that nothing on a page matched",
        "template": f"{markup} that no page's JavaScript selects",
    }
    return tuple((key, blurbs[key], kinds) for key, kinds in _GROUP_KINDS)


def _unreached_key(kind: str) -> str:
    for key, kinds in _GROUP_KINDS:
        if kind in kinds:
            return key
    return UNREACHED_OTHER[0]


def _bucket_page(page: str, title: str, where: str, symbols: list[Symbol], graph: Graph,
                 services=None) -> PageMap:
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
    # No count in `where`: the picker appends one, and both showed ("— 3 — 3 nodes").
    return PageMap(page=page, nodes=nodes, edges=edges, title=title, where=where)


def build_unreached_pages(graph: Graph, covered: set[str], services=None,
                          detected_backends: frozenset[str] = frozenset()) -> list[PageMap]:
    """One page per family of symbols that no page entry reaches."""
    buckets: dict[str, list[Symbol]] = {}
    for symbol in graph.symbols:
        if symbol.id not in covered:
            buckets.setdefault(_unreached_key(symbol.kind), []).append(symbol)
    groups = unreached_groups(detected_backends, buckets.get("js", ()))
    blurbs = {key: blurb for key, blurb, _ in groups}
    blurbs[UNREACHED_OTHER[0]] = UNREACHED_OTHER[1]
    order = [key for key, _, _ in groups] + [UNREACHED_OTHER[0]]
    return [
        _bucket_page(f"{UNREACHED_PAGE}:{key}", "Not reached from any page", blurbs[key],
                     buckets[key], graph, services)
        for key in order if buckets.get(key)
    ]


def build_undrawn_page(graph: Graph, drawn: set[str], reached_files: set[str],
                       services=None) -> PageMap | None:
    """The bucket for symbols a page's files hold that no page draws, or None."""
    symbols = [s for s in graph.symbols if s.file in reached_files and s.id not in drawn]
    if not symbols:
        return None
    return _bucket_page(f"{UNDRAWN_PAGE}:onpage", "On a page, nothing to draw",
                        "In files a page reaches, but not a request, a query or an element "
                        "it selects, so nothing starts a chain from them",
                        symbols, graph, services)


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
    calls: dict[str, list[str]] | None = None,
    defined: dict[str, str] | None = None,
    detected_backends: frozenset[str] = frozenset(),
) -> ConnectivityMap:
    adjacency = build_adjacency(graph)
    per_file: dict[str, int] = {}
    for symbol in graph.symbols:
        if symbol.file:
            per_file[symbol.file] = per_file.get(symbol.file, 0) + 1
    page_maps = []
    for page, files in sorted(pages.items()):
        name = (names or {}).get(page)
        seeds = _SERVER_SEED_KINDS if name and name.kind == "server" else _SEED_KINDS
        page_map = build_page_map(page, files, graph, adjacency, services=services, seed_kinds=seeds)
        page_map.title = name.title if name else page
        page_map.where = name.where if name else ""
        page_map.group = name.group if name else ""
        page_map.kind = name.kind if name else "page"
        page_map.reached = sum(per_file.get(path, 0) for path in files)
        if name:
            # nodes[0] is the page itself (build_page_map makes it first). `note` travels
            # in the detail chunk, so the sheet says why this is an entry.
            page_map.nodes[0].note = " · ".join(part for part in (name.evidence, name.note) if part)
            # "next:/pricing" is an identifier, and the card showed it verbatim. The id keeps
            # the key; what a reader sees is the entry's label.
            if name.label:
                page_map.nodes[0].label = name.label
        page_maps.append(page_map)
    # No page is dropped for having nothing to draw: dropping them is how every Next.js
    # page vanished. Pages, then server entries; inside each, by the name a reader
    # recognises, then by bundle, so the sidebar reads as a site rather than a manifest.
    page_maps.sort(key=lambda page_map: (_KIND_ORDER.get(page_map.kind, 9),
                                         page_map.title.lower(), page_map.page))
    # ...then what the pages hold but do not draw, and last what no page reaches, so
    # "where did the rest go" has an answer on the same picker. A symbol is on a page when
    # an entry reaches its FILE, drawn or not.
    drawn = {node.id for page_map in page_maps for node in page_map.nodes}
    reached_files = set().union(*pages.values()) if pages else set()
    undrawn = build_undrawn_page(graph, drawn, reached_files, services=services)
    if undrawn is not None:
        page_maps.append(undrawn)
    covered = drawn | {symbol.id for symbol in graph.symbols if symbol.file in reached_files}
    page_maps += build_unreached_pages(graph, covered, services=services,
                                       detected_backends=detected_backends)

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
        calls=calls or {},
        defined=defined or {},
    )
