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

---

# Update · 0.8.1 · re-measured on the same project

Upgraded and re-scanned after the cleanup. **Everything in scope for the release landed**, and the
findings triaged with `--wrong` words came back correctly classified. Recorded here so the loop is
visible from this side too.

## Verified fixed

| # | Item | Evidence on re-scan |
|---|---|---|
| **T1** | Path normalisation (absolute vs relative) | same-file-counted-twice **299 → 0**. Multi-writer findings **370 → 48**, and all 48 are genuine |
| **T3** | `share` could not import the project | imports cleanly, and reports **47,131 symbols / 96,007 edges — matching the scan exactly**, where it used to disagree (46,309 vs 47,834) |
| **T4** | No changelog | `CHANGELOG.md`, with coverage/precision/recall/render per release, and the honest note that `uncertain` is not counted as a claim |
| **F6** | Interpolated attribute value hid a literal attribute name | `data-tab` → **connected** on all three sites |
| **F5** | Django's generated `id_<field>` | `id_multiplier_1_3_hours` → **uncertain** |
| **F4** | Class assembled by a Django tag | `ad-badge-` is **no longer emitted at all** |
| **F3** | `querySelectorAll` connected only the first declaration | `js-open-achievements` → **connected on every line**, including the five that were the victims |
| **F1** | CDN icon font judged against local CSS | `fas` → **uncertain**, not `unresolved` |
| **C7** | Rank a dead selector by what it costs | **built** (`87dbfbfd8`, `e599f6dc7`), after the v0.8.1 tag |

**The control held.** `ru-stat-item` — hand-verified as genuinely dead, marked
`confirmed / genuinely-dead` — is **still `unresolved`**. The fixes did not blanket-downgrade
everything, which is the failure mode a precision push is most at risk of.

## Numbers on the same project

| | 0.8.0, before cleanup | 0.8.1, after | |
|---|---:|---:|---|
| unresolved | 3,492 | **2,695** | −23% |
| unused | 2,136 | **1,560** | −27% |
| uncertain | 2,883 | 3,345 | **+462 — the right direction** |
| edges | 48,313 | **96,007** | ×2 |

Two of those deserve a note in the release copy, because both look wrong at a glance:

- **`uncertain` rising is the tool getting more honest.** Those 462 moved *out of* `unresolved` and
  `unused`, where it had been confidently wrong. The number to quote as improvement is
  `unresolved`, down 23%.
- **Edges doubling** is `b7ea4f323` — *HTML reads an id without any JavaScript, and none of it
  counted*. ~48,000 real connections the graph did not previously have.

## Two mechanisms observed working on real data

**Triage expiry.** `seamcheck check` reported four marks as *"triage invalidated — the evidence
behind this disposition changed, so the mark no longer applies."* Exactly the documented design: the
marks were keyed to evidence, the evidence changed when the bugs were fixed, and the approvals
expired rather than silently outliving the code they approved.

**The line-shift artefact, now demonstrated.** `check` reported three `new_unresolved` for
`dom_selector:data:avatar` in one file. They are not new — `data-avatar` appears **6 times before
and 6 times after** the edit. Deleting 317 lines re-ids everything below it, because symbol ids
embed `path:line`. Worth solving before `check` is trusted as a PR gate: a refactor that moves code
will always look like it introduced findings.

---

# New checks, from finishing the job

Three more, all found after the first version of this file — two of them by running the host
project's own test suite to completion for the first time.

### C10 — an assertion whose collection can be empty

```python
targets = self._switcher_targets('/cps-test/')   # returned {} once the parser broke
for code, target in targets.items():             # a loop over {} asserts nothing
    self.assertEqual(...)
```

This test was **green for the entire time its parser was broken**. It is not a rare shape: the same
session found three instances of one bug class —

1. this loop over an empty dict;
2. a regression plug-in whose `preflight_fn(ctx)` destructured a parameter its only caller does not
   pass, so it **died before the first assertion** while appearing in the ledger as covering two;
3. 107 anomalies raised as raw strings instead of registered constants, so they can never fail a run.

All three are "the check did not run" wearing the costume of "the check passed". Static analysis can
see all three: a loop with no non-empty precondition, a destructure against a known call signature,
a raise of a string where a registry constant is expected.

> **A test that cannot fail is indistinguishable from a test that passed — and both are green.**

### C11 — global state activated and not restored

```python
translation.activate('es')      # simulating what the middleware left behind
...                             # …and never restored
```

Django's active language is thread-local, not per-test. That one line made the **whole suite
order-dependent**: `test_pps_service` asserts English substrings in validation errors and got back
*"Duración no válida"* and *"CPS demasiado alto"* — **ten failures in a file that passes on its own,
in a directory that passes on its own.** The failures pointed at the victim, never at the cause.

Worth a check because the shape is mechanical: a call that mutates process-global state
(`translation.activate`, `timezone.activate`, `settings` mutation, `locale.setlocale`) inside a test
or fixture, with no `finally`, no `override` context manager, and no teardown.

### C12 — the scan's own caveat should travel with the finding

The summary prints *"Celery tasks, Redis subscribers, WebSocket handlers and Stripe webhooks are not
traced yet"*. That caveat is the entire explanation for a whole class of `redis_key unused` rows,
and it lives 200 lines away from them. Attach it to the rows it explains.

---

# Still blocking, from the consuming side

**F12 — `redis_key unused` does not read f-string key construction.** `api:user_stats:*` is reported
unused; it is used in three places as `f"api:user_stats:{user_id}"`. That is the dominant idiom in a
Django codebase and `*`-patterns are exactly what it produces. **All 107 `redis_key unused` findings
on this project were left untouched for this reason** — not because they were checked and dismissed,
but because the category cannot currently be trusted enough to act on.

It is the one open item that changes what a consumer is able to do with the output.

---

# Update · 0.8.2 · two new bugs in the Redis lens, and a correction to my own C7

## Stats: confirmed working, twice

`share` reports **47,879 symbols / 95,941 edges**, matching the scan exactly. T3 has now held across
two releases.

| | 0.8.0 (pre-cleanup) | 0.8.1 | 0.8.2 |
|---|---:|---:|---:|
| unresolved | 3,492 | 2,695 | **2,584** |
| unused | 2,136 | 1,560 | 1,560 |
| uncertain | 2,883 | 3,345 | 3,630 |
| connected | 39,323 | 39,531 | **40,105** |
| multi-writer | 370 | 48 | 48 (all genuine) |

## C7 — my recommendation was wrong, and the revert is the better call

`dd1e66fc1` reverted the cost-ranking check after testing it against real historical code rather
than a fixture. **That was the right decision and this file should say so**, because the proposal
came from here and was overconfident.

I described it as *"one line of post-processing"*. Three granularities, none both precise and
complete:

- **proximity** (my exact proposal) — flags an unrelated selector seven lines below a timer;
- **statement containment** — cannot see the real case, whose `getElementById`, `setInterval`,
  `fetch` and write sit in four methods ~95 lines apart;
- **file level** — 88 findings for 3 real ones, because two files poll legitimately.

The question is *"is this element's writer reached from a timer"* — **a call-graph question, not a
text question.** My query worked because I hand-verified all sixteen hits; as an automated check it
buries three real findings in eighty-eight. A triage aid with that ratio is worse than none.

**The finding was real. The claim that it generalised cheaply was never tested before I made it.**

---

## T5 — a symbol with incoming `connected` edges is still reported `unused`

0.8.2 adds `redis_key_use` (570 symbols, all connected) and 570 `redis_key_use → redis_key` edges.
**The extraction works and the join works.** The status pass ignores both:

```
USE : redis_key_use:api:user_stats:*:pointless/views/admin_views.py:1586
   -> redis_key:api:user_stats:*
      edge status   = connected
      target status = unused      <-- the contradiction
```

| | count |
|---|---:|
| `redis_key` reported `unused` | 107 |
| …with incoming `connected` use edges | **107** |
| …genuinely with no incoming use | **0** |

`api:user_stats:*` has 6 uses. `api:streak_opportunities:*` has 6. `user:*:achievement_timeline`
has 6. All still `unused`.

**Suggested fix, and it is cheap:** assert as a scan-time invariant that *no symbol may hold status
`unused` while an incoming edge holds status `connected`*. That single assertion catches this class
before release, for every kind, forever.

**Deliberately scoped to `unused`.** `unresolved` is a different axis — "read here and written
nowhere", or "reaches a name that does not exist" — and an incoming edge does not contradict it. I
checked before generalising: 24 `redis_key|unresolved` and 7 `fetch_target|unresolved` also have
incoming connected edges and are **not** contradictions.

## F15 — a write through a pipeline object is not counted as a write

The other half of the Redis lens. `redis_key|unresolved` claims *"read here and written nowhere in
this repo, so this lookup can only ever miss"*. For at least six keys that is wrong, because the
write goes through a pipeline rather than the client:

```python
pipe.setex("admin:config_sync_lock", 15, 1)              # period_calculator.py:293
pipe.hincrby('admin:global_stats', 'lifetime_pushes', n) # push_views.py:2360
hist_pipe.hset('analytics:history:concurrent', ...)      # admin_analytics_views.py:296
pipe.setex("global:mode_switch_occurred", ...)           # period_calculator.py:298
pipe.hset("push_arena:config", mapping={...})            # signals.py:747
```

`r.pipeline()` returns an object with the same command set; the extractor appears to match on the
client receiver only. **Track locals assigned from `.pipeline()`** — including
`with r.pipeline() as pipe:` — and treat their command calls as the client's.

Six verified against exact string literals across three files. The remaining 18 of 24 are not
claimed: a base-string search is too crude for wildcard keys like `push_arena:*`.

**Why this matters more than six:** pipelining is the house style on this project, not an edge case.
Its scaling rules mandate pipelined reads, and the page-render path alone queues 30+ operations on
one pipe. A Redis lens blind to `pipe.set(...)` reads the cold paths correctly and mis-reports the
hottest ones.

---

# Update · 0.8.2 · every push_arena finding adjudicated, one by one

2026-09-03. The owner asked for the thing this file has been circling: **a graded set.** Every
non-connected symbol on one surface — `push_arena.js`, `push_arena.html`, everything under
`push_arena/` — given a real/not-real verdict with the evidence attached. **712 findings.**

Table: `pointlessbutton/OTHER/seamcheck-pusharena-adjudication.csv`.
Script: `pointlessbutton/OTHER/navbar-mob/seam_final.py`.

| Verdict | Count | Share |
|---|---:|---:|
| NOT REAL | 515 | 72.3% |
| REAL | 94 | 13.2% |
| LIMITATION (tool correctly says "cannot resolve") | 73 | 10.3% |
| REVIEW (multi-writer) | 24 | 3.4% |
| UNCLEAR | 4 | 0.6% |
| TOOL BUG | 2 | 0.3% |

**Precision on this surface: 13.2%** — 14.7% if LIMITATION is excluded, which it should be, since
`<dynamic>` is the tool being honest rather than making a claim. The 94 are real and nothing else
finds them; the job is cutting noise without losing them.

## T6 — intra-file references are not credited · **306 findings, 59% of all noise**

The single biggest defect, and the cheapest fix in this document.

A script that does `el.classList.add('goal-celebrated')` on one line and
`querySelector('.goal-celebrated')` on another **is its own evidence**. seamcheck reports the second
as unresolved because it only looks for the link in *other* files. `push_arena.js` alone produced
188 findings, overwhelmingly this shape.

**Fix:** before declaring a `dom_selector` or `dom_attr` unresolved, search the declaring file for
the same name. Measured effect here: 306 of 515 false positives disappear.

Verification detail that matters for implementing it — count occurrences, not presence. The finding's
own line is one occurrence; **more than one** means a genuine second reference.

## F16 — class names a third-party library consumes · **121 findings**

`fa-moon`, `fa-ghost`, `fas`, `far`. Font Awesome swaps the `<i>` for an SVG at runtime. Nothing in
the repo references these and nothing should — "unused by our code" is true, "unused" is false.

**Fix:** a known-vendor prefix list (`fa-`, `swiper-`, `leaflet-`, `select2-`, `choices__`, `tippy-`,
`noUi-`) is the 20-minute version. The inferred version: a class that appears only in markup and
never in any selector or stylesheet in the project is probably somebody else's.

## F17 — `fetch_target` never stats the file · **11 findings, and a missed check**

Every `/static/…` path flagged uncertain on this surface **exists on disk** — all 11 verified.

This one is worth more than the noise it removes: stat the file, and the same code path becomes a
check that catches a **genuine 404 asset**, which is a real bug class in this project (a season's
`icon_folder` can point at art that was never uploaded).

## T7 — a display string parsed as a URL · **2 findings**

```js
periodsTotalElement.textContent = '/24';   // arena_inline_boot.js:665  → reported as a fetch target
divisionEl.textContent = '/4';             // arena_lobby_boot.js:255   → same
```

That is the `/24` in "period 3/24". A fetch target should come from a fetch/XHR/`src`/`href`, not
from any string literal beginning with `/`.

## M2 — multi-writer needs a runtime half, and here is the shape of it

24 multi-writer findings on this surface. Static detection cannot settle any of them, because a
multi-writer is a **risk**, not a defect — it becomes a defect only when the writers *disagree*.

At runtime that has a signature: a value that changes with the page idle. All flagged elements were
sampled 14 times over ~12 seconds with nothing touching the page.

- **14 were on screen and not one moved.** Writers coexist. Several by explicit design — in this
  project `unlimited_pushes` has two writers and *both* carry a monotonic guard whose comments cite
  each other.
- **10 were not rendered** in the page's default state, so they are untested rather than clean.

**Suggestion:** `seamcheck observe` already exists. If it can sample flagged elements on an idle page
and report which ones move, the multi-writer lens goes from "here are 98 things to read" to "here are
the 3 that actually fight". That is the difference between a lint and a bug finder.

## Two ways to get the adjudication itself wrong

Both were made while producing this, and anyone verifying seamcheck's output will hit them.

**A token index under-reports hyphenated names.** Splitting the corpus into identifier tokens and
looking each label up is fast and wrong: `achievement-category` never appears as its own token in a
file containing `achievement-category-title`, because the tokeniser takes the longest run.
**Measured: 5 of 52 verdicts were wrong this way** — all of them live classes graded dead. Plain
substring search is the correct method.

**The corpus filter decides the answer.** `staticfiles/` holds collectstatic *copies* of the same
sources, and `docs/maps/connectivity-map.html` is seamcheck's **own output**. Counting either as
"usage" makes every finding look connected: a first sample scored 10/10 wrong from this alone.

Worth putting in seamcheck's own docs — the tool should tell people how to check it.

## F18 — `dom_attr` "unused attribute" on a template: 26 findings, 0 defects

The clearest actionability result from the graded set, and the strongest case for demoting a whole
finding kind.

All 26 `dom_attr` findings on `push_arena.html` pass every automated test: each name appears exactly
once in the entire repository, so nothing references it. Reading them in context, **not one is dead
code.** Every single one is a *label* — a class or an id — on a live, working element:

| Finding | What it is |
|---|---|
| `lazy-btn-cancel`, `lazy-btn-confirm` | classes on buttons that also carry `id="lazyCancelBtn"` / `id="lazyConfirmBtn"` — the ids are what JS binds |
| `notificationContainer` | the unused **id**; the element's **class** `.notification-container` is read by three JS files |
| `bcrStrip` | unused **id** on a div another module reads the contents of |
| `achievementModalPercent` | unused **id** on a span already filled server-side |
| `success-popup-content/-header/-body` | unstyled wrapper classes inside a `.success-popup` that IS styled and IS driven by JS |
| `ms-ladder--cyan`, `ms-ladder--green` | BEM modifiers with no rule; `.ms-ladder` is styled |

An `id` or `class` in markup is frequently a **name**, not a **hook**. "Nothing reads this
attribute" is therefore true and useless on its own.

### Three rules that would remove nearly all of it

1. **Do not report an unused id/class when the same element carries another attribute that IS
   referenced.** `id="lazyConfirmBtn"` is used, so `class="lazy-btn-confirm"` on that tag is a label.
   This one rule kills most of the 26.
2. **Treat a BEM modifier (`block--modifier`) as covered when its base block is styled.** A modifier
   with no rule is a *styling gap* — a different report, with different urgency, and arguably a more
   interesting one.
3. **Separate "unreferenced" from "unreachable", and rank them differently.** In the same audit,
   `dom_selector` findings led to a 292-line unreachable region plus 9 orphan declarations —
   **329 lines deleted**, and 14 of those findings were one cause. The 26 `dom_attr` findings led to
   **nothing**. Presenting both at one severity is precisely what makes 712 findings read as noise
   and get skimmed.

## M3 — a multi-writer report can be a dead-code report in disguise

Deleting the unreachable region retired two multi-writer findings for free: `.progress` and
`.goal-bar` were reported as "two places in push_arena.js write this element — initPushArenaDOM,
`resetProgressBars`", and `resetProgressBars` was itself dead.

So before reporting N writers, check whether any of them is reachable. "Two writers, one of them
dead" is a dead-code finding with a much clearer fix than "pick a canonical owner".

## Score for this surface, after acting on it

