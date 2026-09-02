"""The one seam between Seamcheck and the framework that serves the routes.

Of the ~36,800 symbols in a real scan, roughly 1,000 are server-side. Everything else -
JavaScript, CSS, DOM attributes, template markup - never reads the backend at all, and the
runtime probe patches `fetch` and `querySelector` in a browser that has no idea what served
the page. So the tool is already framework-agnostic except for one component: whatever can
produce the list of routes.

That component is what this protocol names. It matters more than it looks: with no route
list, `match_js_to_django` marks **100%** of fetch targets `unresolved` - not "unknown",
actively wrong on every endpoint. A backend reader is mandatory, and it is also the only
thing a new framework needs.

The word "universal" is not used anywhere public until a second adapter exists and has been
run against real repositories. A protocol is not an implementation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from seamcheck.graph import Edge, Symbol


@dataclass
class ServerScan:
    """Everything the backend contributes to the graph.

    `route_names` maps a framework's own name for a route to its path - Django's
    `{% url 'profile' %}`, Rails' `profile_path`, Laravel's `route('profile')`. Anything
    that lets a reference be resolved without repeating the literal URL.
    """

    symbols: list[Symbol] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    route_names: dict[str, str] = field(default_factory=dict)
    # Whether the route table above is the WHOLE route table. An adapter that fell back to
    # reading source, or that could see it reached only some of the URLconf files, sets
    # this False - and then nothing downstream may call a route missing, because the
    # reader has just said it did not look everywhere. The Django static reader used to
    # print exactly that warning and the classifier reported `unresolved` regardless: 127
    # runtime-built admin URLs on one project, 12 of 12 sampled false.
    complete: bool = True
    coverage_note: str = ""


@runtime_checkable
class ServerAdapter(Protocol):
    """A reader for one backend framework.

    Implementations stay honest about two things. `detect` returns a confidence rather than
    a boolean, because a monorepo can hold two backends and the answer is which one fits
    better, not whether one fits at all. And `scan` returns whatever it could read - an
    adapter that finds no routes must return an empty ServerScan, never raise, because a
    project that half-parses is still worth the frontend half of a graph.
    """

    name: str

    def detect(self, repo_root: str, config: dict) -> float:
        """0.0-1.0 confidence that this adapter fits the repository. Highest wins."""

    def scan(self, repo_root: str, config: dict, progress) -> ServerScan:
        """Routes, the edges between them and their handlers, and their names."""
