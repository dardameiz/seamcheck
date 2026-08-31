"""The URLconf, read as text instead of imported.

`extract_django_urls_views` asks Django to resolve the URLconf, which is exact and requires
the project to *run*: settings on the environment, every app importable, every dependency
installed, every view's module free of import errors. That is fine inside a project you own
and fatal everywhere else - a cloned repository has none of it, which is why this tool could
only ever scan the one machine it was written on.

So this reads `urls.py` with `ast`. No import, no settings, no dependencies. It cannot be as
exact as asking Django - it says so below - but it turns "cannot scan this project at all"
into "can scan this project, with these limits named".

**What it handles**, because a real URLconf uses all of it:

* `urlpatterns = [...]`, `urlpatterns += [...]`, and both inside an `if` block
* wrapper calls - `i18n_patterns(...)`, `decorator_include(...)` - by reading their arguments
* `include("dotted.module")`, followed into that module's own file, with the prefix carried
* `include(module.urlpatterns)`, resolved through the file's imports
* views written as a bare name, an attribute, a `.as_view()` call, or a string

**What it cannot do**, stated rather than hidden:

* A pattern list built by a loop, a comprehension or a function call is invisible. Django
  would have resolved it; text cannot.
* A router's generated routes (DRF `router.urls`) are not expanded.
* `include()` of a third-party package is followed only if its source is inside the repo.
"""

from __future__ import annotations

import ast
import pathlib

from seamcheck.graph import Edge, Status, Symbol
from seamcheck.nodetools import report

_PATH_CALLS = frozenset({"path", "re_path", "url"})
_INCLUDE_CALLS = frozenset({"include"})
# Calls that WRAP a list of patterns rather than declaring one. Their arguments are patterns.
_WRAPPER_CALLS = frozenset({"i18n_patterns", "decorator_include", "format_suffix_patterns"})


def _call_name(node: ast.AST) -> str | None:
    """`path`, `views.thing` -> `thing`, `Thing.as_view()` -> `Thing`."""
    if isinstance(node, ast.Call):
        return _call_name(node.func)
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _literal(node: ast.AST) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


_VENDORED = {"node_modules", ".git", "site-packages", "__pycache__", ".venv", "venv",
             "build", "dist", "migrations", ".tox"}


def module_file(repo_root: pathlib.Path, dotted: str) -> pathlib.Path | None:
    """`pointless.api_urls` -> the file, without importing anything.

    A dotted name is a package path OR a module path, and the same name can be either, so
    both are tried. Nothing outside the repo is resolved: a third-party include is somebody
    else's routing table and following it would report their code as this project's.
    """
    parts = dotted.split(".")
    # The dotted name is relative to whatever is on sys.path, which is not necessarily the
    # repository root. A src/ layout is the standard packaging layout, and the largest
    # Django project in the corpus uses it: ROOT_URLCONF is "sentry.conf.urls" and the file
    # is src/sentry/conf/urls.py. Looking only at the root read ZERO routes from 1.7M lines.
    for prefix in ((), ("src",), ("lib",), ("app",), ("backend",), ("server",)):
        base = repo_root.joinpath(*prefix)
        for candidate in (
            base.joinpath(*parts).with_suffix(".py"),
            base.joinpath(*parts, "__init__.py"),
        ):
            if candidate.is_file():
                return candidate
    return None


def _parse(path: pathlib.Path) -> ast.Module | None:
    try:
        return ast.parse(path.read_text(encoding="utf-8", errors="replace"), filename=str(path))
    except (OSError, SyntaxError):
        return None


def _import_map(tree: ast.Module, package: str = "") -> dict[str, str]:
    """Local name -> the dotted module it came from.

    `package` is the dotted package the file lives in, needed because a URLconf almost always
    imports its views RELATIVELY: `from . import views` inside `pointless/api_urls.py` means
    `pointless.views`. Skipping relative imports resolved `views` to the bare string "views",
    which matches no file - 230 of one project's 231 routes lost their view that way, and the
    scan reported 3% of the URLs it should have.

    `from pointless.views import seo_views` makes `seo_views` mean `pointless.views.seo_views`;
    `from pointless.views.seo_views import robots_txt` makes `robots_txt` mean that module. The
    value is always the FULL dotted path to the imported object, and the caller strips the last
    segment when it wants the module - which is what tells a `views.thing` reference apart from
    a bare `thing`.
    """
    found: dict[str, str] = {}
    segments = package.split(".") if package else []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level:
                # `.` is this package, `..` its parent, and so on.
                base = ".".join(segments[: len(segments) - (node.level - 1)] or segments)
                origin = f"{base}.{node.module}" if node.module else base
            else:
                origin = node.module or ""
            if not origin:
                continue
            for alias in node.names:
                found[alias.asname or alias.name] = f"{origin}.{alias.name}"
        elif isinstance(node, ast.Import):
            for alias in node.names:
                found[alias.asname or alias.name.split(".")[0]] = alias.name
    return found


