"""Every way a project points at one of its own routes, other than fetch().

Seamcheck could only see one: a `fetch()` from JavaScript. So on a real project **77% of
routes and 73% of views were `uncertain`** - not "possibly dead", but *unmeasured*, with the
classifier itself carrying a note admitting there was no extractor for the rest. Uncertain
at that scale is not caution, it is the tool declining to do its job.

A Django project references its routes four other ways, and all four are static text:

* `{% url 'name' %}` in a template - by far the most common
* `reverse('name')` / `reverse_lazy('name')` / `redirect('name')` in Python
* `<a href="/path">`, `<form action="/path">` in a template
* `hx-get` / `hx-post` and friends, for an HTMX project

Names and paths are both handled, because a project uses both. A name goes through the
URLconf's own name index; a literal path goes through the same route resolver a fetch()
call does, converters included.

What this still cannot see is stated rather than hidden: a URL built by string concatenation,
one typed by a person, one held by an external service, and one a mobile client hard-codes.
That list is what keeps a route with no reference honest - it becomes a finding with those
possibilities named, not a verdict.
"""

from __future__ import annotations

import ast
import pathlib
import re

from seamcheck.graph import Edge, Status, Symbol

# {% url 'name' %} and {% url "name" arg %}. The tag may span lines in a formatted template.
_URL_TAG_RE = re.compile(r"\{%\s*url\s+(['\"])([^'\"]+)\1", re.S)
# The Python side is read with `ast`, not a regex. Two false positives on the first run
# came straight out of that choice: a regex matched `reverse('admin:...')` inside a code
# COMMENT explaining how namespaces work, and it could not see that another call sat inside
# a `try/except NoReverseMatch`. A parser gets comments, docstrings and structure for free.
_REVERSE_CALLS = frozenset({"reverse", "reverse_lazy", "redirect"})
_OPTIONAL_EXCEPTIONS = frozenset({"NoReverseMatch", "Resolver404", "Exception"})
# A literal first-party path in markup. Anchors, forms, and HTMX's request attributes.
_LITERAL_PATH_RE = re.compile(
    r"""(?:href|action|hx-(?:get|post|put|patch|delete))\s*=\s*(['"])(/[^'"#?\s]*)\1""",
    re.I,
)

# What still is not read, said out loud wherever a route turns out to have no reference.
UNREFERENCED_NOTE = (
    "No {% url %} tag, reverse(), redirect(), <a href>, form action, HTMX attribute or "
    "fetch() call points at this route. Still possible: a path built by string "
    "concatenation, one typed by a person, or one held by an external service or a mobile "
    "client."
)

_KIND = "url_reference"


def _line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _read(paths: list[str]) -> list[tuple[str, str]]:
    out = []
    for path in paths:
        try:
            out.append((path, pathlib.Path(path).read_text(encoding="utf-8", errors="replace")))
        except OSError:
            continue
    return out


def _reference(
    handle: str, sub: str, file_path: str, line: int, snippet: str
) -> Symbol:
    return Symbol(
        # Keyed by handle AND site: the same route referenced from twelve templates is
        # twelve pieces of evidence, and collapsing them loses eleven of the places a
        # reader might need to go.
        id=f"{_KIND}:{sub}:{handle}:{file_path}:{line}",
        kind=_KIND,
        label=handle,
        sub=sub,
        file=file_path,
        line=line,
        status=Status.UNCERTAIN,
        snippet=snippet,
        chain=[pathlib.Path(file_path).name, handle],
        note="",
    )


def _optional_call_lines(tree: ast.AST) -> set[int]:
    """Line numbers inside a `try` that catches a reverse failure on purpose.

        try:
            login_url = reverse("sandbox_login")
        except NoReverseMatch:
            login_url = "/sandbox-login/"

    The author has already said this route may not exist and written the fallback. Calling
    that a broken reference is telling someone their own error handling is a bug.
    """
    optional: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        caught = set()
        for handler in node.handlers:
            names = handler.type
            for candidate in (names.elts if isinstance(names, ast.Tuple) else [names]):
                if isinstance(candidate, ast.Name):
                    caught.add(candidate.id)
                elif isinstance(candidate, ast.Attribute):
                    caught.add(candidate.attr)
        if not caught & _OPTIONAL_EXCEPTIONS:
            continue
        for statement in node.body:
            for inner in ast.walk(statement):
                if getattr(inner, "lineno", None):
                    optional.add(inner.lineno)
    return optional


