# Findings from the measurement project

Everything learned about **seamcheck itself** while using 0.8.0 to clean up
`pointlessbutton` (511k lines, Django + vanilla JS) on 2026-09-02. Four cleanup passes,
−1,248 lines of dead JavaScript removed, three phantom polls worth ~12,000 req/s at the
project's 50k target.

This file is the upstream half: **bugs in the tool, false-positive classes it produces,
and checks it does not have but should.** Every item carries the evidence that produced
it and, where the fix is small, the patch.

Counts are from one project. Treat them as "this happens and here is how often on a real
codebase", not as a universal rate.

---

## A. Confirmed bugs

### T1 — path normalisation is still incomplete · **299 of 370 findings false**

0.8.0 fixed `./x.js` vs `x.js` (thank you). The same class survives one level up: the
detector still counts an **absolute** and a **relative** spelling of one file as two
writers.

Evidence — the note text of a real finding, unedited:

```
multi_writer_element:5
chain: ["pointless/static/pointless/push_arena.js",
        "pointless/static/pointless/push_arena.js"]
note:  "More than one file writes this element… Writers:
        /Users/balazssimon/dev/pointlessbutton/pointless/static/pointless/push_arena.js,
        pointless/static/pointless/push_arena.js."
```

The `chain` looks like a duplicate because `graph.shorten()` normalises at export; the
`note` is built before that and shows the real pair.

**Impact:** 299 of 370 `multi_writer_element` findings on this project are one file
counted twice — **81%**. It also masks success: after fixing a real multi-writer issue and
leaving exactly one write site, six elements *still* reported as multi-writer for this
reason, so the metric could not show the work.

**Fix** — same shape as the 0.8.0 one, applied to the absolute case. In
`detect_multi_writers`, canonicalise before the gate and build the chain from the same
set rather than from a parallel basename map:

```python
def _one_spelling(path: str, repo_root: str | None = None) -> str:
    """One spelling per file. `/abs/x.js`, `./x.js` and `x.js` are the same file, and the
    graph only learns that later (graph.shorten) — after this detector has already counted
    them as separate writers."""
    p = os.path.normpath(path)
    if repo_root:
        try:
            p = os.path.relpath(p, repo_root)
        except ValueError:
            pass
    return p.replace(os.sep, "/")
```

…then key `paths` with it, drop the separate `writers` basename map, and derive the
displayed chain from the deduped paths — falling back to full paths when two basenames
collide, which is the case the path gate exists for.

**Regression test that fails today:**

```python
def test_absolute_and_relative_spellings_are_one_writer():
    w = lambda p: Symbol(id=f"dom_selector:id:write:{p}:1", kind="dom_selector",
                         sub="id:write", label="x", file=p, line=1)
    assert detect_multi_writers([w("/repo/a/b.js"), w("a/b.js")]) == []
```

---

### T2 — `seamcheck help triage`'s own example does not run

```
$ seamcheck triage 'dom_attr:class:fas:…:98' --wrong consumed-by-dependency
--triage requires --status.

$ seamcheck triage '…' --status approved --wrong consumed-by-dependency
marked approved.
```

The documented example is the first command anyone copies, and the error names a flag the
example never mentions. The whole 0.8.0 feature is about getting people to report findings
back; this is the first step of that loop.

**Fix:** default `--status` to `approved` when `--wrong` is given — marking something
wrong *is* approving it away — or put the flag in the example.

---

### T3 — `share` cannot import the project that `scan` just imported

```
$ seamcheck share
could not import this project (ModuleNotFoundError: No module named 'myproject'),
so its routes were read from source instead… Run seamcheck from the project's own
virtualenv for the exact list.
```

It **is** the project's virtualenv — `seamcheck scan` imports fine in the same shell,
seconds earlier. The payload then reports **46,309 symbols** where the scan reports
**47,834**.

The one command whose job is to be trustworthy enough to send is the one with different
numbers, and its advice is to do what was already being done.

---

### T4 — nine tags, zero releases, no CHANGELOG

`gh release list` returns nothing against nine tags, and there is no `CHANGELOG.md`.
0.7.1 → 0.8.0 nearly doubled `unresolved` on this project (1,764 → 3,492) for good
reasons — new lenses, templates counted as writers — and nothing in the repository says
so. Anyone with a stored baseline sees a gate go red and has no document to check.

The commit messages are already better than most changelogs. They need collecting, not
writing.

---

## B. False-positive classes, measured

Ordered by count on this project.

