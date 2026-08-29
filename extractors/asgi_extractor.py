"""URL paths routed inside an ASGI callable, before Django's resolver ever runs.

A hot endpoint is sometimes short-circuited in asgi.py to skip middleware. Such a path
appears in no URLconf, so a URLconf-only map reports the busiest endpoint in the app as
unresolved. These symbols are emitted as `url` so the matcher treats them like any other
endpoint.
"""

from __future__ import annotations

import ast

from signal_map.graph import Status, Symbol

_NOTE = "Routed by the ASGI callable before Django's URL resolver; absent from every URLconf."


def _is_path_lookup(node: ast.AST) -> bool:
    """True for `path`-ish names and `scope["path"]` subscripts."""
    if isinstance(node, ast.Name):
        return "path" in node.id.lower()
    if isinstance(node, ast.Subscript):
        key = node.slice
        return isinstance(key, ast.Constant) and key.value == "path"
    return False


def _compared_literals(test: ast.AST) -> list[ast.Constant]:
    """String constants a path expression is compared against in one `if` test."""
    literals: list[ast.Constant] = []
    for node in ast.walk(test):
        if not isinstance(node, ast.Compare) or not _is_path_lookup(node.left):
            continue
        for comparator in node.comparators:
            elements = comparator.elts if isinstance(comparator, ast.Tuple | ast.List) else [comparator]
            literals.extend(
                element
                for element in elements
                if isinstance(element, ast.Constant)
                and isinstance(element.value, str)
                and element.value.startswith("/")
            )
    return literals


def extract_asgi_routes(asgi_file: str) -> list[Symbol]:
    with open(asgi_file, encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=asgi_file)

    symbols: list[Symbol] = []
    seen: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        for literal in _compared_literals(node.test):
            path = literal.value
            if path in seen:
                continue
            seen.add(path)
            symbols.append(
                Symbol(
                    id=f"url:{path.lstrip('/')}",
                    kind="url",
                    label=path.lstrip("/"),
                    sub="ASGI",
                    file=asgi_file,
                    line=literal.lineno,
                    status=Status.CONNECTED,
                    snippet=f'if path in ("{path}", ...):',
                    chain=[path],
                    note=_NOTE,
                )
            )
    return symbols
