"""Next.js pages: every App Router `page.*`, rooted with the layouts and templates around
it, and every Pages Router file that is not `_app`, `_document` or under `api/`.

The URL, the route-group and slot rules and the app qualification all come from the
Next.js adapter, so a page here and the route the adapter reports can never disagree.
Route handlers (`route.*`) are server entries - ServerEntrySource finds them.
"""

from __future__ import annotations

import pathlib
import re

from seamcheck.adapters.nextjs_adapter import _EXTENSIONS, _SKIP, NextJSAdapter, _roots, _url_from
from seamcheck.entries.base import Entry
from seamcheck.resolve import norm_path

# Innermost first: template.* wraps the page inside its layout.
_WRAPPERS = ("template", "layout")


class NextJSSource:
    name = "nextjs"

    def detect(self, repo_root: str, config: dict) -> float:
        return NextJSAdapter().detect(repo_root, config or {})

    def entries(self, repo_root: str, config: dict, graph) -> list[Entry]:
        routable = _roots(repo_root)
        # Qualified exactly as the adapter qualifies its url ids, so a page key and the
        # route it serves always name the same app.
        qualify = len({NextJSAdapter._app_of(root, repo_root) for root, _ in routable}) > 1
        found = []
        for router_root, kind in routable:
            app = NextJSAdapter._app_of(router_root, repo_root)
            for path in _page_files(router_root, kind):
                url = _url_from(path, router_root, kind)
                if url is None:
                    continue
                page = norm_path(str(path), repo_root)
                wrappers = [norm_path(str(p), repo_root) for p in _wrappers(path, router_root, kind)]
                found.append(Entry(
                    key=f"next:{app}:{url}" if qualify and app else f"next:{url}",
                    kind="page", roots=(page, *wrappers), title=_title(url),
                    where=f"{url} - {page}", evidence="a page file, routed by the filesystem",
                    label=f"{app}:{url}" if qualify and app else url,
                ))
        return found


def _page_files(router_root: pathlib.Path, kind: str):
    for path in sorted(router_root.rglob("*")):
        if not path.is_file() or path.suffix not in _EXTENSIONS:
            continue
        inside = path.relative_to(router_root).parts
        if any(part in _SKIP for part in inside):
            continue
        if kind == "app":
            if path.stem == "page":
                yield path
        elif not path.stem.startswith("_") and inside[0] != "api":
            yield path


def _wrappers(page: pathlib.Path, router_root: pathlib.Path, kind: str) -> list[pathlib.Path]:
    """The App Router files Next.js renders around this page, nearest first."""
    if kind != "app":
        return []
    found: list[pathlib.Path] = []
    folder = page.parent
    while True:
        for stem in _WRAPPERS:
            found += [folder / f"{stem}{ext}" for ext in _EXTENSIONS if (folder / f"{stem}{ext}").is_file()]
        if folder == router_root or router_root not in folder.parents:
            return found
        folder = folder.parent


def _title(url: str) -> str:
    """The last fixed segment, as words. A dynamic segment names a value, not a page."""
    fixed = [segment for segment in url.split("/") if segment and not segment.startswith("[")]
    if not fixed:
        return "Home" if url == "/" else url
    return " ".join(word.capitalize() for word in re.split(r"[-_.]+", fixed[-1]) if word)
