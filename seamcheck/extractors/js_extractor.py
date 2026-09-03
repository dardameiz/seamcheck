"""HTTP calls made from JavaScript, via an acorn AST walk over the static import graph."""

from __future__ import annotations

import json
import os
import pathlib
import re
import shutil
import tempfile
from collections.abc import Iterator

from seamcheck.graph import Edge, Status, Symbol
from seamcheck.nodetools import node_line, parser_path, report, run_parser

_JS_TOOLS = os.path.join(os.path.dirname(__file__), os.pardir, "js_tools")
# .js first, deliberately: where a project ships `foo.js` beside `foo.ts` the .js is
# usually build output, but preferring it preserves the behaviour every existing scan
# already has. TypeScript and JSX reach acorn through sucrase in parse_js.mjs, which
# strips types and compiles JSX while preserving the line count exactly.
_JS_EXTENSIONS = (".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx", ".mts", ".cts")

# fetch() is the whole HTTP surface in most modern front ends, but sendBeacon() is a real
# request too: omitting it leaves its endpoint looking orphaned.
_FETCH_CALLEES = ("fetch",)
_BEACON_CALLEE = "sendBeacon"

# Everything else the ecosystem uses to make the same request. Reading only fetch() left
# 73% of all findings across a 32-repository corpus sitting at `uncertain` with the note
# "looked for a caller, found none" - while the caller's own name appeared in the source
# 86-98% of the time. The callers were never missing; this reader was.
#
# Bare-identifier callers: `$fetch('/api/x')` (Nuxt), `useSWR('/api/x')`, `request('/x')`.
_PLAIN_HTTP_CALLEES = frozenset({
    "$fetch", "useSWR", "useSWRMutation", "ofetch", "request", "superagent",
})
# Method-style callers: `axios.get(...)`, `this.http.post(...)`, `api.delete(...)`.
# `.get` and `.delete` are also Map, URLSearchParams, cache and store methods, which is
# why a receiver name alone is never enough - see _http_call_target for the guard that
# makes this safe.
_HTTP_METHODS = frozenset({"get", "post", "put", "patch", "delete", "head", "options"})
_HTTP_RECEIVERS = frozenset({
    "axios", "api", "http", "https", "client", "apiClient", "httpClient", "instance",
    "ky", "got", "request", "req", "agent", "fetcher", "$api", "$http", "service",
    "apiService", "httpService", "restClient", "backend",
})

_DYNAMIC_NOTE = "Fetch target built at runtime -- cannot be statically resolved."
_EXTERNAL_NOTE = (
    "A request to another origin, so there is no route in this project for it to reach. "
    "Recorded because it is a real outbound dependency; never checked against the route "
    "table, because it was never going to be there."
)


def _is_external(target: str) -> bool:
    """Whether this URL belongs to somebody else.

    A protocol-relative `//cdn.example.com/x` counts too: it is the same request to the
    same third party, written without the scheme.
    """
    return target.startswith(("http://", "https://", "//", "data:", "blob:", "mailto:", "tel:"))
_PREFIX_NOTE = (
    "Only the part of this URL before the first runtime value is known. The route it "
    "reaches is not proven -- never read this as evidence that an endpoint is unused."
)


