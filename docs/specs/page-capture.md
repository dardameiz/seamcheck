# Page capture — click the real UI, get the map

**Status:** spec, not built.
**Depends on:** nothing in the scan. This is an optional enrichment step.

## The problem

The map answers "what reaches what" for someone who already knows what the symbols are.
A person who did not write the architecture — or who wrote it with an assistant and never
formed a mental model of it — has no way in. They are looking at 1,450 boxes named
`#floating-combo-container` and being asked to care.

The thing they *do* recognise is the page. They know what the BUY button looks like. They
do not know it is called `#pay-btn`.

So: **let them click the button they recognise, and land on its node.**

## What this is not

Not a reconstruction. An earlier idea was to rebuild an element from its markup plus its
matching CSS rules and render that inside the map. A probe against a real project got as
far as extracting the element and 27 matching rules, and it would have worked for a button —
but it is wrong by construction for anything whose appearance depends on its parent, on
JavaScript-applied state, on images, or on utility CSS. A preview that silently lies about
how something looks is a worse failure for this tool than showing nothing, because the whole
claim is that it never asserts more than its evidence.

**Photograph the page instead.** A screenshot cannot be wrong about what the page looks
like.

## How it works

A new command, `seamcheck shoot`, drives the running application with Playwright:

1. Visit each page. The URLs come from the graph — `url` symbols that a `view` serves and
   that are not `api/`-shaped, i.e. the pages a person can actually open.
2. Screenshot the viewport (and full page, for the scroll).
3. In the same page context, walk every element carrying an `id`, a `class` or a `data-`
   attribute and record `getBoundingClientRect()` against the identifiers it carries.
4. Emit `.seamcheck/shots/<page>.json`: the image, the viewport size, and a list of
   `{rect, ids, classes, data}`.

The map then gains a **Pages** view: the real screenshot, with the recorded rectangles as
invisible hotspots. Click one, and the map opens the symbol under it. Where several symbols
sit under the pointer — a button inside a card inside a section — offer the innermost first
and the ancestors beneath it, because that is how a person points at things.

## The second payoff, which is the real reason to build it

**1,353 of the graph's remaining `uncertain` symbols are runtime-built** — selectors
assembled from a variable, fetch targets concatenated at call time, class names built from a
prefix. No static reader will ever resolve those. They are the irreducible floor of what
this tool can know by reading text.

But the browser knows. While Playwright is driving the page to photograph it, the same
session can record:

- every selector actually passed to `querySelector`/`getElementById`
- every URL actually passed to `fetch`/`XMLHttpRequest`
- every class actually applied to an element

by patching those functions before the page's own scripts run. That is a few dozen lines,
and it collapses the category static analysis cannot touch.

So one feature pays twice: a human gets a page they can click, and the graph gets the only
evidence that can close its largest blind spot.

## Provenance is the whole discipline

A symbol's status must say **how it was earned**:

| provenance | meaning |
|---|---|
| `static` | read from the source. Reproducible, works on a clone, no app needed. |
| `observed` | seen happening in a browser. True of the path that was exercised, and silent about every path that was not. |
| `both` | agreed. The strongest claim available. |

This is not decoration. Observed evidence has a failure mode static evidence does not: **it
only covers what was clicked.** A route nobody exercised looks identical to a route that
does not work. Reporting `connected (observed)` without saying so would turn a coverage gap
into a false all-clear — the exact mistake this tool exists to avoid.

And the reverse case is a genuine finding of its own: a symbol that is `static: connected`
but never `observed` across a full run is code that is wired up and never runs.

## Limits, stated up front

- **Needs the app running.** Unavailable for a cloned repository, which is most of the
  corpus. This is enrichment, never a prerequisite.
- **Only covers what was visited.** Authentication, feature flags and multi-step flows all
  hide pages. `observed` says nothing about what was not reached.
- **Screenshots are large.** They are written beside the scan and referenced, not inlined
  into the map, or a 9 MB file becomes a 90 MB one.
- **The map's "one file, no network" guarantee.** A screenshot must therefore be embedded as
  a data URI, or the Pages view must be a separate artifact. The second is likelier: it
  keeps the map portable, which is the property people actually use.

## Order of work

1. `getBoundingClientRect()` harvesting and the JSON format — no UI, verifiable by eye
   against a single page.
2. The runtime collector on the same session. It is independent of the UI and closes the
   uncertain floor, so it carries the value even if the Pages view is never built.
3. The Pages view in the map.
4. Provenance in the graph, the status model, and the `static ∧ ¬observed` finding.

Step 2 is the one worth doing first if only one gets done.
