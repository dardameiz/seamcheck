"""The last resort, run only when every other source found nothing.

A first-party script no other first-party file imports is where something starts, even if
no framework says so. It is listed under its filename and says plainly that no framework
claims it - the same rule pagenames.py follows for a root no template loads: no invented
names.
"""

from __future__ import annotations

import os

from seamcheck.entries.base import Entry
from seamcheck.resolve import norm_path

# The same ceiling api._js_roots puts on a sweep of the whole tree, for the same reason:
# a repository with thousands of scripts and no entry anywhere is rare, and parsing all of
# them turns a map into a wait.
_MAX_FILES = 4000


class FallbackSource:
    name = "fallback"

    def detect(self, repo_root: str, config: dict) -> float:
        return 0.0

    def entries(self, repo_root: str, config: dict, graph) -> list[Entry]:
        from seamcheck.extractors.js_extractor import build_module_graph
        from seamcheck.extractors.url_reference_extractor import find_js_files

        root = os.path.abspath(repo_root)
        files = sorted(os.path.abspath(path) for path in find_js_files(root))[:_MAX_FILES]
        if not files:
            return []
        imported = set().union(*build_module_graph(root, files).edges.values())
        found = []
        for path in files:
            if path in imported:
                continue
            relative = norm_path(path, root)
            found.append(Entry(
                key=f"entry_file:{relative}", kind="entry_file", roots=(relative,),
                title=os.path.basename(relative), where="no framework says this is a page",
                evidence="a first-party script no other first-party file imports",
                label=relative,
            ))
        return found