def _python_route_references(source: str):
    """(call name, route handle, line) for every reverse/reverse_lazy/redirect literal."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return
    optional = _optional_call_lines(tree)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
        if name not in _REVERSE_CALLS or node.lineno in optional:
            continue
        first = node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            yield name, first.value, node.lineno


def extract_url_references(
    template_files: list[str],
    python_files: list[str],
    route_names: dict[str, str],
    url_symbols: list[Symbol],
) -> tuple[list[Symbol], list[Edge]]:
    """(reference symbols, edges to the routes they reach).

    A reference that resolves is CONNECTED evidence for its route. One that does not is
    UNRESOLVED on its own account - `{% url 'gone' %}` raises NoReverseMatch at render
    time, which is a 500 on a page, and is worth more than most findings this tool makes.
    """
    from seamcheck.matcher import UrlIndex

    index = UrlIndex([s for s in url_symbols if s.kind == "url"])
    symbols: list[Symbol] = []
    edges: list[Edge] = []
    seen: set[str] = set()

    def _add(symbol: Symbol, target: Symbol | None, name_exists: bool = True) -> None:
        if symbol.id in seen:
            return
        seen.add(symbol.id)
        if target is None and name_exists:
            # A valid reference to a route outside the scanned set - a third-party app's.
            # Nothing to claim in either direction, and nothing for a reader to do, so it
            # is not carried into the graph at all.
            return
        symbols.append(symbol)
        if target is not None:
            edges.append(Edge(from_id=symbol.id, to_id=target.id, status=Status.CONNECTED))
        else:
            edges.append(Edge(from_id=symbol.id, to_id=symbol.id, status=Status.UNRESOLVED))

    def _by_name(handle: str) -> tuple[Symbol | None, bool]:
        """(the route symbol, whether the name exists at all).

        Two different answers, and conflating them cost 51 false positives on the first
        run. `route_names` covers EVERY route including third-party ones, while the graph
        holds only first-party routes - so `{% url 'account_login' %}` pointing at a real
        django-allauth route resolved to a path that was not in the graph, and got reported
        as a name that resolves to nothing. It resolves fine; the target is simply out of
        the scan's scope, which is not the template's problem.

        Only a name in NO route anywhere is NoReverseMatch.
        """
        path = route_names.get(handle) or route_names.get(handle.rsplit(":", 1)[-1])
        if path is None:
            return None, False
        return index.resolve(path), True

    for file_path, text in _read(template_files):
        for match in _URL_TAG_RE.finditer(text):
            name = match.group(2)
            target, exists = _by_name(name)
            _add(
                _reference(name, "url_tag", file_path, _line_of(text, match.start()),
                           f"{{% url '{name}' %}}"),
                target, exists,
            )
        for match in _LITERAL_PATH_RE.finditer(text):
            path = match.group(2)
            resolved = index.resolve(path)
            # A link to a path no route serves is usually a link to something the project
            # does not serve at all - a CDN asset, an external redirect, a placeholder -
            # so an unresolved literal link claims nothing.
            if resolved is None:
                continue
            _add(
                _reference(path, "link", file_path, _line_of(text, match.start()),
                           match.group(0)[:80]),
                resolved,
            )

    for file_path, text in _read(python_files):
        for call, handle, line in _python_route_references(text):
            if handle.startswith("/"):
                resolved = index.resolve(handle)
                if resolved is None:
                    continue
                _add(_reference(handle, "link", file_path, line, f"{call}('{handle}')"), resolved)
                continue
            target, exists = _by_name(handle)
            # redirect() also accepts a model instance or a template name, so a string that
            # matches no route name at all is not evidence of a broken reference.
            if not exists and call == "redirect":
                continue
            _add(_reference(handle, call, file_path, line, f"{call}('{handle}')"), target, exists)

    return symbols, edges
