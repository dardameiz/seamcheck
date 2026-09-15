"""What a page is, for any stack: the entry layer's two types.

`seamcheck/adapters/` reads a backend; `seamcheck/entries/` reads where a reader starts - a
page, a route handler, a script no framework claims. Each `EntrySource` detects with a
confidence, several can match one repository, and each returns entries that carry their
own key, title, address and evidence. See docs/plans/2026-09-15-entry-sources-design.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class Entry:
    key: str                  # unique and stable: "next:/pricing/[locale]", "server:app/api/x/route.ts"
    kind: str                 # "page" | "server" | "entry_file" (later phases add more)
    roots: tuple[str, ...]    # repo-relative files the reach walk starts from
    title: str                # "Pricing"
    where: str                # "/pricing/[locale] - app/pricing/[locale]/page.tsx"
    group: str = ""           # entries sharing a group are sections of one page
    evidence: str = ""        # why this is an entry: "a page file, routed by the filesystem"
    note: str = ""            # what a reader should know: "by convention, not declared"
    label: str = ""           # what a reader calls it where the key would show: "/pricing/[locale]";
                              # "" when the key already is that name, as a legacy stem is


@runtime_checkable
class EntrySource(Protocol):
    name: str

    def detect(self, repo_root: str, config: dict) -> float:
        """How sure this source is that it applies, 0.0-1.0. Reads the filesystem only:
        no graph exists yet when sources are chosen."""
        ...

    def entries(self, repo_root: str, config: dict, graph) -> list[Entry]:
        """Every entry this source recognises, given the scanned graph. Never raises; a
        source that finds nothing returns [] - the same contract as ServerAdapter.scan."""
        ...
