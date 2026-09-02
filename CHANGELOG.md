# Changelog

What changed, per release. Dates are when it went to PyPI.

Each release carries the numbers it was measured at, so the trend is visible rather than
asserted. All four are reproducible from a clean checkout — see the commands under
[the corpus](README.md#does-it-work-with-my-stack).

| | what it means | why both are needed |
|---|---|---|
| **coverage** | verdicts ÷ symbols | how much of a project it can speak to at all |
| **precision** | true claims ÷ claims, hand-labelled | whether a finding can be trusted |
| **recall** | planted bugs found ÷ planted | whether it still catches what it used to |
| **render** | corpus repos whose map is a valid document | whether the output survives real input |

Coverage and precision have **different denominators** and neither is meaningful alone: a
backend that answered `uncertain` everywhere would score 100% precision and be useless.
`uncertain` is not counted as a claim in precision, because it is not a claim.

## Unreleased

Coverage became a number this project actually reports, and reporting it found four bugs.

- **Added** — `tools/coverage.py`. Precision alone could never answer "is this useful on my
  stack"; a backend that says `uncertain` to everything scores perfectly and helps nobody.
  Coverage is reported per backend, and split so it is honest in both directions: evidence
  carriers excluded, *no oracle* (the evidence is not in the repository, so nothing can
  settle it) separated from *fixable* (the to-do list).
- **Added** — Sass, SCSS and Less read for the class names they define. Django projects
  compile their styles at deploy, so the `.css` scan saw almost nothing. pretix 83% → 87%.
- **Added** — 14 full-stack repositories, Django first, on a written rule: a repository
  earns a place by containing **both sides** of a seam. An API-only service measures the
  route reader and nothing else.
- **Fixed** — an `uncertain` that could not say what evidence was missing. 91% of NetBox's
  printed as "(no note recorded)", because `Edge` had no note field at all.
- **Fixed** — HTML reads an id without any JavaScript. `<a href="#create">`, `<label for>`,
  `data-bs-target` and the ARIA relations are all reads, and none of them counted. Sentry
  had the anchor four lines above the element, in the same file, and the id was still
  reported unused.
- **Fixed** — the evidence exemption existed in the DOM matcher and not the CSS matcher,
  which reaches the same symbols by a different route: 58 findings against correct markup.

## 0.8.1 — 2 Sep 2026

**Measured:** precision **45%** (166 hand-labelled claims, up from 42%) · recall **6/6** ·
render **32/32** repos, 137,273 nodes, 352 emitted scripts pass `node --check`.

Five false-positive patterns, each found by someone running 0.8.0 on a real project and
checking the findings by hand.

- **Fixed** — an icon font loaded from a CDN was reported as classes nothing styles. The
  "is there a stylesheet here" question is now asked per class family, not per project, so
  a repo with 199 local stylesheets *and* a Font Awesome tag stops judging `fa-`.
- **Fixed** — `querySelectorAll` reached one element. A class written six times came out as
  one connected and five unreached; selectors now match every element carrying the name.
- **Fixed** — a class assembled by a template (`ad-badge-{% if %}urgent{% endif %}`) was
  split into a dangling prefix and the branch text, reported as classes nobody wrote.
- **Fixed** — `[data-tab="${name}"]`: the attribute name is right there in the source even
  when the value is not, so the attribute counts as read.
- **Fixed** — Django renders `id="id_<field>"` from a form. Those ids appear in no
  template, so they can be judged by neither the template nor the stylesheet.
- **Fixed** — `axios.post('/api/x', body)` was read as a route *definition*. It has the
  same shape as `app.post('/api/x', handler)`, so a mistyped endpoint resolved happily
  against a route its own caller had invented — the tool could not find the bug it exists
  for. The owner now has to be a router.
- **Fixed** — the colour key kept the previous page's counts, so it could say "nothing
  unresolved" with a red card on screen.

## 0.8.0 — 2 Sep 2026

- **Added** — a fifth band on the map, **the store**: the data layer as the second seam,
  with a lane per store and a badge saying whether it has a schema to check against.
- **Added** — direction on the wires. Arrowheads, no more self-loops, and a straight
  schematic line when you follow one path.
- **Added** — `seamcheck share`, and **This is wrong** on a finding: mark it with one of
  nine fixed words, and the report carries the shape without carrying your code.
- **Added** — `seamcheck triage --wrong`, `seamcheck unverified`, and the matching MCP
  tools.
- **Fixed** — precision, from hand-verifying 335 findings across eight open-source repos.
  Seventeen symbol kinds had no home on the map; the overview counted two regions of four.

## 0.7.1 — 1 Sep 2026

- **Fixed** — a Supabase project whose schema lives in the dashboard got 728 findings
  saying its tables did not exist. Without a schema in the repo there is nothing to check
  against, and that is now what it says.
- **Fixed** — crashes on four large Django codebases; the reader falls back to source.

## 0.7.0 — 1 Sep 2026

- **Added** — Django is optional. Express, Fastify, NestJS, Next.js, FastAPI and Flask are
  read from source, and so are Supabase, Firebase and Redis.
- **Added** — install instructions per OS, after a report that it would not install at all.

## 0.6.x and earlier

The map, the four statuses, the CI gate, and the first six adapters. See the
[commit history](https://github.com/dardameiz/seamcheck/commits/main).
