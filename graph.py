"""The graph Signal Map builds: what exists, how it connects, and how sure we are."""

from __future__ import annotations

from dataclasses import asdict, dataclass
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
