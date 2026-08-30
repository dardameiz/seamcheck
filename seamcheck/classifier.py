"""Final status per symbol, from the edges that actually constitute evidence of use."""

from __future__ import annotations

from dataclasses import replace

from seamcheck.graph import Edge, Status, Symbol

# Kinds whose status this module decides. Everything else (signal_receiver, admin_action,
# template_tag, model, json_field, context_field) arrives already final from its extractor
# and is passed through untouched rather than re-guessed.
_OWNED_KINDS = frozenset({"view", "url", "js_call", "fetch_target"})

# DOM and CSS kinds carry their verdict on the edges the matchers produced, so the
# symbol has to read it back or every one of them reports "uncertain" forever.
# CONNECTED wins: an element JS writes but CSS never styles is still in use.
_EDGE_STATUS_KINDS = frozenset(
    {"dom_attr", "dom_selector", "css_selector", "css_token_def", "css_token_use"}
)

# A CSS rule is matched against querySelector() calls and template class= attributes.
# Nothing yet reads the four ways JavaScript applies a class, so 'nothing uses this'
# is a claim the scan has not earned. Measured on this project: 5,318 selectors would
# be reported unused while 3,205 class-application sites went unread.
_UNPROVEN_UNUSED_KINDS = frozenset({"css_selector"})
_UNPROVEN_UNUSED_NOTE = (
    "No CSS-side or template-side reference found, but JavaScript that applies classes "
    "via className, classList.add or setAttribute is not yet scanned - so this is not "
    "evidence the rule is dead."
)

_NO_CALLER_EVIDENCE_NOTE = (
    "No fetch() call resolves here. Not claimed unused: page URLs are reached by browser "
    "navigation, {% url %} tags and <a href>, and Core Pipeline has no extractor for those "
    "yet (DOM Wiring plan). Absence of evidence is not evidence of absence."
)


def _from_edges(symbol: Symbol, incoming: dict, outgoing: dict) -> Symbol:
    touching = incoming.get(symbol.id, []) + outgoing.get(symbol.id, [])
    if not touching:
        return symbol
    statuses = {edge.status for edge in touching}
    for status in (Status.CONNECTED, Status.UNRESOLVED, Status.UNUSED):
        if status not in statuses:
            continue
        if status is Status.UNUSED and symbol.kind in _UNPROVEN_UNUSED_KINDS:
            return replace(symbol, status=Status.UNCERTAIN, note=symbol.note or _UNPROVEN_UNUSED_NOTE)
        return replace(symbol, status=status)
    return symbol


def classify(symbols: list[Symbol], edges: list[Edge]) -> list[Symbol]:
    by_id = {symbol.id: symbol for symbol in symbols}
    incoming: dict[str, list[Edge]] = {}
    outgoing: dict[str, list[Edge]] = {}
    for edge in edges:
        outgoing.setdefault(edge.from_id, []).append(edge)
        incoming.setdefault(edge.to_id, []).append(edge)

    def _sources(symbol_id: str, kind: str) -> list[Edge]:
        return [
            edge
            for edge in incoming.get(symbol_id, [])
            if (by_id.get(edge.from_id) or Symbol("", "", "", "", "", None, Status.UNUSED, "", [], "")).kind == kind
        ]

    # Pass 1: a URL is used when a fetch target resolves to it. A url -> view edge is
    # routing, not usage, so it is deliberately not counted here.
    url_status: dict[str, Status] = {}
    for symbol in symbols:
        if symbol.kind != "url":
            continue
        callers = _sources(symbol.id, "fetch_target")
        url_status[symbol.id] = (
            Status.CONNECTED
            if any(edge.status == Status.CONNECTED for edge in callers)
            else Status.UNCERTAIN
        )

    result: list[Symbol] = []
    for symbol in symbols:
        if symbol.kind in _EDGE_STATUS_KINDS:
            result.append(_from_edges(symbol, incoming, outgoing))
            continue
        if symbol.status == Status.UNCERTAIN or symbol.kind not in _OWNED_KINDS:
            result.append(symbol)
            continue

        if symbol.kind == "url":
            status = url_status[symbol.id]
            note = symbol.note or (_NO_CALLER_EVIDENCE_NOTE if status is Status.UNCERTAIN else "")
            result.append(replace(symbol, status=status, note=note))
            continue

        if symbol.kind == "view":
            # A view is used when at least one URL routing to it is used.
            routes = [edge for edge in incoming.get(symbol.id, []) if edge.from_id in url_status]
            status = (
                Status.CONNECTED
                if any(url_status[edge.from_id] is Status.CONNECTED for edge in routes)
                else Status.UNCERTAIN
            )
            note = symbol.note or (_NO_CALLER_EVIDENCE_NOTE if status is Status.UNCERTAIN else "")
            result.append(replace(symbol, status=status, note=note))
            continue

        touching = incoming.get(symbol.id, []) + outgoing.get(symbol.id, [])
        if any(edge.status == Status.UNRESOLVED for edge in touching):
            result.append(replace(symbol, status=Status.UNRESOLVED))
        elif any(edge.status == Status.CONNECTED for edge in touching):
            result.append(replace(symbol, status=Status.CONNECTED))
        else:
            result.append(symbol)

    return result
