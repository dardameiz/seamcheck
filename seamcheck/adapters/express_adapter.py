"""Express and Fastify: the cheapest adapter in the plan, because the parser already ran.

An Express app is plain JavaScript. Seamcheck already builds a syntax tree for every `.js`
file it can find - it simply never looked for `app.get('/x', handler)` in one. So this
adapter adds no parser, no dependency and no new failure mode; it reads an AST that was
being produced and discarded.

What it must get right is the same thing the FastAPI adapter had to: a path is composed, not
declared. `app.use('/api', router)` is Express's `include_router`, and a router mounted two
levels deep inherits both prefixes. Reading only the literal in the handler gives every route
a path the server does not serve, and every call to it then reads as unresolved.

Fastify is included because its routing is the same shape - `fastify.get(path, handler)` -
and its `register(plugin, {prefix})` is the same mounting idea under a different name.
"""

from __future__ import annotations

import os
import pathlib

from seamcheck.adapters.base import ServerScan
from seamcheck.adapters.discovery import SKIP_DIRS, declares
from seamcheck.extractors.js_extractor import _walk, iter_parsed
from seamcheck.graph import Edge, Status, Symbol
from seamcheck.nodetools import node_line, report

_METHODS = ("get", "post", "put", "delete", "patch", "options", "head", "all")
# Names that hold an HTTP CLIENT, not a router. `axios.post('/api/x', body)` is
# indistinguishable from `app.post('/api/x', handler)` by shape alone, and reading it as a
# route made every mistyped endpoint resolve against a route its own caller had invented.
_HTTP_CLIENTS = frozenset({
    "axios", "ky", "got", "http", "https", "client", "apiClient", "httpClient", "api",
    "instance", "request", "fetcher", "agent", "superagent", "$http", "$api", "service",
    "apiService", "httpService", "restClient", "backend", "supabase",
})

# `app.get('view engine')` is Express's SETTINGS reader, not a route. One argument and no
# handler, and the string has no leading slash - that is how it is told apart.
# One shared list, so a directory that must never be scanned is excluded from
# every adapter at once. A missing name here is not a wrong answer, it is a scan
# that walks a vendored checkout - which turned a 75-second suite into ten minutes.
_SKIP_DIRS = SKIP_DIRS
# TypeScript included: an Express app written in TypeScript is still an Express app, and
# the parser reads .ts now. Reading only .js walked 223 files of a 20,263-file project.
_EXTENSIONS = (".js", ".mjs", ".cjs", ".ts", ".mts", ".cts")

_UNMOUNTED_NOTE = (
    "This route is declared on a Router that nothing in the source mounts, so the path shown "
    "is only what was written at the call. The prefix it actually serves under is decided at "
    "runtime, and no reader of source can know it. Do not read a call to it as unresolved."
)


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


def _resolve(importer: str, target: str) -> str:
    """`require('./routes/users')` -> the file it actually names."""
    base = os.path.normpath(os.path.join(os.path.dirname(importer), target))
    # TypeScript too. `require('../shared/url-utils')` in a TS codebase names
    # `url-utils.ts`, and resolving it to a `.js` that does not exist quietly broke every
    # chain that crossed a TypeScript module - on Ghost, the mount that carries
    # `/ghost/api` in front of the entire admin API.
    for candidate in (base, f"{base}.js", f"{base}.mjs", f"{base}.cjs",
                      f"{base}.ts", f"{base}.tsx", f"{base}.mts", f"{base}.cts",
                      os.path.join(base, "index.js"), os.path.join(base, "index.ts"),
                      os.path.join(base, "index.tsx")):
        if os.path.isfile(candidate):
            return candidate
    return f"{base}.js"


def _line(node: dict) -> int | None:
    return node_line(node)


def _join(*parts: str) -> str:
    joined = "/".join(part.strip("/") for part in parts if part and part.strip("/"))
    return f"/{joined}" if joined else "/"


