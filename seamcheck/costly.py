"""A dead selector that sits next to a network call or a timer is not hygiene.

Three findings on the reference project were reported as ordinary `dom_selector unresolved`
rows, visually identical to an unused CSS class in a stylesheet nobody imports. They were:

  * `/api/get-user-stats/` polled every 5 seconds, PER USER, to fill a readout that is on
    no page - roughly 10,000 requests per second at that application's target concurrency;
  * `/get_leaderboard_data/` polled on EVERY page for markup that had been deleted;
  * the same leaderboard poll a second time, in another file, neither copy aware of the
    other.

Around 12,000 requests per second of pure waste, and **none of them ever failed** - a timer
writing into an element that does not exist throws nothing and logs nothing. That is
precisely why they survived. The maintainer's own summary: *"static analysis already had
every fact it needed; it just never joined them."*

So this joins them. It is deliberately post-processing over findings that already exist:

  **it never creates a finding, changes a status, or makes a new claim.** It re-reads the
  source around a selector this scan ALREADY decided was dead, and if that selector sits
  INSIDE the body of a `setInterval`, a `fetch` or a `new MutationObserver`, it says so.

Inside, not near. The first version tested proximity and immediately produced a false
positive on an eight-line fixture - an unrelated selector seven lines below a timer looked
exactly like the one within it. Distance cannot tell those apart at any threshold.

That containment is the whole safety argument. The worst case is a finding that is already
being reported carries a sentence it did not need; it cannot manufacture a false positive,
because it cannot manufacture a finding.

The ranking follows from what each one costs when the element is missing. A timer repeats
forever, and a timer that also fetches repeats over the network, for every user, for as
long as the page is open.
"""

from __future__ import annotations

import dataclasses
import pathlib
import re

from seamcheck.graph import Graph, Status, Symbol

# How much source to read around the selector. This is a read budget, NOT the test - the
# test is whether the selector sits inside the call's own body. A callback longer than this
# is simply not seen, which loses a finding rather than inventing one.
WINDOW = 60

# Ordered worst first. A repeating network call is the expensive one - it multiplies by
# users and by uptime - and a bare observer is the cheapest, being local work only.
_COSTS: tuple[tuple[str, re.Pattern[str], str], ...] = (
    (
        "polling-fetch",
        re.compile(r"\b(setInterval|setTimeout)\s*\(", re.M),
        "a repeating timer",
    ),
    (
        "fetch",
        re.compile(r"\bfetch\s*\(|\baxios\s*[.(]|new\s+XMLHttpRequest\b|\$\.(get|post|ajax)\s*\(", re.M),
        "a network call",
    ),
    (
        "observer",
        re.compile(r"new\s+(MutationObserver|ResizeObserver|IntersectionObserver)\b", re.M),
        "an observer",
    ),
)

_NOTE = (
    "This element is not in any template, and the write to it is {distance} line(s) inside "
    "{what}. Work that repeats for a value nothing displays: it cannot fail, so nothing "
    "reports it. Check whether the {advice}"
)
_ADVICE = {
    "polling-fetch": "timer and its request can be removed outright.",
    "fetch": "request is still needed at all.",
    "observer": "observer is watching for something that can still happen.",
}

# Only these can be "dead" in the sense that matters here. A CSS rule nobody uses costs a
# few bytes; a selector a timer writes into costs requests.
_CLAIMS = (Status.UNRESOLVED, Status.UNUSED)


def _window(path: str, line: int, cache: dict[str, list[str]]) -> tuple[str, int]:
    """The source around a line, and the line number the window starts at."""
    if path not in cache:
        try:
            cache[path] = pathlib.Path(path).read_text(
                encoding="utf-8", errors="replace").splitlines()
        except OSError:
            cache[path] = []
    lines = cache[path]
    if not lines:
        return "", 0
    start = max(0, line - 1 - WINDOW)
    end = min(len(lines), line + WINDOW)
    return "\n".join(lines[start:end]), start + 1


def _contains(text: str, start_line: int, target: int, pattern: re.Pattern[str]) -> int | None:
    """Distance in lines, but ONLY if the target sits INSIDE the call's own body.

    Proximity alone is not evidence and the first version of this got it wrong: in a short
    file an unrelated selector seven lines below a `setInterval` looked identical to the
    one inside it. Both were flagged, and one was a false positive - which is the failure
    this whole project exists to avoid, arriving through the check meant to prove its
    value.

    So the test is containment, not distance: from the call, walk forward counting
    brackets, and the body ends where the depth returns to where it started. A selector
    inside that span is genuinely work this timer does. One after it is a neighbour.
    """
    for match in pattern.finditer(text):
        opened = text.find("(", match.start())
        if opened < 0:
            continue
        depth = 0
        end = None
        for index in range(opened, len(text)):
            char = text[index]
            if char in "([{":
                depth += 1
            elif char in ")]}":
                depth -= 1
                if depth == 0:
                    end = index
                    break
        if end is None:            # unbalanced - the window cut the body off
            end = len(text) - 1
        first = start_line + text.count("\n", 0, opened)
        last = start_line + text.count("\n", 0, end)
        if first <= target <= last:
            return max(0, target - first)
    return None


def flag_costly(graph: Graph, repo_root: str = "") -> Graph:
    """Annotate dead selectors that sit beside work which repeats or goes to the network.

    Returns a graph whose symbols are the same symbols, with notes added to some of them.
    No status is changed and nothing is added or removed.
    """
    cache: dict[str, list[str]] = {}
    root = pathlib.Path(repo_root) if repo_root else None
    out: list[Symbol] = []
    for symbol in graph.symbols:
        if (symbol.kind != "dom_selector" or symbol.status not in _CLAIMS
                or not symbol.file or not symbol.line):
            out.append(symbol)
            continue
        path = symbol.file
        if root is not None and not pathlib.Path(path).is_absolute():
            path = str(root / path)
        # Templates count, because an inline <script> reports its TEMPLATE's path: on the
        # reference project every remaining selector claim was in a .html or .css file and
        # a JS-only filter saw nothing at all. Prose is not a risk here - matching needs
        # the literal `setInterval(` and a bracket span that encloses the selector's line,
        # which English does not produce.
        if not path.endswith((".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx",
                              ".html", ".htm", ".jinja", ".jinja2", ".j2", ".vue", ".svelte")):
            out.append(symbol)
            continue
        text, start = _window(path, symbol.line, cache)
        if not text:
            out.append(symbol)
            continue
        for name, pattern, what in _COSTS:
            distance = _contains(text, start, symbol.line, pattern)
            if distance is None:
                continue
            note = _NOTE.format(what=what, distance=distance, advice=_ADVICE[name])
            out.append(dataclasses.replace(
                symbol,
                note=f"{symbol.note} {note}".strip() if symbol.note else note,
                sub=f"{symbol.sub}|costly:{name}",
            ))
            break
        else:
            out.append(symbol)
    return Graph(symbols=out, edges=graph.edges, schema_version=graph.schema_version)