| Finding kind | Findings | Outcome |
|---|---:|---|
| `dom_selector` (unreachable) | 23 | **329 lines deleted** — 14 of them were one region |
| `css_selector` | 24 | **24 selectors deleted** from 4 stylesheets |
| `dom_attr` | 26 | **0 actioned** — all labels on live elements |
| `multi_writer_element` | 24 | 14 cleared by runtime idle-sampling; 2 retired by the dead-code deletion |
| `fetch_target` | 13 | 11 assets verified present; 2 were the `/24` parser bug |

**353 lines of genuinely dead code removed from one surface.** That is the number that justifies the
tool — and the reason to fix the noise rather than lower the ambition.

---

# Update · 0.8.2 · the STORE surface graded (401 findings)

Same method as the push_arena set. **401 findings: 308 NOT REAL (76.8%), 39 REAL (9.7%), 24
LIMITATION, 14 LABEL, 13 REVIEW.** Precision lands close to push_arena's 13.2%, which suggests
~10-15% is the current baseline on a mature Django + vanilla-JS surface.

**One genuine defect found, and it is a good one.** `.badge-unlock-notification` was styled nowhere,
and `showBadgeNotification()` appends that div to `document.body`. With no rule, `position` fell back
to `static` — so a "New Badge Unlocked!" toast rendered as unstyled raw text at the end of the
document, below the footer, for three seconds. Its own comment said "Remove after animation"; there
was no animation. Nothing but a connectivity scan finds that: the code is correct, the markup is
correct, and the missing half is a CSS rule that was never written. **That is the case for the tool.**

**Also good news for the backend lenses:** all 5 `view`, 5 `url`, 13 `json_field` and 7
`fetch_target` findings on this surface were false. No dead endpoints. The server-side lenses are
behaving.

## F19 — the Redis/cache lens counts an INVALIDATION as the key's definition · 8/8 false

Every store Redis key reported "unused" is live.

| Key | Cited at | Reality |
|---|---|---|
| `store:current_period` | `admin.py:4043` | 6 non-test modules |
| `stripe:receipt:*` | `stripe_service.py:1261` | 3 modules |
| `store:purchasable_buttons` | `button_utils.py:124` | read at `:104`, deleted at `:124` |
| `store:basic_items:*` | `button_utils.py:125-126` | read at `:22` via an f-string `cache_key` |
| `store:total_buttons_count` | `button_utils.py:127` | `circuit_cached(...)` in `store_views.py:74` |

Two compounding causes:

1. **The cited line is `cache.delete('key')`.** An invalidation is a *use* of a key, not a
   declaration of one. Treating it as the definition means the key's only "site" is the one place
   guaranteed not to read it.
2. **The read is behind a variable.** `cache_key = f"store:basic_items:{item_type}"` then
   `cache.get(cache_key)`. Resolving a string variable within the same function would close most of
   this.

Repo-wide this lens reports **145 non-connected keys** — including `cache:cps:stats`, which I proved
live earlier the same day while working on an unrelated bug. Until it can follow a variable, this
lens is closer to noise than signal, and it is the one I would suppress by default.

## F20 — a note for whoever acts on `css_selector` findings: brace counts are not a syntax check

Not a seamcheck bug — a consumer bug, and worth documenting because the findings invite it.

I pruned 32 verified-dead selectors from three store stylesheets with a regex
(`[^{}]+?\{([^{}]*)\}`) and broke the build: `postcss: button_badges.css:185:1 Unexpected }`. Brace
counts balanced perfectly (59/59). The regex cannot see a rule nested inside `@media`, so removing
one left the at-rule malformed in a way counting cannot detect.

If seamcheck ever offers to apply a CSS fix, it must go through a real CSS parser. And the finding
text could say so: *"verify with your CSS build after removing — nested at-rules make brace counting
unreliable."*

## A smaller one worth recording: a failed shell glob looks exactly like a true negative

`button_badges.css` briefly appeared orphaned because `grep -rn "button_badges" --include=*` failed
under zsh (`no matches found: --include=*`) and printed nothing. It is imported by
`js/buttons-css-main.js`. **A tool that errors and a tool that finds nothing produce the same empty
output**, and in a verification workflow that turns into a deletion. Anything that checks its own
findings should assert the search RAN, not just that it was empty.

## F21 — the report emits the same symbol more than once · 49 of 401 rows (12.2%)

| | |
|---|---:|
| rows in the store report | 401 |
| distinct `(kind, label, file, line)` | **352** |
| symbols emitted more than once | 47 |
| extra rows from duplication | **49 (12.2%)** |

Multiplicity is 2 for 46 of them and **4** for `itemPurchaseModal` (`store.js:316`). By kind:
`dom_selector` 45, `js_call` 2. Adjacent lines duplicate independently — `auto-close-timeout` at
`store.js:89` and `:90` are two separate doubled entries.

Three of the REAL findings appear twice, which is why my "39 REAL" is really 36 distinct symbols.
**Deduping on `(kind, label, file, line)` before output costs nothing and removes an eighth of the
report.** It also matters for trust: a reader who spots the same finding twice starts doubting the
count, and the count is what gets quoted.

## F23 — split `multi_writer_element` on "does this element exist at runtime?"

**The best result of the whole store pass came from a `multi_writer_element` finding that was
mislabelled — and the truth was BETTER than the report.**

`modal-container` was flagged as multi-writer in `store.js`. The runtime watch could not find that
element on the page at all: 14 samples, ~12 idle seconds, purchase modal open, nothing. Reading the
code explained it — `store.js` had the same block in four places:

```js
// Support both old (.modal-container) and new (.push-store-glass) modal structures
const glassModal = modal.querySelector('.push-store-glass');
const modalContainer = modal.querySelector('.modal-container');
if (glassModal) { … } else if (modalContainer) { … }
```

`store.js` runs on `store.html` alone; that template carries 12 `.push-store-glass` and zero
`.modal-container`. **Four dead branches.** (`.modal-container` does exist elsewhere in the app, on
pages `store.js` never runs on — which is exactly why the fallback survived every code review.)

So: **a "multi-writer" finding whose element does not exist is a dead-code finding wearing the wrong
label.** If the tool can answer "does this element ever exist in the rendered templates this file
runs against?", the kind splits into two much sharper verdicts:

- **element exists, several writers** → a genuine flicker risk, hand it to a runtime check
- **element never exists** → **dead branch; the surviving branch is canonical** — actionable
  immediately, and it is the single most valuable finding shape for a codebase that has been
  refactored more than once

The template side is statically knowable: which templates load this JS (script tags, and the Vite
entry graph), and which selectors those templates contain. That is the same resolution the
`dom_selector` lens already needs.

## Ranked ask list, from both surveyed surfaces

1. **Credit a reference the scan can already see** — cross-file (194) + intra-file (68 store, 306
   arena). On the store that is **85% of all noise**.
2. **Dedupe the output** — 12.2% of rows, free (F21).
3. **Allowlist library prefixes** (`fa-`, `fas`, `far`, `fab`, `swiper-`, `leaflet-`, `select2-`,
   `choices__`, `tippy-`, `noUi-`) — 11% of store noise, ~10 lines of config.
4. **Split `multi_writer_element` on element existence** (F23) — turns a vague risk into either a
   flicker check or a dead-code finding.
5. **Fix or suppress the Redis/cache lens** — 8/8 false on the store, 145 repo-wide (F19).
6. **Suppress `dom_attr` on templates by default** — 145 findings across two surfaces, **0 defects**
   (F18).
7. **`os.path.exists` for `/static/` fetch targets** — converts 6 false positives into a check that
   would catch a real 404, which is a bug only a static scan finds.
8. **Report LIMITATION findings in their own section** — 24 unresolvable-by-design should not dilute
   the actionable count. They are the tool being honest and should read that way.
9. **Minimum label length 3** — drops the `'ok'` / `'sm'` class silently.

**And the counter-argument to all of the above, which should go in the README:** the one real defect
this pass found — a notification div appended to `document.body` with no CSS rule anywhere, so it
rendered as raw text below the footer for three seconds — **is invisible to every other instrument.**
The JS is correct. The markup is correct. Nothing is missing but a rule nobody wrote. No unit test,
no lint, no type checker, and no code review that has ever read past that function will find it.
A connectivity map is the only tool that looks for the missing half of a pair, and that is the pitch:
not "find dead code", but **"find the pair with one half missing"**.

## The best outcome so far: 15 `css_selector` findings led to a whole dead feature

Worth writing up in full, because it is the strongest argument for the tool I have found and it did
not come from a finding being *right* — it came from a finding being a **thread**.

The store report listed 15 unreferenced selectors in `button_badges.css`. Pulling on them:

1. **Grep**: 24 of the file's 33 class selectors are referenced by no JS, HTML or Python.
2. **Grep, tokens**: the file reads 6 custom properties and **5 are defined nowhere in the repo**.
   9 of its 17 `var()` declarations resolved to nothing.
3. **Reading the selectors rather than the class names**: the 9 "referenced" classes are generic —
   `locked`, `unlocked`, `achieved`, `tooltip` — and every rule using them is a descendant or
   compound of `.button-badge`. They cannot match without it. **A class-name census calls those
   live; a selector-aware one does not.** This is a concrete improvement for the `css_selector`
   lens: resolve the whole selector, not the names in it.
4. **Rendering the two pages the stylesheet loads on**: 32 of its 33 classes matched **zero**
   elements. All 69 rules unreachable.

That unwound into four dead layers and a twin: the stylesheet (377 lines), its bundle import
(8.9 KB shipped to every store and arena visitor), a **per-render server context build no template
read**, DOM writers in three JS places aiming at an element that has never existed — and
`AVATAR_BADGES`, an identical parallel copy. **569 lines removed.**

Two things to take from it:

- **The finding was not the bug; it was the entrance.** 15 selectors were reported. What was
  actually wrong was a whole feature. A tool that reports "these 15 names are unreferenced" is more
  valuable if it can also say **"and they are 15 of the 33 in one file, whose other 18 only match as
  descendants of one of them"** — i.e. cluster findings by file and by selector dependency. That
  sentence is what turned a tidy-up into a real cleanup.
- **The server-side half was invisible to the CSS lens and would have been the bigger win.** A
  Django context key computed on every request and read by no template is exactly the shape this
  tool should own, and it currently has no lens for it: `view` → `template variable` connectivity.
  On the hottest page in this app it was two loops, eight dict copies and two sorts, per request,
  for nothing.

**And the caution, from the same episode:** the first removal pass introduced a crash
(`ReferenceError`, every arena load) because a second use of a deleted declaration sat 36 lines
below the matched block. Syntax checks passed. **If seamcheck ever gains an "apply fix" mode, a
removal must be followed by a render, not a parse.** Dead-code removal is the change that looks
safest and is not — everything about it says nothing depended on this.

---

# Update · 0.10.0 · the REDIS layer, adjudicated key by key (117 claims → 92 red)

The Redis layer got the treatment the DOM lenses got: **every product red read at its actual line
before any verdict**, because both obvious methods are wrong in opposite directions — a shape-grep
(`user:[^"']*:stats`) cannot see a key assembled in Lua, and a fragment-grep matches a hash *field*
that happens to share the key's last segment. Both were run and disagreements resolved by reading.

**Of 63 product reds: 8 real and fixed · 8 real and logged OPEN · 20 deliberate keeps · 27 false.**

## T8 — the layer found a production-class bug that a full test suite had walked past

This is the headline, and it is worth more than the noise costs.

Five call sites deleted `user:{id}:stats_cache`. **Nothing has written that key** since the per-user
stats cache moved to stale-while-revalidate (`swr:user_stats:{uid}`). Four of them happened to also
call the correct helper and worked *by accident*. The fifth — the accelerated-period rollover — did
not, so in testing mode every simulated hour boundary invalidated nothing and the stats endpoint
served the pre-rollover blob until the TTL expired. Testing mode is how every timed regression
scenario runs, so the harness could disbelieve a reset that genuinely happened.

**Why no test caught it, which is the transferable part:** the existing boundary check compared the
**API against the DOM**. When the cache is stale, both surfaces read the same stale copy and agree.
Two views of one number can only ever prove they came from the same place. The scan found it by
asking a different question — *who writes this key?* — and the answer was nobody.

Three more phantoms fell out of the same question, each a key whose last writer was removed by a
feature demolition: `hof_rewards:{uid}`, `leaderboard:cumulative:{cat}:baselines`, and a login-time
`user:{id}:username` SETEX that put PII in Redis for an hour for no reader.

## F24 — the INCR return value IS the read · 6 keys

The single most common false red, and fully mechanical. A rate limiter never reads its counter
back; it uses what the write returned.

```python
count = await ar.incr(f"appeal_rate:{ip}")   # reported "0 read". This IS the read.
if count > 3: return 429
```

Also positionally out of a pipeline, which is the harder half:

```python
p.incr(f"user:{uid}:high_pps_count"); p.expire(...)
mismatch_count, _ = p.execute()              # ← the read, by tuple position
```

**Rule:** an `INCR`/`INCRBY`/`HINCRBY` whose result is bound to a name — directly, awaited, or
unpacked from `pipeline.execute()` — is a read as well as a write. Affected here:
`appeal_rate:*`, `unlimited:rate:*`, `user:*:high_pps_count`, `user:*:ws_time_hb_daily:*`,
`ws:push_rate:*`, `challenges:zero_joined:*`.

## F25 — F19's "read behind a variable", now measured: 8 keys, and three shapes

