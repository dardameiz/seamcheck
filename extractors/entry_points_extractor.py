"""Entry points Django calls without an HTTP request: signals, admin actions, template tags.

Each is self-certain: the decorator or list membership IS the evidence that something
invokes it, so no edge from a caller is needed to justify CONNECTED.
"""

from __future__ import annotations

import ast

from signal_map.graph import Status, Symbol

# `async def` parses to a different node type than `def`. Missing it would report a live
# entry point as an orphan, which is the one error class this tool must never make.
_FUNCTION_NODES = (ast.FunctionDef, ast.AsyncFunctionDef)

_TAG_DECORATORS = frozenset({"filter", "simple_tag", "inclusion_tag", "simple_block_tag", "tag"})


def _decorator_names(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    names = []
    for decorator in node.decorator_list:
        target = decorator.func if isinstance(decorator, ast.Call) else decorator
        if isinstance(target, ast.Attribute):
            names.append(target.attr)
        elif isinstance(target, ast.Name):
            names.append(target.id)
    return names


def _symbol(kind: str, name: str, file_path: str, line: int, sub: str, snippet: str) -> Symbol:
    return Symbol(
        id=f"{kind}:{file_path}:{name}",
        kind=kind,
        label=name,
        sub=sub,
        file=file_path,
        line=line,
        status=Status.CONNECTED,
        snippet=snippet,
        chain=[name],
        note="",
    )


def _extract_decorated(tree: ast.Module, file_path: str) -> list[Symbol]:
    symbols = []
    for node in ast.walk(tree):
        if not isinstance(node, _FUNCTION_NODES):
            continue
        decorators = _decorator_names(node)
        if "receiver" in decorators:
            symbols.append(
                _symbol(
                    "signal_receiver", node.name, file_path, node.lineno, file_path,
                    f"@receiver(...)\ndef {node.name}(...): ...",
                )
            )
        tag = next((d for d in decorators if d in _TAG_DECORATORS), None)
        if tag:
            symbols.append(
                _symbol(
                    "template_tag", node.name, file_path, node.lineno, file_path,
                    f"@register.{tag}\ndef {node.name}(...): ...",
                )
            )
    return symbols


def _extract_admin_actions(tree: ast.Module, file_path: str) -> list[Symbol]:
    symbols = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        method_names = {n.name for n in node.body if isinstance(n, _FUNCTION_NODES)}
        for stmt in node.body:
            if not (isinstance(stmt, ast.Assign) and isinstance(stmt.value, ast.List)):
                continue
            if "actions" not in [t.id for t in stmt.targets if isinstance(t, ast.Name)]:
                continue
            for element in stmt.value.elts:
                # Only claim entries backed by a real method on the class: an `actions`
                # string with no matching def is a callable defined elsewhere (or a typo),
                # and inventing a symbol for it would be over-reporting.
                if (
                    isinstance(element, ast.Constant)
                    and isinstance(element.value, str)
                    and element.value in method_names
                ):
                    symbols.append(
                        Symbol(
                            id=f"admin_action:{file_path}:{node.name}.{element.value}",
                            kind="admin_action",
                            label=element.value,
                            sub=node.name,
                            file=file_path,
                            line=stmt.lineno,
                            status=Status.CONNECTED,
                            snippet=f'actions = [..., "{element.value}", ...]',
                            chain=[node.name, element.value],
                            note="",
                        )
                    )
    return symbols


def extract_entry_points(files: set[str]) -> list[Symbol]:
    symbols: list[Symbol] = []
    for file_path in sorted(files):
        with open(file_path, encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=file_path)
        symbols.extend(_extract_decorated(tree, file_path))
        symbols.extend(_extract_admin_actions(tree, file_path))
    return symbols
