"""Next.js: the routes are the filesystem, so there is nothing to parse to find them.

`app/api/orders/route.ts` serves `/api/orders`. `app/blog/[slug]/page.tsx` serves
`/blog/[slug]`. No decorator, no urlpatterns, no `include_router` - the directory tree IS
the routing table, which makes this the cheapest route reader of the set and the only one
that cannot be defeated by an unusual mounting idiom.

What it still has to get right is the conventions that make a directory NOT a path segment:

    app/(marketing)/about/page.tsx   ->  /about        route group: parentheses vanish
    app/@modal/login/page.tsx        ->  (skipped)     parallel route slot, not a URL
    app/_lib/helpers.ts              ->  (skipped)     private folder, never routable
    app/blog/[...slug]/page.tsx      ->  /blog/[...slug]
    pages/api/orders.ts              ->  /api/orders   the older Pages Router
    pages/index.tsx                  ->  /

Reading these as literal segments would put every route in a grouped app under a path the
server does not serve, and a project that uses route groups uses them everywhere.
"""

from __future__ import annotations

import pathlib
import re

from seamcheck.adapters.base import ServerScan
from seamcheck.adapters.discovery import SKIP_DIRS
from seamcheck.graph import Edge, Status, Symbol

_ROUTE_FILES = ("route", "page")            # App Router
_EXTENSIONS = (".ts", ".tsx", ".js", ".jsx", ".mjs")
_SKIP = SKIP_DIRS

# `export async function GET(...)` / `export const POST = ...` - the App Router's contract.
_METHOD_RE = re.compile(
    r"export\s+(?:async\s+)?(?:function|const|let|var)\s+"
    r"(GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS)\b"
)


# A real Next.js product is nearly always a monorepo - `apps/web/next.config.js`, with
# `packages/*` beside it - so looking only at the repository root finds nothing at all.
# Three levels covers apps/web and apps/web/src without walking a whole node_modules tree.
_MAX_DEPTH = 3


def _app_dirs(repo_root: str) -> list[pathlib.Path]:
    """Every directory that looks like a Next.js application, root or nested."""
    base = pathlib.Path(repo_root)
    found: list[pathlib.Path] = []
    for depth in range(_MAX_DEPTH + 1):
        candidates = [base] if depth == 0 else base.glob("/".join(["*"] * depth))
        for candidate in candidates:
            relative = candidate.relative_to(base) if candidate.is_relative_to(base) else candidate
            if not candidate.is_dir() or any(part in _SKIP for part in relative.parts):
                continue
            if any((candidate / name).is_file() for name in
                   ("next.config.js", "next.config.mjs", "next.config.ts", "next.config.cjs")):
                found.append(candidate)
                continue
            package = candidate / "package.json"
            if package.is_file():
                try:
                    if '"next"' in package.read_text(encoding="utf-8", errors="replace"):
                        found.append(candidate)
                except OSError:
                    pass
    return found


# Directories whose contents demonstrate the project rather than being it.
_INCIDENTAL = {"examples", "example", "demo", "demos", "samples", "sample",
               "templates", "template", "fixtures", "playground", "e2e", "test", "tests"}


def _is_incidental(app: pathlib.Path, repo_root: str) -> bool:
    try:
        relative = app.relative_to(pathlib.Path(repo_root))
    except ValueError:
        return False
    return any(part.lower() in _INCIDENTAL for part in relative.parts)


def _roots(repo_root: str) -> list[tuple[pathlib.Path, str]]:
    """(directory, kind) for every routable tree in every Next.js app in the repository."""
    found = []
    apps = _app_dirs(repo_root) or [pathlib.Path(repo_root)]
    for app in apps:
        for prefix in ("", "src"):
            for kind in ("app", "pages"):
                candidate = (app / prefix / kind) if prefix else (app / kind)
                if candidate.is_dir():
                    found.append((candidate, kind))
    return found


def _segment(name: str) -> str | None:
    """A directory's contribution to the URL, or None when it contributes nothing."""
    if name.startswith("(") and name.endswith(")"):
        return ""          # route group: organisational only
    if name.startswith("@"):
        return None        # parallel route slot: never a path segment
    if name.startswith("_"):
        return None        # private folder: not routable at all
    return name


