from __future__ import annotations

import re

from seamcheck.graph import Edge, Status, Symbol

# What Django's own path converters accept. A route written `<str:division_id>` matches
# the concrete `/api/season/division/all/` that JavaScript actually calls, and matching
# only exact strings reported every such call as an endpoint that does not exist.
_CONVERTERS = {
    "int": r"[0-9]+",
    "str": r"[^/]+",
    "slug": r"[-a-zA-Z0-9_]+",
    "uuid": r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
    "path": r".+",
}
# Every framework spells a dynamic segment differently, and this understood exactly one of
# them. A Next.js route is `[id]`, an Express or Nest route is `:id`, a FastAPI route is
# `{id}` - none contain `<`, so _as_pattern returned None for all of them and dynamic
# routes were simply unmatchable outside Django. cal.com's catch-all
# `/api/integrations/[...args]` is why this was found: ten real calls to it were reported
# as calls to a route that does not exist.
#
#   <int:pk> <pk>     Django, Flask
#   :id               Express, NestJS, Fastify
#   [id]              Next.js         - one segment
#   [...args]         Next.js         - catch-all, one segment or more
#   [[...args]]       Next.js         - optional catch-all, zero segments or more
#   {id} {id:path}    FastAPI, Starlette
_PARAMETER = re.compile(
    r"<(?:(?P<django_conv>\w+):)?(?P<django>\w+)>"
    r"|\[\[\.\.\.(?P<optional_catch_all>\w+)\]\]"
    r"|\[\.\.\.(?P<catch_all>\w+)\]"
    r"|\[(?P<next>\w+)\]"
    r"|\{(?P<fastapi>\w+)(?::(?P<fastapi_conv>\w+))?\}"
    r"|:(?P<express>\w+)"
)


def _segment_pattern(match: re.Match) -> str:
    """The regex one dynamic segment stands for."""
    groups = match.groupdict()
    if groups.get("catch_all"):
        # One segment or more, slashes included: /a, /a/b, /a/b/c.
        return r".+"
    if groups.get("optional_catch_all"):
        # Zero segments or more. The leading slash is part of the escaped literal before
        # it, so this has to be able to swallow that too.
        return r".*"
    converter = groups.get("django_conv") or groups.get("fastapi_conv") or "str"
    return _CONVERTERS.get(converter, r"[^/]+")


def _normalize(path: str) -> str:
    return path.strip("/")


def _as_pattern(path: str) -> re.Pattern | None:
    """A route with dynamic segments, compiled. None for a route that has none."""
    if not any(character in path for character in "<[{:"):
        return None
    out, cursor = [], 0
    found = False
    for parameter in _PARAMETER.finditer(path):
        found = True
        literal = path[cursor:parameter.start()]
        if parameter.groupdict().get("optional_catch_all") and literal.endswith("/"):
            # `/blog/[[...slug]]` serves `/blog` itself as well as `/blog/a/b`, so the
            # separator in front of it is part of the optional piece rather than a
            # required literal - otherwise the bare `/blog` never matches its own route.
            out.append(re.escape(literal[:-1]))
            out.append(r"(?:/.*)?")
            cursor = parameter.end()
            continue
        out.append(re.escape(literal))
        out.append(_segment_pattern(parameter))
        cursor = parameter.end()
    if not found:
        return None
    out.append(re.escape(path[cursor:]))
    try:
        return re.compile("".join(out) + r"\Z")
    except re.error:
        return None


class UrlIndex:
    """Routes, arranged so a concrete path can be resolved to the one Django would pick.

    Shared rather than rebuilt: fetch() calls, `{% url %}` tags, `<a href>` links and
    `redirect()` calls all have to answer the same question, and two copies of "which route
    serves this path" is two copies to get wrong.
    """

    def __init__(self, urls: list[Symbol]):
        self.urls = urls
        self.by_path = {_normalize(s.label): s for s in urls}
        # URLconf order, so the first route that accepts a path wins - the same one Django
        # would dispatch to.
        self.parameterised = [
            (pattern, s) for s in urls if (pattern := _as_pattern(_normalize(s.label)))
        ]

    def resolve(self, path: str) -> Symbol | None:
        """The route that serves `path`, exactly or through its converters."""
        normalised = _normalize(path)
        found = self.by_path.get(normalised)
        if found is not None:
            return found
        return next((s for pattern, s in self.parameterised if pattern.match(normalised)), None)

    def by_suffix(self, path: str) -> list[Symbol]:
        """Routes whose path ends with `path` - for a reference with no leading slash."""
        normalised = _normalize(path)
        return [s for s in self.urls if _normalize(s.label).endswith("/" + normalised)]


def match_js_to_django(django_symbols: list[Symbol], js_symbols: list[Symbol]) -> list[Edge]:
    urls = [s for s in django_symbols if s.kind == "url"]
    index = UrlIndex(urls)

    edges: list[Edge] = []
    for target in js_symbols:
        if target.kind != "fetch_target":
            continue
        path = _normalize(target.label)

        matched_url = index.resolve(path)

        if matched_url is None and not target.label.startswith("/"):
            # A fetch without a leading slash resolves against whatever page is open, so
            # its absolute path is not in the source. This project's admin dashboard calls
            # `api/add-user/`, which lives at asd/pointless/challengesdashboard/api/add-user/
            # - reported as an endpoint that does not exist. Matched by suffix instead,
            # and only when exactly one route can be meant.
            candidates = index.by_suffix(path)
            if len(candidates) == 1:
                matched_url = candidates[0]
            elif candidates:
                edges.append(Edge(from_id=target.id, to_id=target.id, status=Status.UNCERTAIN))
                continue

        if matched_url:
            # A sighting is not a call. `const ENDPOINT = '/api/x/'` names a route, but the
            # request happens elsewhere: the edge records where the name was seen and
            # leaves both ends uncertain, so nothing is claimed in either direction.
            status = Status.UNCERTAIN if target.sub == "literal" else Status.CONNECTED
            edges.append(Edge(from_id=target.id, to_id=matched_url.id, status=status))
        elif target.sub == "literal":
            # A path-shaped string that matches no route is not a broken endpoint; it may
            # not be a URL at all.
            edges.append(Edge(from_id=target.id, to_id=target.id, status=Status.UNCERTAIN))
        else:
            edges.append(Edge(from_id=target.id, to_id=target.id, status=Status.UNRESOLVED))

    return edges
