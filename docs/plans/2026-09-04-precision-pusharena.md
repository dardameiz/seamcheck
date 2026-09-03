# Precision — what the push_arena adjudication proved — 4 Sep 2026

Approved design. Every non-connected symbol on one surface of the reference project
(`push_arena.js`, `push_arena.html`, everything under `push_arena/`) was graded one by one:
**712 findings, 94 real, 515 false, 73 honest "cannot resolve", 24 needing runtime, 2 tool
bugs.** Precision on that surface is **13.2 %**, and 59 % of the noise has a single cause.

Evidence: `docs/FINDINGS-FROM-POINTLESSBUTTON.md` (T6, F16, F17, T7, M2) and, in the
reference project, `OTHER/seamcheck-pusharena-adjudication.csv` (712 rows with the files
that prove each verdict) plus `OTHER/SEAMCHECK-FINDINGS-PUSHARENA.md`.

**The point is not the ratio.** The 94 are real and nothing else finds them. Every change
below is judged twice: how much noise it removes, and whether any of the 94 survives it.
The adjudication CSV is the fixture for that second question.

## The shape of the 515, counted from the CSV

| Cause | Count | Kinds affected |
|---|---:|---|
| Referenced in the **same file** that was flagged | 281 | `dom_selector` 193, `dom_attr` 61, `css_selector` 27 |
| Consumed by a **third-party library** | 121 | `css_selector` / `dom_attr` (`fas`, `fa-crown`, …) |
| Referenced in **another file** | 78 | `dom_selector` 52, `dom_attr` 19, `css_selector` 7 |
| Name **assembled at runtime** | 24 | `'division-' + tier` |
| Static asset that **exists on disk** | 11 | `fetch_target` |

## 1. T6 — a file is its own evidence · 281 findings

`el.classList.add('goal-celebrated')` on one line and `querySelector('.goal-celebrated')`
on another: the file proves the element exists, and seamcheck reports the second line as
reaching for nothing. `push_arena.js` alone threw 188 findings of this shape.

**Why it happens, precisely.** `extract_js_class_usages` records a class JavaScript applies
as a `dom_selector` with sub `class:apply` — deliberately, so twenty modules adding
`.active` never reach multi-writer detection. `match_dom_selectors` then matches readers
only against `dom_attr` symbols (markup), and `class:apply` is not one, so the reader finds
nothing. Ids assigned in generated markup already become `dom_attr` and already match; the
gap is classes, plus `el.id = 'x'` and `setAttribute('id', …)` where those are not markup.

**Fix.** Before emitting UNRESOLVED for a `dom_selector`, consult a per-file index of names
the same file applies or assigns. A hit becomes a CONNECTED edge to that apply-site with a
note saying the evidence is in the same file. Counted, not merely present: the finding's own
line does not count as its own evidence — **more than one occurrence** is the test, which is
exactly the trap the adjudication hit and recorded.

**Risk to the 94:** a real dead branch that both writes and reads its own dead class becomes
connected. That is correct — the pair is live code reaching live code; what is dead is the
branch, which is a reachability question this tool does not claim to answer.

## 2. F16 — somebody else's class names · 121 findings

`fas`, `fa-crown`, `fa-moon`. Font Awesome replaces the `<i>` with an SVG at runtime.
Nothing in the repository references these and nothing should: "unused **by our code**" is
true, "unused" is false.

**Fix, two halves.** A vendor prefix list (`fa-`, `fas`, `far`, `fab`, `swiper-`,
`leaflet-`, `select2-`, `choices__`, `tippy-`, `noUi-`, `flatpickr-`, `ql-`) — cheap and
exact for the common libraries — **and** the inferred rule that generalises past the list: a
class that appears only in markup, is never selected by any JavaScript and never appears in
any stylesheet **in this project**, while a package of that name is in `package.json`, is
somebody else's. Reported as `uncertain` with the library named, never as a finding.
`dom_matcher._from_a_cdn` already exists and is the place this belongs.