def _files(repo_root: str, limit: int | None = None) -> list[str]:
    found: list[str] = []
    for root, directories, names in os.walk(repo_root):
        directories[:] = [d for d in directories if d not in _SKIP_DIRS and not d.startswith(".")]
        for name in sorted(names):
            if name.endswith(_EXTENSIONS) and not name.endswith(
                (".min.js", ".test.js", ".spec.js", ".d.ts", ".test.ts", ".spec.ts")
            ):
                found.append(os.path.join(root, name))
                if limit and len(found) >= limit:
                    return found
    return found


class _File:
    """One JavaScript file, read for routers, routes and mounts."""

    def __init__(self, path: str) -> None:
        self.path = path
        self.routers: set[str] = set()          # variables holding a Router() or an app
        self.apps: set[str] = set()             # variables holding the application itself
        self.routes: list[tuple[str, str, str, int]] = []   # owner, METHOD, path, line
        self.mounts: list[tuple[tuple[str, str], tuple[str, str] | None, str]] = []
        # In CommonJS the router crosses the file boundary anonymously: `module.exports =
        # router` on one side, `const users = require('./routes/users')` on the other. The
        # names never match, so a router is keyed by the FILE it is exported from, not by
        # whatever the mounting file happens to call it.
        self.requires: dict[str, str] = {}   # local name -> resolved file path
        self.exported: str | None = None     # the variable this module exports
        # `module.exports = function apiRoutes() { const router = ...; return router; }` is
        # the dominant Express idiom for a route module, and the export is a FACTORY rather
        # than the router itself. Ghost writes every one of its route files this way.
        self.exports_factory = False
        self.reexports: str | None = None    # `module.exports = require('./routes')`
        # `const BASE_API_PATH = '/ghost/api'` - a mount prefix is as likely to be a
        # constant as a literal in any codebase with more than one entry point, and a
        # dropped prefix does not lose one route: it moves every route beneath it.
        self.constants: dict[str, str] = {}
        # `const { BASE_API_PATH } = require('../shared/url-utils')` - the same constant,
        # one file away, which is where a shared prefix always lives.
        self.imported: dict[str, tuple[str, str]] = {}   # local -> (file, exported name)

    def read(self, ast: dict) -> None:
        # The tree is read once and not kept: on a large repository the trees do not all
        # fit at once, and everything this file needs from it is in the fields above.
        for node, _ in _walk(ast):
            kind = node.get("type")
            if kind == "VariableDeclarator":
                self._read_declaration(node)
                self._read_require(node)
            elif kind == "CallExpression":
                self._read_call(node)
            elif kind in ("AssignmentExpression", "ExportDefaultDeclaration"):
                self._read_export(node)

    def _read_declaration(self, node: dict) -> None:
        name = (node.get("id") or {}).get("name")
        init = node.get("init") or {}
        if name and init.get("type") == "Literal" and isinstance(init.get("value"), str):
            self.constants[name] = init["value"]
            return
        if not name or init.get("type") != "CallExpression":
            return
        callee = init.get("callee") or {}
        # express()  |  express.Router()  |  Router()  |  fastify()
        called = callee.get("name") or (callee.get("property") or {}).get("name")
        if called in ("express", "fastify", "Fastify"):
            self.routers.add(name)
            self.apps.add(name)
        elif called == "Router":
            self.routers.add(name)

    def _read_require(self, node: dict) -> None:
        identifier = node.get("id") or {}
        name = identifier.get("name")
        init = node.get("init") or {}
        if init.get("type") != "CallExpression":
            return
        if (init.get("callee") or {}).get("name") != "require":
            return
        target = _literal((init.get("arguments") or [None])[0])
        if not target or not target.startswith("."):
            return
        resolved = _resolve(self.path, target)
        if name:
            self.requires[name] = resolved
            return
        # `const { BASE_API_PATH } = require('./url-utils')` - destructured, so there is
        # no single name to bind. Each property is its own import.
        if identifier.get("type") == "ObjectPattern":
            for prop in identifier.get("properties") or []:
                local = (prop.get("value") or {}).get("name")
                exported = (prop.get("key") or {}).get("name")
                if local and exported:
                    self.imported[local] = (resolved, exported)

    def _read_export(self, node: dict) -> None:
        if node.get("type") == "ExportDefaultDeclaration":
            name = (node.get("declaration") or {}).get("name")
            if name:
                self.exported = name
            return
        left = node.get("left") or {}
        # module.exports = router   |   exports = router
        owner = (left.get("object") or {}).get("name")
        attribute = (left.get("property") or {}).get("name")
        if not ((owner == "module" and attribute == "exports") or left.get("name") == "exports"):
            return
        right = node.get("right") or {}
        if right.get("name"):
            self.exported = right["name"]
        elif right.get("type") in ("FunctionExpression", "ArrowFunctionExpression"):
            self.exports_factory = True
        elif right.get("type") == "CallExpression":
            callee = right.get("callee") or {}
            target = _literal((right.get("arguments") or [None])[0])
            if callee.get("name") == "require" and target and target.startswith("."):
                self.reexports = _resolve(self.path, target)

    def key(self, name: str) -> tuple[str, str]:
        """Where the router called `name` here actually lives."""
        if name in self.requires:
            return (self.requires[name], "<default>")
        return (self.path, name)

    def own_keys(self, name: str) -> list[tuple[str, str]]:
        """Every key a router defined HERE can be referred to by.

        A factory export counts only when the file builds exactly ONE router: two routers
        behind one factory is genuinely ambiguous, and guessing would put half the routes
        at the wrong prefix.
        """
        keys = [(self.path, name)]
        if self.exported == name or (self.exports_factory and len(self.routers) == 1):
            keys.append((self.path, "<default>"))
        return keys

    def _is_router(self, owner: str) -> bool:
        """Whether this name holds an app or a router, rather than an HTTP client.

        Permissive on purpose where it cannot know: a router imported from another module
        is not declared here, and refusing those would lose real routes. So a name is
        accepted unless it is declared as something else, or is one of the handful of
        names the ecosystem uses for a client.
        """
        if owner in self.routers or owner in self.apps:
            return True
        return owner not in _HTTP_CLIENTS

    def _read_call(self, node: dict) -> None:
        callee = node.get("callee") or {}
        if callee.get("type") != "MemberExpression":
            return
        method = (callee.get("property") or {}).get("name")
        owner = (callee.get("object") or {}).get("name")
        if not owner or not method:
            return
        arguments = node.get("arguments") or []

        if method in _METHODS:
            path = _literal(arguments[0] if arguments else None)
            # A route needs a path AND something to run. `app.get('view engine')` is the
            # settings reader and has neither a leading slash nor a handler.
            #
            # AND the owner has to be a router. `axios.post('/api/x', body)` is the same
            # shape as `app.post('/api/x', handler)` - a member call, a path, two
            # arguments - so without this every client call REGISTERED THE ROUTE IT WAS
            # CALLING. The tool then could not find the one bug it exists for: a typo'd
            # endpoint resolved happily against the phantom route its own call had
            # invented. Found by pointing it at a demo shop whose whole point was that
            # typo, and watching it come back connected.
            if (path is not None and path.startswith("/") and len(arguments) >= 2
                    and self._is_router(owner)):
                self.routes.append((owner, method.upper(), path, _line(node)))
            return

        # `use`, `register`, and anything ENDING in "use" - Ghost mounts every one of its
        # API routers with `lazyUse`, a wrapper it added to its own express app so the
        # module behind the mount is required lazily. The path and the module argument
        # still have to look like a mount, so a method that merely ends in "use" cannot
        # invent one on its own.
        if method in ("use", "register") or (len(method) > 3 and method.endswith("Use")):
            first = arguments[0] if arguments else None
            prefix = _literal(first)
            prefix_from = None
            # An identifier in FIRST position is a path only when something follows it:
            # `use(versionMatch)` is one middleware and no path at all, and reading it as
            # a prefix mounts the app under a name.
            if (prefix is None and len(arguments) >= 2
                    and (first or {}).get("type") == "Identifier"):
                # Resolved at scan time, when every file has been read: the constant is
                # usually in another module, and this one has not been opened yet.
                prefix = _CONSTANT_MARK + (first.get("name") or "")
                prefix_from = id(first)
            if method == "register":
                # `register(plugin, { prefix: '/api' })` - the path is not argument 0.
                prefix = _options_prefix(arguments) or prefix
            child = None
            # Skip by TYPE, not by position: Express writes use(path, router) but Fastify
            # writes register(plugin, {prefix}), so the module is argument 0 there and a
            # positional skip drops it.
            for argument in arguments:
                if argument.get("type") in ("Literal", "TemplateLiteral", "ObjectExpression"):
                    continue
                # ...and never the argument that just supplied the path. `lazyUse(
                # BASE_API_PATH, require('../api'))` mounted the app under a router named
                # `BASE_API_PATH`, which is the prefix wearing the child's hat.
                if prefix_from is not None and id(argument) == prefix_from:
                    continue
                # `use('/x', router)` and `use('/x', routes())` are the same mount; the
                # second is what a factory export forces every caller to write.
                name = argument.get("name")
                if not name and argument.get("type") == "CallExpression":
                    callee = argument.get("callee") or {}
                    # `use('/x', require('./y'))` mounts a module without ever naming it.
                    if callee.get("name") == "require":
                        target = _literal((argument.get("arguments") or [None])[0])
                        if target and target.startswith(".") and prefix is not None:
                            self.mounts.append((
                                (_resolve(self.path, target), "<default>"),
                                (self.path, owner), prefix,
                            ))
                            return
                    name = callee.get("name")
                if name:
                    child = name
                    break
            # `use(router)` with no path mounts at the parent's own root. It contributes
            # nothing to the path and everything to the CHAIN: without it the parent's
            # prefix never reaches the routes underneath.
            if child and prefix is None:
                prefix = ""
            if child and (prefix == "" or prefix.startswith("/")
                          or prefix.startswith(_CONSTANT_MARK)):
                self.mounts.append((self.key(child), (self.path, owner), prefix))