[F19](#f19--the-rediscache-lens-counts-an-invalidation-as-the-keys-definition--88-false) named this
cause. Here is what it costs a year later, and the three distinct shapes, in rising difficulty:

1. **Assigned one line above the use** — `cache_key = f'challenges:leaderboard_cache:{sid}:{limit}'`
   then `safe_get(self.r, cache_key)`. Single hop, same function.
2. **Returned by a key-builder function** — `def _rate_key(uid): return f'user:{uid}:cooldown_reset_count'`.
   The key never appears at a call site at all.
3. **Held in a module constant** — `_SETUP_COMPLETE_CACHE_PREFIX = 'user:setup_complete:'`, or
   returned by a helper (`swr:cold:slots` comes back from a function whose whole body is that
   string).

Shape 1 alone would close most of it.

## F26 — the key is assembled inside a Lua string · 2 keys

Neither of these spellings exists anywhere in Python source:

```python
# lua_scripts.py — concatenation across quote boundaries
local daily_fired_lock = "user:" .. user_id .. ":daily_reset_fired:" .. daily_scope
# redis_atomic_operations.py — built entirely inside register_script(...)
local key = 'dedup:' .. user_id .. ':' .. request_id
```

`user:*:daily_reset_fired:*` is *both* written and read this way — the value comes back to Python as
`result[7]`. **Rule:** parse Lua inside `register_script(...)` / `SCRIPT LOAD` string literals;
`..` concatenation is the give-away. This project runs ten such scripts on its hottest path, so the
blind spot sits exactly where the traffic is.

## F27 — the reader is a SCAN over a prefix, not a lookup · 3 keys

`analytics:seo:ref:{host}:{day}` reads write-only until you find, in a different app,
`r.scan_iter('analytics:seo:*', count=500)` powering the admin dashboard. **Rule:** a
`scan_iter`/`SCAN MATCH` pattern is a read of every key it matches.

## F28 — the reader is a legacy-fallback accessor · 3 keys

```python
hash_get_or_string(r, f"user:{id}:stats", "total_avatars", f"user:{id}:obtained_avatars")
```

Reads the hash field, falling back to the standalone key. The third argument is a genuine reader
that no `get(` pattern matches. **Rule:** treat a project's own accessor helpers as read sites for
every key-shaped argument. (Discoverable: these helpers are the functions that take a key-shaped
f-string in more than one argument position.)

## The ask list from this surface, ranked

### 1. Split `delete-only` from `write-only`. They share one red and the ratio is 43 : 17

Straight from each node's own `sub` field, on the 60 product reds:
`N write / 0 read` **43** · `N invalidate / 0 read` **17** · `0 write / 1 read` **1**.

**Every fix that mattered this session came from the 17.** The asymmetry is not stylistic:

- **A write with no reader is usually correct.** `telemetry:*`, `admin:config_actions`,
  `audit:item_deletion:*` exist to be read by a human with `redis-cli` after an incident. No scan
  will ever see that reader. Permanently, correctly red.
- **A delete with no writer is almost always a bug, and it is invisible by construction.** `DEL` on
  a missing key returns 0, raises nothing, logs nothing, and the surrounding code reads as working
  invalidation. That is precisely how T8 survived a full suite.

Two finding types with different default severity would do more for this codebase than any new
analysis. Right now the important signal is buried under the boring one at 2.5 : 1.

### 2. Collapse a defensive reset block into ONE finding · 22 of 92 reds (24%)

All 22 test-side reds come from a single contiguous list at `pointless/views/test_auth.py:275` — a
harness reset that clears many keys defensively. One deliberate decision, reported twenty-two times.
**Rule:** when N keys are touched *only* by one contiguous delete/reset block in one function, emit
one finding naming the block, with the keys as its rows.

### 3. Cleanup/erasure context is legitimate and can never be actioned · 6 keys

Six of the seventeen delete-only keys are GDPR erasure (`gdpr_service.py:2302–2347`) or logout/leave
cleanup. Deleting a key an old account might still hold **is the point** — the code is correct, the
key is genuinely dead, and the finding is unactionable forever. **Rule:** a delete inside a function
whose name or docstring matches `erase|anonymize|gdpr|cleanup|reset|leave|logout|teardown` is a
distinct low-severity type. Don't suppress it; don't rank it beside a phantom bust on a hot path.

### 4. Scope: read `.gitignore`, skip archived paths · 9 reds

`OTHER/seed_demo.py`, `OTHER/cold_prep.py`, `OTHER/trace_thread_sensitive.py`,
`OTHER/management_commands_archived/`, `docs/audits/_legacy/`. In this repo `OTHER/` is gitignored
by convention and the other two are dead by name. ~10% of the noise for a `.gitignore` read — and it
is the same rule that already stops the map from "confirming" findings about itself.

### 5. Report SITES, not keys

Eight real sites were removed and the count moved **95 → 92**, because a key only leaves the list
when *every* site touching it is gone. `user:*:username` still shows `3 invalidate / 0 read` — the
defect (the login SETEX) is gone, three legitimate deletes remain. Anyone reading the headline would
conclude almost nothing happened. "8 sites resolved across 5 keys" is the true unit of work.

### 6. State the limits next to the count

Asked "is all Redis clean now?", the honest answer needed four caveats, each of which the tool knows
and the report does not say:

1. **Code is not the keyspace.** Four environments here run independent Redis instances; keys
   written by code that no longer exists still sit in preprod and prod. `0 red` would say nothing
   about them.
2. **Untraced entry points** — Celery, Redis subscribers, WS handlers, Stripe webhooks. The scan
   prints this globally; it belongs *beside the Redis count*, since Redis is exactly where those
   four reach.
3. **53 keys are `uncertain`** — not evidence of health.
4. **The count cannot reach zero on correct code**, per asks 1 and 3.

"17 delete-only, 6 of them erasure context; 43 write-only; 53 uncertain; Celery/WS/webhook paths
untraced" is a far more useful line than "92".

## Score for this surface, after acting on it

| Finding kind | Product reds | Outcome |
|---|---:|---|
| delete-only (`N invalidate / 0 read`) | 17 | **8 sites fixed across 5 keys**, incl. the T8 defect; 6 are legitimate erasure context |
| write-only (`N write / 0 read`) | 43 | 8 logged OPEN as real (need a product decision, not a delete); ~20 deliberate audit/telemetry |
| read-only (`0 write / 1 read`) | 1 | fixed earlier the same week (a snapshot reading a streak key nothing wrote, so every band was empty) |

**Precision on the class that matters — delete-only — was 11/17 actionable (65%).** That is far
better than the 10-15% baseline the DOM lenses run at, and it is the argument for splitting the
types rather than tuning one threshold.

## Same day · the asks landed, verified from the consumer side

Re-scanned pointlessbutton a few hours later and the number moved 95 → 33 red. Almost none of
that was us fixing code — it was the scanner learning to see keys it had been calling dead.
Confirmed by reading the nodes, not the count:

| key | before | after | which fix |
|---|---|---|---|
| `appeal_rate:*` | `1 write / 0 read` | `connected · 2 write / 1 read` | F24, INCR return is the read |
| `unlimited:rate:*` | red | `connected · 5 write / 2 read` | F24 via pipeline unpack |
| `user:*:obtained_avatars` | red | `connected · 8 write / 4 read` | F28, legacy-fallback accessor |
| `user:*:daily_reset_fired:*` | red | resolved | F26, assembled in a Lua string |
| the 22 from one test reset list | 22 rows | 3 | the aggregate-symbol fix |

**`seamcheck report` now leads with "Invalidations that clear nothing" (26)** and separates
**"Erasure and teardown deletes (correct, and dead)" (7)**. That is ask #1 and ask #3, and the
framing line — *"a delete of a key nothing writes clears nothing, returns 0, and raises nothing,
so it reads as working invalidation forever"* — is the whole point in one sentence. The limits
caveat on the Redis section (ask #6) is there too.

**One caution from adjudicating the new output.** We briefly thought delete-only keys had been
*suppressed*, because the graph JSON no longer carries a `redis_key` node for them — they exist
as `redis_key_use` with status `connected`, and a consumer counting red nodes in the JSON sees
them vanish. They are correctly present in `report`. Worth making the JSON say what the report
says, since anything automated reads the JSON: a delete-only key wants a node with a status a
script can filter on, not just a report section. Otherwise the highest-value class is the one
least visible to tooling.

**Still open: ask #4 (scope).** 10 of the 32 remaining "Redis keys" rows are `OTHER/seed_demo.py`,
`OTHER/cold_prep.py`, `OTHER/hourly_user_probe.py`, `OTHER/management_commands_archived/` and
`docs/audits/_legacy/`. `OTHER/` is in this repo's `.gitignore`. That is ~31% of what is left in
that section, for a `.gitignore` read.

**And the split immediately paid for itself.** Working the new "invalidations that clear nothing"
list turned up a live bug in the store: `_clear_store_rotation_caches` deletes
`store_rotation:test:{date}:{period}`, but below a 1-hour rotation interval the cache is keyed
`store_rotation:test:{unix_timestamp}` — a shape no `{date}:{period}` delete can match. Sub-hour
intervals are what the preprod environment runs, so on the environment where an admin is most
likely to add a store item, the "appears immediately" guarantee silently did not hold. Verified
against the pre-fix tree at 60s, 120s, 300s and 1800s: the stale cache survived every time.
Nothing errored and nothing logged, because deleting an absent key is a success.

---

## Answered · 0.11.0 · what the lens does now, ask by ask

Written from the other side: this is the tool's reply to the list above, and every line of
it is a test that fails without the change.

| ask | state |
|---|---|
| 1 · split delete-only from write-only | **done** — `redis_invalidation` and `redis_key`, ranked apart |
| 3 · erasure context is its own low-severity type | **done** — `redis_cleanup`, by the name of the function or the module it sits in |
| 6 · state the limits beside the count | **done** — the caveat rides with the Redis groups, not only the footer |
| 4 · read `.gitignore`, skip archived paths | **done** — simple entries only; a glob, a path or a negation is left alone |
| 2 · collapse a defensive reset block into one finding | **not done** — the dedupe and the erasure kind took most of it (22 rows → 3); the general rule is still worth writing |
| 5 · report SITES, not keys | **not done** — the honest unit of work, and a bigger change than it looks: it is the symbol model, not the Redis lens |

And the five false-positive classes:

- **F24** — an `INCR`/`HINCRBY`/`DECR` whose value is used, awaited, or unpacked from a
  pipeline that is actually drained, is a read. A pipeline nobody drains stays a write.
  Deliberately not `rpush`: its answer is a length, and a dead key that looks alive costs
  more than the red it removes.
- **F25** — all three shapes. The one that mattered was not indirection at all: **two
  functions of one name**. `safe_get(r, key, default=None)` is the wrapper; a helper
  nested in an export is also `safe_get(key, default=None)`, and keeping only the last
  signature filed every key under `default`.
- **F26** — a Lua local keeps its whole name now, and a Lua `SET … "NX"` is the same
  guard as `set(key, v, nx=True)`: `if not redis.call(…)` is the read.
- **F27** — a sweep is classified by **what consumes it**. `scan → hgetall` is a read of
  every key it matches — and a `*` spans separators, so `analytics:seo:*` reaches
  `analytics:seo:ref:{host}:{day}`. `scan → delete` is still a wipe and still vouches for
  nothing. Two guards: two named segments, and the sweep must end in a read.
- **F28** — already closed by the parameter hop: a key handed to a helper is a key that
  helper touches, whichever argument it arrives in.

**On the caution.** A delete-only key does have its own node in the graph JSON — kind
`redis_invalidation`, status `unused`. What is true is that a consumer filtering
`kind == "redis_key"` loses it silently, which is the same trap as any renamed field. The
map, the report and the console all carry the new kinds; **anything automated should read
`redis_key`, `redis_invalidation` and `redis_cleanup` as one family.**

Reference project after all of it: **92 claims → 62**, no code deleted to get there, and
the two claims that were provably wrong are connected.

## A new ask, found by removing a dead key: the tests assert on dead keys too

Removing the `user:{uid}:stats_cache` deletes broke exactly one test, and it broke for the wrong
reason:

```python
assert any(f"user:{uid}:stats_cache" in str(c) for c in fake_redis.delete.call_args_list)
inval.assert_called_once_with("user_stats", uid)   # ← the line that carries the behaviour
```

The key has had no writer since that cache moved to SWR. So the assertion guarded a call that
cleared a key which does not exist — **it watched the CALL, not the EFFECT.** It passed for as
long as the dead code was there, and failed the moment the dead code was removed: the one change
it should have welcomed.

The comment immediately above it said the same thing about a *different* dead key, removed in an
earlier pass. Two dead assertions, one line apart, and only one had been noticed.

**The ask:** the scan already knows which keys have no writer. It also parses the test tree. A
string literal naming a writer-less key, inside a test, is a **dead assertion** — a distinct and
quite valuable finding, because it is where false confidence is stored. It also explains why a
delete-only key can survive so long: the suite is actively defending it.

Related, and cheap: the same rule flags a test that asserts on a key which no product code
touches at all — a test guarding a feature that has already been deleted.

## Correction: F28 is WRONG, and backwards. Please do not implement it as written

F28 said an accessor helper's key-shaped arguments should all count as read sites, using this:

```python
hash_get_or_string(r, f"user:{id}:stats", "total_avatars", f"user:{id}:obtained_avatars")
```

I checked the helper's body afterwards. It does not read the fourth argument:

```python
def hash_get_or_string(r, hash_key, field, string_key=None, default=None, cast=None):
    """Read from hash only. String fallback removed (Phase 3 migration).

    The string_key parameter is retained for call-site compatibility but ignored.
    """
```

**The parameter is dead at 78 product call sites**, carrying ~23 distinct legacy key names —
`user:{id}:pbits` ×11, `:avatar` ×5, `:country` ×4, `:hour_streak` ×7, and so on.

So the rule I gave you would have taught the scanner to see a **phantom reader** on every one of
them, and phantom readers are worse than phantom deletes: a phantom delete makes you look at
something harmless, a phantom reader makes a genuinely dead key look alive and drops it out of
the findings entirely. It would have *hidden* real work — including two live bugs I only found
afterwards by reading the project's own OPEN log (an admin column and a cold-rebuild path both
reading keys nothing writes).

**The rule that is actually right, and it is a better feature than F28 was:**

> A key-shaped literal passed into a parameter the callee never reads is a **phantom reader**.
> Resolve the helper's body once; if a parameter is unused, every key passed to it is *not* a
> read site — and the call sites are themselves a finding worth reporting.

That second half is the valuable part. This codebase's own audit log already carries the finding
(`PB-REDIS-PHASE3-DEADWRITE`: "delete the ignored parameter from all 124 call sites so the next
reader is not misled into thinking a fallback exists") — and *the next reader was misled*, by me,
within the hour. A scanner that resolves one function signature catches what a careful human
reading the same line did not.

F24 (INCR's return value), F25 (variable/builder/constant), F26 (Lua string) and F27 (SCAN
prefix) all still stand — I verified each against the helper bodies. F28 does not.

## The dead-assertion feature landed — first read from the consumer side

`seamcheck report` now carries **"Tests holding a dead key in place" (28)** on pointlessbutton.
Thank you — that is the ask from the previous section, and it is the right shape.

Two things from using it, one small and one that repeats an earlier caution.

**1. It inherits the Redis lens's existing blind spots, so its precision is roughly the lens's.**
Of the first nine rows, at least four are false, and all four are shapes already written up here:

| row | why it is not a dead key |
|---|---|
| `store_rotation:test:*` | written as `cache_key = f"store_rotation:test:{period_start_time}"` then `cache.set(cache_key, …)` — F25, variable one hop away |
| `store:basic_items:button` | same shape, `button_utils.py` |
| `store:basic_items:avatar` | same shape |
| `audit:item_deletion:*:*` | a write-only audit trail **by design** — read by a human with `redis-cli` after an incident, which is the "erasure and teardown" carve-out applied to a different intent |

That is expected — the new section is downstream of the same key resolution — but it is worth
saying out loud, because a section titled "tests holding a dead key in place" reads more
authoritative than "unused", and a false positive here costs more: acting on it means DELETING a
test assertion that is actually load-bearing. Of the two error directions, this one should lean
conservative.

**2. It is report-only. There are no JSON nodes for it** — the same gap flagged two sections up,
now reproduced in the new feature. A consumer counting rows in `connectivity-map.json` sees zero
of these 28. Anything automated reads the JSON, so a finding that exists only in the markdown is
invisible to exactly the tooling most likely to act on it.

**What the fix cycle looked like from here, since that is the real measure.** Acting on
`PB-REDIS-PHASE3-DEADWRITE` — the phantom-reader class this repo's audit log had already
described and nobody had actioned — removed 78 call sites and four dual writes. Straight after,
the scan moved:

- **Redis keys 32 → 21**
- **Invalidations that clear nothing 26 → 28** — up, and *correctly*: removing the phantom readers
  exposed two keys as delete-only. A number going up because the graph got more honest is the
  behaviour you want.

The project's own key-registry ratchet independently demanded its baseline shrink by three
(`total_avatars`, `total_hour_streak`, `yesterday_total_pushes`) — those names existed in the tree
*only* as arguments to the ignored parameter. Two independent tools agreeing that the codebase got
smaller is the strongest signal either of them produced today.


---

# 2026-09-07 · from the seamcheck side · open items, deliberately deferred

Written by the session that rebuilt the CLI and MCP surfaces for agents (branch
`agent-first-surfaces`, 37 commits). Everything below was **found and reproduced**, then left
unfixed on purpose so the branch could ship for hands-on testing. Recorded here rather than lost,
in the same spirit as the rest of this file: a known gap is cheaper than a surprise.

Whoever picks one up: the branch's own reports carry the file:line and the reproduction commands,
under `.superpowers/sdd/2026-09-06-agent-first-surfaces-plan/` — `branch-review-A-properties-seams.md`,
`branch-review-B-guards-duplication.md`, `branch-review-C-claims-userfacing.md`.

## S1 — `seamcheck map` blocks forever, and the README quickstart does not say so · HIGH

`map`/`serve` end in `serve_forever()` with no timeout and no TTY guard. A human presses Ctrl-C; a
program hangs until it is killed. `llms.txt` now warns, but `README.md`'s own quickstart
(`pip install seamcheck && seamcheck map`) and `docs/commands.md`'s one-liner do not.

**Suggested fix:** default to not serving when stdout is not a TTY — the ordinary behaviour for a
tool that may be piped or driven by a program — keep the interactive default, and say so in both
documents. Small; deferred only because a human tester will never hit it.

## S2 — the two CLI doors still disagree on exit codes · HIGH

Four `raise CommandError(...)` sites take Django's default `returncode` of 1, where `cli.main()`
returns 3 for the same failure. Worse: an unrecognised flag on `manage.py seamcheck` exits **2**,
which collides with `EXIT_NO_BASELINE` — and `docs/ci.md`'s own recipe reads 2 as "no baseline yet,
do not fail the build". So a typo'd flag in CI reads as a clean first run.

No document claims the two doors agree, so nothing shipped is factually *wrong* — it is silent.
The exit-code hygiene guard (`seamcheck/tests/test_exit_code_hygiene.py`) does not cover this shape
because these codes come from Django's `run_from_argv`, not from a literal in our source.

## S3 — what the derived guards do not catch

The four guards this branch added each catch the regression they were written for — that was
verified, including against the original bug's exact shape. Their blind spots, none reachable in
the code as it stands today:

| guard | misses |
|---|---|
| `test_tool_state_writes.py` | a path built by string concatenation or `os.path.join`; matches `repo_root` **by name**, so an unrelated local of that name would be misflagged; multi-arg `Path()`; `open(..., "r+")` |
| `test_exit_code_hygiene.py` | a code stashed in a local first (`code = 2; return code`); `sys.exit(N)`; a negative literal (`return -1` parses as `UnaryOp`); a helper defined in another file |
| `test_cli_entrypoint.py` flag parity | proves a flag is recognised and reaches the options dict, never that anything **reads** it — `--no-progress` already lives in that gap |
| `test_docs_promises.py` | substring matching: `serve` is a substring of `observe`, so a command's own doc line could be deleted and the test would keep passing |

## S4 — duplication that survived

- The `--json`/`--format` and `--serve`/`--no-serve` fold logic exists in both `cli.py` and
  `Command.handle()`. They agree today and tests pin that, but the rule lives in two places.
- Six scanner sub-walks (`find_js_files`, `inventory`, `services`, `env_extractor`,
  `redis_extractor`, `callgraph`) each hard-code their own "skip dot-directories" rule on top of
  `SKIP_DIRS`, so the scan cache hashes slightly more than the scanner reads. One-directional:
  extra cache misses, never a stale answer.

## S5 — smaller, each real

- `check`/`report`/`map` stamp `generated_at`, so two identical runs differ. The determinism test
  added on this branch covers only the new envelope commands.
- `seamcheck_check` (MCP) is still unbounded — no `limit`/`cursor`/`since` — and its description
  says findings are "new since the last snapshot" while `passed` is true for any untriaged finding,
  new or not. On a repo with a backlog an agent draws the wrong conclusion.
- `--limit banana` is refused by argparse and **silently falls back to the default** on the plain
  door. The test pinning it is named for parity while what it pins is non-parity.
- A bare `=5` argument is dropped by the plain door and refused by argparse.
- `_map_plain` never honours `--open` in its non-serving branch; the Django door does.

## S6 — performance, measured before any design work · the biggest single win in the codebase

Profiled a real scan of the reference project: **53.0s clean wall, 52,174 symbols.** Under
cProfile (147s total — the ratios are the answer, not the seconds):

| where the CPU is | share |
|---|---|
| `pipeline.py:625-630` | **~36%** |
| AST walking (`ast.walk` + `iter_child_nodes` + `iter_fields` + `isinstance`) | ~26% |
| everything else (I/O, paths, graph assembly) | ~38% |

**The 36% is one quadratic.** For each of ~102,000 `dom_selector` symbols it rescans the entire
`dom_edges` list looking for an unresolved self-edge — **852,616,050 generator iterations**:

```python
and any(edge.from_id == symbol.id and edge.to_id == symbol.id
        and edge.status is Status.UNRESOLVED for edge in dom_edges)
```

Build that id set **once**, then test `symbol.id in it`: O(N+M) instead of O(N×M), same answer,
one small edit, worth roughly **15–19 seconds of the 53**.

The ~26% in AST walking is the part worth **parallelising** — it is per-file independent, and the
package currently uses **no parallelism at all** (`multiprocessing`/`ProcessPool`: zero call sites)
on a 12-core machine.

**A rewrite in Go or C++ is not indicated.** `ast` and `re` are already C inside CPython; the hot
path is a Python-level algorithm choice, and no language rewrite fixes an O(N×M) loop — it just
runs the wrong shape faster. Do the quadratic first, parallelism second, and measure again before
anything more drastic.

## S7 — three tests read a machine-wide cache, so they are not deterministic · and the gap that hid behind them

`test_a_bare_check_scans_once` and two siblings wrap the **real** `api.scan` and assert it is called
once, against `repo_root='.'` — this actual checkout — using the real on-disk cache at
`~/.cache/seamcheck/<hash of repo path>/`, with no isolation. Any prior `seamcheck` invocation
anywhere on the machine warms that cache and falsifies the assertion, regardless of whether the
code is correct. A reviewer found three stale entries from its own runs already sitting there.

**These tests can pass or fail on machine state.** That is worth fixing on its own: every green
run of this suite has been trusted, and these three are only as trustworthy as whatever ran before
them.

**The gap they hid:** routing the Django door's `check` through the scan cache was correct and was
*not* done, because it made these three tests go red and the red was read as the cache being wrong.
So today `manage.py seamcheck --check` still pays a full scan every run while the plain
`seamcheck check` and MCP's `seamcheck_check` do not — a two-door performance divergence, which is
the exact class the CLI/MCP branch existed to eliminate.

**Fix shape:** point the three tests at an isolated cache root (a temp directory), then convert the
Django door's pre-scan. Small, and it closes both the non-determinism and the divergence.

## F29 — connected but unreachable: a CSS rule matches, so the graph sees an edge, but nothing can reveal the element

(Recorded from CLAUDE.md, 2026-09-07, where it was already assigned this number.) Two whole dead
features in pointlessbutton — a purchase confirmation popup and a filter bar — are *connected but
unreachable*. A CSS rule matches them, so the reference graph has an inbound edge; nothing in any
script can ever reveal them. Seamcheck models whether a name is **referenced**, not whether an
element can be **reached at runtime**. Both were found by a human reading JS.

## F30 — the same CSS property written by four files, each correct on its own

**The evidence, unedited.** Push Arena, 2026-09-12. Three nested boxes share one rectangle:

```
.main-push-area-wrapper   border-radius: 19px      css/pages/push-arena/main-push-area.css
.main-push-area           border-radius: 16px      css/pages/push-arena/main-push-area.css
                          border-radius: var(--mobile-border-radius)   mobile-core.css     @media (max-width:1199px)
                          border-radius: 12px                          mobile-breakpoints.css @media (max-width:430px)
                          border-radius: 10px                          mobile-breakpoints.css @media (max-width:360px)
#activeButton             border-radius: 16px      × 59 files in buttons/css/*.css
                          border-radius: 16px      inline, buttons/js/retro_arcade.js:88 and fish_hunter.js:249
```

Measured on the running page at 430px: **19 / 12 / 16**. The innermost box paints its corner 4px
inside the clip meant to contain it, so a wedge of page background shows at every corner of the
game's main play area. It shipped because on desktop the three happen to agree (19/16/16 nests
correctly) and because the defect is only *visible* against a light-coloured scene — with a dark
button equipped the wedge is the same colour as the page.

**Why it was missed.** Every one of those declarations is reachable, referenced and used. There is
no dead code here and no broken link. Seamcheck asks "is this name referenced"; the question this
bug needs is "**how many places write this property on this element, and do they agree**". Related
to, but not the same as, the multiple-writers rule the project already applies to DOM updates —
here the writers are CSS rules in five files across three media queries plus two `style.cssText`
strings, and the winner depends on bundle order, which a Vite build re-decides on every deploy.

**The lens that would catch it.** For each element selector that the CSS graph can resolve, group
the declarations of a *layout-visual* property (`border-radius`, `padding`, `overflow`, `z-index`)
by property and count the distinct **literal** values across all matching rules, ignoring rules that
consume the same custom property. Flag where a literal appears in N≥2 files for the same element AND
at least one of the values differs. The signal is not "duplicated" — it is "duplicated **and
disagreeing**".

**False-positive class it produces, so the cost is visible:** deliberate responsive overrides, which
are extremely common (`padding` at three breakpoints is normal and correct). This lens is only
useful if it can tell a *breakpoint ladder* (same property, monotonic values, one file, one element)
from a *drift* (same property, values that are not related to each other, different files, elements
that are nested inside one another). The nesting relationship is the discriminator worth building:
three boxes with identical `getBoundingClientRect` and three different radii is a shape a static
graph can approximate from the selectors, and it is almost never intentional.

**Counts from this project:** 1 element, 4 writers of one property, 59 downstream files carrying a
literal that contradicted it, and 2 more writing it inline from JavaScript. The static test written
to close it flags **59 offenders** on the pre-fix tree — that is the scale a tool would have
surfaced in one run.

## F31 — a database row naming code that does not exist: the product with no implementation

**The evidence, unedited.** `StoreItem(item_type='button', item_id='pinata')` was a sellable,
giftable, 300-PBit product in pointlessbutton for months. It had
`static/pointless/img/buttons/pinata/preview.svg` and `static/pointless/sounds/pinata.mp4`. It had
**no `static/pointless/buttons/js/pinata.js`** and **no entry in the `BUTTON_LOADERS` map in
`static/pointless/js/main.js`**, which is what the page lazy-loads buttons from. Buying it gave the
player something that could never be mounted. Reported by the owner 2026-06-06, deactivated three
separate times as data, and still re-addable from the admin form on 2026-09-12.

**Correctly out of scope, and worth writing down as such.** Seamcheck scans a repository; the row
lives in a database, and no scan of the source can see it. The tool did not flag this and **should
not be expected to** — recording it here so the next person does not build a lens for a fact that is
not in the corpus.

**What IS in scope, and was missed:** the two assets. `img/buttons/pinata/preview.svg` and
`sounds/pinata.mp4` have **no reference anywhere in the source tree** — no template, no CSS, no JS,
no Python. They were reachable only by a `{item_id}` string interpolation against a DB value. Every
other button's assets are reachable the same way, so a naive "unreferenced asset" sweep either flags
all 63 or none. The discriminating fact is that 62 of those ids **also** appear as a literal key in
`main.js`, and `pinata` does not.

**The lens that would catch it.** When assets live in a directory whose names are consumed by
runtime interpolation (`img/buttons/<id>/`, `sounds/<id>.mp4`), seamcheck already has to decide
between "all assembled, all reachable" and "none referenced". A third answer is available and is
the useful one: **enumerate the sibling set, find the key set that the interpolation is driven by
(here a literal object map in one file), and report the difference.** 62 siblings with a key, 1
without, is a far stronger signal than either "63 unreferenced" or "63 fine". The same shape covers
icon directories, locale directories, per-template partial directories.

**False-positive class:** a sibling set genuinely driven from the database or from user content,
where "not in the literal map" is expected and not a defect. So this must report the *asymmetry*
("1 of 63 does not appear in the driving map") rather than asserting the file is dead, and it needs
the driving map to be a single unambiguous literal — if the ids come from more than one place, the
lens should decline rather than guess.

**Counts from this project:** 63 sibling asset directories, 62 driven keys, 1 asymmetric — and that
1 was a real, months-old production data bug that a human found by playing the game.

## F32 — `multi_writer_element` catches JS writers of one node; it does not catch CSS writers of one property

**The evidence, unedited.** Live-queried against pointlessbutton's own
`docs/maps/connectivity-map.json` (53,989 symbols, 175,762 edges) on 2026-09-12, the same day
`PB-ARENA-STAGE-RADIUS` was fixed there:

```
$ python3 -c "
import json
d = json.load(open('docs/maps/connectivity-map.json'))
mw = [s for s in d['symbols'] if s.get('kind')=='multi_writer_element']
hits = [s for s in mw if 'push-area' in s.get('label','').lower() or 'activeButton' in s.get('label','')]
print(len(hits))"
0
```

Zero. The real bug that day was `.main-push-area`'s `border-radius` declared by **four** CSS
files with disagreeing values (16px / `var(--mobile-border-radius)` / 12px / 10px), producing a
visible corner mismatch confirmed on a live render. `multi_writer_element` (91 hits project-wide,
genuinely the closest existing feature to this bug class — its own note text says "Pick one
canonical owner and route the others through it," which is exactly the fix this bug needed) is
scoped to JavaScript writes discovered via `querySelector`/`getElementById` call sites
(`snippet` values in every sampled hit are `querySelector(...)`, `getElementById(...)`, or a
`.className =` style assignment). It never compares a **CSS declaration** in one file against
the same property on the same selector in another file.

**Why it was missed, in terms of the model.** The detector's edge is "a JS statement writes to a
property of a node reached by this selector." A `border-radius: 12px;` rule in a stylesheet is
not a JS write and has no reaching call site — it is invisible to the detector's traversal
entirely, not merely unflagged. The four rules that fought over the arena's corner were each,
individually, a completely normal, connected, correctly-scoped CSS declaration; nothing about
any single one of them is wrong. The defect only exists in the RELATION between the four.

**The lens that would catch it** (this is F30, restated with the live confirmation above rather
than as a hypothesis): for each CSS selector the graph already resolves, group `border-radius` /
`padding` / `overflow` / `z-index` declarations across every file that targets it (ignoring rules
that consume the same `var(--token)`), and flag where **two or more literal values disagree**.
`multi_writer_element`'s existing "pick one canonical owner" framing is the right output shape —
this is asking for the same treatment extended from JS-writes-a-node to CSS-declares-a-property.

**False-positive class:** a deliberate responsive ladder (`padding: 8px` at 1200px narrowing to
`padding: 4px` at 360px) is *also* "N files disagree on one property for one selector," and is
completely normal. The discriminator that survived contact with this bug: a ladder's values are
monotonic and live in ONE file organized by media query; a drift's values are scattered across
files with no visible relationship, and — the strongest signal — the values disagree even at the
SAME media-query bucket (the arena bug's 12px-vs-16px collision was both inside `@media
(max-width: 1199px)`, in two different files, not a step of a ladder).

**Counts from this project:** 1 selector, 4 conflicting declarations across 3 files, plus 59
downstream files carrying a fifth, independently-drifted literal for the same visual property on
a nested element — `multi_writer_element` currently reports 0 of any of this.

## Correction / extension — F31, phoenix confirms the pattern generalizes past assets

While building a project-local completeness audit in response to F31 (see
`scripts/audit_button_completeness.py` in pointlessbutton, 2026-09-12), the same asymmetric-sibling
technique immediately surfaced a **second**, independent instance of the identical shape — not an
asset this time, but a template branch: `button_card_grid.html` and `store.html` each carried an
`{% elif item.preview_button_id == 'phoenix' %}` branch (three occurrences total) rendering an
entirely unstyled "PHOENIX"/"BLOCK BUSTER" preview. `phoenix` is not a key in
`STORE_ID_TO_BUTTON_ID` (the dict that produces every legal value of `preview_button_id`) and never
was in the version of the constant checked — the branch is provably unreachable, and its CSS
classes (`phoenix-preview`, `phoenix-minimal`, `phoenix-super-title`, ...) have zero rules anywhere
in the stylesheet tree. Removed in the same commit as the F31 fix — real, small, dead.

This confirms F31's lens generalizes beyond image/sound assets: **any place a template or script
branches on a string literal drawn from an enumerable, small key-set (a Python dict's values, a JS
object's keys) is a candidate for the same asymmetric-sibling check** — enumerate the literal
branch values on one side, the enumerable key-set on the other, and report values on the branch
side with no matching key. Here the "sibling set" was template `elif` branches rather than files in
a directory, and the "driving map" was `STORE_ID_TO_BUTTON_ID`'s value set rather than a
`BUTTON_LOADERS` object's keys — the shape of the check is identical, only the two enumerable sets
being diffed changed.

**Counts:** 3 `elif` branches (2 templates, one with a mobile-duplicate section) reachable by
nothing, confirmed via a plain string search across the dict that would have to produce the value
for the branch to ever execute.


## F33 — `redis_key` "written here, read nowhere" is wrong when the reader loops over a suffix list

**The evidence, unedited.** `findings --status unused` flags `pointless/views/push_views.py:2291`:
`pipe.zadd(interactions_key, ...)` where `interactions_key = f"user:{user_id}:interactions"`, note
"Written here and read nowhere in this repo." It IS read: `pointless/services/tiering_service.py`
defines `USER_KEY_REGISTRY['sorted_sets'] = [..., 'interactions']` and `read_all_user_keys(r, uid)`
/ `write_all_user_keys(r, uid, data)` both do `for suffix in USER_KEY_REGISTRY['sorted_sets']: ...
f"user:{uid}:{suffix}"` — the literal string `'interactions'` sits in a Python list, not next to a
`zrange`/`zadd` call, so nothing in the source has the substring `"user:{uid}:interactions"` or
`zrange(interactions_key` for a grep-shaped or call-site-shaped detector to find. A dedicated
completeness test (`tests/unit/services/test_key_registry_complete.py`) already exists specifically
because this registry is the single source of truth for cold-storage archive/restore, and a key
missing from it is a silent data-loss bug on rehydrate — so this key is about as "read" as a key
can be, just not through a literal adjacent to the write.

**Why it was missed, in terms of the model.** Same shape as F26 (key assembled inside a Lua string)
and F28 (legacy-fallback accessor): the read is real but the KEY NAME is one hop removed from the
call site — here via a registry list consumed by a generic loop, there via Lua/legacy accessors.
The common thread across F24–F28 and this one: **any place a key suffix lives in a data structure
(list, dict, dispatch table) that a loop later interpolates into `user:{uid}:{suffix}` is invisible
to a call-site scan**, regardless of which language feature does the indirection.

**The lens that would catch it.** For a Django/Python project specifically: when a `zadd`/`hset`/
`set` call builds a key as `f"user:{{uid}}:{const}"` where `const` is a literal string, search the
whole repo for `const` appearing **as a list/dict element** (not just as a call-site argument) —
`'{const}'` inside `[...]` or `{...}` literal syntax — and treat that structure's generic consumers
(any function that iterates `for x in that_list`) as readers of every key the structure names. This
is the same "enumerable driving set" shape as F31, applied to Redis keys instead of files.

**False-positive class:** a suffix that appears in an unrelated list by coincidence (e.g. a UI
label list that happens to contain the word "interactions"). The lens should require the list
itself to be interpolated into an `f"...:{}"` pattern inside the SAME file, not just contain the
matching string anywhere.

**Counts from this project:** 1 key checked, 1 false "unused" — this is the entire push-arena-scope
`redis_key` finding count from this sweep (1 of 1, so 100% false on this small sample; not a claim
about the 91-project-wide `redis_key` finding count, which was not re-audited here).

## F34 — two live readers of a MISSING element made the tool MORE confident it exists, backwards

**The evidence, unedited.** `findings --status uncertain` on `level_progress_bridge.js:338`,
kind `dead_region`, label `showLostStreakBuyBackButton`: *"The guard returns on lostStreakBtn,
lostStreakText, which no template renders - but 1 other module(s) reach for the same element, so
it is more likely rendered [elsewhere / dynamically]."* Independent check: `grep -rn
'lostStreakBtn\|lostStreakText' pointless/templates/` (whole tree, not just arena) → **zero
matches**, in any template. Both `level_progress_bridge.js` (`showLostStreakBuyBackButton()` /
`hideLostStreakBuyBackButton()`) and `pointless/static/pointless/js/hourly_streak_manager.js`
(`recoveryButton: document.getElementById('lostStreakBtn')`) read the id and both guard on it
being null (`if (!buyBackBtn || !buyBackText) return`). The backend endpoint they poll
(`/api/streak/save/opportunities/`, every 60s via `window.updateLostStreakButton`) is live and
real — the whole "Wake Up Sleepy Streak" hourly-streak-buyback UI is coded, wired to a real API,
and has a CSS class (`.streak-buy-back-btn`, independently flagged `unused` in the same sweep at
`extracted-inline.css:187`) — but the button itself does not exist anywhere in the DOM. Two live,
non-dead call sites reach for it and both silently no-op, forever.

**Why it was missed, in terms of the model.** The heuristic behind "1 other module reaches for
the same element, so it is more likely rendered" is sound in general (F23's territory: multiple
independent readers usually correlate with the element being real) but it silently assumes at
least one reader is unguarded, or that guardedness is independent across readers. Here both
readers use the IDENTICAL guard shape (`if (!el) return`), which is exactly the "silent no-op"
pattern this project's own tooling warns about elsewhere — so "N readers, all guarded, 0
templates" is actually a STRONGER dead-feature signal than 1 reader, not a weaker one. The model
counted readers without checking whether every one of them was defensively coded to survive the
element's absence.

**The lens that would catch it.** When ALL readers of a `getElementById`/`querySelector` target
share a `if (!el` / `if (!el1 || !el2` early-return guard immediately after the lookup (this
project's own dominant idiom, per CLAUDE.md's `[hidden]` and `stale-py` guard warnings), downgrade
the "multiple readers implies real" heuristic rather than upgrade it — a guard is evidence the
AUTHOR already knew the element might not exist, not evidence that it does.

**False-positive class:** legitimate optional UI (a banner that only renders for some user
segments) is *also* "all readers guarded, template doesn't always render it" — the discriminator
is whether ANY code path in the whole template tree, across ALL conditionals, ever emits the id —
not whether the specific page under test happens not to. Here the answer was a hard zero
repo-wide, not "zero on this page."

**Counts from this project:** 1 dead_region finding checked, confirmed genuinely dead by a route
the tool's own hedge explicitly said made it LESS likely to be dead (2 readers) — the opposite of
what the hedge concluded.

## F35 — `.success-popup` in push_arena.html: a second, independent confirmation of F29's pattern

**The evidence, unedited.** Not flagged at all by `findings` — this is a `connected` symbol, so it
never surfaces as a finding, which is F29's exact blind spot re-confirmed with a concrete arena
instance. `push_arena.html:2808`: `<div class="success-popup" id="successPopup">`. CSS
(`pointless/static/pointless/buttons/css/purchase_button.css:352`): `.success-popup { display:
none; } .success-popup.active { display: flex; ... }` — the reveal is gated on the `active` class.
JS (`pointless/static/pointless/buttons/js/purchase_button.js`, 3 call sites, lines 18-20, 677-679,
687-689): every single one does `document.querySelector('.success-popup')` then either
`.style.display = 'none'` or `.classList.remove('show')` — note `'show'`, not `'active'`, so even
that removal targets the wrong class name. **Zero occurrences anywhere in the JS tree of
`.success-popup` + `classList.add` or `.active`.** CSS provides an inbound edge (rule exists, so
the graph sees it referenced); JS provides an inbound edge (selector exists, so the graph sees it
read); the tool correctly has no vocabulary for "and yet nothing ever REVEALS it," which is F29's
exact wording, right down to "a purchase confirmation popup" being one of F29's two named
examples — this may be the same instance already generalized in F29, re-derived independently in
the arena scope rather than a new one.

**Why it was missed, in terms of the model.** Identical to F29: reference ≠ reachability. Adding
here for the record because it is now confirmed with file:line evidence rather than referenced
secondhand from CLAUDE.md, and because the specific failure mode (JS only ever calls the HIDE path,
never the SHOW path, and the one hide-adjacent class name it does touch — `'show'` — doesn't even
match the CSS's real toggle class `'active'`) is a slightly different, possibly more general lens
than F29's original phrasing: **for an element gated by exactly one boolean-ish class, if every
JS reference to that class only ever REMOVES it (or removes a same-shaped but differently-spelled
sibling name) and none ever ADDS it, the element is provably permanently hidden** — narrower than
"reachable at runtime" in general, but mechanically checkable without a browser: for each
`.class.modifier { display: ... }` CSS rule, grep the JS tree for `classList.add('modifier')` /
`classList.remove('modifier')` and flag "remove-only."

**False-positive class:** a modifier class removed on load and added later by a DIFFERENT file
this grep didn't check (the "removed in file A, added in file B" split is normal for modal
open/close pairs); the check must be repo-wide add/remove counting, not per-file.

**Counts from this project:** 1 element checked in the arena scope, 1 confirmed remove-only /
never-add — consistent with F29's existing count of 2 (this may or may not be one of those 2;
not disambiguated here).

## F36 — a class string built by concatenation + conditional suffix, injected via innerHTML, is invisible to `css_selector`

**The evidence, unedited.** Two `css_selector` "unused" findings in the arena scope are false:
`challenges.css:31 arena-challenge-card` and `challenges.css:45 idle`. Independent check —
`pointless/static/pointless/push_arena/sidebar.js:624-627`:
```js
let cardClass = 'arena-challenge-card';
if (allDone) cardClass += ' done';
else if (hasClaimable) cardClass += ' claimable';
else if (currentValue === 0) cardClass += ' idle';
```
`cardClass` is then interpolated into an HTML string later assigned via `innerHTML`. No literal
`'arena-challenge-card'` or `'idle'` appears as a `.className =` or `classList.add(...)` call with
a bare string — it is the BASE of a variable that is conditionally suffixed, then used inside a
template-literal HTML blob. This is a different shape from F26 (Lua string) and F31/F33
(enumerable list) — it is plain JS string concatenation feeding `innerHTML`, one of the most
common JS authoring patterns for building card/list markup, and it defeats a literal-string scan
of `classList.add`/`.className =` call sites entirely because the FULL class list never exists as
one string literal in the source — only in a runtime-built variable.

**The lens that would catch it.** Track a variable initialized to a string literal (`let x =
'foo'`) that is later mutated only via `+=' bar'` / `+=` template-literal-with-string-literal, and
treat every literal segment that can appear in the variable (the base plus each conditional
branch's addend) as a `classList`-equivalent reference to that class name — the same "this name is
assembled, so no single literal exists" reasoning the project's own CLAUDE.md already states for
`'btn_' + id`-style dispatch, generalized to string-concatenation-into-innerHTML rather than
property/key lookup.

**False-positive class:** a genuinely dead class that happens to share a literal segment with a
live one (e.g., `cardClass += ' locked'` where `.locked` is never styled) would still read as
"referenced" under this lens even if it in fact is dead in a different sense (the JS builds it,
but no CSS rule matches it) — this lens only closes the CSS-selector-unused gap, it does not by
itself prove the class does anything, so it should output "referenced from JS, verify the CSS
rule separately" rather than a hard "used."

**Counts from this project:** 2 of the ~226 `css_selector unused` findings this sweep matched in
the arena scope were checked against this exact pattern and both were false; the remaining ~224
were NOT individually re-verified in this pass (do not treat the unchecked remainder as either
confirmed or refuted — this project's own history is that such sweeps over-report badly, e.g. only
~4 of 15 prior "dead" findings were genuinely dead).


## F37 — the SOURCE graph said connected, the BUILD graph never included it (21 of 62 modules)

**Evidence, unedited.** `pointless/static/pointless/js/main.js` registers 62 loaders of the shape
`() => import('../buttons/js/<id>.js')`. Measured from `dist/.vite/manifest.json`:
`manifest['js/main.js'].dynamicImports.length === 41`. The other **21** modules had **no manifest
entry at all**, and the browser was fetching them as **raw, unminified source** from
`/static/pointless/buttons/js/` — `aurora_borealis.js`, 45 KB unminified, on a page that had
already downloaded the bundle. Shipping for five days. Zero errors, zero 404s, no visual defect;
the only symptom was weight, which nothing measures per-module.

**Cause:** the build runs `vite-plugin-javascript-obfuscator` with `stringArray: true,
stringArrayThreshold: 0.3`, which moves ~a third of a file's string literals into a base64 lookup
table and replaces them with a call. `main.js` is *nothing but* string literals, so ~a third of
the `import()` specifiers stopped being static strings — and an `import()` with a computed
specifier cannot be statically analysed, so Vite declines to bundle it and leaves the relative
path for the browser to resolve. The seeded-random 30% is why it hit 21 and not all or none.

**Why seamcheck missed it, in terms of the model:** every one of those 62 modules IS referenced,
from a real entry, by a real `import()`. The source graph is perfectly connected and perfectly
correct. What was broken lives one layer down — **the bundler's output did not contain what the
source graph promised** — and seamcheck models only the source.

**The lens that would catch it:** read the build manifest (`dist/.vite/manifest.json`,
`webpack-stats.json`, `rollup` output, an esbuild metafile) and assert that **every module
seamcheck finds reachable from an entry appears in the build output**. One set difference. It
would have printed 21 names instantly. This is a different question from "is this code
referenced" and, on a project with a bundler, arguably a more valuable one: it catches a whole
class — obfuscator/minifier plugins eating dynamic-import specifiers, a `manualChunks` rule
swallowing a module, an alias that resolves in the IDE and not in the build.

**False-positive class it would produce:** deliberately externalised modules (`external`,
`rollupOptions.external`, CDN globals), dev-only entries excluded from the production build, and
modules intentionally loaded at runtime from a URL. All three are declarable, so the check needs
an ignore list rather than a heuristic.

**Counts from this project:** 41/62 before, 62/62 after excluding `main.js` from the obfuscator.

## F38 — the inverse of F29: an attribute that is QUERIED but no longer PRODUCED

**Evidence, unedited.** `buttons/js/button_manager.js::setInitialButton()` resolved which button to
mount with `document.querySelector('[data-button-type="' + type + '"]')`. That attribute was only
ever emitted by `components/button_card_grid.html`. A change moved that template off the page (the
collection grid became a fetch-on-first-open). Result on a clean session: the query returned
`null`, the fallback query returned `null`, and the arena **mounted no button at all** — a blank
0×0 play area, nothing clickable, **and not one line of console output**. Every new player would
have been unable to push. Found by a human driving the page; no static check flagged it.

**Why it was missed, in terms of the model:** F29 is *connected but unreachable* — a name is
referenced, so an edge exists, but nothing can reveal the element at runtime. **This is the
mirror image: the consumer is alive and well-connected, and its PRODUCER is gone.** Nothing is
unreferenced, so nothing looks dead. `data-button-type` still exists in the codebase — in a
template that this page no longer renders. The graph has no notion of "which producers reach
which page".

**The lens:** model DOM attributes and classes as having **producers** (templates, and JS that
writes them) and **consumers** (selectors in JS/CSS), *scoped per rendered page*. Flag a consumer
on a page none of whose producers render. The per-page scoping is the hard and necessary part — a
global "does `data-button-type` exist anywhere" check returns yes and learns nothing, which is
exactly the answer a grep gave me while I was fixing the neighbouring call site.

**False-positive class:** attributes injected at runtime (`innerHTML`, framework rendering,
fetched fragments — this very change legitimately introduces one), and consumers that are
*supposed* to find nothing and are correctly guarded. The second is distinguishable: a guarded
consumer (`if (!el) return`) is defensive, an unguarded one (`el.dataset.x`) is a crash waiting.
Ranking unguarded consumers above guarded ones would have put this bug at the top: the fallback
path dereferenced the result of a query that could only return null.

**Counts:** 1 occurrence, 100% user-facing severity (arena unusable), 0 static tools flagged it,
5/5 clean browser contexts reproduced it.

## F39 — multiple writers managing DIFFERENT SUBSETS of the same marks (3 instances in one day)

**Evidence, unedited.** A collection card in this project carries three marks: `.active` and
`.selected` on `.push-button-option`, and `selected` on its nested `.button-select-indicator`.
Three writers touch them:

| writer | manages |
|---|---|
| `button_manager.js::selectButton()` (click path) | card `.active`, card `.selected` |
| `button_manager.js::bindCollectionCards()` (mount path) | card `.active`, card `.selected` |
| `arena_inline_boot.js::selectButton()` (picker-click path) | card `.active`, indicator `selected` |

**No writer managed all three**, and each missed a different one, so the symptom changed shape
depending on which path ran: two cards glowing; one card left at `transform: scale(0.97)`
(visibly pressed-in) while a different button was equipped; or the card glowing on one button
while the tick sat on another. **Three separate fixes in one day, each of which looked complete.**
Worse, the writer that handles the real click calls `e.stopPropagation()`, so the writer carrying
the *most correct* sweep never ran on a click at all.

**Why it was missed:** seamcheck flagged `multi_writer_element` for two elements in this scope and
was RIGHT to. But multi-writer alone is not the defect — plenty of elements have several
legitimate writers. The defect is **asymmetry**: writers of the same conceptual state managing
*different subsets* of the classes that express it.

**The lens:** for each (element selector, class) pair, enumerate every `classList.add/remove`
site. Group by element. Flag a group where writer A manages `{a,b}` and writer B manages `{a,c}`
— i.e. the union is larger than any single writer's set. That is mechanically checkable from the
AST, needs no runtime, and is a much sharper signal than "this element has 2 writers". Bonus
signal, free from the same data: a writer that *adds* a class nothing ever *removes*.

**False-positive class:** deliberately layered state where one writer owns visibility and another
owns emphasis (a record's value vs its new-record glow — this project has exactly that and it is
correct). Those are distinguishable because the class sets are disjoint; the defect is
*overlapping but unequal* sets.

**Counts:** 3 instances, 3 different files, 1 day. 2 flagged as `multi_writer_element` (neither
ranked as a defect), 1 not flagged at all.

## F40 — rank dead code by RUNTIME COST, not by size. This is the prioritisation ask.

**Evidence, unedited.** `push_arena/level_progress_bridge.js` contains
`checkStreakSaveOpportunities()` → `showLostStreakBuyBackButton()`, which does
`getElementById('lostStreakBtn')`. **`#lostStreakBtn` and `#lostStreakText` appear in zero
templates.** `arena_inline_boot.js` drives it from `setInterval(..., 60000)`, so every player on
the arena makes an authenticated `GET /api/streak/save/opportunities/` **every 60 seconds for the
whole session** to populate an element that cannot exist. At this project's 50k-concurrent target
that is **~833 requests/second** of pure waste on a rate-limited endpoint.

**And it damages the live feature.** Both the dead path and the live one
(`hourly_streak_manager.js`, whose own comment says the banner is *"now the only recovery UI"*)
write the global `window.currentStreakOpportunity`. The dead path sets it to **`null`** every 60s
when it finds nothing. The live "RECOVER STREAK" button reads exactly that global, and on null
shows an error and an alert telling the player to refresh. **So a dead feature intermittently
breaks a live one** — the F39 pattern again, on a `window` global instead of a DOM element.

**Seamcheck found the region.** It reported `dead_region showLostStreakBuyBackButton` — and
marked it **uncertain**, which is F34 (its confidence heuristic ran backwards: two modules
reaching for a missing element *raised* its confidence that the element is rendered).

**The ask is not detection, it is ORDER.** This was one candidate among **609** in the arena
scope, ~590 of which are Font-Awesome-class and `data-*` noise the tool itself hedges. Nobody
reads 609. What makes this one worth acting on is not its size — it is ~60 lines — but that it is
**reachable from a timer and makes a network call**. So: escalate a dead region's severity when
it is reachable from `setInterval`/`setTimeout`/an event loop, when it performs `fetch`/`XHR`, or
when it lives on a page the project marks hot. Report "dead + costs a request every 60s per user"
above "dead + 200 unused CSS selectors". A `--rank-by cost` flag, or just that signal in the
default sort, would change this from a list nobody finishes into a list with an obvious top item.

**False-positive class:** a dead region whose fetch is itself dead (never invoked) would be
over-ranked. Cheap to avoid: only escalate when the region is reachable from a live trigger.

**Counts from this project:** 609 findings in the arena scope · ~15 independently verified · 2
confirmed false positives among those checked · 1 finding (this one) with a measurable per-user
runtime cost · ~590 hedged noise. The confirmed-actionable rate on the checked sample is what a
user needs to see on the summary line.

## F41 — attribute findings in GENERATED files to their source, or the fix goes in the wrong file

**Evidence, unedited.** This project's own type-floor gate (`scripts/audit_font_floor.py`, not
seamcheck) failed today with 9 sub-13px declarations in
`pointless/static/pointless/buttons/css/previews/{garden_chaos,retro_arcade,lego_builder}.preview.css`.
Those files are **generated** (`node OTHER/perf/split_preview_css.js`) as a verbatim copy of rules
that already live in the skin beside them. So every violation was reported **twice**, and the new
copy sat at a path the baseline did not cover — turning a pre-existing, already-accepted set of
values into a CI failure with no new defect behind it. The trap in the other direction is worse:
someone "fixes" the size in the generated file and the next regeneration silently reverts it.

**Why this is a seamcheck concern too:** it scans a source tree and will meet generated trees —
compiled CSS, codegen'd clients, extracted fragments. A finding in generated output is never
actionable *where it is reported*.

**The lens:** detect generated files (a `GENERATED`/`DO NOT EDIT` marker in the first lines, a
path convention, or a declared glob in config), and either exclude them or — better — attribute
the finding to the source file and **deduplicate against it**, so the count stays honest. Our fix
was to exclude the directory, with the same reasoning already applied to `/dist/`.

**False-positive class:** none new; the risk is under-reporting if a generated tree is the ONLY
place a rule exists (generated-from-data with no source file). Detectable: exclude only when a
source attribution can be resolved.

**Counts:** 9 declarations, ×2 counted, 0 real defects, 1 CI failure.

---

### What seamcheck was RIGHT about in this scan (recorded, per the rule that both statements matter)

- `mobile-accordions.css` — `#mobile-band-topup-accordion` / `#mobile-band-collectors-accordion`
  rules: **confirmed dead**, those ids exist in no template. Verified independently.
- 8 unused CSS custom properties (`--mobile-accent-*`, `--mobile-text-*`, `--mobile-spacing-unit`,
  `--lr-board-depth`): **confirmed dead**, `var()` count 0 for each. Three of them additionally
  reinvent this project's mandated contrast tokens.
- Two unread `data-base-*` attributes on `#achievementModalFooter`: **confirmed dead**.
- `multi_writer_element #periods-total`: **confirmed** two writers (harmless today only because
  both write the same literal — see F39).

### Correctly OUT OF SCOPE / correctly hedged

- The Font-Awesome `fas`/`fa-*` and `data-*` families it marks "uncertain — almost certainly
  real": the hedge is right, and the volume is the problem (F40), not the verdict.
- `user:*:interactions` Redis key, reported as written-never-read: **false positive**, and a fair
  miss — the read is a generic loop over a key registry, one hop from any literal (this is F33).
- `.arena-challenge-card` / `.idle`: **false positive** — the class name is assembled by string
  concatenation with a conditional suffix and injected via `innerHTML` (this is F36).

### A by-product worth naming: the scan found a LIVE bug, not just dead code

Checking the dead accordion ids surfaced that `arena_band.html` renders
`#mobile-band-movers-accordion`, and **that id is missing from the accordion CSS id list** — so
the real "Top Movers" mobile drawer currently ships with no header/chevron/hover/open styling, and
`test_arena_band.py` only asserts the four old drawer names. A dead-code scan that reports
*orphaned selectors* alongside *elements with no matching selector* would have caught both halves
at once. That inverse check — **markup that no selector matches** — may be the cheapest high-value
addition on this list, and it is the same shape as F38.


## F42 — seamcheck was RIGHT about a dead animation class, and it sat at index 2,536 of 3,438

**Evidence, unedited** (`seamcheck findings`, 0.14.0, on this project):
`id: css_selector:class:se-pod-climbing · kind: css_selector · status: unused ·
file: pointless/static/pointless/buttons/css/space_elevator.css · line: 243`

**It is correct.** `.se-pod-climbing { animation: sePodClimb 0.8s ease-in-out infinite; }` is defined, and
the class is referenced by **zero** files outside its own stylesheet — scanned across
`pointless/static` and `pointless/templates` (.js/.css/.html/.py, build output and minified copies
excluded). A confirmed-RIGHT data point; triage vocabulary `genuinely-dead`.

**Why it matters more than its status suggests.** This one dead class explains a user-visible
difference. The owner reported hot_air_balloon as lagging and space_elevator as smooth, yet the two
share the same moving-element design: the same `bottom:` custom-property layout path every frame,
the same `will-change: bottom`, the same drop-shadow. The difference is animation. The balloon puts
`habBalloonSway` on its BASE rule, so it runs forever and re-rasterizes the blurred SVG every frame
even while parked. The pod's only animation lives on `.se-pod-climbing`, which nothing ever applies — so it
never runs. The smooth comparison button was smooth for a reason seamcheck had already found, and
nobody could see it among 3,438.

**The ask — a sharpening of F40:** an unused selector whose own declarations include `animation`,
`transition`, `will-change` or `filter` is dead MOTION or dead PAINT intent, not dead colour. Tag
those (e.g. `css_selector` + `motion` / `paint`) and rank them above plain unused selectors. It is
cheap — the tool already reads the rule to know the selector — and it turns "unused class #2,536"
into "an animation that was meant to run and does not", which is precisely the finding that explains
why two similar components feel different.

**False-positive class it would amplify:** animation classes added through string-assembled names
(`'se-pod-' + state`) would surface as dead motion — the F36 blind spot, now ranked higher and so more
visible. That is the right trade: a wrongly-ranked dead animation gets looked at once; a correctly
found one stops drowning.

**Correctly OUT OF SCOPE, recorded so nobody chases it upstream:**
- **The hot_air_balloon lag itself.** Every rule and function involved is used and reachable. The
  defect is the runtime paint cost of a permanent `animation` on a `filter`ed element that is not its
  own compositor layer. A static reference graph should not flag that, and did not.
- **The journey-button economics** — ~1,075 pushes to reach the top at 5 CPS against a 100-push
  hourly allowance for a new player, with progress reset on every reload. Game balance, not code
  connectivity.

**Counts:** 1 finding · confirmed right · 1 of 3,438 in the unused+unresolved set · list index 2,536.

---

## F37 — IMPLEMENTED (2026-09-12): the build-graph divergence check

(F37 itself was written up by pointlessbutton-71's session, per its own attribution note below
this entry — F29-F32 predate both of us, F33-F36 came from a scan agent it dispatched, F37-F42
are its own. This entry only records the implementation.)

Built, not just written up. Three pieces, all tested — the third exists only because running
this against the actual reference project (not just synthetic fixtures) surfaced two real bugs
in the first pass, both fixed before this was called done.

**1. The import walk now follows `import()`, not just `import ... from`.** `_imported_paths`
(`js_extractor.py`) used to see only `ImportDeclaration`; a dynamic `import('./x.js')` is an
`ImportExpression` node and was invisible to it entirely. That made `discover_js_files` — the
function every DOM/CSS/multi-writer pass in `pipeline.py` calls to get "everything reachable
from an entry" — blind to code-split modules project-wide, not just on this reference project.
Only a bare string-literal specifier is followed (`import(variable)` and an interpolated
template literal are left alone, same "not proven" line every other dynamic edge in that file
already draws). A second function, `discover_dynamic_import_targets`, exposes JUST the modules
that are EVER a dynamic-import target (see point 3 for why that narrower set exists). Tests:
`JsFileDiscoveryTests` + `DynamicImportTargetsTests` in `test_js_extractor.py`.

**2. `seamcheck/build_graph.py` — the actual F37 check.** `read_build_manifest(path)` parses a
Vite `manifest.json`; `find_build_gaps(check_files, manifest_path, build_root, entry_files=,
ignore=)` diffs a set of modules against it and returns one `build_gap` symbol (kind
`build_gap`, status `unresolved`) per module absent from the manifest. Guards, all tested:
  - an unreadable/missing manifest → no findings (a project with no build step, or not yet
    built, is not a claim);
  - a `build_root` that matches nothing in the manifest → no findings, not a false-positive
    wall (a wrong root would otherwise flag literally everything checked) — anchored on
    `entry_files` when given, since those always have a real manifest key even in the
    worst-case incident this whole check exists to catch (see point 3);
  - an explicit `ignore` set for genuinely-externalised/CDN/dev-only modules, never guessed.

**3. Two real bugs, found by running it against THIS project, not a fixture — fixed before
calling this done:**

  - **~190 false positives from statically-shared modules.** First pass checked
    `discover_js_files`'s FULL reachable set (every module, however it was imported) against the
    manifest. On this project's own build that flagged `base_button.js`, `button_manager.js`,
    `stats_manager.js` and ~190 others the exact same way as a real gap — every one of them a
    perfectly normal module Vite folded into a shared chunk because it is only ever reached by
    static `import ... from`. Vite's manifest gets a standalone entry per TRUE entry point and
    per dynamic-import TARGET; a statically-shared module has no such promise, and reporting
    its absence as a "gap" is exactly the false-positive-avalanche shape this project's own
    CLAUDE.md warns audit tooling about repeatedly. **Fix:** narrowed the checked set from "every
    reachable file" to "every file that is EVER a dynamic-import target"
    (`discover_dynamic_import_targets`) — the only set a manifest entry is actually guaranteed
    for. Re-run after the fix: 62 dynamic-import targets found on this project, 0 false gaps.
  - **The root-sanity guard could hide a total failure.** The guard added in the first pass
    backed off to "no findings" whenever NOTHING in the checked set matched the manifest, to
    protect against a wrong `build_root` producing a false-positive wall. Once the checked set
    was narrowed to dynamic-import targets only, that guard became indistinguishable from the
    worst real incident: if an obfuscator broke EVERY dynamic import (not just 21 of 62, as it
    did here), 100% of the checked set would legitimately miss the manifest even with a
    correctly-guessed root — and the guard would silently swallow exactly the incident it exists
    to catch. **Fix:** the guard now anchors on `entry_files` (true entry points, which always
    have a manifest key) when the caller provides them, falling back to the checked set itself
    only when they are not given. `check_files` can now be 100% absent from the manifest and
    still be trusted, as long as a real entry point is present too.

**Wired in, opt-in, zero cost when absent:** `autoconfig.py` detects a manifest at the
conventional Vite locations (`dist/.vite/manifest.json` etc.), searched under the repo root, the
detected Django static root, AND one level of wildcard nesting below each (`*/dist/.vite/…`) —
needed because Django's own `<app>/static/<app>/…` namespacing convention put this project's
real manifest one directory deeper than its detected `static_root`, which the first pass of this
same detection code missed on its own reference project. Deliberately NOT via `_find_file`,
since `dist`/`.vite` are `EXCLUDED_DIRS` everywhere else in that module on purpose, and this is
the one file inside them the tool actually wants, read directly by path, never walked as source.
The Vite ROOT (not just the manifest path) is recorded too, as `js_vite_root` — derived from
WHERE the manifest was actually found, not from `vite.config.js`'s own location or the JS
project root: this project's own `vite.config.js` sits at the repo root but sets
`root: 'pointless/static/pointless'`, so neither of the other two guesses would have lined up
with what the manifest's paths are actually relative to. `api.py` passes both through to
`run_scan()`, which — only when a manifest path is given — appends `find_build_gaps`'s result to
the symbol list, anchored by the real entry files. `report.py` gets a title: "Reachable in
source, missing from the build". Config key is `js_vite_manifest` / `js_vite_root` (matching
this project's own pre-existing, previously-unconsumed `SEAMCHECK_CONFIG["js_vite_manifest"]"`
declaration in `myproject/settings.py` — renamed from an initial `vite_manifest` guess to match
it, per pointlessbutton-71's catch). Tests: six cases in `test_autoconfig.py` (repo root, under
static root, app-namespace nesting one level deeper, a `vite.config.js` at the repo root with a
custom `root:`, absent → no key); `RunScanSurfacesBuildGapsTests` in `test_build_graph.py` proves
the full `run_scan(build_manifest_path=...)` path end to end, and that omitting the flag is a
no-op, not a crash.

**Not done:** webpack/rollup/esbuild manifest shapes (Vite only, for now); no CLI flag to
override the manifest path or the ignore list (currently library-only via `run_scan`'s new
kwargs — a `--build-manifest`/`--build-ignore` CLI surface is the natural next step).

**Verified against the real reference project, not just fixtures** (this is what caught both
bugs above): `discover_dynamic_import_targets` finds exactly 62 dynamic-import targets on
pointlessbutton today (matching the known count of button loaders in `js/main.js`), and
`find_build_gaps` reports **0 gaps** — correct, since the obfuscator regression this check is
built to catch was already fixed on the PB side (commit `0246c7824`).

**True-positive run (2026-09-12, at pointlessbutton-71's suggestion — "0 gaps proves the check
stays quiet when nothing is wrong; it has not been shown to FIRE on the defect it targets"):**
`git worktree add` at `0246c7824~1` — the parent commit, where the obfuscator had NOT yet been
excluded from `main.js` and its own dist/ is committed, so no rebuild was needed. Known ground
truth (pointlessbutton-71): 21 of 62 button modules missing from that build, named list supplied.

**Result: 59 flagged, not 21 — but the 21 named ones are ALL present, zero misses.** The 38
extra: at that commit, Vite's own chunking merged large groups of buttons into a handful of
SHARED anonymous chunks (`_chunk-LYiTCPvz.js` alone is `main.js`'s `dynamicImports` target for
25 different buttons) with **no `src` field at all** — so this check's `src`-identity match
cannot tell "genuinely missing" apart from "bundled fine, but Vite did not preserve which source
file this shared chunk came from." Confirmed this is specific to that historical build, not a
standing flaw: on the CURRENT commit, every one of `main.js`'s 62 `dynamicImports` entries has
its own clean, distinct `src` — no merging at all, which is why the current-commit run above
came back with genuine, trustworthy zeros rather than an accidental one.

**Disposition: real precision gap, zero false negatives, not fixed this pass.** The check as
built is sound for catching the incident (nothing in the 21 was missed) but overclaims scope
when a bundler's chunk-merging drops `src` attribution for otherwise-fine modules. A more
robust version would fall back from per-file identity matching to a **count check** on entries
whose `dynamicImports` contains `src`-less chunks — comparing `len(dynamicImports)` against the
number of distinct `import()` call sites in source for that entry, which is closer to how the
ORIGINAL 41-of-62 ground truth was itself first measured (`manifest['js/main.js'].dynamicImports
.length`) and does not require per-file attribution at all. Left as the next refinement rather
than built now, so the false-positive-avalanche risk of a rushed fix is not traded for a
false-negative one.

`python -m pytest -q` — full suite green apart from one unrelated pre-existing failure,
`test_a_pending_update_is_printed_on_stderr_not_stdout` (a hardcoded version-string assertion
against package metadata, not touched by this work). `ruff check` clean on every changed/new
file.

---

## New capability (2026-09-13): `--scope` + git hooks + a pre-focused map per touched page

Not a finding from scanning pointlessbutton - a feature request from its owner, working from
this doc's own use case: "before an LLM-driven commit/push, seamcheck should show what's
actually being touched" - and built directly in this repo rather than written up for someone
else to pick up, since it changes the tool's own surface, not what it detects.

**The question this answers, in the owner's own framing:** "I worked on push_arena in this
round - I want to see THAT. I worked on achievements a while back and changed what it does -
I should be able to see that too, whenever I ask, not just right after the commit that did it."
Two different asks, both served by the same underlying data seamcheck already computed
internally for the map (which page/feature reaches which symbol) - never exposed as a filter
a caller could ask for directly until now.

**Built, in order of how they compose:**

1. **`seamcheck/changescope.py`** - `changed_files(repo_root, scope)` ("commit": staged files;
   "push": every file in commits not yet on the upstream branch, via `git diff
   @{upstream}...HEAD`; raises rather than guessing a base branch name when there is no
   upstream). `pages_touched()` / `features_touched()` map a changed-file list onto page/feature
   labels - the SAME labels the map itself uses (`api.page_files()`, made public for this;
   `symbol.sub`'s `[Feature Name]` suffix, already written by every scan).

2. **`api.scoped_findings(repo_root, scope)`** - the CURRENT unresolved/unused findings for
   every page a scope touches, scoped to the whole page (not just the literally-changed
   files) - deliberately different from `check --since`/`diff`'s "what's NEW" question:
   this one surfaces a pre-existing issue in the same neighbourhood too, which is exactly
   what the owner's "achievements" example was asking for.

3. **CLI**: `seamcheck scope commit` / `seamcheck scope push` - JSON envelope
   (`queries.scope`), same shape as `symbols`/`findings`/`diff`. Exits 1 (`EXIT_FINDINGS`) if
   any touched page has a finding - a real gate for whoever wants one; exits via the ordinary
   envelope mapping otherwise (new `no_upstream` error code for the no-upstream case).

4. **`seamcheck install-hooks`** - writes plain git `pre-commit`/`pre-push` hooks (tool-agnostic
   trigger: fires for a human, an agent, anything that shells to git). Calls back into
   `seamcheck/hooks.py` (not the JSON CLI path - a human does not want a raw envelope dumped
   after every commit) for a short text summary. **Advisory only, hard-coded**: always exits 0,
   whatever `scope` itself would have exited - the owner's explicit requirement was "no hard
   gate, only recommendations". A hand-written hook already in place is left alone, never
   silently overwritten.

5. **The visual half - `api.scoped_map_document()` + `seamcheck scope <mode> --serve`.** A page
   can now be rendered already focused: `map_html.render(..., initial_page=X)` appends one
   small, separate `<script>` tag AFTER the existing (6800-line, untouched) map script - it
   calls the SAME `pickPage()`/`switchTo()` the page's own "Page" dropdown already calls when a
   reader clicks it, so opening pre-focused is indistinguishable from a reader having just
   clicked there themselves. `seamcheck/scopedserve.py` renders one such document PER touched
   page and serves each on its own port (`serve.py`'s existing `serve_addresses`/`public_tunnel`,
   completely unchanged - safer than trying to generalise its token-routed single-document
   model to many documents under time pressure) - a SEPARATE link per page, per the owner's own
   requirement, not one map with a page-picker at the top.

**Verified against the real reference project:** `seamcheck scope commit` against
pointlessbutton's own working tree (14 files genuinely staged by a concurrent session at the
time) correctly listed them and correctly reported `"pages": {}` - none of them are source
files a page's import graph reaches (they were `dist/` build output), which is the right
answer, not a false positive. `seamcheck scope push` against a branch with nothing unpushed
correctly reported empty. `seamcheck help scope` / `seamcheck help install-hooks` render
correctly through the existing help system with no changes to it.

**A real test-isolation bug found and fixed while building this:** the first version of
`scopedserve`'s own tests did not mock `wants_tunnel`/`public_tunnel` in cases that were not
testing tunnel behaviour - and this development machine has `seamcheck config --tunnel always`
set globally, so those tests were silently invoking a REAL `cloudflared` subprocess, costing
~15s across 6 tests instead of milliseconds. Fixed by passing `local_only=True` in every test
that does not care about tunnelling, rather than mocking global state - the correct simulation
of "a caller who does not want a tunnel", not a workaround.

**Not done:** the map-serving path spins up one `ThreadingHTTPServer` per touched page rather
than sharing one port/tunnel - a `git push` touching many pages opens many cloudflared
processes if `--tunnel` is set. Consolidating onto one server would need generalising
`serve.py`'s single-document, single-token routing model, deliberately deferred rather than
risked under this session's own time pressure. `--scope`'s CLI is not yet documented in
`docs/commands.md`/`docs/ci.md` (the CI-gate use case in particular deserves a line there,
parallel to `check`'s own).

**Tests:** `test_changescope.py` (14), `test_scoped_findings.py` (3), `test_hooks.py` (10),
`test_scope_dispatch.py` (12, both CLI doors), `test_scoped_map_document.py` (2),
`test_scopedserve.py` (6), plus additions to `test_renderer_map.py` (3, the `initial_page`
focus script) and `test_autoconfig.py`/`test_js_extractor.py` were untouched by this entry
(those belong to F37, above). Full suite: 1657 passed, the one pre-existing unrelated
failure. `ruff check` clean on the whole package.

---

## Run 2026-09-14 — hooks, `scope push` and `check --since` on two unreleased commits, every finding checked by hand

pointlessbutton `development` was two commits ahead of `preprod`: `c42e05a36` (docs) and `4c6e128cc` (a
feature, ~600 lines of code plus plans and `.po` files). seamcheck: editable install at `e3c857380`.
Ran, in order: both installed git hooks, `seamcheck scope push`, `seamcheck backfill 1 --backfill-ref
adfa6f0a5` (the preprod tip had no snapshot), `seamcheck check --since adfa6f0a5`, `seamcheck diff --since
adfa6f0a5`. Then every one of the 110 findings `scope push` reported was checked against the source with
an exact producer search (templates, `pointless/static`, views, services; build output and `.min.js`
excluded). Result: **84 right, 26 wrong**, and **0 of the 32 "new since baseline" findings came from the
two commits**.

Triage marks were NOT written — see T9 for why.

## T9 — the hooks run a bare `python3`, and a host directory named `seamcheck/` shadows the package

**Evidence.** `.git/hooks/pre-commit` is `python3 -m seamcheck.hooks commit`. With the project venv not
on `PATH` (a GUI git client, a terminal that did not `source venv/bin/activate`), `python3` is
`/opt/homebrew/bin/python3`, and:

```
$ env -i PATH=/opt/homebrew/bin:/usr/bin:/bin python3 -c "import seamcheck.hooks"
ModuleNotFoundError: No module named 'seamcheck.hooks'
```

This repo root holds a gitignored `seamcheck/` directory (a stale clone of the tool, `.gitignore:147
/seamcheck/`). From the repo root it imports as a namespace package (`seamcheck.__file__` is `None`), so
even an interpreter that had seamcheck installed would pick the wrong thing up. The hook prints a
traceback and exits 0, so it silently does nothing for every commit made outside the venv.

**The same collision, second symptom.** `triage._TRIAGE_FILE = seamcheck/triage.json`. In this repo that
path is inside the nested clone: `git -C seamcheck status` → `?? triage.json` (3,608 B, 2026-09-07). The
marks are tracked by neither repository, although `triage.py` calls triage.json "a checked-in file".
Writing new marks today would have put them into another repository's working tree.

**Fix.** Write `sys.executable` (the interpreter that ran `install-hooks`) into the hook, and run it with
`-P` (3.11+, do not prepend the cwd) or from outside the repo root. Give triage a configurable path, or a
dotted directory (`.seamcheck/triage.json`) that cannot collide with a folder named after the tool.
False-positive cost: none.

## T10 — the hooks skip `quiet()`, so the host's startup log invalidates the scan cache on every commit

**Evidence** (53,091-symbol graph, same tree, back to back):

| run | wall | scan cache |
|---|---|---|
| pre-commit hook, **nothing staged** | 62.8 s (user 59.8) | miss |
| pre-push hook, 2 unpushed commits | 72.2 s (user 71.0) | miss |
| `seamcheck scope push`, right after | ~12 s | hit (`cost.cached: true`, `scan_seconds: 0.0`) |

**Why.** `hooks._run()` calls `setup_django_if_any()` and `api.scoped_findings()` without
`quiet.quiet()`. The host's `AppConfig.ready()` logs WARNING lines (`[AUTH_CACHE] ModelBackend.get_user
monkey-patched…`) to stdout AND to its file handler, `django.log` in the repo root. Those lines are
stamped `15:02:39` and `15:03:41` in `django.log`, the two hook starts, with nothing at `15:04:53` or
`15:06:29`, the two CLI runs, which are quieted. `_scan_tree()` walks every file, including the untracked
and gitignored `django.log`, so `latest_input_mtime_ns` is newer than the cache entry and every hook run
scans cold.

Three smaller things in the same path:
- `scoped_findings()` calls `cached_scan()` before `changed_files()`, so "nothing staged" still pays the
  full scan.
- Hook output carries the host noise the CLI hides: 2× `RuntimeWarning: Accessing the database during app
  initialization`, 3 WARNING log lines, and `<unknown>:67: SyntaxWarning: invalid escape sequence '\{'`
  twice. That warning is unattributed because several extractors call `ast.parse()` without `filename=`
  (`filetree.py:34`, `pagenames.py:60`, `callgraph.py:125`, `env_extractor.py:153`,
  `url_reference_extractor.py:128`, …). The file is a gitignored one-off, `OTHER/cps_verify.py:67`.
- The page line lists every touched feature inline: `push-arena-main (achievementCountMobile, …)`, 35 ids
  in one parenthesis.

**Fix.** Wrap `_run` in `quiet()`. Resolve `changed_files` before scanning and return early when empty.
Leave untracked and gitignored files out of the freshness walk (see F43). Pass `filename=` to `ast.parse`.

## T11 — snapshots spell some ids with `./` and the live scan does not, so `--since` compares twins

This is T1's class ("0.8.0 fixed `./x.js` vs `x.js`") surviving in the **id**, not the `file` field.

**Evidence.**
- `:./` inside ids: snapshot `adfa6f0a5` (from `backfill`, `api.scan(".")`) **2,725**. Snapshots written
  by `write_map`: **2,733** (09-12 18:58), **2,654** (09-12 11:37), **1,160** (08-31). The cached HEAD
  graph, where `cached_scan` resolves the root to an absolute path: **0**.
- One symbol, twice, in the snapshot (both have the plain `file` field):
  `dom_attr:id:quarantine-modal:./pointless/static/pointless/utils/quarantine_manager.js:101` and
  `dom_attr:id:quarantine-modal:pointless/static/pointless/utils/quarantine_manager.js:101`. Edges on
  that line: 16 in the snapshot, 6 live.
- Symbols per file, snapshot vs live: `store.js` 875/456, `stripe_payment.js` 312/160,
  `quarantine_manager.js` 114/58, `button_manager.js` 149/76. Totals: 51,999 symbols and **169,130**
  edges vs 53,091 and **126,054**.
- **The twins connect to each other.** `getElementById('particles-js')` (`push_arena.js:1728`) has zero
  producers in any template or script. In the snapshot its `./` twin and its plain twin are joined by an
  edge with status `connected` and note "Reached from the markup itself - a label, an ARIA
  relationship…". Every snapshot therefore says `connected`; the live scan says `unresolved`. Same for
  `purchasePopup` ×4, `lostStreakBtn` ×2 and `quarantine-modal`.

**Impact on this push.** `seamcheck diff --since adfa6f0a5`: appeared **7,066**, vanished **5,974**,
changed **100**. Classified by hand:
- 4,962 of the appeared are line moves (T12).
- 1,988 are in gitignored directories (F43).
- **1,012 vanished from 10 files the commits never touched**: `store.js` 432, `stripe_payment.js` 151,
  `store_page.js` 115, `push_store.js` 86, `button_manager.js` 73, `supporter_badge_buy.js` 64,
  `quarantine_manager.js` 55, `store_user_data.js` 12, `auth_page.js` 11, `cookie_consent.js` 10. These
  are the `./` twins.
- **60** `dom_selector connected → unresolved`, all in untouched files. 22 of them are among the 110 live
  findings below, which is how a stale "connected" baseline turns into a "new" finding.

`seamcheck check --since adfa6f0a5` exited 1 with **32 new**:
- 7 are gitignored one-offs (F43).
- The other 25 are elements that existed before both commits (push_arena.html ids, `push_arena.js:146`),
  none of them on a line either commit added.

I did not trace why each of the 25 reads as new. Line moves and twin-connected baselines are the two
mechanisms measured in this diff.

**Fix.** Normalise the path once, before any id is built (the `file` field already is). Bump the snapshot
schema, or rewrite `./` ids on load. Never let an edge join two symbols whose ids differ only in path
spelling.

## T12 — ids embed the line number, so any insertion re-ids everything below it

**Evidence.** Of the 7,066 appeared symbols in the diff above, **4,962** have an identical
`(kind, label, file, status)` among the vanished. They only changed line: two lines went into
`push_arena.html` (a `json_script` island and one `<script src>`), and two into `gdpr_service.py`.
`check` absorbs most of this (32, not thousands); `diff` does not.

**Ask.** Match across snapshots on `(kind, label, file, sub, owner/snippet)` and use the line only as a
tie-breaker. Or anchor ids to content rather than position.

## T13 — `check --format json` exits 3 and prints nothing

**Evidence.** `seamcheck check --since adfa6f0a5 --format json` → exit **3**, stderr: "The whole graph is
75.0 MB (~18,739,500 tokens). Refusing to print it." `help check` documents 0/1/2 only. For `check`,
`--format json` means "the whole graph", not "the verdict". An agent asking the gate for machine-readable
output gets neither the verdict nor a documented exit code. The markdown digest carried the useful
content (32 new, 16 resolved, 3 outlived marks).

**Ask.** Make `check --format json` emit the digest as JSON, or document exit 3 in `help check` and point
to `sarif`.

## F43 — the working-tree scan reads gitignored directories; `backfill`'s worktree cannot

So the two sides of `--since` scan different projects, and gitignored one-offs also leak into live
findings.

**Evidence.**
- **1,988** symbols appeared only because the working tree has gitignored directories: `OTHER/seo` 646,
  `OTHER/_archive` 263, `OTHER/navbar-mob` 263, `docs/maps` 262, `OTHER/management_commands_archived`
  218, `OTHER/divisions-redesign` 56. None of them can exist in a fresh `git worktree`.
- **7 of `check`'s 32 new** are these: `pps-technique-chip` `OTHER/seo/arena_tech.mjs:60`; `pps-tapmap`,
  `pps-tapmap-wrap` and `pps-tapmap-caption` in `OTHER/seo/cps_final.mjs`; `bcr-strip`
  `OTHER/seo/row_after_equip.mjs:12`; `clicks-tiles`/`box` in `OTHER/seo/strip_check.mjs`;
  `/api/admin/regression/set-user-stats/` `OTHER/_archive/oneoff_scripts/bt10-harness.js:40`.
- **In live findings:** `multi_writer_element achievementsCard` names its writers as `cards_check.mjs,
  push_arena.js`. `cards_check.mjs` is `OTHER/seo/cards_check.mjs`, a one-off Playwright probe
  (`.gitignore:100 OTHER/`). The project's own convention is that `OTHER/` holds one-off scripts.
- `django.log` (T10) is the same gap seen from the cache side.

**Lens.** Build both the scan input and the `_scan_tree` freshness walk from `git ls-files --cached
--others --exclude-standard`, so .gitignore is honoured by default. Opt-in config for a project that
really keeps read code in an ignored directory.

**False-positive class it would create:** generated code that is gitignored but imported (a local build
dir). Rare here; opt-in covers it.

## F44 — 26 of 110 live findings wrong, in six classes (checked one by one)

Scope: the four pages `scope push` resolved (`main` 18, `push-arena-main` 92, `harness` 0,
`journey_progress` 0).

| # | class | findings | live, working code flagged? | example (unedited) |
|---|---|---|---|---|
| 1 | a data-attribute **write**, or a string constant, reported as an unresolved **read** (`sub=data:read`, note "Reads a data attribute") | 16 | 3 yes, 13 dead or write-only anyway | `button_manager.js:689` `setAttribute('data-active', 'true')` → `dom_selector active`, snippet `setAttribute('data-active')` |
| 2 | CSS escape not unescaped | 1 | yes | `push_arena.js:609` `querySelector('.rounded-full.p-0\\.5')` → `dom_selector 5`, `sub=class:write` |
| 3 | multi-writer keyed on the attribute **name**, not name+value | 1 | yes | `speed_tap.js:1428` `[data-stat="ultimate_best_pps"]` → "Writers: speed_tap.js, stats_manager.js, … push_arena.html" (9 files, each a different `data-stat` value) |
| 4 | multi-writer from a scoped or descendant query | 3 | yes | `purchase_button.js:406` `.nav-right .pbits-amount` → reported as a writer of `.nav-right` |
| 5 | multi-writer counting a non-product file as a writer | 2 | yes | `quarantine-modal` → "Writers: 02_playwright_e2e_anti_cheat.spec.ts, quarantine_manager.js" (`docs/audits/_legacy/2026-04-regression-suite/…`) |
| 6 | the note contradicts the evidence | 3 | no (status right) | `push_arena.js:142` `dataset.baseAchieved = …` → "No template renders it" |

**Class 1 in detail.**
- Live and working, flagged anyway:
  - `arena_band.js:199`/`:200` `setAttribute`/`removeAttribute('data-state')`, consumed by
    `arena-band.css:156-157` `.ab-buy-status[data-state="ok"]`.
  - `arena_band.js:31` `const BUSY = 'data-ab-busy'`. The literal itself is reported. The attribute is set
    at `:205` `setAttribute(BUSY, '1')`, read at `:67` `hasAttribute(BUSY)` and cleared at `:281`, all
    through the identifier.
- Mislabelled (the code is dead or write-only regardless):
  - `data-active` ×2 and `data-segment` (`unlimited.js:247`): written, read nowhere, so they should be an
    `unused` attribute.
  - `data-completed` ×9: 7 writes (`push_arena.js:903/990/1501`, `stats_manager.js:1422/1424/4301/4307`)
    and 2 reads (`push_arena.js:924` `getAttribute`, `stats_manager.js:4291` `hasAttribute`) whose writers
    are those same files. The `.goals-card` they all target has 0 producers.
  - `data-goal-celebrated` (`push_arena.js:989`): only ever removed.
- **Lens:** treat `setAttribute`/`removeAttribute`/`toggleAttribute`/`dataset.x =` as producers, and join
  `getAttribute`/`hasAttribute`/`dataset.x` reads to them. Resolve a `const` string passed as the first
  argument.

**Class 2.** Tailwind's `p-0.5` is written `p-0\.5` in a selector. Unescape `\.` `\:` `\/` `\[` `\]` before
splitting compounds. Only 1 finding here, but every fractional or variant Tailwind class used in a
selector will hit it.

**Class 4, all three:**
- `text`: `button_manager.js:249` `buttonInfo.querySelector('.text:not(.price):not(.owned)')` and
  `purchase_button.js` query `.text` under different parents.
- `nav-right`: the written element is the last compound, `.pbits-amount`, which is already its own finding.
- `price-amount`: `purchase_button.js:183` `popup.querySelector` where `popup` is `#purchasePopup`, which
  nothing produces, so that branch is dead. `push_store.js:449` `this.popup` is a different popup. The
  `comboValue`-style note ("writers of a missing element … every one of these branches is dead") would
  have been exactly right here.
- **Lens:** the written element is the last compound; queries on different roots are different elements
  unless the roots resolve to the same node.

**Class 5, the other one:** `achievementsCard` ← `OTHER/seo/cards_check.mjs` (F43). **Lens:** leave
test/spec/fixture patterns and ignored files out of writer sets.

**Class 6.** `push_arena.html:759, 2230, 2240, 2705` render `data-base-achieved` and `data-base-total`.
Nothing reads them, so `unused` is right, but the note sends the reader looking for a template gap that
does not exist. **Lens:** map `dataset` camelCase to kebab-case when joining to template attributes.

## F45 — seamcheck was RIGHT on 84 of the same 110 (confirmed; `genuinely-dead` in spirit, not marked — T9)

**What the page scope bought:** 110 items on the pages a push touched, instead of the repo-wide backlog
(`check` lists 1,755 unreached template elements and 1,135 unreferenced selectors). Every one of these 84
held up.

**Reads with zero producers (verified by exact id/class/attribute regex):** 60 on `push-arena-main` and 7
on `main`.
- **`stats_manager.js`, 30:** `.detonator-highlight`, `.energy-field`, `.timer-display`,
  `.quantum-button`, `.goal-bar .progress`, `.leaderboard-entry`, `#top-person`, `#top-country`,
  `.pps-wrapper`, `.speed-gauge-fill[data-gauge]`, `.speed-particles`, `[data-formula-multiplier-value]`,
  `[data-formula-team-wrap]`, `#dailyProgressBar` (+`dataset.max`), `#celebrationGlow`, `.button-grid`,
  `#push-button`, `.special-button`, `[data-progress-bar]`, `[data-streak-progress-bar]`,
  `[data-level-progress-bar-1/2]`, `[data-mobile-level-progress-bar-1/2]`, `[data-modal-level-number]`.
  The template renders only the `data-mobile-daily-progress-bar` / `data-mobile-streak-progress-bar`
  twins.
- **Elsewhere in the arena:** `push_arena.js` `#particles-js`, `#lazyModeButton(Flash)`, `.button-option`;
  `purchase_button.js` `#purchasePopup` ×4 plus `dataset.requirement`/`tooltip`; `prominent_counters.js`
  `#solidComboCounter`/`#solidPpsCounter`; `guides.js` `#levelsInfoButton`/`#statsInfoButton`;
  `league_switcher.js` `#mobile-league-current`; `reset_countdown.js` `#resetCountdown`,
  `.daily-reset-notification`, `.blue-notification`; `sidebar.js` `.sidebar-overlay`;
  `arena_page_scripts.js` `.consistency-streak`/`-time-remaining`; `hourly_streak_manager.js`
  `#lostStreakBtn` ×2; `push_store.js` `.pbits-value`; `background.js` `.background`.
- **On `main`:** `button_manager.js` `.custom-background`; `brick_smash.js` `.blockbuster-preview` (the
  dead half of an `A || B`); `.ice-hockey-sound-toggle`, `.tennis-sound-toggle`, `.sketch-sound-toggle`,
  `.ski-sound-toggle` ×2. `BaseButton.createSoundToggle` makes `.button-sound-toggle` and stops the
  click's propagation, so those four guards are dead, not a live push-through.

**Also right:**
- `--tapmap-fill`: read through `getComputedStyle`, defined in no stylesheet, so the replay's "fill" mode
  can never switch on.
- `--ribbon-phase`, `data-fish-size`, `data-fish-speed`: written, read nowhere.
- The two `fetch_target` `preview.svg` findings: the files are missing, and the `initPreview()` that
  references them is never called.
- `dead_region createSpeedParticles`.

**Multi-writer, confirmed real (10):**
- `blockedReason`, `blockedTimeRemaining`, `blockedNote`, `blockedNoteContainer` and `modal-content`: an
  inline fallback in `push_arena.html` duplicates `showBlockedModal` from `push_arena.js`.
- `stat-label`: `stats_manager.js:2135` plus an inline script at `push_arena.html:1373/1379`.
- `mobile-current-streak`: `hourly_streak_manager.js:79` plus `arena_inline_boot.js:678`.
- `profile-avatar`: `push_arena.js:3307` plus `arena_modals_boot.js:129`, on the same page.
- `pbits-amount`: 11 writer files, although the project names `pbits_dom.js` as the canonical one.
- `comboValue`: its note, "writers of a missing element", is exactly right.

**Counts:** 110 findings · 84 right · 26 wrong (F44) · 0 introduced by the commits under test.

## T9-T13, F43, F44 — IMPLEMENTED (2026-09-14): hooks/cache/gitignore fixes, four of six false-positive classes

Six commits, each tested against this repo's own suite and (where the finding named a real
pointlessbutton file/line) verified against a fresh `--refresh` scan of this project directly,
not synthetic fixtures.

**T9 — hooks broken outside the venv + triage path collision.** `install_hooks()` wrote
`python3 -m seamcheck.hooks <mode>` into `.git/hooks/pre-commit`/`pre-push`; a bare `python3`
can resolve to an interpreter with no seamcheck installed, and `-m` prepends the hook's CWD to
`sys.path`, so a repo with a directory literally named `seamcheck` (this project's own
gitignored reference clone) shadowed the real package. Hooks now embed `sys.executable` and run
the module by its own absolute path. `triage.json` had the identical collision under the same
name; it now defaults to `.seamcheck/triage.json`, with `load_triage()` falling back to the old
path so existing marks migrate on the next save rather than being stranded.

**T10 — hooks invalidated their own scan cache; ast.parse warnings pointed at the wrong file.**
`hooks._run()` imported and scanned without `quiet()`, so the host's own startup logging wrote a
fresh file (`django.log`) on every hook run; the scan cache's freshness walk saw that as the
newest input and forced a cold scan every commit/push (measured ~60-70s instead of a ~12s cache
hit). Wrapped in `quiet()` now. `scoped_findings()` also checked `changed_files()` AFTER
`cached_scan()`; reordered so "nothing staged" skips the scan entirely. Eight `ast.parse()` calls
across the extractors now pass `filename=`.

**T13 — `check --format json` exited 3 with nothing printed on a real-sized graph.** It fell into
`api.report()`'s whole-graph JSON renderer, which hit its own size gate (75 MB on
pointlessbutton) before the CI verdict was ever computed. Now prints `api.check()`'s own digest
(passed/new_unresolved/counts/...) as JSON on both CLI doors - verified directly against this
project: previously reproduced the exact failure (exit 3, nothing printed); now exits through
the normal `gate_code()` ladder with the verdict printed (exit 1, 1992 unresolved + 1388 unused,
`passed: false`).

**F43 — gitignored one-off scripts fed both the scan and the cache's freshness walk.** New
`seamcheck.gitfiles.tracked_files()` asks `git ls-files --cached --others --exclude-standard`
directly rather than re-parsing `.gitignore` by hand, and both `find_js_files` (the JS fallback
walk) and `scancache._scan_tree` (the freshness walk) now honour it, falling back to unfiltered
(never to "find nothing") outside a git repo. Verified directly: `find_js_files('.')` on
pointlessbutton now returns 0 files under `OTHER/` (previously the source of F43's own evidence,
including `OTHER/seo/cards_check.mjs` naming itself a writer of `achievementsCard`) - gone as a
side effect, no separate fix needed for F44 class 5.

**F44 — four of six false-positive classes fixed, one deferred, one not reproducible:**
- **Class 1 (setAttribute/removeAttribute/toggleAttribute, and a `const` name, as producers) —
  fixed.** `_definitions_in`'s setAttribute-for-data branch reused `_DATA_NAME_RE` (built for a
  BARE string found anywhere in source, "two segments minimum") - reused here it silently
  excluded every single-word data attribute (`data-active`, `data-state`, `data-completed`), so
  each one's own setAttribute call stayed reported as an unresolved READ. Now uses the same loose
  check the read side already did. `removeAttribute`/`toggleAttribute` never contributed a
  definition at all; both now do. Neither direction resolved a `const NAME = 'literal'` binding
  (`setAttribute(BUSY, ...)` where `BUSY` is a variable) - new `_const_string_bindings`/
  `_resolved_string` do, on both the read and definition sides. Verified: `button_manager.js:
  689/943` (`setAttribute('data-active', 'true')`, F44's own example) - both `dom_selector:data:
  active` unresolved findings gone after a fresh scan; `arena_band.js` (the `const BUSY` example)
  now scans to 0 findings.
- **Class 2 (CSS escapes) — fixed.** `_TOKEN_RE`'s class/id token was plain `[\w-]+`, truncating
  at a CSS escape (Tailwind's `.p-0\.5`) - now escape-aware and unescaped with the same pattern
  `css_extractor.py`'s `_SELECTOR_TOKEN_RE`/`_CSS_ESCAPE_RE` already use on the CSS side.
- **Class 3 (multi-writer keyed on name, not name+value) — deferred, not implemented.** Fixing
  this without breaking the general `dom_attr`↔`dom_selector` connectivity matcher (which keys
  `[data-x="y"]` selectors on the attribute NAME alone by design, to match a template's
  attribute-EXISTENCE claim) needs the VALUE threaded through `detect_multi_writers` specifically
  - `Symbol` has no value field, and widening `.label` to `name=value` for ALL data-attribute
  selectors would break every existing name-only connectivity match. Real, but a larger, separate
  change than this pass's other fixes; left as an open item.
- **Class 4 (last compound in a scoped/descendant query) — fixed, for the compound-selector
  case.** A descendant selector (`.nav-right .pbits-amount`) had every compound counted as
  written; new `_last_compound` narrows a WRITE selector to its last compound before tokenizing.
  Reads keep the existing full segment-presence match (a stated v1 limitation for connectivity,
  a different and looser question). Verified: purchase_button.js's `multi_writer_element:
  nav-right` finding (F44's own example) gone after a fresh scan. F44's THIRD class-4 example
  (`.text`) is confirmed NOT fixed by this, and is a different problem: two files each call
  `.querySelector('.text')` off a different PARENT ELEMENT VARIABLE, not a combinator string -
  a data-flow question ("are these two parents the same component") this tool has never
  attempted to answer.
- **Class 5 (test/gitignored files counted as writers) — resolved as a side effect of F43,**
  see above; no separate code change needed.
- **Class 6 (dataset camelCase→kebab-case in note text) — examined, could not reproduce.**
  `push_arena.html`'s `data-base-achieved`/`data-base-total` (F44's own example) have no JS
  reader anywhere in the project (grepped the whole tree) - `_dataset_name()`'s camelCase→kebab
  mapping already looks correct on inspection, and the current `dom_attr|unused` note text does
  not mention a "template gap". Left as-is rather than guessing at a fix with nothing to verify
  it against.

Re-measure whenever pointlessbutton scans this again - all four fixed classes were verified
against real findings in this project directly, not synthetic fixtures, but the corpus-wide
count (26 wrong of 110, F44's own headline number) is pointlessbutton's to re-take.

## T14 — `install-hooks` writes `.git/hooks/`, but git reads hooks from `core.hooksPath` when one is set (husky), so the hooks never run

**Evidence (pointlessbutton, 2026-09-14):**

```
$ git config --get core.hooksPath
.husky/_
$ git rev-parse --git-path hooks
.husky/_
```

- `seamcheck install-hooks` wrote `.git/hooks/pre-commit` and `.git/hooks/pre-push` (both carry the marker line), and they exist.
- Git never looks there, because husky v9 sets `core.hooksPath=.husky/_`.
- Commit `ee7318d3c` (214 files, same day) printed husky's lint-staged output and **no seamcheck summary**.
- The T10 timings were taken by running the hook files by hand (`sh .git/hooks/pre-commit`), not by git.
- The project's CLAUDE.md now documents the hooks as installed and running. In practice nothing runs.

**Why it was missed:** `install_hooks()` builds the path as `os.path.join(repo_root, ".git", "hooks")` and never asks git where hooks
live. It also returns "Installed: pre-commit, pre-push." as if they were live.

**Fix:**
- Resolve the directory with `git rev-parse --git-path hooks`. That respects `core.hooksPath`, worktrees and submodules, which the
  current code already warns about.
- When the resolved path is husky's `.husky/_` (regenerated by husky), do not write into it. Tell the user to add
  `python3 -m seamcheck.hooks commit` to `.husky/pre-commit`, or offer to append it.
- Print which directory git will actually execute.

**False-positive cost:** none. Husky or lefthook or a custom `core.hooksPath` is common in JS and mixed repos, which is exactly this
project's shape. T9 and T10 still apply once the hook really runs: bare `python3`, and a 60–70 s cold scan per commit.

## T14 — IMPLEMENTED (2026-09-14)

`install_hooks()` now resolves the write target with `git rev-parse --git-path hooks` (new `hooks._git_hooks_dir`) instead of a
hardcoded `os.path.join(repo_root, ".git", "hooks")` - so it follows `core.hooksPath`, and also now resolves correctly for a
worktree (hooks live in the main checkout) and a submodule, both named in the fix ask. Verified against pointlessbutton directly:
`install_hooks('.')` now reports `./.husky/_` as the (husky-managed) target instead of silently writing to `.git/hooks/` again.

When the resolved directory is husky's own `.husky/_` (regenerated by `npm install`, so a file written directly there would be
discarded on the next install regardless of whether git read it), nothing is written at all - the caller is told to add the
invocation to `.husky/pre-commit`/`.husky/pre-push` by hand, with the exact `sys.executable` + absolute-path line to paste
(T9's own fix, so this is not a second, different invocation shape). Not implemented as a generic "detect any hooks manager" -
only husky's specific, regenerated-directory convention is recognised; lefthook or a hand-rolled `core.hooksPath` still get a
real file written (or a `.git/hooks does not exist` complaint if `--git-path` itself fails), which is a smaller miss than the
one this closes. Tests: `test_a_worktree_writes_to_the_main_checkouts_hooks_dir`, `test_husky_managed_hooks_are_never_written_to`.

This project's own `.git/hooks/pre-commit`/`pre-push` (installed by an earlier session via `seamcheck install-hooks`, before this
fix existed) are now confirmed-dead files per T14's own evidence - they were never read by this project's git at all. Left as-is:
implementing here is seamcheck's job, but writing into `.husky/` is pointlessbutton's own file, out of scope for this repo's
session. Re-running `seamcheck install-hooks` here will now report the husky path and the lines to add by hand.

### Re-measured after T9–T14 / F43–F44 were implemented (2026-09-14, seamcheck `2f0c90ea7`)

- **`scope`-equivalent page findings.** `push-arena-main` 92 → **75**; `main` 18 → **15**. **0 new, 20 gone.** The ones gone are
  exactly the F44 false positives:
  - `5`, `ab-busy`, `completed` ×9, `goal-celebrated`, `state` ×2, `achievementsCard`, `nav-right` (push-arena-main);
  - `active` ×2, `segment` (main).
  - Still present (F44 did not claim them): `text`, `stat`, `price-amount`, `quarantine-modal` (multi-writer classes 3–5), and
    the `base-achieved`/`base-total` note (class 6).
- **Correction — one TRUE finding was lost with the class-4 fix.** `push_arena.js:1485` `document.querySelector('.goal-bar .progress')`
  used to raise `dom_selector goal-bar` unresolved, and it was right: `.goal-bar` has 0 producers anywhere, so this whole read
  can never match. After "writes attributed to the last compound only" it is gone.
  - The ancestor in a READ is still a requirement: the element must exist for the query to match. Only for WRITES is the last
    compound the element written.
  - Ask: keep ancestor compounds as reads, including in multi-writer attribution.
  - Count here: 1 lost true finding in the two pages.
- **Scan cache (T10) holds:** a cached page query took 9.8 s from disk.
- **The hooks (T14):** `.git/hooks/pre-commit` is still the old script. `seamcheck install-hooks` was not re-run here, because the fix
  refuses `.husky/_` and asks for a line in `.husky/pre-commit`, which is an owner decision.
- **Files changed in this project on 2026-09-14:** 102 unresolved/unused findings, and `git blame` puts **0** of them on that day's
  commits. A scope view shows pre-existing findings for any page a commit touches, by design.
  - A user reading the hook output took them for "new unresolved ones".
  - Ask: mark each finding in the scope summary as `introduced` (its line changed in the scoped commits) or `pre-existing`.
    `git blame -L` per finding line was enough to do it here.

## Correction — the class-4 fix (F44) — IMPLEMENTED (2026-09-14)

The lost `.goal-bar` finding above is real and now fixed. `_selector_tokens` narrowed a WRITE selector to only its last
compound's tokens, dropping the ancestor's entirely - correct for WRITE ATTRIBUTION (`.nav-right` never receives the write in
`.nav-right .pbits-amount`) but wrong for EXISTENCE: `querySelector` cannot match anything unless the ancestor exists too, so
`.goal-bar .progress` with zero `.goal-bar` producers anywhere is exactly as dead as it was before the class-4 fix, and had
gone silent.

`_selector_tokens` now returns a role per token (new `_split_last_compound`, replacing `_last_compound`): the last compound is
`"target"` (write attribution, when the call is a write), every ancestor compound is always `"ancestor"` - read-checked
regardless of the call's own access, never write-attributed. `push_arena.js:1485`'s `goal-bar` finding is confirmed back after
this fix (verified via a fresh `--refresh` scan), and `nav-right`/`pbits-amount` from the original class-4 fix are unaffected -
`nav-right` now reads `class:read` rather than being silently absent, `pbits-amount` still `class:write` alone.

Test: `test_a_write_selectors_ancestor_compound_is_still_checked_as_a_read` in `test_dom_js_extractor.py`.
