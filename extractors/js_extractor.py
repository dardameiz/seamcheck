"""HTTP calls made from JavaScript, via an acorn AST walk over the static import graph."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile

from signal_map.graph import Edge, Status, Symbol

_PARSE_SCRIPT = os.path.join(os.path.dirname(__file__), os.pardir, "js_tools", "parse_js.mjs")
_JS_EXTENSIONS = (".js", ".mjs", ".jsx")

# fetch() is the whole HTTP surface in most modern front ends, but sendBeacon() is a real
# request too: omitting it leaves its endpoint looking orphaned.
_FETCH_CALLEES = ("fetch",)
_BEACON_CALLEE = "sendBeacon"

_DYNAMIC_NOTE = "Fetch target built at runtime -- cannot be statically resolved."

_FUNCTION_TYPES = ("FunctionDeclaration", "FunctionExpression", "ArrowFunctionExpression")


def _parse_files(paths: list[str]) -> dict[str, dict]:
    if not paths:
        return {}
    result = subprocess.run(
        ["node", _PARSE_SCRIPT],
        input="\n".join(paths),
        capture_output=True,
        text=True,
        check=True,
    )
    parsed: dict[str, dict] = {}
    for line in result.stdout.splitlines():
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
    if node_type in ("MethodDefinition", "PropertyDefinition", "Property"):
        if (node.get("value") or {}).get("type") in _FUNCTION_TYPES or node_type != "Property":
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

            for node, enclosing in _walk(ast):
                is_http, first_argument = _http_call_target(node)
                if not is_http:
                    continue

                line = ((node.get("loc") or {}).get("start") or {}).get("line")
                basename = os.path.basename(path)
                chain = [basename, enclosing] if enclosing else [basename]
                call_id = f"jscall:{path}:{line}"

                if (first_argument or {}).get("type") == "Literal":
                    target = first_argument["value"]
                    symbols.append(
                        Symbol(
                            id=call_id, kind="js_call", label="fetch()", sub=basename, file=path,
                            line=line, status=Status.CONNECTED, snippet=f'fetch("{target}")',
                            chain=chain, note="",
                        )
                    )
                    target_id = f"fetch:{target}"
                    if target_id not in seen_target_ids:
                        seen_target_ids.add(target_id)
                        symbols.append(
                            Symbol(
                                id=target_id, kind="fetch_target", label=target, sub="", file=path,
                                line=line, status=Status.CONNECTED, snippet=f'fetch("{target}")',
                                chain=[target], note="",
                            )
                        )
                    edges.append(Edge(from_id=call_id, to_id=target_id, status=Status.CONNECTED))
                else:
                    symbols.append(
                        Symbol(
                            id=call_id, kind="js_call", label="fetch()", sub=basename, file=path,
                            line=line, status=Status.UNCERTAIN, snippet="fetch(<dynamic value>)",
                            chain=chain, note=_DYNAMIC_NOTE,
                        )
                    )

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