def _static_url(node: dict | None) -> tuple[str | None, bool]:
    """(url, is_exact) for a fetch argument.

    Three shapes carry a usable URL and only the first was read, so 13 of this project's
    endpoints looked as if nothing called them:

      fetch("/api/x/")                 a Literal
      fetch(`/api/x/`)                 a TemplateLiteral with nothing interpolated - as
                                       static as a quoted string, and the style this
                                       codebase mostly uses
      fetch(`/api/x/?${q}`)            a prefix that is known, and a tail that is not
      fetch("/api/x/?t=" + Date.now()) the same thing written as concatenation

    The query string is dropped: it never selects a different route. A prefix that stops
    inside the path is returned inexact, so the caller can record the endpoint without
    claiming which route it hits.
    """
    if not node:
        return None, False
    kind = node.get("type")

    if kind == "Literal":
        value = node.get("value")
        return (value.split("?")[0], "?" not in value) if isinstance(value, str) else (None, False)

    if kind == "TemplateLiteral":
        quasis = node.get("quasis") or []
        head = ((quasis[0].get("value") or {}).get("cooked") or "") if quasis else ""
        interpolated = bool(node.get("expressions"))
        if not interpolated and len(quasis) == 1:
            return (head.split("?")[0], "?" not in head) if head else (None, False)
        # Interpolation inside the query string still leaves the route known.
        before_query = head.split("?")[0]
        return (before_query, "?" in head) if before_query else (None, False)

    if kind == "BinaryExpression" and node.get("operator") == "+":
        left, _ = _static_url(node.get("left"))
        if left:
            head = ((node.get("left") or {}).get("value") or "")
            return left, isinstance(head, str) and "?" in head
        return None, False

    return None, False

_FUNCTION_TYPES = ("FunctionDeclaration", "FunctionExpression", "ArrowFunctionExpression")


# path -> ((mtime_ns, size), ast). Eleven extractors and adapters each ask for the AST
# of the same JavaScript files - on the reference project the DOM extractor alone asked
# four times, and the map's page attribution once more per entry point - so a file was
# parsed by node and json.loads'd two hundred times per scan. Now once, validated by the
# file's stamp, held for the process up to a budget; clear_parse_cache() at the end of a
# report hands the memory back.
_AST_CACHE: dict[str, tuple[tuple[int, int], dict]] = {}
# The cache admits files until their parser output adds up to the budget, then stops
# admitting: every extractor after that re-parses the tail, but the memory stays put.
# Admit-until-full rather than evict-least-recent because every extractor reads the files
# in the same order - an LRU would throw out each file just before the next extractor asks
# for it and cache nothing at all. Measured: a tree costs ~5.8x its NDJSON bytes as Python
# dicts (pointlessbutton: 36 MB of parser output, 211 MB held), and a 21,000-file monorepo
# came to 3.4 GB of RSS with no bound, which is fine on a 48 GB workstation and fatal on an
# 8 GB CI box - so the default is a quarter of physical memory.
_AST_BYTES_PER_NDJSON_BYTE = 5.8
_ast_cached_bytes = 0
# Other per-scan memos that hang off the ASTs (an extractor's per-file result, the inline
# blocks) register here so one call empties them all.
_CACHES: list[dict] = [_AST_CACHE]


def _ast_budget() -> int:
    """Bytes of parser output the cache may hold. SEAMCHECK_AST_CACHE_MB is in RSS terms."""
    setting = os.environ.get("SEAMCHECK_AST_CACHE_MB", "")
    if setting.isdigit():
        rss = int(setting) * 1024 * 1024
    else:
        try:
            rss = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") // 4
        except (ValueError, OSError, AttributeError):
            rss = 2 * 1024 ** 3
    return int(rss / _AST_BYTES_PER_NDJSON_BYTE)


def register_cache(cache: dict) -> dict:
    _CACHES.append(cache)
    return cache


def clear_parse_cache() -> None:
    global _ast_cached_bytes
    for cache in _CACHES:
        cache.clear()
    _ast_cached_bytes = 0


def _stamp(path: str) -> tuple[int, int] | None:
    try:
        stat = os.stat(path)
    except OSError:
        return None
    return (stat.st_mtime_ns, stat.st_size)


def _parse_files(paths: list[str], *, report_failures: bool = True) -> dict[str, dict]:
    """Every tree at once. For a handful of files; a whole repository goes through
    iter_parsed so that only one tree at a time has to fit next to the cache."""
    return dict(iter_parsed(paths, report_failures=report_failures))


