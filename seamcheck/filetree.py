"""Per-file view: what the scan knows about each file, and what it never looked at.

The map is rooted at pages, which is how a browser reaches code - but not how anyone edits
it. Reading it, you cannot tell whether every function in a file was considered or only the
handful that happened to sit on a page's path.

So this is the other axis: one record per file, with how many of its declarations appear in
the graph at all. That number is *coverage*, never a finding. A helper that makes no request
and touches no element produces no symbol because there is nothing to model, not because it
is dead - and 346 of this project's 349 model methods are exactly that.
"""

from __future__ import annotations

import ast
import pathlib
from collections import defaultdict
from dataclasses import dataclass, field

from seamcheck.graph import Graph


@dataclass
class FileRecord:
    path: str
    counts: dict[str, int] = field(default_factory=dict)
    declarations: int = 0
    known: int = 0


def _python_declarations(path: str) -> list[tuple[str, int, int]]:
    try:
        tree = ast.parse(pathlib.Path(path).read_text(encoding="utf-8", errors="replace"))
    except (OSError, SyntaxError):
        return []
    declarations = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            continue
        # A decorated function is reported by inspect from its first decorator, which is
        # above the `def` ast reports - so views came out unmatched. ast knows where the
        # decorators start; a slack window instead swallowed the next function along.
        start = min([node.lineno] + [d.lineno for d in node.decorator_list])
        declarations.append((node.name, start, node.end_lineno or node.lineno))
    return declarations


def _js_declarations(tree: dict) -> list[tuple[str, int, int]]:
    """Named functions, class methods and arrow constants, from an acorn tree."""
    found: list[tuple[str, int, int]] = []
    stack: list = [tree]
    while stack:
        node = stack.pop()
        if isinstance(node, list):
            stack.extend(node)
            continue
        if not isinstance(node, dict):
            continue
        kind, name = node.get("type"), None
        if kind in ("FunctionDeclaration", "ClassDeclaration"):
            name = (node.get("id") or {}).get("name")
        elif kind == "MethodDefinition":
            name = (node.get("key") or {}).get("name")
        elif kind == "VariableDeclarator" and (node.get("init") or {}).get("type") in (
            "ArrowFunctionExpression", "FunctionExpression"
        ):
            name = (node.get("id") or {}).get("name")
        if name:
            start = ((node.get("loc") or {}).get("start") or {}).get("line") or 0
            end = ((node.get("loc") or {}).get("end") or {}).get("line") or start
            found.append((name, start, end))
        stack.extend(value for value in node.values() if isinstance(value, dict | list))
    return found


def build_file_tree(graph: Graph, js_files: list[str] | None = None) -> list[FileRecord]:
    """One record per file any symbol came from, plus any JS file that produced none."""
    from seamcheck.extractors.js_extractor import _parse_files

    per_file: dict[str, list] = defaultdict(list)
    for symbol in graph.symbols:
        if symbol.file:
            per_file[symbol.file].append(symbol)

    javascript = sorted({p for p in list(per_file) + list(js_files or []) if p.endswith(".js")})
    trees = _parse_files([p for p in javascript if pathlib.Path(p).is_file()])

    records: list[FileRecord] = []
    for path in sorted(set(per_file) | set(javascript)):
        symbols = per_file.get(path, [])
        counts: dict[str, int] = {}
        for symbol in symbols:
            counts[symbol.status.value] = counts.get(symbol.status.value, 0) + 1

        if path.endswith(".py"):
            declared = _python_declarations(path)
        elif path in trees:
            declared = _js_declarations(trees[path])
        else:
            declared = []

        names = {symbol.label for symbol in symbols}
        lines = {symbol.line for symbol in symbols if symbol.line}
        known = sum(
            1
            for name, start, end in declared
            if name in names or any(start <= line <= end for line in lines)
        )
        records.append(FileRecord(path=path, counts=counts,
                                  declarations=len(declared), known=known))
    return records