# A prefix that is a name, not a path yet. Resolved in scan() once every file is read;
# a mount still holding one of these is dropped rather than mounted at a made-up path.
_CONSTANT_MARK = "\x00const:"


def _options_prefix(arguments: list[dict]) -> str | None:
    """Fastify mounts with `register(plugin, { prefix: '/api' })`."""
    for argument in arguments:
        if argument.get("type") != "ObjectExpression":
            continue
        for prop in argument.get("properties") or []:
            key = prop.get("key") or {}
            if (key.get("name") or key.get("value")) == "prefix":
                return _literal(prop.get("value"))
    return None


class ExpressAdapter:
    name = "express"

    def detect(self, repo_root: str, config: dict) -> float:
        # n8n declares express in packages/cli/package.json, not at the root.
        if declares(repo_root, "express", "fastify"):
            return 0.9
        for path in _files(repo_root, limit=200):
            try:
                text = pathlib.Path(path).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if "require('express')" in text or 'require("express")' in text:
                return 0.8
            if "from 'express'" in text or 'from "express"' in text:
                return 0.8
        return 0.0

    def scan(self, repo_root: str, config: dict, progress) -> ServerScan:
        progress.step("URLs and views")
        paths = _files(repo_root)
        if not paths:
            return ServerScan()
        files: list[_File] = []
        _all: list[_File] = []
        for path, tree in iter_parsed(paths, report_failures=False):
            source = _File(path)
            source.read(tree)
            _all.append(source)
            if source.routes or source.mounts:
                files.append(source)

        # A mount is an edge; a router two levels down inherits both prefixes.
        # `module.exports = require('./routes')` makes one file stand for another, so a
        # mount on the index file has to reach the router in the file behind it.
        forwards: dict[str, str] = {}
        for source in _all:
            if source.reexports:
                forwards[source.path] = source.reexports

        def follow(key: tuple[str, str]) -> tuple[str, str]:
            path, name = key
            seen: set[str] = set()
            while path in forwards and path not in seen:
                seen.add(path)
                path = forwards[path]
            return (path, name)

        # `(api.js, 'router')` and `(api.js, '<default>')` are the same object when the
        # file exports that router. A mount recorded under one name and looked up under the
        # other silently ends the prefix chain one level early.
        alias: dict[tuple[str, str], tuple[str, str]] = {}
        for source in _all:
            for name in source.routers:
                if source.exported == name or (source.exports_factory and len(source.routers) == 1):
                    alias[(source.path, name)] = (source.path, "<default>")

        # Every constant every file declares, so a prefix written as a name can be looked
        # up wherever it was defined.
        constants = {(f.path, name): value
                     for f in _all for name, value in f.constants.items()}

        def resolve_prefix(source: _File, prefix: str) -> str | None:
            if not prefix.startswith(_CONSTANT_MARK):
                return prefix
            name = prefix[len(_CONSTANT_MARK):]
            if name in source.constants:
                return source.constants[name]
            where = source.imported.get(name)
            if where:
                path, exported = where
                found = constants.get((path, exported))
                if found is not None:
                    return found
            # Unknown. Dropping the mount loses a prefix; mounting at a made-up path
            # moves every route under it to somewhere that does not exist, and reports
            # each one as a route the frontend calls and the server does not serve.
            return None

        parents: dict[tuple[str, str], tuple[tuple[str, str] | None, str]] = {}
        for source in files:
            for child, parent, prefix in source.mounts:
                resolved = resolve_prefix(source, prefix)
                if resolved is None:
                    continue
                parents[follow(child)] = (parent, resolved)

        def full_prefix(keys: list[tuple[str, str]]) -> str:
            chain: list[str] = []
            seen: set[tuple[str, str]] = set()
            current = next(
                (alias.get(key, key) for key in keys if alias.get(key, key) in parents), None
            )
            while current and current in parents and current not in seen:
                seen.add(current)
                parent, prefix = parents[current]
                if prefix:
                    chain.append(prefix)
                current = alias.get(parent, parent) if parent else None
            return _join(*reversed(chain))

        routes: dict[str, dict] = {}
        symbols: list[Symbol] = []
        edges: list[Edge] = []
        seen_views: set[str] = set()
        for source in files:
            relative = os.path.relpath(source.path, repo_root)
            for owner, method, path, line in source.routes:
                keys = [follow(key) for key in source.own_keys(owner)]
                keys += [alias.get(key, key) for key in keys]
                full = _join(full_prefix(keys), path)
                url_id = f"url:{full}"
                view_id = f"view:{relative}:{path}"
                unmounted = (
                    owner in source.routers and owner not in source.apps
                    and not any(key in parents for key in keys)
                )
                route = routes.setdefault(url_id, {
                    "label": full, "methods": [], "file": relative, "line": line,
                    "owner": owner, "path": path, "unmounted": unmounted,
                })
                if method not in route["methods"]:
                    route["methods"].append(method)
                if view_id not in seen_views:
                    seen_views.add(view_id)
                    symbols.append(Symbol(
                        id=view_id, kind="view", label=f"{method.lower()} {path}",
                        sub=relative, file=relative, line=line, status=Status.CONNECTED,
                        snippet=f"{owner}.{method.lower()}('{path}', handler)",
                        chain=[full, path], note="",
                    ))
                edge = Edge(from_id=url_id, to_id=view_id, status=Status.CONNECTED)
                if edge not in edges:
                    edges.append(edge)

        for url_id, route in routes.items():
            symbols.append(Symbol(
                id=url_id, kind="url", label=route["label"],
                sub="/".join(sorted(route["methods"])), file=route["file"], line=route["line"],
                status=Status.UNCERTAIN if route["unmounted"] else Status.CONNECTED,
                snippet=f'{route["owner"]}.{route["methods"][0].lower()}("{route["path"]}")',
                chain=[route["label"]], note=_UNMOUNTED_NOTE if route["unmounted"] else "",
            ))

        # A near-empty result on a large codebase is not "this app has five endpoints"; it
        # means the project routes through an abstraction this reader does not speak.
        # parse-server declares its routes as `this.route('GET', path)` on its own
        # PromiseRouter class, so five is what a reader of Express's API finds in 2,000
        # files. Saying nothing there would let a confident-looking near-zero pass as fact.
        if len(paths) > 200 and len(routes) < 10:
            report(
                "express-few-routes",
                "only %s route(s) found across %s JavaScript files. This project probably "
                "registers routes through its own helper rather than app.get()/router.get(), "
                "which this reader does not follow. Treat the route list as incomplete.",
                len(routes), len(paths),
            )

        # Express has no named routes: nothing to reverse, so the index is empty by nature.
        return ServerScan(symbols=symbols, edges=edges, route_names={})