def _pattern_calls(tree: ast.Module) -> list[ast.Call]:
    """Every `path()`/`include()` call reachable from a `urlpatterns` binding.

    Walks assignments AND augmented assignments, at any nesting - a URLconf routinely adds
    routes inside `if settings.DEBUG:`, and reading only the top-level assignment silently
    dropped them.
    """
    found: list[ast.Call] = []
    # A large URLconf factors repeated route sets into a helper and splats the result:
    # `urlpatterns = [*create_group_urls("sentry-api-0-group"), ...]`. The path() calls are
    # then in the helper's body, not in the assignment, and reading only the assignment
    # found 123 of Sentry's routes instead of its routing table. Following a helper defined
    # in the SAME module is bounded and needs no imports.
    helpers = {
        node.name: node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    }
    entered: set[str] = set()

    def _collect(node: ast.AST) -> None:
        if isinstance(node, ast.Call):
            name = _call_name(node.func)
            if name in _PATH_CALLS:
                found.append(node)
                return
            if name in _WRAPPER_CALLS or name in _INCLUDE_CALLS:
                for argument in node.args:
                    _collect(argument)
                return
            # A helper that BUILDS routes. Entered once: recursion here would be a hang on
            # somebody else's repository, which is worse than missing a route.
            helper = helpers.get(name) if name else None
            if helper is not None and name not in entered:
                entered.add(name)
                for statement in helper.body:
                    _collect(statement)
                return
        for child in ast.iter_child_nodes(node):
            _collect(child)

    for node in ast.walk(tree):
        targets = (
            node.targets if isinstance(node, ast.Assign)
            else [node.target] if isinstance(node, ast.AugAssign)
            else []
        )
        if any(isinstance(t, ast.Name) and t.id == "urlpatterns" for t in targets):
            _collect(node.value)
    return found


def _attribute_chain(node: ast.AST) -> list[str] | None:
    """`views.announcement_views.mark_read` -> ["views", "announcement_views", "mark_read"].

    Chains are routinely more than one deep - a project with a views PACKAGE writes exactly
    this - and reading only `owner.attr` lost every route in such a file: 136 on the project
    measured, all of them in one module.
    """
    if isinstance(node, ast.Name):
        return [node.id]
    if isinstance(node, ast.Attribute):
        head = _attribute_chain(node.value)
        return [*head, node.attr] if head else None
    return None


def _view_reference(node: ast.AST, imports: dict[str, str]) -> tuple[str, str] | None:
    """(view name, the dotted module it lives in) for a view expression, or None."""
    if isinstance(node, ast.Call):  # Thing.as_view(...)
        return _view_reference(node.func, imports)
    if isinstance(node, ast.Attribute) and node.attr == "as_view":
        return _view_reference(node.value, imports)

    chain = _attribute_chain(node)
    if chain:
        name = chain[-1]
        # The first segment is a local name; the import map says what it really is. Any
        # middle segments are sub-modules hanging off it.
        head = imports.get(chain[0], chain[0])
        middle = chain[1:-1]
        if len(chain) == 1:
            # A bare name: the import map holds the full dotted path TO the view itself, so
            # the module is everything before the last segment.
            return name, head.rsplit(".", 1)[0] if "." in head else ""
        return name, ".".join([head, *middle])

    literal = _literal(node)
    if literal and "." in literal:  # the old string form: "app.views.thing"
        module, _, name = literal.rpartition(".")
        return name, module
    return None


def _definition_line(path: pathlib.Path, name: str) -> int | None:
    tree = _parse(path)
    if tree is None:
        return None
    for node in ast.walk(tree):
        if (isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
                and node.name == name):
            return node.lineno
    return None