| # | Class | Count | Right `--wrong` word | Fix |
|---|---|---:|---|---|
| **F6** | **Interpolated attribute VALUE hides a literal attribute NAME.** `` querySelector(`[data-tab="${tabName}"]`) `` — the value is runtime, the name is a literal, sixty lines below the `data-tab` it is reported as unused | **638** | `built-at-runtime` | Parse the name out of `[data-NAME="${…}"]` and credit `data-NAME` as read, value unknown |
| **F1** | **CDN icon font judged against local CSS.** Every `fas` / `fa-home` / `fa-chevron-down` | **453** | `consumed-by-dependency` | `styles_are_local` is per-*project*; this project is the mixed case — 199 local stylesheets **and** a CDN icon font. Make the oracle per class-family, plus a known-prefix table (`fa-`, `bi-`, `mdi-`, `glyphicon-`) |
| **F11** | **Elements built at runtime by JS.** Of 490 product `dom_selector` findings, **248 (51%)** target an element created by `innerHTML`/`createElement` in the same codebase | **248** | `built-at-runtime` | The single biggest precision win here — and exactly what `observe` exists for |
| **F3** | **One selector reaches one element.** `querySelectorAll('.x')` connects the FIRST declaration and leaves the rest `unresolved` — six identical elements in one file, one connected, five not | **92** | — matcher bug | Match declarations to selectors many-to-many. For `querySelector` (singular) the honest status for the rest is `uncertain`, not `unresolved` |
| **F4** | **Class assembled by a Django tag.** `class="ad-badge ad-badge-{% if p==4 %}urgent{% elif p==3 %}high{% endif %}"` yields the fragments `ad-badge-`, `high`, `normal` as if they were classes — and misses the real `ad-badge-high` | **83** | `built-at-runtime` | `_assemblable()` already models this for JavaScript. Template interpolation is the same problem in the other language: emit a PREFIX as `uncertain`, and never emit the branch text |
| **F7** | **A generated test artifact scanned as project CSS.** `assets/style.css` at the repo root is a committed pytest-html report | 36 | `generated` | Recognise common report/build artifacts |
| **F5** | **Django's generated form ids.** `getElementById('id_multiplier_1_3_hours')` × 7 in one admin `change_form` | ~21 | `framework-implicit` | Django adapter: an id matching `id_<name>` where `<name>` is a field on any model/form resolves against that field; `uncertain` when the field is not found |
| **F2** | **Utility classes treated as owned elements.** `text-yellow-400`, `text-green-400`, `text-purple-400` reported as multi-writer because three files call `classList.add` on them | 3 (+ the whole Tailwind surface) | `framework-implicit` | A colour utility has no owner — every file that colours something touches it. Exclude framework utility namespaces from multi-writer |
| **F12** | **`redis_key unused` does not read f-string key construction.** `api:user_stats:*` reported unused; used in three places as `f"api:user_stats:{user_id}"` | **107 unreliable** | `built-at-runtime` | Match a key pattern against the literal prefix of an f-string. This is the dominant idiom in this codebase, and `*`-patterns are exactly what it produces. **Until this lands, `redis_key unused` is not actionable on a Django project** |
| **F14** | **`json_script` ids are invisible.** `{{ x\|json_script:"arena-lobby-boot" }}` renders `<script id="arena-lobby-boot">`, but the id never appears literally, so `getElementById('arena-lobby-boot')` reads as unresolved | — | `framework-implicit` | Django adapter: read `json_script`'s argument as an id declaration |
| **F9** | **Conditionally-registered URLs.** `/api/admin/regression/*` are registered only under `if settings.REGRESSION_TESTING` | 4 | `framework-implicit` | Notice the conditional and say "registered only when X" rather than "does not exist" |
| **F10** | **`i18n_patterns` prefixes.** `/hu/lobby/` is `/lobby/` under a language prefix | 1 | `framework-implicit` | Strip a known `LANGUAGES` prefix before matching a fetch target |
| **F8** | **A `{% url %}` inside a config-guarded branch.** Three MFA recovery-code routes genuinely absent — but the whole panel sits under `{% if "recovery_codes" in MFA_SUPPORTED_TYPES %}`, which is false | 3 | — | When a `{% url %}` sits under an `{% if %}` on a settings-derived variable, report `uncertain`. **This one nearly cost a false bug report** — see §D |
| **F13** | The scan prints a caveat about untraced Celery/WS/webhook paths **in the summary**. It belongs on the findings | — | — | A `redis_key unused` row should carry "and Celery is not traced" where the reader sees it |

---

## C. Checks it does not have, ranked by what they would have found here

### C7 — a dead selector next to a timer or a request · **worth ~12,000 req/s**

**The single highest-value item in this document.**

Three findings on this project were reported as ordinary `dom_selector unresolved` rows,
visually identical to an unused CSS class. They were:

| what | endpoint | cadence | scope | 50k cost |
|---|---|---|---|---:|
| a latency readout | `/api/get-user-stats/` — **per-user** | **5 s** | every arena page | **~10,000 req/s** |
| a leaderboard | `/get_leaderboard_data/` | 30 s / 120 s | **every page but one** | ~417–1,667 req/s |
| the same leaderboard, second file | `/get_leaderboard_data/` | 30 s / 120 s | every arena page, unguarded | ~417–1,667 req/s |

Each fetched on a timer and wrote into elements that exist in no template and are built by
no script. One did not even bail when its three target ids came back `null`.

The query that separated them from the cosmetic findings:

> **a dead selector within N lines of a `fetch(`, a `setInterval(` or a `new MutationObserver`**

