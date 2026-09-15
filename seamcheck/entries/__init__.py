"""Where a page comes from, for every stack - the second adapter layer.

Mirrors `seamcheck/adapters/__init__.py`: every source at or above 0.5 confidence runs,
so a monorepo gets all of them (leanos-app is Next.js pages AND route handlers), and the
`entry_sources` config key forces a choice the way `server_adapter` does.
"""

from __future__ import annotations

from seamcheck.entries.base import Entry, EntrySource
from seamcheck.entries.fallback import FallbackSource
from seamcheck.entries.legacy import LegacySource
from seamcheck.entries.nextjs import NextJSSource
from seamcheck.entries.server import ServerEntrySource

_SOURCES: tuple[EntrySource, ...] = (NextJSSource(), ServerEntrySource(), LegacySource())
_CONFIDENT = 0.5

__all__ = ["Entry", "EntrySource", "all_entries", "available", "describe", "select_all"]


def available() -> list[str]:
    return [source.name for source in _SOURCES]


def _ranked(repo_root: str, config: dict) -> list[tuple[EntrySource, float]]:
    return sorted(((source, source.detect(repo_root, config)) for source in _SOURCES),
                  key=lambda pair: -pair[1])


def select_all(repo_root: str, config: dict) -> list[tuple[EntrySource, float]]:
    config = config or {}
    forced = config.get("entry_sources")
    if forced:
        names = [forced] if isinstance(forced, str) else list(forced)
        by_name = {source.name: source for source in _SOURCES}
        for name in names:
            if name not in by_name:
                raise ValueError(
                    f"Unknown entry source {name!r}. Available: {', '.join(available())}"
                )
        return [(by_name[name], 1.0) for name in names]
    ranked = _ranked(repo_root, config)
    confident = [pair for pair in ranked if pair[1] >= _CONFIDENT]
    return confident or ranked[:1]


def describe(repo_root: str, config: dict) -> list[str]:
    """One line per source for `seamcheck config`: its confidence, and whether it runs."""
    config = config or {}
    running = {source.name for source, _ in select_all(repo_root, config)}
    forced = " (forced by entry_sources)" if config.get("entry_sources") else ""
    return [
        f"{source.name:<8} {confidence:4.2f}  "
        f"{'runs' + forced if source.name in running else 'does not run'}"
        for source, confidence in _ranked(repo_root, config)
    ]


def all_entries(repo_root: str, config: dict, graph) -> list[Entry]:
    """Every entry from every selected source; the fallback only when all found nothing."""
    config = config or {}
    found: list[Entry] = []
    for source, _confidence in select_all(repo_root, config):
        found.extend(source.entries(repo_root, config, graph))
    # A Next.js page file also carries a `view` symbol, so it arrives twice - as the page
    # it is and as a server entry. The page is the name a reader knows it by.
    on_pages = {root for entry in found if entry.kind == "page" for root in entry.roots}
    found = [entry for entry in found
             if not (entry.kind == "server" and set(entry.roots) <= on_pages)]
    if not found:
        found = FallbackSource().entries(repo_root, config, graph)
    return found
