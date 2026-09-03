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

## Two corrections to the adjudication, found while implementing it

Both were found by replaying the table against the tool's own contract, and both change
what the numbers mean. Neither reduces the value of the adjudication - it is the only
graded set this tool has ever had.

**1. `uncertain` rows are not claims, and 393 of the 712 are `uncertain`.** Precision is
claims that are true ÷ claims made, and this tool's whole discipline is that `uncertain`
is not a claim - it is the scan saying it cannot see the evidence either way. Counting
those rows as findings puts the tool's honesty in the denominator. Over the rows that
ARE claims - `unresolved` and `unused`, 298 of them once REVIEW and UNCLEAR are set
aside - the graded precision is **73/298 = 24.5%**, not 13.2%.

**2. Twenty-seven CSS claims were graded false on evidence that is the stylesheet
itself.** Their recorded proof reads `referenced from 1 file: achievements.css (same
file, x2)` - the second occurrence being the same rule repeated inside a media query. A
rule written twice in its own stylesheet is not a rule anything uses. Hand-checked
`achievement-header`: it appears in exactly two files, `achievements.css` and
`shared/header_line.css`, and in no template, no JavaScript and no Python. It is dead,
and the tool was right. Correcting those twenty-seven: **100/298 = 33.6%**.

This is also a hard constraint on §1 below: the same-file rule must never be applied to a
stylesheet, or it would silence exactly these twenty-seven true findings.

**And the other direction:** 21 rows the tool called `uncertain` were graded REAL. Those
are recall, not precision - things genuinely dead that the scan could not commit to. They
are the more interesting half of the table and are not addressed by any fix below.

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

## 7. Two findings from the cleanup pass, raised by the person doing it

Both are about the SHAPE of a report rather than its truth, and both come from acting on
these findings by hand on `push_arena.js`:

- **A dead region should be reported as a region, not as N symbols.** One guard -
  `if (!#leaderboard-list || !#pioneers-btn || !#paradises-btn) return;` - fired on every
  load because all three elements had been deleted from the markup, and 292 lines below
  it were unreachable: a fetch, two click listeners, a `setTimeout`, three functions. The
  scan reported **14 separate findings**. They were one. Where an early return depends on
  elements that are all unresolved, everything after it in that function is unreachable,
  and saying so once is worth more than fourteen rows that each look like a small chore.
- **A multi-writer finding can be a dead-code finding wearing a disguise.** Deleting
  `resetProgressBars()` retired two multi-writer reports, because one of the two writers
  was in the dead region. Before reporting two writers, check whether either is
  unreachable - a "risk" whose second writer never runs is not a risk, it is a corpse.

## 8. F18, and a third correction: a mention is not a use

Raised after the person acting on these findings read all 26 `dom_attr` rows on
`push_arena.html` and **deleted none of them** - "26 findings, 0 defects". Three rules,
all three now shipped or recorded:

- **An element can carry more than one handle, and one of them is enough.**
  `<button id="lazyConfirmBtn" class="lazy-btn-confirm">` is bound by its id; the class is
  a spare label on a working button. Reporting it invites someone to strip an attribute
  off live markup for a handful of bytes. Implemented, and it needed a new `element`
  field: "same line" is not the same question on a template whose lines run 400
  characters and carry four unrelated tags.
- **A BEM modifier of a styled block is a variant, not a dead class.** `ms-ladder--cyan`
  with `.ms-ladder` styled is usually a rule nobody wrote yet, and the label is the only
  surviving trace that somebody meant to. Implemented.
- **"Unreferenced" and "unreachable" are different findings and must not share a
  severity.** The first was worth 0 fixes on that surface; the second was worth 329
  lines. Not yet implemented - see §7, the dead region.

**And the third correction to the adjudication.** Its evidence column counts any mention
of a name as a reference, including a WRITE. `data-base-achieved` is rendered by the
template and set by `dataset.baseAchieved = count` in two places, and read by nothing -
no `getAttribute`, no `[data-base-achieved]` selector, no stylesheet. Graded false on
"referenced from 2 files"; it is a true finding, and the same shape as correction 2. A
mention is not a use.

## Measured, 2026-09-04

Reference project, claims (`unresolved` + `unused`) before and after §1-§4 and §8:

| | before | after |
|---|---:|---:|
| claims | 4,023 | 3,658 |
| unresolved | 2,554 | 2,249 |
| unused | 1,469 | 1,409 |
| connected | 40,169 | 45,334 |

Replayed against the graded table, counting only rows whose code still exists: **87 of
143 false claims are no longer claims**, and **no real finding was lost** - the three that
stopped being claims are `ms-ladder--cyan`, `ms-ladder--green` and `mobile-hourly-base`,
which are exactly the rows the hands-on pass reclassified as not-defects. 56 false claims
remain, dominated by names assembled at runtime and by shapes where a mention really is
the only evidence either way.

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
