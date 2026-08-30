"""Labels that make a finding actionable: which feature, and which page, it belongs to."""

from __future__ import annotations

from collections import defaultdict

from seamcheck.graph import Graph, Symbol


def attribute_by_feature(graph: Graph, dom_roots: list[Symbol]) -> dict[str, list[str]]:
    """Walk outward from each interactive root, labelling everything it reaches.

    "This selector is unresolved" is a fact; "the Purchase button reaches an unresolved
    selector" is something a person can act on without reading the graph.
    """
    outgoing: dict[str, list[str]] = defaultdict(list)
    for edge in graph.edges:
        outgoing[edge.from_id].append(edge.to_id)
        outgoing[edge.to_id].append(edge.from_id)

    labels: dict[str, set[str]] = defaultdict(set)
    for root in dom_roots:
        seen = {root.id}
        queue = [root.id]
        while queue:
            current = queue.pop()
            labels[current].add(root.label)
            for neighbour in outgoing.get(current, []):
                if neighbour not in seen:
                    seen.add(neighbour)
                    queue.append(neighbour)
    return {symbol_id: sorted(names) for symbol_id, names in labels.items()}


def attribute_by_page(js_symbols_by_entry: dict[str, list[Symbol]]) -> dict[str, list[str]]:
    """Which bundler entry points reach each symbol.

    A symbol reachable from several entries is shared, which is exactly what makes a
    change to it risky - so the list is kept, never collapsed to a single owner.
    """
    pages: dict[str, set[str]] = defaultdict(set)
    for entry, symbols in js_symbols_by_entry.items():
        for symbol in symbols:
            pages[symbol.id].add(entry)
    return {symbol_id: sorted(entries) for symbol_id, entries in pages.items()}
