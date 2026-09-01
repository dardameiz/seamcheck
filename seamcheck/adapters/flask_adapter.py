"""Flask: decorators again, with blueprints instead of routers.

Third Python framework and the last of the big three. The shape is the FastAPI one - a
decorator carries the path, something above it carries a prefix - but Flask spells every
part differently and each difference silently moves a route if it is missed:

    @app.route('/users/<int:id>', methods=['GET', 'POST'])   the path AND its verbs
    @app.get('/x')                                           the Flask 2 shortcut
    bp = Blueprint('api', __name__, url_prefix='/api')       a prefix on the blueprint
    app.register_blueprint(bp, url_prefix='/v1')             and another where it mounts
    app.add_url_rule('/thing', view_func=Thing.as_view())    class-based, no decorator

`methods=` matters more here than anywhere else: Flask defaults to GET only, so a route
declared without it answers GET and nothing else - and a POST to it is a 405, not a 404,
which is the kind of difference a reader needs the tool to have got right.
"""

from __future__ import annotations

import ast
import os
import pathlib
import re

from seamcheck.adapters.base import ServerScan
from seamcheck.adapters.discovery import SKIP_DIRS
from seamcheck.graph import Edge, Status, Symbol
from seamcheck.nodetools import report

# A real import statement and a real route decorator, each ANCHORED to the start of its
# own line rather than found anywhere inside one. As bare substrings both appear in this
# very file - it is the reader for Flask, so it necessarily quotes them - and seamcheck
# detected ITSELF as a Flask app at 0.9 confidence.
_IMPORT_RE = re.compile(r"^\s*(?:from\s+flask(?:\.\w+)*\s+import|import\s+flask)\b", re.M)
_ROUTE_RE = re.compile(
    r"^\s*@\s*\w+(?:\.\w+)*\.(?:route|get|post|put|patch|delete)\s*\(|"
    r"^\s*\w+\s*=\s*Blueprint\s*\(",
    re.M,
)

_SKIP = SKIP_DIRS
_SHORTCUTS = ("get", "post", "put", "delete", "patch", "options", "head")

_UNMOUNTED_NOTE = (
    "Declared on a Blueprint that nothing in the source registers, so the path shown is "
    "only what the decorator says. The prefix it actually serves under is decided at "
    "runtime and no reader of source can know it. Do not read a call to it as unresolved."
)


def _string(node: ast.AST | None) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _keyword(call: ast.Call, name: str) -> ast.AST | None:
    for keyword in call.keywords:
        if keyword.arg == name:
            return keyword.value
    return None


def _methods(call: ast.Call) -> list[str]:
    """The verbs a rule answers. Flask defaults to GET alone, which is load-bearing."""
    node = _keyword(call, "methods")
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        found = [_string(item) for item in node.elts]
        verbs = sorted({v.upper() for v in found if v})
        if verbs:
            return verbs
    return ["GET"]


def _join(*parts: str) -> str:
    joined = "/".join(part.strip("/") for part in parts if part and part.strip("/"))
    return f"/{joined}" if joined else "/"


def _line(node: ast.AST) -> int | None:
    return getattr(node, "lineno", None)


def _files(repo_root: str, limit: int | None = None) -> list[str]:
    base = pathlib.Path(repo_root)
    found: list[str] = []
    for directory, subdirectories, names in os.walk(base):
        subdirectories[:] = [d for d in subdirectories
                             if d not in _SKIP and not d.startswith(".")]
        for name in sorted(names):
            if name.endswith(".py") and not name.startswith("test_"):
                found.append(os.path.join(directory, name))
                if limit and len(found) >= limit:
                    return found
    return found