def iter_parsed(paths: list[str], *, report_failures: bool = True) -> Iterator[tuple[str, dict]]:
    """Yield (path, ast) for each path, cached ones first, the rest as the parser sends them.

    `report_failures=False` when the caller has better names: inline <script> blocks are
    written to a scratch directory as 0.js, 1.js, ... so the generic message named temp
    files and blamed TypeScript. The template is what a reader can actually open, so
    parse_inline_blocks reports its own.
    """
    global _ast_cached_bytes
    if not paths:
        return
    stamps = {path: _stamp(path) for path in dict.fromkeys(paths)}
    fresh = []
    for path, stamp in stamps.items():
        hit = _AST_CACHE.get(path)
        if hit is not None and stamp is not None and hit[0] == stamp:
            yield path, hit[1]
        else:
            fresh.append(path)
    if not fresh:
        return
    budget = _ast_budget()
    failed: list[str] = []
    for line in run_parser(parser_path(_JS_TOOLS, "parse_js"), fresh, "JavaScript"):
        record = json.loads(line)
        if "ast" in record:
            stamp = stamps.get(record["path"])
            if stamp is not None and _ast_cached_bytes + len(line) <= budget:
                _AST_CACHE[record["path"]] = (stamp, record["ast"])
                _ast_cached_bytes += len(line)
            yield record["path"], record["ast"]
        else:
            # A file the parser could not read used to vanish here without a word. Every
            # symbol in it then went missing from a scan that still reported success --
            # the exact shape of bug this tool exists to find.
            failed.append(record.get("path", "?"))
    if failed and report_failures:
        shown = ", ".join(os.path.basename(path) for path in failed[:3])
        report(
            "js-parse-failures",
            "%s JavaScript file(s) could not be parsed and contributed no symbols (%s%s). "
            "Anything they reference will look unused.",
            len(failed), shown, ", ..." if len(failed) > 3 else "",
        )


def _carrier_name(node: dict) -> str | None:
    """The name a function is *declared under*, which is where modern JS keeps it.

    An ArrowFunctionExpression has no `id` of its own -- `const f = () => {}` carries the
    name on the VariableDeclarator, and a class method carries it on the MethodDefinition.
    """
    node_type = node.get("type")
    if node_type in ("FunctionDeclaration", "FunctionExpression", "ClassDeclaration"):
        return (node.get("id") or {}).get("name")
    if node_type in ("MethodDefinition", "PropertyDefinition", "Property") and (
        (node.get("value") or {}).get("type") in _FUNCTION_TYPES or node_type != "Property"
    ):
        key = node.get("key") or {}
        # `key.value` is whatever the literal held. A webpack bundle is one big object
        # keyed by NUMERIC module ids - `{3726: function(){...}}` - so this returned an
        # int, which travelled all the way to relativise() and crashed the whole scan on
        # any repo that ships a bundled app.js. Coerced here, where the type is known.
        name = key.get("name")
        if name is None:
            name = key.get("value")
        return None if name is None else str(name)
    if node_type == "VariableDeclarator" and (node.get("init") or {}).get("type") in _FUNCTION_TYPES:
        return (node.get("id") or {}).get("name")
    return None


# Position metadata, never a node - and `loc` alone is three dicts per node. Nothing
# else is excluded by name: `value` carries a function on a Property, `name` a
# JSXIdentifier on a JSXAttribute.
_NOT_A_CHILD = frozenset(("loc", "range"))


def _walk(node, enclosing: str = ""):
    """Yield (node, enclosing_function_name) once per AST node, pre-order, in one pass.

    An explicit stack, not recursion: a recursive generator re-yields every node through
    every frame above it, so a node forty levels deep cost forty resumptions - 692 million
    frames for 24 million nodes on the reference project, half the scan.
    """
    stack = [(node, enclosing)]
    pop, push = stack.pop, stack.extend
    while stack:
        node, enclosing = pop()
        if isinstance(node, dict):
            if node.get("type"):
                enclosing = _carrier_name(node) or enclosing
                yield node, enclosing
            push(
                (value, enclosing)
                for key, value in reversed(list(node.items()))
                if key not in _NOT_A_CHILD and isinstance(value, (dict, list))
            )
        elif isinstance(node, list):
            push((item, enclosing) for item in reversed(node))


