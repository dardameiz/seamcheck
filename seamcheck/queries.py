"""The questions, answered small.

An agent's questions are "what is this called", "what is wrong in this file", "who calls
this" and "what changed since main". Until now the only way to ask any of them was
`seamcheck json`, which is 72.6 MB on the reference project, so the agent paid about 18
million tokens for four answers it could have had for a few hundred each.

Everything here reads the cached scan, returns an envelope, and is bounded by default. The
CLI and the MCP server both call these functions and neither adds logic of its own, because
two implementations of one question is how two surfaces come to disagree.
"""
from __future__ import annotations

import difflib

from seamcheck import envelope, scancache


def _row(symbol) -> dict:
    return {"id": symbol.id, "kind": symbol.kind, "label": symbol.label,
            "status": symbol.status.value, "file": symbol.file, "line": symbol.line,
            "owner": symbol.owner or "", "note": symbol.note or ""}


def _scan(repo_root: str):
    graph, how = scancache.cached_scan(repo_root)
    return graph, {"scan_seconds": how.get("seconds", 0.0), "cached": how.get("cached", False)}


def symbols(repo_root: str = ".", search: str = "", kind: str = "", limit: int = 25,
            cursor: str = "") -> dict:
    """Find a symbol by substring. The cheap way to turn a name into an id."""
    graph, cost = _scan(repo_root)
    needle = search.lower()
    rows = [_row(s) for s in graph.symbols
            if (not needle or needle in s.id.lower() or needle in (s.label or "").lower())
            and (not kind or s.kind == kind)]
    rows.sort(key=lambda row: (row["kind"], row["id"]))
    shown, cut = envelope.page(rows, limit, cursor)
    return envelope.answer("symbols", {"symbols": shown}, repo=repo_root,
                           truncated=cut, cost=cost)


def near(repo_root: str = ".", symbol_id: str = "", limit: int = 5) -> list[str]:
    """Ids close to one that does not exist, for the hint on a miss."""
    graph, _ = _scan(repo_root)
    return difflib.get_close_matches(symbol_id, [s.id for s in graph.symbols],
                                     n=limit, cutoff=0.5)
