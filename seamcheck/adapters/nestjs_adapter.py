"""NestJS: routes are decorators, which is why this one waited for the parser.

`@Controller('users')` on a class and `@Get(':id')` on a method compose to `/users/:id`.
Nothing about that is hard to read - but acorn could not parse a decorator at all, so
before @babel/parser landed a NestJS file was not partially understood, it was lost
entirely. This adapter is the payoff for that work.

Two things beyond the obvious composition, and both are load-bearing:

    app.setGlobalPrefix('api')     every route in the application moves under /api
    @Controller({ path: 'users' }) the object form, which the docs use for versioning

Missing either puts every route in the project at a path the server does not serve.
"""

from __future__ import annotations

import os
import pathlib

from seamcheck.adapters.base import ServerScan
from seamcheck.adapters.discovery import SKIP_DIRS, declares
from seamcheck.extractors.js_extractor import _parse_files, _walk
from seamcheck.graph import Edge, Status, Symbol

_METHODS = ("Get", "Post", "Put", "Delete", "Patch", "Options", "Head", "All")
_EXTENSIONS = (".ts", ".mts", ".cts")
# One shared list, so a directory that must never be scanned is excluded from
# every adapter at once. A missing name here is not a wrong answer, it is a scan
# that walks a vendored checkout - which turned a 75-second suite into ten minutes.
_SKIP = SKIP_DIRS


def _literal(node: dict | None) -> str | None:
    if not node:
        return None
    if node.get("type") == "Literal" and isinstance(node.get("value"), str):
        return node["value"]
    if node.get("type") == "TemplateLiteral" and not node.get("expressions"):
        quasis = node.get("quasis") or []
        if len(quasis) == 1:
            return (quasis[0].get("value") or {}).get("cooked")
    return None


def _decorator_path(call: dict | None) -> str:
    """The path a routing decorator declares, in either form Nest accepts."""
    if not call:
        return ""
    arguments = call.get("arguments") or []
    if not arguments:
        return ""
    direct = _literal(arguments[0])
    if direct is not None:
        return direct
    # @Controller({ path: 'users', version: '1' })
    if arguments[0].get("type") == "ObjectExpression":
        for prop in arguments[0].get("properties") or []:
            key = prop.get("key") or {}
            if (key.get("name") or key.get("value")) == "path":
                return _literal(prop.get("value")) or ""
    return ""


def _decorators(node: dict) -> list[tuple[str, dict]]:
    """(decorator name, its call node) for every decorator on this node."""
    found = []
    for decorator in node.get("decorators") or []:
        expression = decorator.get("expression") or {}
        name = (expression.get("callee") or {}).get("name") or expression.get("name")
        if name:
            found.append((name, expression))
    return found


def _join(*parts: str) -> str:
    joined = "/".join(part.strip("/") for part in parts if part and part.strip("/"))
    return f"/{joined}" if joined else "/"


def _line(node: dict) -> int | None:
    return ((node.get("loc") or {}).get("start") or {}).get("line")


def _files(repo_root: str, limit: int | None = None) -> list[str]:
    found: list[str] = []
    for root, directories, names in os.walk(repo_root):
        directories[:] = [d for d in directories if d not in _SKIP and not d.startswith(".")]
        for name in sorted(names):
            if name.endswith(_EXTENSIONS) and not name.endswith((".d.ts", ".spec.ts", ".test.ts")):
                found.append(os.path.join(root, name))
                if limit and len(found) >= limit:
                    return found
    return found


class NestJSAdapter:
    name = "nestjs"

    def detect(self, repo_root: str, config: dict) -> float:
        # Not just the root manifest: immich declares @nestjs/core in server/package.json,
        # and reading only the root made a large production NestJS app look like a FastAPI
        # project with five routes.
        if declares(repo_root, "@nestjs/core"):
            return 0.95
        for path in _files(repo_root, limit=300):
            try:
                text = pathlib.Path(path).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if "@nestjs/common" in text and "@Controller" in text:
                return 0.9
        return 0.0

    def scan(self, repo_root: str, config: dict, progress) -> ServerScan:
        progress.step("URLs and views")
        paths = _files(repo_root)
        if not paths:
            return ServerScan()
        parsed = _parse_files(paths, report_failures=False)

        global_prefix = ""
        controllers: list[tuple[str, str, list[tuple[str, str, str, int]]]] = []
        for path, tree in parsed.items():
            for node, _ in _walk(tree):
                if node.get("type") == "CallExpression":
                    callee = node.get("callee") or {}
                    if (callee.get("property") or {}).get("name") == "setGlobalPrefix":
                        global_prefix = _literal((node.get("arguments") or [None])[0]) or ""
                if node.get("type") != "ClassDeclaration":
                    continue
                names = dict(_decorators(node))
                if "Controller" not in names:
                    continue
                prefix = _decorator_path(names["Controller"])
                routes = []
                for member, _ in _walk(node.get("body") or {}):
                    if member.get("type") != "MethodDefinition":
                        continue
                    for name, call in _decorators(member):
                        if name in _METHODS:
                            routes.append((
                                name.upper(), _decorator_path(call),
                                (member.get("key") or {}).get("name") or "handler",
                                _line(member) or 1,
                            ))
                if routes:
                    controllers.append((path, prefix, routes))

        symbols: list[Symbol] = []
        edges: list[Edge] = []
        seen_views: set[str] = set()
        routes_by_url: dict[str, dict] = {}
        for path, prefix, routes in controllers:
            relative = os.path.relpath(path, repo_root)
            for method, sub_path, handler, line in routes:
                full = _join(global_prefix, prefix, sub_path)
                url_id = f"url:{full}"
                view_id = f"view:{relative}.{handler}"
                route = routes_by_url.setdefault(url_id, {
                    "label": full, "methods": [], "file": relative, "line": line,
                    "handler": handler, "prefix": prefix,
                })
                if method not in route["methods"]:
                    route["methods"].append(method)
                if view_id not in seen_views:
                    seen_views.add(view_id)
                    symbols.append(Symbol(
                        id=view_id, kind="view", label=handler, sub=f"{relative}:{line}",
                        file=relative, line=line, status=Status.CONNECTED,
                        snippet=f"@{method.title()}('{sub_path}') {handler}(...)",
                        chain=[full, handler], note="",
                    ))
                edge = Edge(from_id=url_id, to_id=view_id, status=Status.CONNECTED)
                if edge not in edges:
                    edges.append(edge)

        for url_id, route in routes_by_url.items():
            symbols.append(Symbol(
                id=url_id, kind="url", label=route["label"],
                sub="/".join(sorted(route["methods"])), file=route["file"], line=route["line"],
                status=Status.CONNECTED,
                snippet=f"@Controller('{route['prefix']}')", chain=[route["label"]], note="",
            ))

        # Nest has no reverse(): a client writes the literal path.
        return ServerScan(symbols=symbols, edges=edges, route_names={})
