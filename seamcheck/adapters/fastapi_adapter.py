"""FastAPI: the second adapter, and the cheapest possible proof the seam is real.

Same language as the first, same `ast` module, no new parser - which is exactly why it goes
second. What it validates is not FastAPI support; it is that `ServerAdapter` is a real
boundary rather than a shape drawn around the Django code that was already there.

Routes are decorators on functions, so they are read the way the Django static reader reads
a `urls.py`: as text, with no import of the project. That matters more here than it does for
Django, because a FastAPI app imports its own dependencies at module scope and a cloned
repository will not have them installed.

Three things compose a path, and missing any one of them silently produces wrong routes:

    router = APIRouter(prefix="/users")     # the router's own prefix
    @router.get("/{user_id}")               # the decorator's path
    app.include_router(router, prefix="/api")   # and the prefix it is mounted under

giving `/api/users/{user_id}`. A reader that takes only the decorator reports `/{user_id}`,
which matches nothing and would mark every call to it unresolved.
"""

from __future__ import annotations

import ast
import pathlib

from seamcheck.adapters.base import ServerScan
from seamcheck.graph import Edge, Status, Symbol

_UNMOUNTED_NOTE = (
    "This route is declared on an APIRouter that nothing in the source mounts, so the path "
    "shown is only the part written at the decorator. The prefix it actually serves under "
    "is decided at runtime - a plugin registry, a conditional include - and no reader of "
    "source can know it. Do not read this as the real URL, and do not read a call to it as "
    "unresolved."
)

_METHODS = ("get", "post", "put", "delete", "patch", "options", "head", "trace")

# Directories that are never the application under scan. A vendored copy of FastAPI's own
# examples inside site-packages would otherwise contribute hundreds of phantom routes.
_SKIP = {
    ".git", ".venv", "venv", "env", "node_modules", "site-packages", "__pycache__",
    "build", "dist", ".tox", ".mypy_cache", ".pytest_cache", "migrations",
}


def _string(node: ast.AST | None) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _keyword(call: ast.Call, name: str) -> str | None:
    for keyword in call.keywords:
        if keyword.arg == name:
            return _string(keyword.value)
    return None


def _prefix_node(call: ast.Call) -> ast.AST | None:
    for keyword in call.keywords:
        if keyword.arg == "prefix":
            return keyword.value
    return None


def _resolve_prefix(node: ast.AST | None, constants: dict[str, str]) -> str:
    """The mount prefix, whether it was written inline or held in a setting.

    `app.include_router(api_router, prefix=settings.API_V1_STR)` is how both the official
    FastAPI template and the RealWorld example mount their API root, so a reader that only
    accepts a literal loses the first segment of every route in the project.

    Only unambiguous names are resolved: if two files define `API_PREFIX` as different
    strings there is no evidence which one applies here, and a guess would put every route
    at a path that does not exist.
    """
    if node is None:
        return ""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    name = getattr(node, "id", None) or getattr(node, "attr", None)
    return constants.get(name, "") if name else ""


def _string_constants(trees: list[ast.Module]) -> dict[str, str]:
    """name -> its string value, for names that mean exactly one thing repo-wide."""
    seen: dict[str, set[str]] = {}
    for tree in trees:
        for node in ast.walk(tree):
            targets: list[ast.AST] = []
            value: ast.AST | None = None
            if isinstance(node, ast.Assign):
                targets, value = list(node.targets), node.value
            elif isinstance(node, ast.AnnAssign) and node.value is not None:
                targets, value = [node.target], node.value
            text = _string(value)
            if text is None or not text.startswith("/"):
                continue
            for target in targets:
                key = getattr(target, "id", None) or getattr(target, "attr", None)
                if key:
                    seen.setdefault(key, set()).add(text)
    return {key: values.pop() for key, values in seen.items() if len(values) == 1}


def _join(*parts: str) -> str:
    """Compose path segments the way Starlette does: exactly one slash between them."""
    joined = "/".join(part.strip("/") for part in parts if part and part.strip("/"))
    return f"/{joined}" if joined else "/"


