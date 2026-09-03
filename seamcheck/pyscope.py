"""Which function a line of Python sits in.

Every Python extractor walks with `ast.walk`, which is flat: a `pipe.set("user:{id}")`
hit knows its file and its line but not that it is inside `def submit_push`. That is the
one thing a developer always knows and the graph never did - so a card could name the key
and the file, but never the function whose behaviour the key IS.

One pass per file, shared by every extractor, rather than each one growing its own scope
tracking.
"""
from __future__ import annotations

import ast

_FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)


def owners_of(tree: ast.AST) -> dict[int, str]:
    """Map every line inside a function body to that function's dotted name.

    `submit_push`, `StoreManager.apply`, `outer.inner`. Lines at module level, and lines
    in a class body but not in a method, are absent rather than mapped to `""`: "nobody
    owns this line" is a fact worth keeping distinct from "owned by a function called
    nothing", and a missing key says it without inventing a name.

    The innermost function wins, so a helper defined inside a view reports the helper.
    """
    owners: dict[int, str] = {}

    def walk(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                # A class body is not a function: a model field does not run when you
                # call the class, so naming the class as its owner would be a claim
                # about behaviour that is not true. Only its methods get an owner, and
                # they carry the class name so `apply` reads as `StoreManager.apply`.
                walk(child, f"{prefix}{child.name}.")
            elif isinstance(child, _FUNCTIONS):
                name = f"{prefix}{child.name}"
                # From the first decorator, not from `def`: `@ratelimit(key="user:{id}")`
                # is the view's key. Attributing it to the module is how a Redis write
                # ends up owned by nobody.
                first = min(
                    [child.lineno] + [d.lineno for d in child.decorator_list if
                                      getattr(d, "lineno", None)]
                )
                last = getattr(child, "end_lineno", None) or first
                for line in range(first, last + 1):
                    owners[line] = name
                walk(child, f"{name}.")
            else:
                walk(child, prefix)

    walk(tree, "")
    return owners
