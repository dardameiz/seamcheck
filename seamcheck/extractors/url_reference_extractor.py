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
from seamcheck.nodetools import node_line

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

# N1: a template literal that truncates right where a route's required dynamic segment
# begins - `href={`/kaizen/${locale}`}` - names the route by its literal prefix and
# nothing more. That is real evidence the route is reached, but not evidence of which
# COMPLETE value was requested, so it is carried on the reference rather than folded
# silently into the same note-less evidence a full literal match gets.
_PREFIX_MATCH_NOTE = (
    "Only a literal PREFIX of this reference is known - it stops exactly where a "
    "required dynamic route segment begins, and the segment's value (e.g. a locale) is "
    "filled in at runtime. The complete path was never known, only the route it reaches."
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
    handle: str, sub: str, file_path: str, line: int, snippet: str, note: str = ""
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
        note=note,
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


def _python_route_references(source: str, filename: str = "<unknown>"):
    """(call name, route handle, line) for every reverse/reverse_lazy/redirect literal."""
    try:
        tree = ast.parse(source, filename=filename)
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


# ── the JavaScript side of the same question ────────────────────────────────
# A React or Next project points at its own routes with markup and a router, never with a
# `{% url %}` tag, so a reader that knew only Django vocabulary saw none of it. In a
# 32-repository corpus `<Link href>` appears in 169 files and `router.push` in 337.
_JS_EXTENSIONS = (".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts")
# Attributes that carry a route: `href` (Next, plain anchors), `to` (React Router),
# `action` (a form, and a Next server action's target when written as a string).
_NAV_ATTRS = frozenset({"href", "to", "action"})
# Calls that navigate. `redirect` and `permanentRedirect` are Next server-side; `navigate`
# is React Router's hook; `push`/`replace`/`prefetch` are the router object's.
_NAV_METHODS = frozenset({"push", "replace", "prefetch"})
_NAV_FUNCTIONS = frozenset({"redirect", "permanentRedirect", "navigate", "revalidatePath"})
_NAV_RECEIVERS = frozenset({"router", "history", "navigation", "nav", "$router"})
# Cheap text gate before parsing: most files in a repository contain none of this, and
# parsing every one of them is the difference between a scan and a coffee break.
_NAV_NEEDLES = ("href", "to=", "router.", "redirect(", "navigate(", "action=", "location")
# `window.location.assign(...)` / `.replace(...)` and `location.href = "..."` - a full-page
# navigation, deliberately used to bypass the client router (an admin shell mounted
# separately, a hard reload after sign-out, breaking out of an iframe). N2 in
# docs/seamcheck-findings-from-leanos.md: `_NAV_RECEIVERS` only ever matched a bare
# Identifier, so `window.location` - whose own `object` is itself a MemberExpression -
# matched no receiver at all, and the property-assignment form (`location.href = ...`) is
# an AssignmentExpression, a node shape this reader never walked.
_LOCATION_METHODS = frozenset({"assign", "replace"})


def _is_location(node: dict) -> bool:
    """`window.location` or bare `location` - never the client router."""
    if node.get("type") == "Identifier":
        return node.get("name") == "location"
    if node.get("type") == "MemberExpression":
        obj = node.get("object") or {}
        prop = (node.get("property") or {}).get("name")
        return (prop == "location" and obj.get("type") == "Identifier"
                and obj.get("name") == "window")
    return False


def _js_nav_targets(ast: dict) -> list[tuple[str, int, str, bool]]:
    """(path, line, how, exact) for every route this module points at.

    Only STATIC paths, and only ones beginning with `/`. A route assembled from a variable
    is exactly the case the four statuses exist for, and it is left to the fetch reader
    which already records it as a sighting rather than a claim.

    `exact` is `_static_url`'s own flag, carried through rather than discarded: False
    means the literal text is only a known PREFIX (a template literal truncated at an
    interpolation) - not a complete path, so `resolve()` alone cannot be trusted to find
    the route it reaches. `_read_js_references` is where that prefix still gets credited,
    against a parameterised route's own literal lead-in.
    """
    from seamcheck.extractors.js_extractor import _static_url, _walk

    found: list[tuple[str, int, str, bool]] = []

    def _line(node: dict) -> int:
        return node_line(node) or 0

    for node, _ in _walk(ast):
        node_type = node.get("type")

        if node_type == "JSXAttribute":
            name = (node.get("name") or {}).get("name")
            if name not in _NAV_ATTRS:
                continue
            value = node.get("value") or {}
            if value.get("type") == "JSXExpressionContainer":
                value = value.get("expression") or {}
            path, exact = _static_url(value)
            if path and path.startswith("/"):
                found.append((path, _line(node), f'{name}="{path}"', exact))
            continue

        if node_type == "AssignmentExpression":
            left = node.get("left") or {}
            how = ""
            if _is_location(left):
                # `window.location = "/x"` / `location = "/x"` - the whole receiver
                # reassigned, not one of its properties.
                how = "location="
            else:
                prop = (left.get("property") or {}).get("name")
                obj = left.get("object") or {}
                if prop == "href" and _is_location(obj):
                    how = "location.href="
            if how:
                path, exact = _static_url(node.get("right"))
                if path and path.startswith("/"):
                    found.append((path, _line(node), f'{how}"{path}"', exact))
            continue

        if node_type != "CallExpression":
            continue
        callee = node.get("callee") or {}
        arguments = node.get("arguments") or []
        first = arguments[0] if arguments else None
        how = ""
        if callee.get("type") == "Identifier" and callee.get("name") in _NAV_FUNCTIONS:
            how = f"{callee['name']}()"
        elif callee.get("type") == "MemberExpression":
            prop = (callee.get("property") or {}).get("name") or ""
            obj = callee.get("object") or {}
            receiver = obj.get("name") if obj.get("type") == "Identifier" else ""
            if prop in _NAV_METHODS and receiver in _NAV_RECEIVERS:
                how = f"{receiver}.{prop}()"
            elif prop in _LOCATION_METHODS and _is_location(obj):
                how = f"location.{prop}()"
        if not how:
            continue
        path, exact = _static_url(first)
        if path and path.startswith("/"):
            found.append((path, _line(node), f"{how[:-1]}'{path}')", exact))
    return found


def find_js_files(repo_root: str) -> list[str]:
    """Every first-party JavaScript-family file, vendored trees excluded.

    "First-party" is a git question, not a directory-name one: a project keeps one-off
    scripts somewhere it names in `.gitignore` (`OTHER/`, `scratch/`, an archived folder),
    and those are not part of the product any more than `node_modules` is - on the
    reference project a one-off Playwright probe living in one (`OTHER/seo/cards_check
    .mjs`) was read as a first-party writer of a DOM element it never touches in
    production. `gitfiles.tracked_files` answers "does this repo's own git consider this
    file in", honouring nested `.gitignore` files and negation the way a hand-rolled
    parser cannot; `None` on a non-git repo means this filters nothing, unchanged from
    before.
    """
    import os

    from seamcheck import gitfiles
    from seamcheck.adapters.discovery import SKIP_DIRS

    tracked = gitfiles.tracked_files(repo_root)
    found: list[str] = []
    for current, directories, files in os.walk(repo_root):
        directories[:] = [d for d in directories if d not in SKIP_DIRS and not d.startswith(".")]
        for name in files:
            if name.endswith(_JS_EXTENSIONS) and not name.endswith((".min.js", ".d.ts")):
                path = os.path.join(current, name)
                if tracked is not None:
                    relative = os.path.relpath(path, repo_root).replace(os.sep, "/")
                    if relative not in tracked:
                        continue
                found.append(path)
    return found


def extract_url_references(
    template_files: list[str],
    python_files: list[str],
    route_names: dict[str, str],
    url_symbols: list[Symbol],
    js_files: list[str] | None = None,
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
        for call, handle, line in _python_route_references(text, file_path):
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

    _read_js_references(js_files or [], index, _add)
    return symbols, edges


def _read_js_references(js_files: list[str], index, _add) -> None:
    """Navigation in JavaScript, resolved against the same route index as everything else.

    An unresolved path claims NOTHING here, exactly as a template's `<a href>` does: most
    paths in a React app point at a CDN asset, an external site or a route served by a
    different service, and reporting those as broken links would bury the real findings.
    So this pass can only ever ADD evidence that a route is reached - it cannot invent a
    failure, which is what makes it safe to turn on across a whole repository.
    """
    if not js_files:
        return
    from seamcheck.extractors.js_extractor import iter_parsed

    candidates = []
    for path in js_files:
        try:
            text = pathlib.Path(path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        # A bundle is the same code already read from source, and megabytes of it.
        if len(text) > 400_000:
            continue
        if any(needle in text for needle in _NAV_NEEDLES):
            candidates.append(path)

    for path, parsed in iter_parsed(candidates):
        for target_path, line, snippet, exact in _js_nav_targets(parsed):
            resolved = index.resolve(target_path)
            note = ""
            if resolved is None and not exact:
                # The complete path was never known - only ask the weaker question a
                # prefix can still answer: which route does this literal lead-in belong
                # to. Guarded inside resolve_prefix itself against a partial word.
                resolved = index.resolve_prefix(target_path)
                note = _PREFIX_MATCH_NOTE if resolved is not None else ""
            if resolved is None:
                continue
            _add(_reference(target_path, "link", path, line, snippet[:80], note), resolved)