def _imported_paths(ast: dict) -> list[str]:
    return [
        node["source"]["value"]
        for node, _ in _walk(ast)
        if node["type"] == "ImportDeclaration" and node.get("source")
    ]


def _resolve_import(current_file: str, import_path: str) -> str | None:
    # Bare specifiers ('gsap') are node_modules, not first-party source.
    if not import_path.startswith("."):
        return None
    base = os.path.normpath(os.path.join(os.path.dirname(current_file), import_path))
    if os.path.isfile(base):
        return base
    for extension in _JS_EXTENSIONS:
        if os.path.isfile(base + extension):
            return base + extension
    for index_name in ("index.js", "index.mjs", "index.ts", "index.tsx", "index.jsx"):
        candidate = os.path.join(base, index_name)
        if os.path.isfile(candidate):
            return candidate
    return None


def _receiver_name(callee: dict) -> str:
    """The object a method is called on: `axios` in axios.get, `http` in this.http.get."""
    obj = callee.get("object") or {}
    if obj.get("type") == "Identifier":
        return obj.get("name") or ""
    # `this.http.get(...)` - the Angular and Nest idiom - is a MemberExpression whose own
    # object is ThisExpression, so the name sits one level further in.
    if obj.get("type") == "MemberExpression":
        return (obj.get("property") or {}).get("name") or ""
    return ""


def _looks_like_a_url(node: dict | None) -> bool:
    """Whether the first argument is a path, rather than a map key that happens to be one.

    THE guard that makes reading `.get()` safe at all. `params.get('q')`, `map.get(key)`
    and `cache.get(id)` are everywhere in real code, and every one of them fails here:
    the argument is either not a static string or does not start with a path separator.
    Requiring a path-shaped argument is a far stronger filter than any receiver allow-list,
    and it is the reason this can be turned on without drowning the reader.
    """
    target, _ = _static_url(node)
    return bool(target) and (target.startswith("/") or target.startswith("http"))


def _http_call_target(node: dict) -> tuple[bool, dict | None]:
    """(is_http_call, first_argument_node) for every way this codebase asks the network.

    fetch() and sendBeacon() are unconditional - their name says what they do. Everything
    else has to clear _looks_like_a_url first, because the method names HTTP clients chose
    are the same ones Map, URLSearchParams and every cache in the language chose.
    """
    if node.get("type") != "CallExpression":
        return False, None
    callee = node.get("callee") or {}
    arguments = node.get("arguments") or []
    first = arguments[0] if arguments else None

    if callee.get("type") == "Identifier":
        name = callee.get("name") or ""
        if name in _FETCH_CALLEES:
            return True, first
        if name in _PLAIN_HTTP_CALLEES and _looks_like_a_url(first):
            return True, first
        return False, None

    if callee.get("type") == "MemberExpression":
        prop = (callee.get("property") or {}).get("name") or ""
        if prop == _BEACON_CALLEE:
            return True, first
        # Both conditions, never either: a path-shaped argument alone would claim
        # `routes.get('/admin')` in a router DEFINITION as a call to itself.
        if (prop in _HTTP_METHODS and _receiver_name(callee) in _HTTP_RECEIVERS
                and _looks_like_a_url(first)):
            return True, first
        # axios({url: '/api/x'}) and ky.extend(...).get(...) - the config-object form.
        if prop in ("request",) and _looks_like_a_url(first):
            return True, first
    return False, None


def discover_js_files(entry_files: list[str], project_root: str) -> list[str]:
    """Every module reachable from the entries by static import.

    Shared so the DOM extractor sees the same file set: handing it only the entry
    points hides every write made by an imported module.
    """
    visited: set[str] = set()
    to_visit = [os.path.join(project_root, name) for name in entry_files]
    while to_visit:
        batch = [
            path
            for path in dict.fromkeys(to_visit)
            if path not in visited and path.endswith(_JS_EXTENSIONS) and os.path.isfile(path)
        ]
        to_visit = []
        if not batch:
            break
        visited.update(batch)
        for path, ast in iter_parsed(batch):
            for import_path in _imported_paths(ast):
                resolved = _resolve_import(path, import_path)
                if resolved and resolved not in visited:
                    to_visit.append(resolved)
    return sorted(visited)


