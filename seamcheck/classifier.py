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
    {"dom_attr", "dom_selector", "css_selector", "css_token_def", "css_token_use",
     # A `{% url 'gone' %}` that resolves to nothing is NoReverseMatch at render time - a
     # 500 on a real page, and one of the most valuable things this tool can find. It has
     # to read its verdict off its edge like the DOM kinds do, or it reports nothing.
     "url_reference"}
)

# Emptied. A blanket downgrade of every unreferenced CSS rule to `uncertain` was correct
# while the class-application reader was incomplete: 3,205 sites went unread, so "nothing
# uses this" was a claim the scan had not earned.
#
# It reads className, classList, setAttribute, generated markup, template attributes and
# Python now, and the one remaining hole - a name assembled at runtime from a prefix - is
# detected where the evidence lives, in match_css_selectors. So the downgrade was no longer
# caution; it was hiding 3,878 rules whose names appear in no source file at all, in the
# status that means "unmeasured".
#
# Kept as a mechanism rather than deleted: the next kind whose extractor is half-built will
# want exactly this, and the note explains what it is for.
_UNPROVEN_UNUSED_KINDS: frozenset[str] = frozenset()
_UNPROVEN_UNUSED_NOTE = (
    "The extractor for this kind cannot see every way it could be referenced, so "
    "'nothing uses this' is not a claim the scan has earned."
)

# `{% url %}`, reverse(), redirect(), <a href>, form actions and HTMX attributes are all
# read now (url_reference_extractor), which moved 81 of one project's routes and 72 of its
# views out of `uncertain` and into evidenced. It does NOT license claiming the remainder
# dead, and that was tried and measured: flipping "no reference" to `unused` reported
# robots.txt, sitemap.xml, llms.txt and admin/ as unused code. A crawler, a browser address
# bar and Django's own admin are all real callers that appear in no source file.
#
# So the status stays `uncertain` and the note carries what changed: it now names every
# reference kind that WAS searched, which is a far more useful thing to hand a reader than
# an admission that the tool had not looked.
# A status word with nothing beside it is the thing this tool exists not to produce, and
# 49% of one project's `uncertain` symbols carried no explanation at all. Where a kind ends
# uncertain for a reason the matcher knows and the symbol does not, the reason is attached
# here rather than left to the reader to infer.
_UNCERTAIN_NOTES = {
    "css_selector": (
        "Nothing references this rule, but its name could be assembled at runtime from a "
        "prefix that does appear in the JavaScript - so it is live and unprovable at once, "
        "and calling it dead would be a guess."
    ),
}

_NO_CALLER_EVIDENCE_NOTE = (
    "Searched and not found: fetch() calls, {% url %} tags, reverse(), redirect(), "
    "<a href>, form actions and HTMX attributes. Not claimed unused - a route can be "
    "reached by a crawler, a typed address, an external service or a mobile client, and "
    "none of those appear in any source file."
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
    # Nothing but UNCERTAIN edges. The loop above never reaches this case - it walks only
    # the three decisive statuses - so a symbol held back on purpose fell through with no
    # explanation, which is the one thing a status word must never do here.
    if not symbol.note and symbol.kind in _UNCERTAIN_NOTES:
        return replace(symbol, note=_UNCERTAIN_NOTES[symbol.kind])
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

    # Pass 1: a URL is used when something resolves to it. A url -> view edge is routing,
    # not usage, so it is deliberately not counted here.
    #
    # Both caller kinds count. A fetch() is one way a route is reached and was the only one
    # this ever read; a `{% url %}` tag, a reverse(), a redirect(), an <a href>, a form
    # action or an HTMX attribute is exactly as much evidence, and reading only fetch() left
    # every server-rendered page in the project unmeasured.
    url_status: dict[str, Status] = {}
    for symbol in symbols:
        if symbol.kind != "url":
            continue
        callers = _sources(symbol.id, "fetch_target") + _sources(symbol.id, "url_reference")
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