class _Module:
    """One Python file, read for the things that make a route.

    Routers are identified by (module, original variable name) rather than by the local
    name, because `from .users import router as users_router` renames it and a mount keyed
    on the local name silently matches nothing. That failure is invisible: the routes still
    appear, just at the wrong paths, so every call to them reads as unresolved.
    """

    def __init__(self, path: str, tree: ast.Module, dotted: str) -> None:
        self.path = path
        self.tree = tree
        self.dotted = dotted
        self.aliases: dict[str, tuple[str, str]] = {}   # local name -> (module, real name)
        self.router_prefixes: dict[str, str] = {}  # local variable name -> APIRouter(prefix=)
        self.app_roots: set[str] = set()           # variables holding a FastAPI(), not a router
        self.routes: list[tuple[str, str, str, str, int]] = []  # var, method, path, fn, line
        # (child router, parent router, prefix node). A mount is an edge in a tree, and
        # only the whole path from the root gives a route its real prefix.
        self.mounts: list[tuple[tuple[str, str], tuple[str, str] | None, ast.AST | None]] = []

    def read(self) -> None:
        for node in ast.walk(self.tree):
            if isinstance(node, ast.ImportFrom):
                self._read_import(node)
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Assign):
                self._read_router_assignment(node)
            elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                self._read_decorators(node)
            elif isinstance(node, ast.Call):
                self._read_include(node)
                self._read_mount(node)

    def key(self, local_name: str) -> tuple[str, str]:
        """Where the router called `local_name` here actually lives."""
        return self.aliases.get(local_name, (self.dotted, local_name))

    def key_of(self, node: ast.AST) -> tuple[str, str] | None:
        """The router a mount refers to, in either of the two forms real code uses.

            from .items import router as items_router   ->  items_router
            from . import items                         ->  items.router

        The second form is what the official FastAPI template uses, and reading only the
        attribute name resolved every leaf router to the mounting module instead of the one
        it lives in - so no prefix ever attached and every route came out at its bare path.
        """
        if isinstance(node, ast.Name):
            return self.key(node.id)
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            base = node.value.id
            module, name = self.aliases.get(base, (self.dotted, base))
            # `from app.api.routes import items` makes `items` the module app.api.routes.items
            return (f"{module}.{name}" if module else name, node.attr)
        return None

    def _read_mount(self, node: ast.Call) -> None:
        """`app.mount("/api/v1", app=api)` - a whole sub-application under a path.

        A different mechanism from include_router and just as load-bearing: a production
        app puts its entire API behind one mount, so missing it drops the same segment from
        every route in the project.
        """
        if getattr(node.func, "attr", None) != "mount" or not node.args:
            return
        path = _string(node.args[0])
        if not path or path == "/":
            return
        sub = None
        for keyword in node.keywords:
            if keyword.arg == "app":
                sub = self.key_of(keyword.value)
        if sub is None and len(node.args) > 1:
            sub = self.key_of(node.args[1])
        if sub:
            parent = self.key_of(node.func.value) if isinstance(node.func, ast.Attribute) else None
            self.mounts.append((sub, parent, ast.Constant(value=path)))

    def _read_import(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        if node.level:
            # A relative import resolves against this module's own package.
            package = self.dotted.rsplit(".", node.level)[0] if "." in self.dotted else ""
            module = f"{package}.{module}".strip(".") if module else package
        for alias in node.names:
            self.aliases[alias.asname or alias.name] = (module, alias.name)

    def _read_router_assignment(self, node: ast.Assign) -> None:
        call = node.value
        if not isinstance(call, ast.Call):
            return
        name = getattr(call.func, "id", None) or getattr(call.func, "attr", None)
        if name not in ("APIRouter", "FastAPI"):
            return
        prefix = _keyword(call, "prefix") or ""
        for target in node.targets:
            if isinstance(target, ast.Name):
                self.router_prefixes[target.id] = prefix
                # A FastAPI() is an application root: its routes are AT the path they
                # declare. An APIRouter() has no path until something mounts it.
                if name == "FastAPI":
                    self.app_roots.add(target.id)

    def _read_decorators(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue
            attribute = decorator.func
            if not isinstance(attribute, ast.Attribute) or attribute.attr not in _METHODS:
                continue
            owner = getattr(attribute.value, "id", None)
            if owner is None:
                continue
            path = _string(decorator.args[0]) if decorator.args else _keyword(decorator, "path")
            if path is None:
                continue
            self.routes.append(
                (owner, attribute.attr.upper(), path, node.name, node.lineno)
            )

    def _read_include(self, node: ast.Call) -> None:
        if getattr(node.func, "attr", None) != "include_router" or not node.args:
            return
        router_key = self.key_of(node.args[0])
        if router_key:
            parent = self.key_of(node.func.value) if isinstance(node.func, ast.Attribute) else None
            self.mounts.append((router_key, parent, _prefix_node(node)))


class FastAPIAdapter:
    name = "fastapi"

    def detect(self, repo_root: str, config: dict) -> float:
        """Confidence by artefact: an import of FastAPI, and a decorator that routes."""
        imports = decorators = False
        for path in _python_files(repo_root, limit=400):
            try:
                text = pathlib.Path(path).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if "fastapi" not in text.lower():
                continue
            if "from fastapi import" in text or "import fastapi" in text:
                imports = True
            if any(f".{method}(" in text for method in _METHODS) and "@" in text:
                decorators = True
            if imports and decorators:
                return 0.9
        return 0.5 if imports else 0.0

    def scan(self, repo_root: str, config: dict, progress) -> ServerScan:
        progress.step("URLs and views")
        modules = []
        trees: list[ast.Module] = []
        for path in _python_files(repo_root):
            try:
                tree = ast.parse(
                    pathlib.Path(path).read_text(encoding="utf-8", errors="replace"), filename=path
                )
            except (OSError, SyntaxError, ValueError):
                # A file that will not parse contributes nothing, and must not stop the rest.
                continue
            module = _Module(path, tree, _module_name(path))
            module.read()
            trees.append(tree)
            if module.routes or module.mounts:
                modules.append(module)
        constants = _string_constants(trees)

        # A router's full prefix is its own plus every prefix it is mounted under. Mount
        # prefixes are resolved by variable name across files, which is how the common
        # `from .users import router` / `include_router(router, prefix=...)` layout works.
        # A router's real prefix is its own mount prefix plus every prefix above it. The
        # official FastAPI template mounts leaf routers into an api_router with no prefix,
        # then mounts THAT under settings.API_V1_STR - so a reader that stops at the direct
        # parent loses "/api/v1" from every route in the project.
        parents: dict[tuple[str, str], tuple[tuple[str, str] | None, str]] = {}
        own: dict[tuple[str, str], str] = {}
        for module in modules:
            for variable, prefix in module.router_prefixes.items():
                if prefix:
                    own[(module.dotted, variable)] = prefix
            for router_key, parent_key, prefix_node in module.mounts:
                parents[router_key] = (parent_key, _resolve_prefix(prefix_node, constants))

        def full_prefix(key: tuple[str, str]) -> str:
            """Every prefix between the application root and this router.

            Two contribute at each level and both are needed. `APIRouter(prefix="/x")` is
            the router's OWN prefix; `include_router(child, prefix="/y")` is the prefix it
            is mounted under. A production app nests them - a router declared
            `prefix="/{organization}"` holding children mounted at "/projects" - so reading
            only the mount prefix drops "/{organization}" from a hundred routes.
            """
            chain: list[str] = []
            seen: set[tuple[str, str]] = set()
            current: tuple[str, str] | None = key
            while current and current in parents and current not in seen:
                seen.add(current)
                parent, prefix = parents[current]
                if prefix:
                    chain.append(prefix)
                if parent and own.get(parent):
                    chain.append(own[parent])
                current = parent
            return _join(*reversed(chain))

        mounted = {key: full_prefix(key) for key in parents}

        # Module names are matched by suffix, not equality. A package without __init__.py
        # (PEP 420, and most modern layouts) gives a file no importable prefix at all, so
        # `app/users.py` reads as `users` while the import that mounts it says `app.users`.
        # An exact match wins; a suffix match is used only when exactly one candidate fits,
        # because two modules ending `.views` holding a `router` each is not evidence.
        by_variable: dict[str, list[tuple[str, str]]] = {}
        for (dotted, variable), prefix in mounted.items():
            by_variable.setdefault(variable, []).append((dotted, prefix))

        def prefix_for(*keys: tuple[str, str]) -> str:
            for key in keys:
                if key in mounted:
                    return mounted[key]
            for dotted, variable in keys:
                candidates = [
                    prefix for candidate, prefix in by_variable.get(variable, [])
                    if candidate.endswith(f".{dotted}") or dotted.endswith(f".{candidate}")
                ]
                if len(set(candidates)) == 1:
                    return candidates[0]
            return ""

        # One symbol per PATH, not per decorator, matching how the Django adapter treats a
        # route that answers several methods. Five decorators over two paths is the normal
        # CRUD shape, so counting decorators would inflate the route count; the methods are
        # aggregated into `sub` instead of the first one silently standing for all of them.
        symbols: list[Symbol] = []
        edges: list[Edge] = []
        seen_views: set[str] = set()
        routes: dict[str, dict] = {}
        for module in modules:
            relative = _relative(module.path, repo_root)
            for owner, method, path, function, line in module.routes:
                owner_key = (module.dotted, owner)
                mount = prefix_for(owner_key, module.key(owner))
                full = _join(mount, module.router_prefixes.get(owner, ""), path)
                # A router nothing mounts has no path yet. Dispatch loads its Slack plugin
                # through a runtime registry, so `/slack/command` is real and its prefix is
                # genuinely unknowable from source. Reporting the bare path as fact would
                # be a claim with no evidence - and would make every call to it look broken.
                unmounted = (
                    owner in module.router_prefixes
                    and owner not in module.app_roots
                    and owner_key not in parents
                    and module.key(owner) not in parents
                    and not any(
                        candidate.endswith(f".{module.dotted}")
                        or module.dotted.endswith(f".{candidate}")
                        for candidate, _ in by_variable.get(owner, [])
                    )
                )
                url_id = f"url:{full}"
                view_id = f"view:{_module_name(module.path)}.{function}"
                route = routes.setdefault(url_id, {
                    "label": full, "methods": [], "file": relative, "line": line,
                    "owner": owner, "path": path, "function": function,
                    "unmounted": unmounted,
                })
                if method not in route["methods"]:
                    route["methods"].append(method)
                if view_id not in seen_views:
                    seen_views.add(view_id)
                    symbols.append(Symbol(
                        id=view_id, kind="view", label=function,
                        sub=f"{relative}:{line}", file=relative, line=line,
                        status=Status.CONNECTED, snippet=f"def {function}(...): ...",
                        chain=[function], note="",
                    ))
                edge = Edge(from_id=url_id, to_id=view_id, status=Status.CONNECTED)
                if edge not in edges:
                    edges.append(edge)

        for url_id, route in routes.items():
            symbols.append(Symbol(
                id=url_id, kind="url", label=route["label"],
                sub="/".join(sorted(route["methods"])),
                file=route["file"], line=route["line"],
                status=Status.UNCERTAIN if route["unmounted"] else Status.CONNECTED,
                snippet=f'@{route["owner"]}.{route["methods"][0].lower()}("{route["path"]}")',
                chain=[route["label"], route["function"]],
                note=_UNMOUNTED_NOTE if route["unmounted"] else "",
            ))

        # FastAPI has no reverse() and no {% url %}: a route has no name to resolve, so the
        # name index is empty by nature rather than by omission.
        return ServerScan(symbols=symbols, edges=edges, route_names={})


def _python_files(repo_root: str, limit: int | None = None) -> list[str]:
    found: list[str] = []
    for path in sorted(pathlib.Path(repo_root).rglob("*.py")):
        if any(part in _SKIP or part.startswith(".") for part in path.parts):
            continue
        found.append(str(path))
        if limit and len(found) >= limit:
            break
    return found


def _relative(path: str, repo_root: str) -> str:
    try:
        return str(pathlib.Path(path).relative_to(pathlib.Path(repo_root).resolve()))
    except ValueError:
        try:
            return str(pathlib.Path(path).relative_to(repo_root))
        except ValueError:
            return path


def _dotted(relative: str) -> str:
    return relative.replace("/", ".").removesuffix(".py")


def _module_name(path: str) -> str:
    """The name this file is imported UNDER, which is not its path from the repo root.

    A `src/` layout - `src/dispatch/case/views.py` imported as `dispatch.case.views` - is
    the standard packaging layout and it broke mount resolution on a real 717-file app:
    every router was keyed `src.dispatch...` while every import said `dispatch...`, so no
    prefix ever matched and 112 routes came out at the wrong paths. Silent, because the
    routes still appeared.

    Walk up while `__init__.py` is there; the last package directory's parent is the root.
    """
    file = pathlib.Path(path).resolve()
    parts = [file.stem]
    directory = file.parent
    while (directory / "__init__.py").is_file():
        parts.append(directory.name)
        directory = directory.parent
    return ".".join(reversed(parts))
