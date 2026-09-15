"""Today's pages, as a source: Vite entries, the scripts templates load, `js_entry_files`,
and autoconfig's sweep of loose `.js` files - with today's filename-stem keys and
`pagenames` titles, so a Django or Vite map does not change.

One deliberate exception. The sweep exists for a project with "no bundler to ask"
(autoconfig's own words). When a page-producing source matched, there is something to
ask, and the sweep's loose files are scripts rather than pages. They leave the page list
only: autoconfig still hands them to the JS extractor, so the scan reads them exactly as
before. A `js_entry_files` written in SEAMCHECK_CONFIG is the project saying what its
pages are, and is never dropped.
"""

from __future__ import annotations

import os

from seamcheck.entries.base import Entry
from seamcheck.graph import Graph
from seamcheck.nodetools import report
from seamcheck.resolve import norm_path

_PAGE_PRODUCING_CONFIDENCE = 0.5


def _page_producing_source_matched(repo_root: str, config: dict) -> bool:
    # Next.js is this phase's only source whose entries are pages. Server entries do not
    # count: an Express API has no page to offer, so its sweep stays.
    from seamcheck.adapters.nextjs_adapter import NextJSAdapter

    return NextJSAdapter().detect(repo_root, config) >= _PAGE_PRODUCING_CONFIDENCE


def _sweep_was_detected(config: dict) -> bool:
    from seamcheck import autoconfig

    return "js_entry_files" in config and "js_entry_files" not in autoconfig.declared_config()


class LegacySource:
    name = "legacy"

    def detect(self, repo_root: str, config: dict) -> float:
        return 0.5 if self._roots(repo_root, config or {}) else 0.0

    def entries(self, repo_root: str, config: dict, graph) -> list[Entry]:
        config = config or {}
        roots = self._roots(repo_root, config)
        if not roots:
            return []
        from seamcheck.pagenames import page_names

        try:
            names = page_names(repo_root, config,
                               graph if graph is not None else Graph(symbols=[], edges=[]))
        except Exception as error:  # noqa: BLE001 - a page under its file name beats no page
            report("entry-names", "Page names could not be read (%s); pages keep their file names.", error)
            names = {}
        found = []
        for root in roots:
            key = os.path.splitext(os.path.basename(root))[0]
            name = names.get(key)
            found.append(Entry(
                key=key, kind="page",
                roots=(norm_path(os.path.join(repo_root, root), repo_root),),
                title=name.title if name else key,
                where=name.where if name else "",
                evidence="a declared JavaScript entry point",
            ))
        return found

    def _roots(self, repo_root: str, config: dict) -> list[str]:
        from seamcheck.api import _entry_roots

        try:
            roots = _entry_roots(config, repo_root)
        except Exception as error:  # noqa: BLE001 - no source raises
            report("entry-legacy", "Declared entry points could not be read (%s).", error)
            return []
        if roots and _sweep_was_detected(config) and _page_producing_source_matched(repo_root, config):
            return []
        return roots
