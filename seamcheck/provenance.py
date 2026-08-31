"""Fold observed evidence into a scanned graph, saying how every claim was earned.

A `<dynamic>` selector is the tool admitting it cannot know what was queried. An observation
says what was queried. Putting the two together is the only way the runtime-built third of a
real graph stops being a shrug - but it has to be done in a way that never lets a coverage
gap read as an all-clear.

So nothing here promotes a symbol quietly. Every status this module changes gets a note that
says the evidence was OBSERVED, on which page, and - the part that matters - that silence
about a path proves nothing about it. A route nobody clicked looks exactly like a route that
does not work, and a tool whose entire claim is that it never asserts more than its evidence
cannot afford to blur that.
"""

from __future__ import annotations

from dataclasses import replace

from seamcheck.graph import Graph, Status, Symbol

# Written into the note of anything this module promotes, every time.
OBSERVED_NOTE = (
    "Seen happening in a browser on {where}. That is evidence about the path exercised and "
    "nothing about the paths that were not: a page nobody visited leaves no trace here, and "
    "looks the same as one that is broken."
)
NOT_OBSERVED_NOTE = (
    "The source says this is reached, and a browser run never reached it. Either the run did "
    "not cover it, or it is wired up and never actually runs."
)


def _pages(row: dict) -> str:
    pages = row.get("pages") or []
    if not pages:
        return "a browser run"
    shown = ", ".join(p.rsplit("/", 1)[-1] or p for p in pages[:2])
    return shown + (f" and {len(pages) - 2} more" if len(pages) > 2 else "")


def apply_observations(graph: Graph, observed: dict[str, dict]) -> Graph:
    """A graph with runtime evidence merged in, and every merge labelled.

    `observed` is `seamcheck.observe.merge()` output: bucket -> key -> {count, hits, pages}.
    """
    selectors = observed.get("selectors", {})
    fetches = observed.get("fetches", {})
    classes = observed.get("classes", {})

    # A selector is only evidence that an element EXISTS if it found one. A querySelector
    # that ran a thousand times and returned null every time is evidence of the opposite,
    # and counting it as a hit would turn the tool's most valuable finding into a pass.
    found = {key for key, row in selectors.items() if row.get("hits")}
    missed = {key for key, row in selectors.items() if not row.get("hits")}

    updated: list[Symbol] = []
    for symbol in graph.symbols:
        new = _observe_symbol(symbol, selectors, fetches, classes, found, missed)
        updated.append(new)
    return Graph(symbols=updated, edges=graph.edges, schema_version=graph.schema_version)


def _observe_symbol(symbol, selectors, fetches, classes, found, missed):
    if symbol.kind == "dom_selector":
        return _observe_selector(symbol, selectors, found, missed, classes)
    if symbol.kind == "fetch_target":
        return _observe_fetch(symbol, fetches)
    return symbol


def _selector_forms(symbol: Symbol) -> list[str]:
    """The strings a browser would have been passed for this symbol."""
    base = symbol.sub.split(":")[0]
    if base == "id":
        return [f"#{symbol.label}"]
    if base == "class":
        return [f".{symbol.label}"]
    if base == "data":
        return [f"[data-{symbol.label}]"]
    return [symbol.label]


def _observe_selector(symbol, selectors, found, missed, classes):
    # The runtime-built ones are the whole point: the scan recorded that a selector was
    # assembled here and could not say what it was. A browser can.
    if symbol.label == "<dynamic>":
        if not found:
            return symbol
        return replace(
            symbol, status=Status.CONNECTED,
            note="Built at runtime, and observed resolving to a real element. "
                 + OBSERVED_NOTE.format(where="a browser run"),
        )

    if symbol.sub.startswith("class:"):
        row = classes.get(symbol.label)
        if row:
            return replace(symbol, status=Status.CONNECTED,
                           note=OBSERVED_NOTE.format(where=_pages(row)))
        return symbol

    forms = _selector_forms(symbol)
    if any(form in found for form in forms):
        return replace(symbol, status=Status.CONNECTED,
                       note=OBSERVED_NOTE.format(
                           where=_pages(next(selectors[f] for f in forms if f in selectors))))
    if any(form in missed for form in forms):
        # Ran, returned nothing. The strongest finding this tool can make: not "we could not
        # find the element" but "the page asked for it and it was not there".
        return replace(
            symbol, status=Status.UNRESOLVED,
            note="Observed returning nothing in a browser: the page asked for this element "
                 "and it was not there. This is a live null, not a static guess.",
        )
    return symbol


def _observe_fetch(symbol, fetches):
    if not fetches:
        return symbol
    label = symbol.label
    if label == "<dynamic>" or symbol.sub == "dynamic":
        return replace(
            symbol, status=Status.CONNECTED,
            note="Assembled at runtime, and observed being requested. "
                 + OBSERVED_NOTE.format(where="a browser run"),
        )
    for url, row in fetches.items():
        if url and (url.endswith(label) or label in url):
            return replace(symbol, status=Status.CONNECTED,
                           note=OBSERVED_NOTE.format(where=_pages(row)))
    return symbol
