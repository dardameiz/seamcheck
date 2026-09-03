"""Which function calls which, for Python.

`Symbol.owner` says which function a line sits in, and that alone answers "what does
`submit_push` touch" only for a handler that does the work itself. Most do not: the
reference project's `submit_push` is a view that delegates, and its Redis writes live in
a service module two calls away. Asked about the function, the map could show the route
it answers and nothing else - true, and useless.

So this reads the calls. Deliberately shallow: a name is resolved when the source says
plainly what it is - a def in the same file, a `from x import y` of a project module, a
`self.method` inside a class - and dropped otherwise. No type inference, no guessing at
what `obj.save()` is. An unresolved call is simply absent, which is the same contract as
`uncertain`: this never claims a call it cannot see.

The names match `Symbol.owner` exactly (`submit_push`, `StoreManager.apply`), because the
map's function index is keyed by that name.
"""
from __future__ import annotations

import ast
import os

from seamcheck.adapters.discovery import SKIP_DIRS
from seamcheck.pyscope import owners_of

_SKIP = set(SKIP_DIRS) | {"migrations"}


def _module_of(rel: str) -> str:
    stem = rel[:-3] if rel.endswith(".py") else rel
    parts = [p for p in stem.replace("\\", "/").split("/") if p]
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _defs_in(tree: ast.AST) -> dict[str, str]:
    """Every function this file defines: the name a caller would write -> its owner name.

    A method is reachable two ways and both are recorded: `apply` for `self.apply()`
    inside its own class, and `StoreManager.apply` for the qualified form.
    """
    found: dict[str, str] = {}

    def walk(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                walk(child, f"{prefix}{child.name}.")
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qualified = f"{prefix}{child.name}"
                found.setdefault(qualified, qualified)
                found.setdefault(child.name, qualified)
                walk(child, f"{qualified}.")
            else:
                walk(child, prefix)

    walk(tree, "")
    return found


def _imports(tree: ast.AST) -> tuple[dict[str, tuple[str, str]], dict[str, str]]:
    """`from a.b import c as d` -> {d: ("a.b", "c")}, and `import a.b as ab` -> {ab: "a.b"}."""
    names: dict[str, tuple[str, str]] = {}
    modules: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and not node.level:
            for alias in node.names:
                names[alias.asname or alias.name] = (node.module, alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                modules[alias.asname or alias.name] = alias.name
    return names, modules


def _callee(node: ast.Call) -> tuple[str, str]:
    """The written form of a call: ("", "helper") or ("self", "method") or ("mod", "fn")."""
    func = node.func
    if isinstance(func, ast.Name):
        return "", func.id
    if isinstance(func, ast.Attribute):
        value = func.value
        if isinstance(value, ast.Name):
            return value.id, func.attr
        if isinstance(value, ast.Attribute) and isinstance(value.value, ast.Name):
            return f"{value.value.id}.{value.attr}", func.attr
    return "", ""


def python_functions(root: str) -> tuple[dict[str, list[str]], dict[str, str]]:
    """`(who calls what, where every function is defined)`.

    Both, from one walk: the picker has to offer EVERY function, not only the ones that
    own a symbol. A helper that does nothing but compute a key owns nothing the scan can
    see, and it is exactly the function a reader types the name of - the work is one
    call further down, and the call graph is what reaches it.
    """
    calls, defs = _walk_project(root)
    return calls, defs


def python_calls(root: str) -> dict[str, list[str]]:
    """caller owner name -> the functions it calls, sorted, deduplicated.

    Keyed by bare owner name, exactly like the map's function index - so two projects
    files that both define `reset` share one entry. That is a real ambiguity and it is
    left visible rather than papered over with a file-qualified key the index could not
    look up.
    """
    return _walk_project(root)[0]


def _walk_project(root: str) -> tuple[dict[str, list[str]], dict[str, str]]:
    files: list[tuple[str, ast.AST]] = []
    defs_by_module: dict[str, dict[str, str]] = {}
    for here, subdirectories, names in os.walk(root):
        subdirectories[:] = [
            d for d in subdirectories if d not in _SKIP and not d.startswith(".")
        ]
        for name in names:
            if not name.endswith(".py"):
                continue
            path = os.path.join(here, name)
            try:
                with open(path, encoding="utf-8", errors="replace") as handle:
                    tree = ast.parse(handle.read())
            except (OSError, SyntaxError, ValueError):
                continue
            rel = os.path.relpath(path, root)
            files.append((rel, tree))
            defs_by_module[_module_of(rel)] = _defs_in(tree)

    # A simple name defined exactly ONCE in the whole project is unambiguous, whatever
    # object it is called on. `service.process_batch_unified()` cannot be resolved by
    # type - and typing is not something this tool does - but if the project defines
    # `process_batch_unified` in one place and one place only, then that IS the function
    # being called. `save`, `get` and `run` are defined many times over and so resolve to
    # nothing, which is the rule doing its job rather than failing.
    unique: dict[str, str] = {}
    clashed: set[str] = set()
    for defs in defs_by_module.values():
        for written, qualified in defs.items():
            if "." in written:
                continue          # the qualified spelling; the bare one is the key
            if written in unique and unique[written] != qualified:
                clashed.add(written)
            unique.setdefault(written, qualified)
    for name in clashed:
        unique.pop(name, None)

    calls: dict[str, set[str]] = {}
    for rel, tree in files:
        here_defs = defs_by_module[_module_of(rel)]
        imported, modules = _imports(tree)
        owners = owners_of(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            caller = owners.get(node.lineno, "")
            if not caller:
                continue          # a call at import time belongs to no function
            receiver, name = _callee(node)
            if not name:
                continue
            target = ""
            if not receiver:
                if name in here_defs:
                    target = here_defs[name]
                elif name in imported:
                    module, original = imported[name]
                    target = (defs_by_module.get(module) or {}).get(original, "")
            elif receiver == "self":
                # `self.helper()` inside `StoreManager.apply` is `StoreManager.helper`,
                # and only when the class really has it: a mixin's method lives in
                # another file and is not claimed here.
                owner_class = caller.rsplit(".", 1)[0] if "." in caller else ""
                if owner_class:
                    target = here_defs.get(f"{owner_class}.{name}", "")
            else:
                module = modules.get(receiver) or (
                    imported.get(receiver, ("", ""))[0]
                    if imported.get(receiver, ("", ""))[1] == receiver else ""
                )
                if module in defs_by_module:
                    target = defs_by_module[module].get(name, "")
                elif module or receiver in imported or receiver in modules:
                    # Somebody else's library. `requests.get()` must never resolve to a
                    # project method that happens to be called `get` - the uniqueness
                    # rule below is for calls on OUR objects, not on theirs.
                    target = ""
                else:
                    target = unique.get(name, "")
            if not target or target == caller:
                continue
            calls.setdefault(caller, set()).add(target)

    # Where each function is written, by the same owner name. A qualified spelling wins
    # over the bare one: `StoreManager.apply` is what `owner` says, so it is the key.
    where: dict[str, str] = {}
    for rel, _ in files:
        for written, qualified in defs_by_module[_module_of(rel)].items():
            if written == qualified:
                where.setdefault(qualified, rel)
    return ({caller: sorted(targets) for caller, targets in sorted(calls.items())}, where)