class _Module:
    def __init__(self, path: str, tree: ast.Module, dotted: str) -> None:
        self.path = path
        self.tree = tree
        self.dotted = dotted
        self.aliases: dict[str, tuple[str, str]] = {}
        self.own_prefix: dict[str, str] = {}    # blueprint variable -> its url_prefix
        self.apps: set[str] = set()             # variables holding a Flask() app
        self.blueprints: set[str] = set()
        self.routes: list[tuple[str, list[str], str, str, int]] = []
        self.mounts: list[tuple[tuple[str, str], str]] = []

    def read(self) -> None:
        for node in ast.walk(self.tree):
            if isinstance(node, ast.ImportFrom):
                self._read_import(node)
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Assign):
                self._read_assignment(node)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self._read_decorators(node)
            elif isinstance(node, ast.Call):
                self._read_register(node)
                self._read_add_url_rule(node)

    def key(self, name: str) -> tuple[str, str]:
        return self.aliases.get(name, (self.dotted, name))

    def _read_import(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        if node.level:
            package = self.dotted.rsplit(".", node.level)[0] if "." in self.dotted else ""
            module = f"{package}.{module}".strip(".") if module else package
        for alias in node.names:
            self.aliases[alias.asname or alias.name] = (module, alias.name)

    def _read_assignment(self, node: ast.Assign) -> None:
        call = node.value
        if not isinstance(call, ast.Call):
            return
        name = getattr(call.func, "id", None) or getattr(call.func, "attr", None)
        if name not in ("Flask", "Blueprint"):
            return
        prefix = _string(_keyword(call, "url_prefix")) or ""
        for target in node.targets:
            if isinstance(target, ast.Name):
                self.own_prefix[target.id] = prefix
                (self.apps if name == "Flask" else self.blueprints).add(target.id)

    def _read_decorators(self, node) -> None:
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue
            attribute = decorator.func
            if not isinstance(attribute, ast.Attribute):
                continue
            owner = getattr(attribute.value, "id", None)
            if owner is None:
                continue
            if attribute.attr == "route":
                path = _string(decorator.args[0]) if decorator.args else None
                if path is not None:
                    self.routes.append(
                        (owner, _methods(decorator), path, node.name, _line(node) or 1))
            elif attribute.attr in _SHORTCUTS:
                path = _string(decorator.args[0]) if decorator.args else None
                if path is not None:
                    self.routes.append(
                        (owner, [attribute.attr.upper()], path, node.name, _line(node) or 1))

    def _read_register(self, node: ast.Call) -> None:
        if getattr(node.func, "attr", None) != "register_blueprint" or not node.args:
            return
        child = getattr(node.args[0], "id", None) or getattr(node.args[0], "attr", None)
        if child:
            self.mounts.append((self.key(child), _string(_keyword(node, "url_prefix")) or ""))

    def _read_add_url_rule(self, node: ast.Call) -> None:
        """`app.add_url_rule('/thing', view_func=Thing.as_view('thing'))` - no decorator.

        The class-based route, which Flask-RESTful and MethodView both produce. Reading
        only decorators misses every one of them.
        """
        if getattr(node.func, "attr", None) != "add_url_rule" or not node.args:
            return
        owner = getattr(node.func.value, "id", None)
        path = _string(node.args[0])
        if owner is None or path is None:
            return
        view = _keyword(node, "view_func")
        name = "view"
        if isinstance(view, ast.Call):
            name = getattr(view.func, "attr", None) or getattr(view.func, "id", None) or name
            inner = getattr(view.func, "value", None)
            name = getattr(inner, "id", None) or name
        elif isinstance(view, ast.Name):
            name = view.id
        self.routes.append((owner, _methods(node), path, name, _line(node) or 1))


class FlaskAdapter:
    name = "flask"

    def detect(self, repo_root: str, config: dict) -> float:
        imports = routes = False
        for path in _files(repo_root, limit=400):
            try:
                text = pathlib.Path(path).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if "flask" not in text.lower():
                continue
            # ANCHORED to the start of a line. As a bare substring, `"from flask import"`
            # and `".route("` both appear in this very file - it is the reader for Flask,
            # so it necessarily quotes them - and seamcheck detected ITSELF as a Flask app
            # at 0.9 confidence, then reported every fixture's fetch call as unresolved.
            # Any repository that documents, vendors or lints these patterns hits the same
            # thing.
            if _IMPORT_RE.search(text):
                imports = True
            if _ROUTE_RE.search(text):
                routes = True
            if imports and routes:
                return 0.9
        return 0.5 if imports else 0.0

    def scan(self, repo_root: str, config: dict, progress) -> ServerScan:
        progress.step("URLs and views")
        modules: list[_Module] = []
        for path in _files(repo_root):
            try:
                tree = ast.parse(
                    pathlib.Path(path).read_text(encoding="utf-8", errors="replace"),
                    filename=path)
            except (OSError, SyntaxError, ValueError):
                continue
            module = _Module(path, tree, _module_name(path))
            module.read()
            if module.routes or module.mounts:
                modules.append(module)

        mounted: dict[tuple[str, str], str] = {}
        for module in modules:
            for key, prefix in module.mounts:
                if prefix:
                    mounted[key] = _join(mounted.get(key, ""), prefix)

        by_variable: dict[str, list[tuple[str, str]]] = {}
        for (dotted, variable), prefix in mounted.items():
            by_variable.setdefault(variable, []).append((dotted, prefix))

        def prefix_for(module: _Module, owner: str) -> tuple[str, bool]:
            for key in ((module.dotted, owner), module.key(owner)):
                if key in mounted:
                    return mounted[key], True
            # Same suffix rule as the other Python adapters: a package without __init__.py
            # gives a file no importable prefix, so exact equality is not enough.
            candidates = [
                prefix for candidate, prefix in by_variable.get(owner, [])
                if candidate.endswith(f".{module.dotted}") or module.dotted.endswith(f".{candidate}")
            ]
            if len(set(candidates)) == 1:
                return candidates[0], True
            return "", False

        symbols: list[Symbol] = []
        edges: list[Edge] = []
        seen_views: set[str] = set()
        routes: dict[str, dict] = {}
        for module in modules:
            relative = os.path.relpath(module.path, repo_root)
            for owner, methods, path, function, line in module.routes:
                mount, registered = prefix_for(module, owner)
                full = _join(mount, module.own_prefix.get(owner, ""), path)
                unmounted = owner in module.blueprints and not registered
                url_id = f"url:{full}"
                view_id = f"view:{module.dotted}.{function}"
                route = routes.setdefault(url_id, {
                    "label": full, "methods": [], "file": relative, "line": line,
                    "owner": owner, "path": path, "unmounted": unmounted,
                })
                for method in methods:
                    if method not in route["methods"]:
                        route["methods"].append(method)
                if view_id not in seen_views:
                    seen_views.add(view_id)
                    symbols.append(Symbol(
                        id=view_id, kind="view", label=function, sub=f"{relative}:{line}",
                        file=relative, line=line, status=Status.CONNECTED,
                        snippet=f"@{owner}.route('{path}')", chain=[full, function], note=""))
                edge = Edge(from_id=url_id, to_id=view_id, status=Status.CONNECTED)
                if edge not in edges:
                    edges.append(edge)

        for url_id, route in routes.items():
            symbols.append(Symbol(
                id=url_id, kind="url", label=route["label"],
                sub="/".join(sorted(route["methods"])), file=route["file"], line=route["line"],
                status=Status.UNCERTAIN if route["unmounted"] else Status.CONNECTED,
                snippet=f"@{route['owner']}.route('{route['path']}')",
                chain=[route["label"]], note=_UNMOUNTED_NOTE if route["unmounted"] else ""))

        # A near-empty result on a codebase that clearly uses Flask means the project
        # registers routes through its own helper. flaskbb wraps every one in
        # `register_view(blueprint, routes=[...], view_func=...)`, and redash routes
        # through Flask-RESTful resources. Supporting either would be overfitting to one
        # project; saying the list is short and why is the honest answer.
        if len(_files(repo_root)) > 40 and len(routes) < 10:
            report(
                "flask-few-routes",
                "only %s route(s) found across %s Python files. This project probably "
                "registers routes through its own helper or an extension rather than "
                "@app.route(), which this reader does not follow. Treat the route list "
                "as incomplete.",
                len(routes), len(_files(repo_root)),
            )

        # Flask HAS names - url_for('users') - and they are the endpoint function's name,
        # blueprint-qualified. Worth mapping: {% url %}-style references resolve through it.
        names: dict[str, str] = {}
        for module in modules:
            for owner, _, path, function, _ in module.routes:
                mount, _ = prefix_for(module, owner)
                full = _join(mount, module.own_prefix.get(owner, ""), path)
                names[function] = full
                if owner in module.blueprints:
                    names[f"{owner}.{function}"] = full
        return ServerScan(symbols=symbols, edges=edges, route_names=names)


def _module_name(path: str) -> str:
    file = pathlib.Path(path).resolve()
    parts = [file.stem]
    directory = file.parent
    while (directory / "__init__.py").is_file():
        parts.append(directory.name)
        directory = directory.parent
    return ".".join(reversed(parts))