# A path-shaped string: leading slash, no whitespace, at least one more segment.
# A path, and a path has a letter in it. `periodsTotalElement.textContent = '/24'` - the
# "/24" in "period 3/24" - matched this and was reported as an endpoint the frontend
# names, twice on the reference project. Nothing routes `/24`, and the digits give it
# away without needing to know what the line does with it.
_URL_LITERAL_RE = re.compile(r"\A/[\w\-./]*[\w\-]/?\Z")
_HAS_A_LETTER = re.compile(r"[A-Za-z]").search
# Where a string is going, when that settles what it is. Text written into an element is
# text; a path is never written to `textContent`.
_DISPLAY_TARGETS = frozenset({"textContent", "innerText", "innerHTML", "nodeValue",
                              "outerHTML", "placeholder", "title", "alt", "value"})
_LITERAL_NOTE = (
    "A URL-shaped string sits here, but the request is made somewhere else - through a "
    "variable, a helper, or a ternary. That this endpoint is called is not proven, and "
    "neither is the opposite: never read it as evidence either way."
)


def _url_literals(
    ast: dict, path: str, known: set[str], line_offset: int = 0
) -> tuple[list[Symbol], list[Edge]]:
    """Endpoints named by a literal that is not itself a fetch argument.

    `const ENDPOINT = '/api/x/'` and `_lobbyAction('/api/lobby/invite/', …)` are how 16 of
    this project's endpoints are called, and a walk that only reads fetch()'s own argument
    saw none of them. Following the value to the request needs data-flow analysis this
    scan does not do, so the string is recorded as a sighting - uncertain, with the line
    it was found on - and never as a proven call.
    """
    symbols: list[Symbol] = []
    edges: list[Edge] = []
    # Collected first, because `_walk` yields no parents: the strings this pass must not
    # read as endpoints are the ones being written into an element.
    display: set[int] = set()
    for node, _enclosing in _walk(ast):
        if node.get("type") != "AssignmentExpression":
            continue
        target = node.get("left") or {}
        if (target.get("property") or {}).get("name") in _DISPLAY_TARGETS:
            right = node.get("right")
            if isinstance(right, dict):
                display.add(id(right))
    for node, enclosing in _walk(ast):
        if node.get("type") != "Literal" or id(node) in display:
            continue
        value = node.get("value")
        if not isinstance(value, str) or not _URL_LITERAL_RE.match(value):
            continue
        if not _HAS_A_LETTER(value):
            continue
        target = value.split("?")[0]
        # `known` holds ids, not paths. Comparing the bare path against it never matched,
        # so every endpoint that a fetch had already claimed was recorded a second time
        # as a sighting - two symbols under one id, and two edges where the model allows
        # one.
        target_id = f"fetch:{target}"
        if target_id in known:
            continue
        known.add(target_id)
        line = node_line(node)
        line = (line + line_offset) if line else None
        basename = os.path.basename(path)
        symbols.append(
            Symbol(
                id=target_id, kind="fetch_target", label=target, sub="literal",
                file=path, line=line, status=Status.UNCERTAIN, snippet=f'"{target}"',
                chain=[basename, enclosing] if enclosing else [basename], note=_LITERAL_NOTE,
                owner=enclosing,
            )
        )
    return symbols, edges


