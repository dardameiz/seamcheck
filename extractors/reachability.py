"""Which first-party modules are reachable from a set of roots, by parsing imports only.

Never imports application code: everything here is `ast.parse` over source text.
"""

from __future__ import annotations

import ast
import os


def _current_package(file_path: str, repo_root: str) -> list[str]:
    """Dotted package parts that a relative import inside `file_path` resolves against."""
    relative = os.path.relpath(file_path, repo_root)
    parts = relative[: -len(".py")].split(os.sep)
    return parts[:-1]


def _parse_imports(file_path: str, repo_root: str) -> list[str]:
    with open(file_path, encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=file_path)

    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = _current_package(file_path, repo_root)
                base = base[: len(base) - (node.level - 1)]
                prefix = ".".join([*base, node.module] if node.module else base)
            else:
                prefix = node.module or ""
            if not prefix:
                continue
            # `from pkg import name` may name a submodule OR an attribute of pkg. Offer
            # both; _module_to_file keeps whichever is a real file. Without the submodule
            # candidate this walk resolves ~1% of a Django codebase's first-party imports.
            modules.append(prefix)
            modules.extend(f"{prefix}.{alias.name}" for alias in node.names)
    return modules


def _is_first_party(dotted_module: str, first_party_prefixes: list[str]) -> bool:
    return any(
        dotted_module == prefix or dotted_module.startswith(prefix + ".")
        for prefix in first_party_prefixes
    )


def _module_to_file(dotted_module: str, repo_root: str) -> str | None:
    candidate = os.path.join(repo_root, *dotted_module.split("."))
    if os.path.exists(candidate + ".py"):
        return candidate + ".py"
    package_init = os.path.join(candidate, "__init__.py")
    return package_init if os.path.exists(package_init) else None


def discover_reachable_modules(
    root_files: list[str], repo_root: str, first_party_prefixes: list[str]
) -> set[str]:
    reached: set[str] = set()
    to_visit = list(root_files)

    while to_visit:
        current = to_visit.pop()
        if current in reached or not os.path.exists(current):
            continue
        reached.add(current)

        for dotted_module in _parse_imports(current, repo_root):
            if not _is_first_party(dotted_module, first_party_prefixes):
                continue
            resolved = _module_to_file(dotted_module, repo_root)
            if resolved and resolved not in reached:
                to_visit.append(resolved)

    return reached