## 3. F17 — stat the file · 11 findings, and a check gained

Every `/static/…` path flagged uncertain on this surface exists on disk; all 11 verified by
hand. The fix is worth more than the noise it removes: once the path is resolved against the
filesystem, the same code path becomes a check for the opposite case — an asset referenced
and **not** on disk, which is a real bug class in the reference project (a season's
`icon_folder` pointing at art that was never uploaded).

**Fix.** For a `fetch_target` whose label is a static path, resolve it against the project's
static roots (the `STATIC_ROOT` / `static/` directories seamcheck already knows from
autoconfig): present → CONNECTED, naming the file; absent → UNRESOLVED, saying the asset is
missing rather than that the target is unknown. Collected copies (`staticfiles/`) are not
proof of a source file and are excluded from the search, for the reason in §6.

## 4. T7 — a display string parsed as a URL · 2 findings

```js
periodsTotalElement.textContent = '/24';   // the "/24" in "period 3/24"
divisionEl.textContent = '/4';
```

Both were reported as fetch targets. **Fix:** a fetch target must come from a fetch/XHR
call, a `src`/`href`, or a router registration — never from any string literal that happens
to begin with `/`. Assignment to `textContent` / `innerText` is a display write, and a
regression test pins both lines.

## 5. M2 — the multi-writer lens gets its runtime half

24 multi-writer findings, and static analysis cannot settle one of them: a multi-writer is a
**risk**, not a defect. It becomes a defect when the writers disagree, which at runtime has
a signature — **a value that changes while the page is idle**. Sampling the 24 fourteen
times over twelve idle seconds: 14 were on screen and not one moved (several coexist by
explicit design — `unlimited_pushes` has two writers whose monotonic guards cite each
other); 10 were not rendered in the default state, so they are untested, not clean.

**Fix.** `seamcheck observe` gains an idle sample: with the page open and nothing touching
it, read every multi-writer element N times over T seconds and record `moved` / `steady` /
`not rendered`. The map shows the three states, and only `moved` is raised. This is the
difference between a lint and a bug finder, and it is the recommendation the adjudication
makes in its own words.

## 6. Tell people how to check this tool — the two traps, in our docs

Both mistakes were made while producing the adjudication, and anyone verifying seamcheck
will make them:

- **A token index under-reports hyphenated names.** `achievement-category` never appears as
  its own token in a file containing `achievement-category-title`, because the tokeniser
  takes the longest run. **5 of 52 verdicts were wrong this way**, all live classes graded
  dead. Plain substring search is the correct method.
- **The corpus filter decides the answer.** `staticfiles/` holds collectstatic *copies* of
  the same sources, and a committed `connectivity-map.html` is seamcheck's **own output**.
  Counting either as usage makes everything look connected: a first sample scored 10/10
  wrong from this alone.

This goes in `docs/verifying.md` — how to grade findings honestly — and is linked from the
README, because a tool that reports precision should say how to measure it.

## Measurement, before and after

`tools/precision.py` gains a mode that reads an adjudication CSV and reports, per cause,
how many graded-false findings the current build still emits and how many graded-true ones
survive. The push_arena CSV is the first fixture. Target after §1–§4: the 515 false
positives fall below 120, and all 94 real findings survive. A commit that loses a real
finding does not ship, whatever it does to the ratio.

## Order and gates

1. T6 (largest, and the one the map most visibly gains from).
2. F16.
3. F17 (a check gained, so a fixture with a missing asset ships with it).
4. T7.
5. `docs/verifying.md` + the `precision.py` mode.
6. M2 (observe idle sample) — last, because it needs a live page.

Gates as always: ruff, the non-map suite, the three map suites, self-scan, `verify_output
--self`, corpus scan — and here, additionally, the adjudication CSV replayed after each
commit. This batch follows the function-first map
(`docs/plans/2026-09-04-function-first-map.md`).