def _http_symbols(
    ast: dict, path: str, seen_target_ids: set[str], line_offset: int = 0
) -> tuple[list[Symbol], list[Edge]]:
    """Every HTTP call in one parsed module. Shared by files and by a template's own
    <script> blocks, so an endpoint called from inline JavaScript is read the same way."""
    symbols: list[Symbol] = []
    edges: list[Edge] = []

    for node, enclosing in _walk(ast):
        is_http, first_argument = _http_call_target(node)
        if not is_http:
            continue

        line = node_line(node)
        line = (line + line_offset) if line else None
        basename = os.path.basename(path)
        chain = [basename, enclosing] if enclosing else [basename]
        call_id = f"jscall:{path}:{line}"

        target, exact = _static_url(first_argument)
        if target and _is_external(target):
            # A call to somebody else's API. It is a real call, so the js_call is recorded
            # - but it must never become a fetch_target, because a fetch_target is matched
            # against THIS project's route table and anything that fails to match there is
            # reported unresolved. That produced 47 findings on one project claiming that
            # `https://zoom.us/oauth/token` and `https://registry.npmjs.org/...` were
            # routes it had lost, which is not a thing anyone can act on and not a claim
            # the scan is entitled to make.
            symbols.append(
                Symbol(
                    id=call_id, kind="js_call", label=target, sub=basename, file=path,
                    line=line, status=Status.CONNECTED,
                    snippet=f'fetch("{target}")', chain=chain, note=_EXTERNAL_NOTE,
                    owner=enclosing,
                )
            )
            continue
        if target:
            status = Status.CONNECTED if exact else Status.UNCERTAIN
            snippet = f'fetch("{target}")' if exact else f'fetch("{target}" + <runtime value>)'
            symbols.append(
                Symbol(
                    id=call_id, kind="js_call", label=target, sub=basename, file=path,
                    line=line, status=status, snippet=snippet,
                    chain=chain, note="" if exact else _PREFIX_NOTE, owner=enclosing,
                )
            )
            target_id = f"fetch:{target}"
            if target_id not in seen_target_ids:
                seen_target_ids.add(target_id)
                symbols.append(
                    Symbol(
                        id=target_id, kind="fetch_target", label=target, sub="", file=path,
                        line=line, status=status, snippet=snippet,
                        chain=[target], note="" if exact else _PREFIX_NOTE, owner=enclosing,
                    )
                )
            edges.append(Edge(from_id=call_id, to_id=target_id, status=status))
        else:
            symbols.append(
                Symbol(
                    id=call_id, kind="js_call", label="fetch(<runtime value>)", sub=basename,
                    file=path, line=line, status=Status.UNCERTAIN,
                    snippet="fetch(<dynamic value>)", chain=chain, note=_DYNAMIC_NOTE,
                    owner=enclosing,
                )
            )
    return symbols, edges


def extract_js(
    entry_files: list[str], project_root: str, extra_files: list[str] | None = None
) -> tuple[list[Symbol], list[Edge]]:
    """Every HTTP call in the project, and the endpoint literals worth recording.

    `entry_files` and everything they import get the full treatment. `extra_files` - the
    rest of the first-party tree, which exists because a Next.js page is routed to by the
    filesystem and imported by nothing - is read for CALLS ONLY.

    That asymmetry is measured, not stylistic. A path-shaped literal inside the entry graph
    is plausibly an endpoint constant, which is why sightings are recorded at all. The same
    literal anywhere in a monorepo is a string: reading them across the whole tree took one
    project from 29% uncertain to 74% while adding eleven connected findings. Sightings are
    the cheapest symbol to produce and the least worth producing at scale.
    """
    symbols: list[Symbol] = []
    edges: list[Edge] = []
    seen_target_ids: set[str] = set()
    # Kept apart from seen_target_ids so literals still de-duplicate against each other
    # while never blocking a real call from claiming the same target.
    literal_ids: set[str] = set()
    pending_literals: list[Symbol] = []

    to_visit = [os.path.join(project_root, name) for name in entry_files]
    visited: set[str] = set()

    while to_visit:
        batch = [
            path
            for path in dict.fromkeys(to_visit)
            if path not in visited and path.endswith(_JS_EXTENSIONS) and os.path.isfile(path)
        ]
        to_visit = []
        if not batch:
            break
        visited.update(batch)

        for path, ast in iter_parsed(batch):
            for import_path in _imported_paths(ast):
                resolved = _resolve_import(path, import_path)
                if resolved and resolved not in visited:
                    to_visit.append(resolved)

            found, found_edges = _http_symbols(ast, path, seen_target_ids)
            symbols += found
            edges += found_edges
            # Literals are DEFERRED to the end of the walk. A bare `'/api/teams'` sitting
            # in a route definition is a sighting; an `axios.get('/api/teams')` in another
            # file is a proven call. Emitting them as each file was parsed meant whichever
            # file the walk happened to reach FIRST decided the status - so a route defined
            # before its caller was read stayed uncertain, and the caller's evidence was
            # thrown away. Order of traversal must never decide a verdict.
            sighted, _ = _url_literals(ast, path, literal_ids)
            pending_literals += sighted

    # The rest of the first-party tree: calls only, no sightings, and no import walk -
    # these files were found by walking the directory, so there is nothing left to follow.
    remaining = [
        path for path in dict.fromkeys(extra_files or [])
        if path not in visited and path.endswith(_JS_EXTENSIONS) and os.path.isfile(path)
    ]
    for path, ast in iter_parsed(remaining):
        found, found_edges = _http_symbols(ast, path, seen_target_ids)
        symbols += found
        edges += found_edges

    # Only the sightings no real call already accounted for.
    symbols += [s for s in pending_literals if s.id not in seen_target_ids]
    return symbols, edges


