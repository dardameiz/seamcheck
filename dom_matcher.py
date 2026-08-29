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


def match_css_selectors(
    dom_selectors: list[Symbol],
    dom_attrs: list[Symbol],
    css_selectors: list[Symbol],
    tailwind_build_classes: set[str],
) -> list[Edge]:
    """Three-way: what the DOM uses, what CSS defines, and what Tailwind generates.

    A class with no hand-written rule is not dead if the utility CSS build emits it -
    without that set, every Tailwind utility in every template reads as unresolved.
    """
    css_by_key = {(symbol.sub, symbol.label): symbol for symbol in css_selectors}
    used_keys: set[tuple[str, str]] = set()

    edges: list[Edge] = []
    for symbol in list(dom_attrs) + list(dom_selectors):
        if symbol.label == "<dynamic>" or _base_sub(symbol) not in ("id", "class"):
            continue
        key = (_base_sub(symbol), symbol.label)
        used_keys.add(key)
        defined = css_by_key.get(key)
        if defined is not None:
            edges.append(Edge(from_id=symbol.id, to_id=defined.id, status=Status.CONNECTED))
        elif key[0] == "class" and symbol.label in tailwind_build_classes:
            edges.append(Edge(from_id=symbol.id, to_id=symbol.id, status=Status.CONNECTED))
        else:
            edges.append(Edge(from_id=symbol.id, to_id=symbol.id, status=Status.UNRESOLVED))

    for key, symbol in css_by_key.items():
        if key not in used_keys:
            edges.append(Edge(from_id=symbol.id, to_id=symbol.id, status=Status.UNUSED))
    return edges


def match_css_tokens(token_defs: list[Symbol], token_uses: list[Symbol]) -> list[Edge]:
    defined = {symbol.label: symbol for symbol in token_defs}
    used = {symbol.label: symbol for symbol in token_uses}

    edges = [
        Edge(from_id=used[name].id, to_id=defined[name].id, status=Status.CONNECTED)
        for name in sorted(defined.keys() & used.keys())
    ]
    edges += [
        Edge(from_id=defined[name].id, to_id=defined[name].id, status=Status.UNUSED)
        for name in sorted(defined.keys() - used.keys())
    ]
    edges += [
        Edge(from_id=used[name].id, to_id=used[name].id, status=Status.UNRESOLVED)
        for name in sorted(used.keys() - defined.keys())
    ]
    return edges
