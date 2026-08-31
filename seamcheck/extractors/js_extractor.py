"""HTTP calls made from JavaScript, via an acorn AST walk over the static import graph."""

from __future__ import annotations

import json
import os
import pathlib
import re
import shutil
import tempfile

from seamcheck.graph import Edge, Status, Symbol
from seamcheck.nodetools import parser_path, run_parser

_JS_TOOLS = os.path.join(os.path.dirname(__file__), os.pardir, "js_tools")
_JS_EXTENSIONS = (".js", ".mjs", ".jsx")

# fetch() is the whole HTTP surface in most modern front ends, but sendBeacon() is a real
# request too: omitting it leaves its endpoint looking orphaned.
_FETCH_CALLEES = ("fetch",)
_BEACON_CALLEE = "sendBeacon"

_DYNAMIC_NOTE = "Fetch target built at runtime -- cannot be statically resolved."
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


def _parse_files(paths: list[str]) -> dict[str, dict]:
    if not paths:
        return {}
    parsed: dict[str, dict] = {}
    for line in run_parser(parser_path(_JS_TOOLS, "parse_js"), paths, "JavaScript"):
        record = json.loads(line)
        if "ast" in record:
            parsed[record["path"]] = record["ast"]
    return parsed


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
        return key.get("name") or key.get("value")
    if node_type == "VariableDeclarator" and (node.get("init") or {}).get("type") in _FUNCTION_TYPES:
        return (node.get("id") or {}).get("name")
    return None


def _walk(node, enclosing: str = ""):
    """Yield (node, enclosing_function_name) once per AST node, in a single pass."""
    if isinstance(node, dict):
        enclosing = _carrier_name(node) or enclosing
        if node.get("type"):
            yield node, enclosing
        for value in node.values():
            yield from _walk(value, enclosing)
    elif isinstance(node, list):
        for item in node:
            yield from _walk(item, enclosing)


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
    for index_name in ("index.js", "index.mjs"):
        candidate = os.path.join(base, index_name)
        if os.path.isfile(candidate):
            return candidate
    return None


def _http_call_target(node: dict) -> tuple[bool, dict | None]:
    """(is_http_call, first_argument_node) for fetch(...) / navigator.sendBeacon(...)."""
    if node.get("type") != "CallExpression":
        return False, None
    callee = node.get("callee") or {}
    arguments = node.get("arguments") or []
    first = arguments[0] if arguments else None

    if callee.get("type") == "Identifier" and callee.get("name") in _FETCH_CALLEES:
        return True, first
    if callee.get("type") == "MemberExpression" and (callee.get("property") or {}).get("name") == _BEACON_CALLEE:
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
        for path, ast in _parse_files(batch).items():
            for import_path in _imported_paths(ast):
                resolved = _resolve_import(path, import_path)
                if resolved and resolved not in visited:
                    to_visit.append(resolved)
    return sorted(visited)


# A path-shaped string: leading slash, no whitespace, at least one more segment.
_URL_LITERAL_RE = re.compile(r"\A/[\w\-./]*[\w\-]/?\Z")
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
    for node, enclosing in _walk(ast):
        if node.get("type") != "Literal":
            continue
        value = node.get("value")
        if not isinstance(value, str) or not _URL_LITERAL_RE.match(value):
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
        line = ((node.get("loc") or {}).get("start") or {}).get("line")
        line = (line + line_offset) if line else None
        basename = os.path.basename(path)
        symbols.append(
            Symbol(
                id=target_id, kind="fetch_target", label=target, sub="literal",
                file=path, line=line, status=Status.UNCERTAIN, snippet=f'"{target}"',
                chain=[basename, enclosing] if enclosing else [basename], note=_LITERAL_NOTE,
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

        line = ((node.get("loc") or {}).get("start") or {}).get("line")
        line = (line + line_offset) if line else None
        basename = os.path.basename(path)
        chain = [basename, enclosing] if enclosing else [basename]
        call_id = f"jscall:{path}:{line}"

        target, exact = _static_url(first_argument)
        if target:
            status = Status.CONNECTED if exact else Status.UNCERTAIN
            snippet = f'fetch("{target}")' if exact else f'fetch("{target}" + <runtime value>)'
            symbols.append(
                Symbol(
                    id=call_id, kind="js_call", label=target, sub=basename, file=path,
                    line=line, status=status, snippet=snippet,
                    chain=chain, note="" if exact else _PREFIX_NOTE,
                )
            )
            target_id = f"fetch:{target}"
            if target_id not in seen_target_ids:
                seen_target_ids.add(target_id)
                symbols.append(
                    Symbol(
                        id=target_id, kind="fetch_target", label=target, sub="", file=path,
                        line=line, status=status, snippet=snippet,
                        chain=[target], note="" if exact else _PREFIX_NOTE,
                    )
                )
            edges.append(Edge(from_id=call_id, to_id=target_id, status=status))
        else:
            symbols.append(
                Symbol(
                    id=call_id, kind="js_call", label="fetch(<runtime value>)", sub=basename,
                    file=path, line=line, status=Status.UNCERTAIN,
                    snippet="fetch(<dynamic value>)", chain=chain, note=_DYNAMIC_NOTE,
                )
            )
    return symbols, edges


def extract_js(entry_files: list[str], project_root: str) -> tuple[list[Symbol], list[Edge]]:
    symbols: list[Symbol] = []
    edges: list[Edge] = []
    seen_target_ids: set[str] = set()

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

        for path, ast in _parse_files(batch).items():
            for import_path in _imported_paths(ast):
                resolved = _resolve_import(path, import_path)
                if resolved and resolved not in visited:
                    to_visit.append(resolved)

            found, found_edges = _http_symbols(ast, path, seen_target_ids)
            symbols += found
            edges += found_edges
            sighted, _ = _url_literals(ast, path, seen_target_ids)
            symbols += sighted

    return symbols, edges


_INLINE_SCRIPT_RE = re.compile(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", re.S | re.I)
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
        for match in _INLINE_SCRIPT_RE.finditer(source):
            body = match.group(1)
            if not body.strip():
                continue
            cleaned = _DJANGO_VAR_RE.sub("0", _DJANGO_TAG_RE.sub("", body))
            blocks.append((template, cleaned, source.count("\n", 0, match.start(1))))
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
        return _parse_files([temporary_path]).get(temporary_path, {})
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
        parsed = _parse_files(paths)
    finally:
        shutil.rmtree(directory, ignore_errors=True)
    return [
        (template, parsed.get(path) or {}, offset)
        for path, (template, _, offset) in zip(paths, blocks, strict=True)
        if (parsed.get(path) or {}).get("type")
    ]
