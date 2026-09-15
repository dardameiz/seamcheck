"""A file that handles a request is an entry, whichever backend wrote it.

One source, not four: every adapter already records a `view` with its file and an edge
from the URL that serves it. A file holding several handlers is one entry, titled by the
routes it serves - not by the view's own label, which the Next.js adapter sets to the file
stem, so every route handler would be called "route" and the picker would merge them.

What such an entry reaches is its own file; for a JavaScript handler the import walk
carries it further, for a Python one the call graph does.
"""

from __future__ import annotations

import os

from seamcheck.entries.base import Entry

_SHOWN = 3


def _address(label: str) -> str:
    # Django's resolver reports patterns without their leading slash; a reader reads
    # addresses with one. Same rule as pagenames._best_url.
    return label if label.startswith("/") else f"/{label}"


class ServerEntrySource:
    name = "server"

    def detect(self, repo_root: str, config: dict) -> float:
        # Only the scanned graph can say whether an adapter found a handler, and only
        # entries() sees it. With no view symbols this costs one pass and returns nothing.
        return 0.6

    def entries(self, repo_root: str, config: dict, graph) -> list[Entry]:
        if graph is None:
            return []
        views = {symbol.id: symbol for symbol in graph.symbols if symbol.kind == "view" and symbol.file}
        if not views:
            return []
        urls = {symbol.id: symbol for symbol in graph.symbols if symbol.kind == "url"}
        addresses: dict[str, set[str]] = {view.file: set() for view in views.values()}
        for edge in graph.edges:
            if edge.from_id in urls and edge.to_id in views:
                addresses[views[edge.to_id].file].add(_address(urls[edge.from_id].label))
        found = []
        for file, served in sorted(addresses.items()):
            ordered = sorted(served)
            if len(ordered) == 1:
                title = ordered[0]
            elif ordered:
                title = f"{len(ordered)} routes"
            else:
                title = os.path.basename(file)
            shown = " · ".join(ordered[:_SHOWN]) + (" …" if len(ordered) > _SHOWN else "")
            found.append(Entry(
                key=f"server:{file}", kind="server", roots=(file,), title=title,
                where=f"{shown} - {file}" if shown else file,
                evidence="serves a route" if ordered else "holds a request handler no route reaches",
            ))
        return found
