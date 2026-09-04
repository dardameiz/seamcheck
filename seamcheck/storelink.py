"""A handler's store touches include the ones its helpers make.

The map drew the reference project's Index page as browser -> seam -> server and stopped
there, on a page whose handler exists to increment a Redis counter. The counter was
found. The handler was found. Nothing joined them: the reads sit in
`read_hero_push_count`, a helper one call away from `hero_push_counter`, and the page
walk follows edges.

Which made the one question a map of a Django project is FOR - *the database, the server,
and everything going out to the client and coming back* - stop at the handler, one hop
short of the answer, on every page whose handler delegates. Delegating is what handlers do.

The edge is evidence and never a verdict. It says this handler's work reaches this key or
this table; whether either end is sound is decided where it always was.
"""
from __future__ import annotations

from seamcheck.graph import Edge, Graph, Status

# The rows that ARE a touch of a store: one per place in the code, which is what a
# handler needs to be joined to.
_STORE_USES = ("redis_key_use", "db_table_use", "db_column_use", "db_function_use",
               "cloud_function_use", "edge_function_use")

# How far a handler's work is followed. Four is the shape of a real view: handler ->
# service -> repository -> the client call. Beyond that the chain says less about this
# handler than about the project, and every function reaches everything.
_MAX_DEPTH = 4


def link_handlers_to_stores(graph: Graph, root: str) -> list[Edge]:
    """Edges from each handler to every store row its own work reaches."""
    uses_by_owner: dict[str, list[str]] = {}
    for symbol in graph.symbols:
        if symbol.kind in _STORE_USES and symbol.owner:
            uses_by_owner.setdefault(symbol.owner.rpartition(".")[2], []).append(symbol.id)
    if not uses_by_owner:
        return []

    from seamcheck.callgraph import python_calls

    try:
        calls = python_calls(root)
    except OSError:
        return []

    edges: list[Edge] = []
    for symbol in graph.symbols:
        if symbol.kind != "view" or not symbol.label:
            continue
        reached: list[str] = []
        seen = {symbol.label}
        frontier = [symbol.label]
        for _ in range(_MAX_DEPTH):
            following: list[str] = []
            for name in frontier:
                # The call graph names a method `Class.method` and a symbol's owner is
                # indexed bare, so every method was dropped: `submit_push` on the
                # reference project walked 74 functions deep and matched the four
                # undotted names among them, reaching ONE store row on the hottest path
                # in the game.
                reached.extend(uses_by_owner.get(name.rpartition(".")[2], []))
                for callee in calls.get(name, ()):
                    if callee not in seen:
                        seen.add(callee)
                        following.append(callee)
            frontier = following
            if not frontier:
                break
        for use_id in dict.fromkeys(reached):
            edges.append(Edge(symbol.id, use_id, Status.CONNECTED))
    return edges
