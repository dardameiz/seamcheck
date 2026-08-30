"""The graph Seamcheck builds: what exists, how it connects, and how sure we are."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from enum import Enum

# Bumped whenever a field is added, removed, or given new meaning. Snapshots are read
# back weeks later by the diff engine, so a stored graph must say which shape it is.
SCHEMA_VERSION = "1.0"


class Status(str, Enum):
    """Inherits str so a Status compares and serializes as its plain wire value."""

    CONNECTED = "connected"
    UNUSED = "unused"
    UNRESOLVED = "unresolved"
    UNCERTAIN = "uncertain"


@dataclass
class Symbol:
    id: str
    kind: str
    label: str
    sub: str
    file: str
    line: int | None
    status: Status
    snippet: str
    chain: list[str]
    note: str


@dataclass
class Edge:
    from_id: str
    to_id: str
    status: Status


@dataclass
class Graph:
    symbols: list[Symbol]
    edges: list[Edge]
    schema_version: str = SCHEMA_VERSION


def _normalize_statuses(rows: list[dict]) -> None:
    for row in rows:
        row["status"] = Status(row["status"]).value


def graph_to_dict(graph: Graph) -> dict:
    data = asdict(graph)
    _normalize_statuses(data["symbols"])
    _normalize_statuses(data["edges"])
    return data


def graph_from_dict(data: dict) -> Graph:
    symbols = [
        Symbol(**{**row, "status": Status(row["status"])}) for row in data["symbols"]
    ]
    edges = [Edge(**{**row, "status": Status(row["status"])}) for row in data["edges"]]
    return Graph(
        symbols=symbols,
        edges=edges,
        schema_version=data.get("schema_version", SCHEMA_VERSION),
    )


def relativise(graph: Graph, repo_root: str) -> Graph:
    """Rewrite absolute paths under `repo_root` to paths relative to it.

    A symbol id is the tool's primary key: diffs join on it, and so do triage marks. Four
    kinds built theirs from the absolute path they happened to be scanned at, so the same
    code scanned from `/Users/me/app` and from `/app` produced different ids - every one
    of them reported added and removed on the next scan, and every triage mark keyed to
    one of them silently stopped matching. The same paths in `file` also put a developer's
    home directory into any report that gets shared.

    One pass at the boundary, so no extractor has to know where the repository lives.
    """
    import os

    prefix = os.path.abspath(repo_root)
    if not prefix.endswith(os.sep):
        prefix += os.sep

    def shorten(text: str | None) -> str | None:
        if not text:
            return text
        if prefix in text:
            text = text.replace(prefix, "")
        # `./pointless/x.js` and `pointless/x.js` are one file with two spellings. Five of
        # this project's files carried both, which split their symbols into two sets: two
        # rows in the file tree, two coverage numbers, and a per-file filter that found
        # half of them.
        return text.replace("./", "") if text.startswith("./") else text

    renamed: dict[str, str] = {}
    symbols = []
    for symbol in graph.symbols:
        new_id = shorten(symbol.id)
        if new_id != symbol.id:
            renamed[symbol.id] = new_id
        symbols.append(
            replace(
                symbol,
                id=new_id,
                file=shorten(symbol.file),
                sub=shorten(symbol.sub),
                snippet=shorten(symbol.snippet),
                chain=[shorten(step) for step in symbol.chain],
            )
        )
    edges = [
        replace(edge, from_id=renamed.get(edge.from_id, edge.from_id),
                to_id=renamed.get(edge.to_id, edge.to_id))
        for edge in graph.edges
    ]
    return Graph(symbols=symbols, edges=edges, schema_version=graph.schema_version)