def extract_urls_views_static(
    repo_root: str,
    urlconf_module: str,
    first_party_prefixes: list[str] | None = None,
) -> tuple[list[Symbol], list[Edge], dict[str, str]]:
    """(symbols, edges, route name -> path) from the URLconf's source text."""
    root = pathlib.Path(repo_root).resolve()
    symbols: list[Symbol] = []
    edges: list[Edge] = []
    names: dict[str, str] = {}
    seen_views: set[str] = set()
    visited: set[str] = set()

    def _walk_module(dotted: str, prefix: str) -> None:
        # A URLconf can include itself through a cycle; a real one on this project does not,
        # but a scan that hangs on a stranger's repository is worse than one that stops.
        if dotted in visited:
            return
        visited.add(dotted)
        path = module_file(root, dotted)
        if path is None:
            return
        tree = _parse(path)
        if tree is None:
            return
        # The package this file lives in, for resolving its relative imports.
        imports = _import_map(tree, dotted.rpartition(".")[0])

        # A URLconf that only re-exports someone else's patterns - `from sentry.web.urls
        # import urlpatterns` is the whole of Sentry's ROOT_URLCONF - has no path() call of
        # its own. Following the import is the difference between 1.7M lines of Django
        # reading as zero routes and reading as its actual routing table.
        calls = _pattern_calls(tree)
        if not calls:
            for node in ast.walk(tree):
                if not isinstance(node, ast.ImportFrom) or not node.module:
                    continue
                if not any(alias.name == "urlpatterns" for alias in node.names):
                    continue
                source = node.module
                if node.level:
                    package = dotted.rsplit(".", node.level)[0] if "." in dotted else ""
                    source = f"{package}.{source}".strip(".") if source else package
                _walk_module(source, prefix)
            return

        for call in calls:
            route = _literal(call.args[0]) if call.args else None
            if route is None:
                continue
            full = prefix + route
            second = call.args[1] if len(call.args) > 1 else None

            if isinstance(second, ast.Call) and _call_name(second.func) in _INCLUDE_CALLS:
                target = _literal(second.args[0]) if second.args else None
                if target is None and second.args:
                    # include(module.urlpatterns) - the module is in the import map.
                    owner = second.args[0]
                    base = (
                        owner.value.id
                        if isinstance(owner, ast.Attribute) and isinstance(owner.value, ast.Name)
                        else None
                    )
                    target = imports.get(base or "", None)
                if target:
                    _walk_module(target, full)
                continue

            route_name = next(
                (_literal(kw.value) for kw in call.keywords if kw.arg == "name"), None
            )
            if route_name:
                names.setdefault(route_name, full)

            reference = _view_reference(second, imports) if second is not None else None
            if reference is None:
                continue
            view_name, view_module = reference
            if first_party_prefixes and view_module and (
                view_module.split(".")[0] not in first_party_prefixes
            ):
                continue

            view_path = module_file(root, view_module) if view_module else None
            if first_party_prefixes and view_path is None:
                # Not a module in this repo, so not this project's view.
                continue
            line = _definition_line(view_path, view_name) if view_path else None
            source = str(view_path.relative_to(root)) if view_path else ""

            url_id = f"url:{full}"
            view_id = f"view:{view_module}.{view_name}" if view_module else f"view:{view_name}"
            symbols.append(Symbol(
                id=url_id, kind="url", label=full, sub="GET/POST", file=source, line=None,
                status=Status.CONNECTED, snippet=f'path("{full}", {view_name})',
                chain=[full, view_name], note="",
            ))
            if view_id not in seen_views:
                seen_views.add(view_id)
                symbols.append(Symbol(
                    id=view_id, kind="view", label=view_name,
                    sub=f"{source}:{line}" if line else source, file=source, line=line,
                    status=Status.CONNECTED, snippet=f"def {view_name}(request): ...",
                    chain=[view_name], note="",
                ))
            edges.append(Edge(from_id=url_id, to_id=view_id, status=Status.CONNECTED))

    _walk_module(urlconf_module, "")

    # How much of the routing table this actually reached. A big project assembles
    # urlpatterns from lists held in other modules - `*workflow_urls.organization_urlpatterns`
    # - and following that is dataflow analysis, not text reading. Sentry has 22 urls.py
    # files and this reaches a handful, so presenting the result as the routing table would
    # be a confident wrong answer about 1.7M lines of Django.
    on_disk = {
        path for path in root.rglob("urls.py")
        if not any(part in _VENDORED for part in path.parts)
    }
    reached = {module_file(root, dotted) for dotted in visited}
    missed = len(on_disk - {path for path in reached if path is not None})
    if missed and len(on_disk) > 2 and missed >= len(on_disk) / 2:
        report(
            "django-partial-urlconf",
            "read %s of %s urls.py files: the rest are not reachable from ROOT_URLCONF by "
            "reading text, usually because urlpatterns is assembled from lists held in "
            "other modules. Routes defined there are NOT in this graph, and a call to one "
            "of them will look unresolved.",
            len(on_disk) - missed, len(on_disk),
        )
    return symbols, edges, names