_INLINE_SCRIPT_RE = re.compile(
    r"<script(?![^>]*\bsrc=)([^>]*)>(.*?)</script>", re.S | re.I
)
_SCRIPT_TYPE_RE = re.compile(r"""\btype\s*=\s*["']?([^"'\s>]+)""", re.I)

# Commented-out code is not code, and prose about code is not code either. A template
# that explains why a tag must stay put -- "it MUST stay a plain <script src> at THIS
# position" -- was read as an inline script whose body was the rest of the paragraph,
# because the src-detecting lookahead wants `src=` and that sentence writes `src` alone.
_COMMENT_RES = (
    re.compile(r"\{%\s*comment\s*%\}.*?\{%\s*endcomment\s*%\}", re.S | re.I),
    re.compile(r"<!--.*?-->", re.S),
)
# `{% if %}0{% else %}null{% endif %}` is one value in the rendered page and both values
# once the tags are stripped, which is the invalid `0null`. Only one branch can be real,
# so keep the first and blank the alternative.
_ELSE_BRANCH_RE = re.compile(r"\{%\s*else\s*%\}.*?(?=\{%\s*endif\s*%\})", re.S | re.I)


def _blank(match: re.Match) -> str:
    """Same length, same newlines, no content - so every line number still points home."""
    return "".join(character if character == "\n" else " " for character in match.group(0))


def _neutralise(source: str) -> str:
    for pattern in _COMMENT_RES:
        source = pattern.sub(_blank, source)
    return _ELSE_BRANCH_RE.sub(_blank, source)

# A <script> element is not necessarily code. `application/ld+json` is how every
# SEO-conscious site ships structured data, `application/json` and Django's own
# {% json_script %} carry server payloads, an importmap is configuration, and
# text/x-template holds markup for a client-side renderer. None of them are
# JavaScript, none of them can parse as JavaScript, and treating them as code
# reported 24 unparseable "files" on the reference project - all of them JSON-LD.
_JS_SCRIPT_TYPES = frozenset({
    "", "text/javascript", "application/javascript", "text/ecmascript",
    "application/ecmascript", "module", "text/babel", "javascript",
})
_DJANGO_TAG_RE = re.compile(r"\{%.*?%\}", re.S)
_DJANGO_VAR_RE = re.compile(r"\{\{.*?\}\}", re.S)