The scan already held both halves — *this selector never matches* and *there is a timer in
the same function*. It had simply never joined them. **One join, ~12,000 req/s.**

Suggested shape: a distinct kind or severity, e.g. `dead_selector_with_cost`, carrying the
timer interval and the request target in the finding.

### C1 — declared, has a writer, but the writer is unreachable

Would have found `PB-LEVELMODAL-STALE` **on its own**. The level modal had exactly ONE
writer, so it was never a multi-writer finding — and that writer was never called from
anywhere. The modal had been frozen at its server-rendered value since it shipped, through
every level-up, with no symptom.

### C2 — N writers, 0 declarations

Seamcheck already knows both halves; they live in different checks and are never joined.
Would have found `PB-BREAKDOWN-NOELEMENT` immediately: two implementations of one panel,
five elements, **zero** of which exist. "2 writers, 0 declarations" is a far stronger claim
than either half alone.

### C4 — reachability for a writer

*Is this function called from anywhere the graph reaches?* Would have separated the live
writer from the dead one in the level-display issue without three call sites of hand
reading. It is the difference between "these two both write X" and "one of these two never
runs", which is the actionable form.

### C5 — a guard reads a symbol it never calls

```js
if (window.updateLevelProgress) {     // ← guards on a function it never calls
    …paints the level-threshold indicators…
}
```

Deleting `updateLevelProgress` would have turned that guard permanently false and silently
stopped the indicators painting. **This is the regression this campaign avoided by hand.**
A guard that tests something unrelated does not fail loudly; it stops doing the work.

### C6 — "can never match" deserves its own status

When a selector's target exists in NO corpus — template, JS string, CSS — the finding is
not "no evidence either way". It is *provably* dead. 219 findings here would have carried
it, and it is a much stronger sort key than `unresolved`.

### C3 — the same function name defined in two files

`updateHourlyBreakdownDisplay` existed twice, writing the same five elements from different
data. `checkPushesAvailable` existed as both a global and a `BaseButton` method. A duplicate
implementation is the sibling of a duplicate writer, and this project's own conventions
treat them as one bug class.

### C8 — an empty callback body

A `MutationObserver` installed on every arena page load whose callback body was empty — the
debug logging inside it had been stripped and the observer left behind, observing in order
to do nothing.

### C9 — a function that destructures a parameter its caller does not pass

`preflight_fn(ctx)` destructuring `const { emit } = ctx`, against `harness.js` calling
`plugin.preflight_fn(opts)`. It threw before the first assertion, so that plug-in had
**never executed once** while appearing in the ledger as covering two assertions. The scan
knows the call site and the definition; this project needed a hand-written pytest.

---

## D. Two process findings worth more than any single bug

### The multi-writer detector cannot see two writers in one file

The gate is `if len(paths[label]) < 2: continue`. **Two writers inside ONE file are never
flagged.** The bug that started this whole campaign — `_reorderWindow` and
`_syncPinnedRow`, both in `league_rails.js`, both writing `.lr-row-rank`, producing a
duplicated rank on screen — was structurally invisible to it.

This project's own 40-line `scripts/canonical_writers.py` reports the in-file case ("2
writers across 1 file(s)"). For the bug class its own README leads with, seamcheck is
currently the weaker of the two tools.

### A finding's snippet can hide the thing that makes it harmless

Three `{% url %}` references to genuinely missing MFA routes, in a template rendered by a
live route. `NoReverseMatch` → 500. It read as a severe production bug and was nearly
reported as one.

It is not a bug: the whole panel sits inside
`{% if "recovery_codes" in MFA_SUPPORTED_TYPES %}`, the setting is `["totp"]`, and
allauth's own view puts that setting in the context. The branch is false and the tags never
evaluate.

**The difference between "severe production bug" and "correctly guarded dead reference" was
one `{% if %}` forty lines above the finding — and neither the finding nor its snippet
showed it.** Findings that sit inside a conditional should say so.

---

## E. What the tool got right

Worth recording, because a list of complaints is not an evaluation.

- **The family heuristic works.** The two largest multi-writer findings (62 and 52 writers,
  every button class writing the shared stage) were correctly down-ranked to `uncertain`
  with a note naming the plugin pattern. That is the right read.
- **Every `url_reference` finding was correct.** All five named routes genuinely raise
  `NoReverseMatch` against the real 658-route URLconf.
- **The `dom_attr` claims were correct where they mattered.** 159 findings on one admin page
  were verified true — the template has no `<style>` block, links only `fonts.css`, and
  nothing in the repository defines `.ru-stat-item`.
- **It found three phantom polls, a broken correctness guarantee, and a display bug frozen
  since launch — none of which had a symptom, a ticket, or a failing test.** No linter,
  type checker or test suite in this project reported any of them.

**The verdict from the consuming side:** the raw counts barely moved during the cleanup
(unresolved 3,492 → 3,428), and reading only that number you would conclude the tool did
nothing. What it actually did was point at ~12,000 req/s of waste and two live bugs. **The
counts are the wrong headline. The joins are the product.**
