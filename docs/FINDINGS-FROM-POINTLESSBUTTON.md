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