def inline_script_blocks(template_files: list[str]) -> list[tuple[str, str, int]]:
    """(template, javascript, line offset) for every <script> a template writes itself.

    This project keeps 200 KB of JavaScript inside its templates, and five of its API
    endpoints are called from nowhere else - invisible to a scan that reads only .js
    files, which then reported those endpoints as having no caller.

    Django's own tags are neutralised first: `{% if %}` is not JavaScript and acorn
    refuses the whole block over it, which cost six of this project's 77 blocks. A
    `{{ value }}` becomes a literal, because what the server interpolates is a value, and
    the shape of the code around it is what matters here.
    """
    blocks: list[tuple[str, str, int]] = []
    for template in sorted(template_files):
        try:
            source = pathlib.Path(template).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        source = _neutralise(source)
        for match in _INLINE_SCRIPT_RE.finditer(source):
            body = match.group(2)
            if not body.strip():
                continue
            declared = _SCRIPT_TYPE_RE.search(match.group(1) or "")
            kind = (declared.group(1) if declared else "").lower()
            if kind not in _JS_SCRIPT_TYPES:
                continue
            cleaned = _DJANGO_VAR_RE.sub("0", _DJANGO_TAG_RE.sub("", body))
            blocks.append((template, cleaned, source.count("\n", 0, match.start(2))))
    return blocks


def extract_template_js(template_files: list[str]) -> tuple[list[Symbol], list[Edge]]:
    """HTTP calls made from JavaScript a template writes inline."""
    symbols: list[Symbol] = []
    edges: list[Edge] = []
    seen_target_ids: set[str] = set()
    for template, ast, offset in parse_inline_blocks(template_files):
        found, found_edges = _http_symbols(ast, template, seen_target_ids, line_offset=offset)
        symbols += found
        edges += found_edges
        sighted, _ = _url_literals(ast, template, seen_target_ids, line_offset=offset)
        symbols += sighted
    return symbols, edges


def parse_js_source(source: str) -> dict:
    """Parse a JS snippet held in memory, through the same acorn path as files."""
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as handle:
        handle.write(source)
        temporary_path = handle.name
    try:
        # A snippet held in memory has no filename worth naming, and its caller treats an
        # empty result as "could not read this" already.
        return _parse_files([temporary_path], report_failures=False).get(temporary_path, {})
    finally:
        os.unlink(temporary_path)


def parse_inline_blocks(template_files: list[str]) -> list[tuple[str, dict, int]]:
    """(template, ast, line offset) for every inline <script>, in ONE parser invocation.

    Batched deliberately. Parsing blocks one at a time costs a node process each, and a
    real project has hundreds of them - on the one this was measured against, 202 KB of
    inline JavaScript across 158 templates. The offset is carried because a symbol's id
    contains its line, and a line number relative to the start of a <script> block points
    at the wrong place in the file a reader is about to open.
    """
    # Three extractors ask for the same templates' blocks in one scan; the answer is
    # the same until a template changes.
    key = tuple((path, _stamp(path)) for path in template_files)
    hit = _INLINE_CACHE.get(key)
    if hit is not None:
        return hit
    blocks = list(inline_script_blocks(template_files))
    if not blocks:
        return []
    directory = tempfile.mkdtemp(prefix="seamcheck-inline-")
    paths = []
    try:
        for index, (_, source, _) in enumerate(blocks):
            path = os.path.join(directory, f"{index}.js")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(source)
            paths.append(path)
        parsed = _parse_files(paths, report_failures=False)
        unreadable = [
            template for path, (template, _, _) in zip(paths, blocks, strict=True)
            if path not in parsed
        ]
        if unreadable:
            shown = ", ".join(os.path.basename(name) for name in unreadable[:3])
            report(
                "inline-parse-failures",
                "%s inline <script> block(s) could not be parsed and contributed no "
                "symbols (%s%s). Anything only they reference will look unused.",
                len(unreadable), shown, ", ..." if len(unreadable) > 3 else "",
            )
    finally:
        shutil.rmtree(directory, ignore_errors=True)
    _INLINE_CACHE[key] = [
        (template, parsed.get(path) or {}, offset)
        for path, (template, _, offset) in zip(paths, blocks, strict=True)
        if (parsed.get(path) or {}).get("type")
    ]
    return _INLINE_CACHE[key]


_INLINE_CACHE: dict[tuple, list[tuple[str, dict, int]]] = register_cache({})
