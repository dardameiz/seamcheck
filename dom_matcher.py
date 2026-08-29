"""Template elements against the JavaScript that touches them."""

from __future__ import annotations

import os
from collections import defaultdict

from signal_map.graph import Edge, Status, Symbol

_MULTI_WRITER_NOTE = (
    "More than one file writes this element. Whichever runs last wins, which is how a "
    "display bug survives being 'fixed' in one of them. Pick one canonical owner and "
    "route the others through it."
)


def _base_sub(symbol: Symbol) -> str:
    return symbol.sub.split(":", 1)[0]


def match_dom_selectors(dom_attrs: list[Symbol], dom_selectors: list[Symbol]) -> list[Edge]:
    """Match by exact id/data key or class-token membership.

    Not a CSS selector engine: a combinator like `.a .b` is matched on segment presence,
    a stated v1 limitation.
    """
    attrs_by_key: dict[tuple[str, str], Symbol] = {}
    for attr in dom_attrs:
        attrs_by_key.setdefault((attr.sub, attr.label), attr)

    edges: list[Edge] = []
    for selector in dom_selectors:
        if selector.label == "<dynamic>":
            continue
        matched = attrs_by_key.get((_base_sub(selector), selector.label))
        if matched:
            edges.append(Edge(from_id=selector.id, to_id=matched.id, status=Status.CONNECTED))
        else:
            edges.append(Edge(from_id=selector.id, to_id=selector.id, status=Status.UNRESOLVED))
    return edges


def detect_multi_writers(dom_selectors: list[Symbol]) -> list[Symbol]:
    writers: dict[str, set[str]] = defaultdict(set)
    evidence: dict[str, Symbol] = {}
    for selector in dom_selectors:
        if not selector.sub.endswith(":write") or selector.label == "<dynamic>":
            continue
        writers[selector.label].add(os.path.basename(selector.file))
        evidence.setdefault(selector.label, selector)

    flagged: list[Symbol] = []
    for label, files in sorted(writers.items()):
        if len(files) < 2:
            continue
        sample = evidence[label]
        ordered = sorted(files)
        flagged.append(
            Symbol(
                id=f"multi_writer_element:{label}",
                kind="multi_writer_element",
                label=label,
                sub=_base_sub(sample),
                file=sample.file,
                line=sample.line,
                status=Status.UNRESOLVED,
                snippet=sample.snippet,
                chain=ordered,
                note=f"{_MULTI_WRITER_NOTE} Writers: {', '.join(ordered)}.",
            )
        )
    return flagged