def _url_from(path: pathlib.Path, root: pathlib.Path, kind: str) -> str | None:
    relative = path.relative_to(root)
    parts = list(relative.parts[:-1]) if kind == "app" else list(relative.parts)
    if kind == "pages":
        # The file name IS the last segment, except index which is the directory itself.
        stem = pathlib.Path(parts[-1]).stem
        parts = parts[:-1] + ([] if stem == "index" else [stem])
    segments = []
    for part in parts:
        segment = _segment(part)
        if segment is None:
            return None
        if segment:
            segments.append(segment)
    return "/" + "/".join(segments) if segments else "/"


class NextJSAdapter:
    name = "nextjs"

    def detect(self, repo_root: str, config: dict) -> float:
        apps = _app_dirs(repo_root)
        if not apps:
            return 0.0
        # An app under examples/ is a demonstration, not the project. Excalidraw ships
        # `examples/with-nextjs/` with two routes; treating that as the repository's
        # backend would let a sample outrank whatever the project actually serves.
        if all(_is_incidental(app, repo_root) for app in apps):
            return 0.4
        # A config file at the repository root is unambiguous; one three levels down in a
        # monorepo is just as real, so the only thing that lowers confidence is finding no
        # routable tree at all.
        return 0.95 if _roots(repo_root) else 0.5

    def scan(self, repo_root: str, config: dict, progress) -> ServerScan:
        progress.step("URLs and views")
        symbols: list[Symbol] = []
        edges: list[Edge] = []
        seen: set[str] = set()

        # In a monorepo two apps can each serve "/" - they are different sites, deployed
        # separately - so the id carries the app when there is more than one. A single-app
        # repository keeps the plain id it always had.
        roots = _roots(repo_root)
        apps = {self._app_of(root, repo_root) for root, _ in roots}
        qualify = len(apps) > 1

        for root, kind in roots:
            for path in sorted(root.rglob("*")):
                if not path.is_file() or path.suffix not in _EXTENSIONS:
                    continue
                if any(part in _SKIP for part in path.relative_to(root).parts):
                    continue
                if kind == "app" and path.stem not in _ROUTE_FILES:
                    continue
                if kind == "pages" and path.stem.startswith("_"):
                    continue    # _app, _document: framework plumbing, not routes
                url = _url_from(path, root, kind)
                if url is None:
                    continue

                relative = str(path.relative_to(repo_root))
                methods = self._methods(path, kind)
                app = self._app_of(root, repo_root)
                url_id = f"url:{app}:{url}" if qualify and app else f"url:{url}"
                view_id = f"view:{relative}"
                if url_id in seen:
                    continue
                seen.add(url_id)
                symbols.append(Symbol(
                    id=url_id, kind="url", label=url, sub="/".join(methods),
                    file=relative, line=1, status=Status.CONNECTED,
                    snippet=f"{relative} ({kind} router)", chain=[url, path.stem], note="",
                ))
                symbols.append(Symbol(
                    id=view_id, kind="view", label=path.stem, sub=relative,
                    file=relative, line=1, status=Status.CONNECTED,
                    snippet=f"export default // {relative}", chain=[path.stem], note="",
                ))
                edges.append(Edge(from_id=url_id, to_id=view_id, status=Status.CONNECTED))

        # Next.js has no reverse(): a link is written as the literal path, which the URL
        # reference extractor already resolves. The name index is empty by nature.
        return ServerScan(symbols=symbols, edges=edges, route_names={})

    @staticmethod
    def _app_of(routable: pathlib.Path, repo_root: str) -> str:
        """The application a routable tree belongs to, relative to the repository."""
        app = routable.parent
        if app.name == "src":
            app = app.parent
        try:
            relative = app.relative_to(pathlib.Path(repo_root))
        except ValueError:
            return ""
        return "" if str(relative) == "." else str(relative)

    def _methods(self, path: pathlib.Path, kind: str) -> list[str]:
        """Which verbs this file answers, read from its exports."""
        if kind == "app" and path.stem == "page":
            return ["GET"]
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ["GET"]
        found = sorted(set(_METHOD_RE.findall(text)))
        return found or ["GET"]
