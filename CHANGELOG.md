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

From the 0.10.0 report: all 869 findings on two surfaces of the reference project
re-adjudicated against this release, every REAL verified against the running page before
anything was touched. The new `dead_region` lens deleted 161 lines that four hand passes
had walked past - and flagged one thing that must never be deleted, which is the first
fix below.

- **Fixed** — **a guard element more than one module reaches for is CONDITIONAL, not
  missing.** `#avatarGrid` is read by one cluster in one file: the region under it was
  dead, and 161 lines went, including a `window` resize listener every arena page was
  registering for code that cannot run. `#lostStreakBtn` had **identical evidence** -
  absent from every template, absent at runtime - and is the production streak-save CTA,
  rendered only when a player has a lost-streak buyback opportunity, read by a second
  module and asserted by the regression suite. A conditionally rendered element and a
  nonexistent one are indistinguishable to guard reachability, and to a probe run on an
  account in the wrong state. What separates them is mechanical: **who else reaches for
  it.** A region whose guard element another module also reads is now `uncertain`, says
  "check first", and does not fold the findings inside it away. Getting this wrong deletes
  a payment-adjacent feature no ordinary test run would catch.
- **Fixed** — **an element the markup itself wires up is in use.** `<label for="ann-title">`
  on line 72 and `<input id="ann-title">` on line 73: the browser resolves that with no
  script and no rule, and the input was reported because no stylesheet names it. Nobody
  styles a form field by id. `for`, `aria-controls`, `aria-labelledby`, `headers`, `list`,
  `form` and `popovertarget` were already read as evidence; the CSS branch never asked.
  246 findings of this shape across two surfaces.
- **Fixed** — **somebody else's stylesheet is an oracle, not a finding.** Django's own
  admin CSS was carried as symbols, so 180 rules in a package directory - which nobody
  reading the report can edit - were listed. It still answers "is this class defined
  anywhere"; it is no longer reported.
- **Fixed** — **a model knows which file it is in.** 65 model symbols carried no path at
  all: not openable, not attributable to a page, not distinguishable from another of the
  same name. Django knows; nothing was asking.
- **Fixed** — **the `unverified` JSON lists one row per place**, like the console and the
  map already do.
- **Fixed** — **copying a command is not marking anything, and the panel said otherwise.**
  Tapping a reason in "This is wrong" replaced that option's DESCRIPTION with "copied",
  permanently and for every option tapped - so five taps left five identical green rows
  with their meanings gone, nothing recorded anywhere, and no way back. It reads exactly
  like five marks that cannot be undone. The description comes back after a few seconds,
  only one option shows the copied state, the button that opens the panel closes it, and
  the panel says plainly that nothing is marked until the command is run.

- **Fixed** — **a `data-` attribute in generated markup declares an element.**
  `arena_band.js` builds its purchase confirm dialog as a string -
  `'<button … data-ab-c-buy>'` - and queries `[data-ab-c-buy]` four times. The
  generated-markup reader looked for `id=` and `class=` in those strings and not for
  `data-`, so thirteen live hooks read as elements nothing renders. Acting on them would
  have broken the arena's push-purchase dialog.
- **Fixed** — **a project's own wrapper around the client is still the client.**
  `safe_set(r, "store:current_period", value, ex=3600)` is a SET, and the lens saw a plain
  function call with a string in it. Learned from the wrapper's BODY, never its name: a
  function is a Redis command when its first parameter is client-shaped and its body calls
  `<that parameter>.<command>(<another parameter>)`. `def set_the_table(guests, key):
  guests.append(key)` is not - `append` is a Redis command and also what every list does,
  which is exactly the trap the body rule avoids. Eleven keys the wrapper hid went from
  claimed to connected.
- **Fixed** — **an alias is not another client.** The "touched through more than one
  client" check compares receiver NAMES, and a wrapper is called `r` in one module,
  `redis_client` in another and `cache` in a third. Feeding it three names for one
  connection turned eleven correct keys uncertain - the same mistake the pipeline change
  had to undo a day earlier, in a second place.

  Measured on the reference project: claims **3,646 → 3,531**, non-connected
  **7,230 → 6,904**, Redis keys seen **240 → 260** with connected **44 → 59**, and
  `showLostStreakBuyBackButton` now reads *"22 lines, check first"* instead of *"22 lines
  unreachable"*.

  **Checked and left alone:** the report asks for `store:current_period` to resolve,
  saying the read is behind a variable. Every literal mention of that key in the project
  is a write, a delete, or membership of a health-check list - there is no read of it
  anywhere - so `written and never read` is the honest answer and it stays.

## 0.10.0 — 4 Sep 2026

**Measured:** coverage **81%** across 47 projects and 147,712 symbols (Flask 92%, Django
84%, Express 68%, FastAPI 57%, NestJS 48%, Next.js 46%) · precision **55%** (134
hand-labelled claims) · recall **6/6** · render **46/46**.

Three releases in one, and the thread between them is that a finding should say something
a person can act on without reading the tool first.

**The function became the unit.** A developer is organised around `def submit_push`, not
around a page, and the map could not answer "what does this touch, and what touches it"
at all. It can now: every symbol carries the function it lives in, a type-ahead over every
function in the project draws that function's whole world - across every page its symbols
land on, following its calls into the helpers that do the work - and counts the
round-trips per call by lane. A handler that should be Redis-only, showing `Postgres 1`,
is a diagnosis in one line.

**Unreferenced and unreachable stopped sharing a severity.** A guard that returns because
an element nothing renders was reported as fourteen small chores; it is one dead region of
292 lines. Nine of them, 224 lines, on the reference project.

**And the accuracy work got an instrument.** Precision is now reported per *stack* and per
*lens*, because one number across six frameworks describes none of them - and the first
thing it said was that Django sat at 62% while Express sat at 0%. Both moved: Express
coverage 46% → 68% and NestJS 32% → 48%, from four missing links in one mount chain.
`docs/verifying.md` hands over the same instrument, the protocol, and the seven ways a
careful person gets the answer wrong. Every one of those was made here.

Found by installing 0.9.0 from PyPI into a clean virtualenv and pointing it at the
reference project - the first time a release was tested the way a user meets it - and then
by adjudicating 1,113 findings on two surfaces of that project, one at a time, by hand.

- **Fixed** — a scan without django-extensions **lost every model symbol and said
  nothing**. The extractor did warn, through `logger.warning`; the CLI runs the whole scan
  inside `quiet()`, which mutes WARNING for the host project's start-up noise, and muted
  that line with it - the reference project scanned 66 symbols short and looked clean.
  The warning now goes through `nodetools.report`, the one path seamcheck's own scan-time
  diagnostics take (it writes to stderr when logging cannot), and names the extra to
  install. Written down in `docs/install.md` as well.
- **Fixed** — a global install pointed at a Django project answered with a **bare
  traceback** (`ModuleNotFoundError: No module named 'django'`). The explanation for
  exactly that situation was five lines below, guarding `django.setup()` but not
  `import django`. Same message, exit 2, either way now.
- **Removed** — the map's "Page" view, the one that drew boxes over a screenshot of a page
  a browser had seen. It was 843 KB of geometry on the reference project's index, for a
  picture that said less than the map beside it. `seamcheck observe` no longer records
  box positions; it still promotes the elements the browser proved exist, which is the
  half that was evidence. Older observation files that carry `boxes` are read past.
- **Fixed** — `seamcheck map` printed its addresses into a buffer when stdout was a file,
  and the server never let the buffer fill: a run served for hours with its link unseen.
  The address block is flushed now.
- **Changed** — the map's footer credits its author, not the tool.
- **Added** — the map's page picker is now two: **Page** and **Section**. Page lists the
  HTML pages a person knows (Push Arena, Leaderboard); Section lists the bundles that page
  loads, with "Whole page" first. Whole page is a union page built at write time
  (`group:<n>`) - one chunk that loads like any other page, never seventy-seven chunks
  merged in the browser - so a page with one bundle shows no Section picker at all.
  Union pages are left out of search: each of their nodes is already there under its own
  section. Pages that share a title and route (`where` up to its first ` - `) form one
  group. The readout beside the pickers no longer repeats the page's name - the pickers
  say it - and it starts where they end instead of centred, where two of them covered it.

- **Added** — **Redis** and **Database** are layers across the whole map, not a lane on
  one page. Pick either in the menu and the store is drawn once, unioned over every page
  *and* over the not-reached buckets, so a key that nothing on any page touches is on the
  same screen as the ones that are; `model` symbols count as database. Keys are parked by
  their first segment (`user:*` 60, `challenges:*` 13, … `other` 49) and open one namespace
  at a time - the reference project's 754 Redis nodes draw in 0.8 s. The Page picker stays
  on while a store is up: **Every page** first, then only the pages that reach something
  in the store, and picking one narrows the store to what that page touches. A card's
  sheet lists the pages it is on, and tapping one jumps there with the card open. The
  Page picker's visibility now has one writer; before, leaving Stripe by way of a jump
  lost the picker until the layer was switched again.

- **Added** — **Shared across pages**, a layer of what two or more pages reach: the
  helper every page imports, the endpoint three pages call, the selector two templates
  write - the change that lands somewhere other than the page it was made on. Two
  sections of one page do not count as two pages. Every card there, and the same card
  on an ordinary page, says **on N pages**; its sheet lists them, each a jump. The
  list travels only for the rows two pages reach, so a page of 3,000 nodes with three
  shared ones sends three. The reference project: 2,447 shared nodes (36 routes, 22
  handlers, 1,426 elements) over 22 pages, drawn in 0.6 s.
- **Added** — **a mark is remembered, and a finding that comes back says so.** A mark
  used to expire silently: the code changed under it, the finding was raised as if new,
  and the person who had looked at it once was not told they had. Now the mark is kept,
  stamped with the day the evidence changed, and the finding is raised as **returned** -
  in the console summary (`returned N`, only when there is one), as a pill in the map's
  findings list, and as a line on the card and in every report naming who marked it,
  when, why, and what it is again. Re-marking settles it; `seamcheck triage <id> --undo`
  takes the mark off for good, and the card has an **Undo the mark** button that puts
  that command on the clipboard. `check` JSON carries the list as `returned`; a mark
  whose finding is gone is listed softly as *outlived its finding*, not raised. Older
  `triage.json` files load unchanged.
- **Added** — **every symbol says which function it lives in.** A card named the variable
  and the file, and left the reader to find the function themselves - the one thing they
  already had open. Each symbol now carries an `owner`: the `def` a Python line sits in
  (`submit_push`, `StoreManager.apply`), or the enclosing JavaScript function, which the
  JS side had been reading all along and throwing away. The card reads top-down - the
  thing, the function, the file, the evidence - the findings list names the function on
  each row, `--explain` prints it, and the `unverified` JSON carries it. Descriptive
  only: no status depends on it, so a wrong owner cannot become a wrong finding.
- **Added** — **a Function filter, beside Page and Section.** Type three letters and every
  function in the project whose name matches is offered, prefix first; the reference
  project's 1,474 functions answer a keystroke in 0.1 ms, from an index that loads once
  (28 ms) and weighs 22 KB. Picking one draws **that function's world**: everything it
  touches, unioned across every page its symbols are drawn on - the page holds its route,
  the store layer holds its keys, and a bucket holds whatever nothing reaches, so any one
  page shows a third of the picture - plus one hop out to what reaches those, widened by
  a button. The breadcrumb counts the round-trips per lane, which is the point:
  `submit_push() - 5 symbols · Redis 2 · Postgres 1 · Celery 1` is a handler that should
  be Redis-only, and the Postgres write is the whole diagnosis.

- **Added** — **the map follows calls.** A handler that delegates owns almost nothing
  itself: the reference project's `submit_push` is a view whose Redis writes live two
  calls away in a service class, and a function view that stopped at its own body showed
  the route it answers and nothing else. It now reads the Python call graph, so the
  function's world is the function *and what it calls*, three levels deep -
  `submit_push() - 11 symbols (1 its own, 10 through helpers) · Redis 10`, which is the
  number a reader wants when the handler is slow at thirty thousand players. Under the
  canvas, **Called by** names everything that calls it, each one a click to that
  function. The picker offers every function the scan saw defined - 9,664 on the
  reference project, not just the 1,474 that own a symbol - because a helper that only
  computes a key owns nothing and is exactly the name people type.

  Resolution is deliberately literal: a def in the same file, a `from x import y` of a
  project module, `self.method` inside its own class, and a method whose simple name is
  defined **exactly once** in the whole project. Anything else - `order.save()` where
  `save` is defined in four places, or any call on an imported library - is left out
  rather than guessed, which is the same contract `uncertain` keeps. Adds 4 s to a
  20 s map render on a 12,000-file project, and nothing to a scan: no symbol, status or
  finding depends on it.

- **Fixed** — **an attribute JavaScript creates and JavaScript reads was a finding on
  both sides.** `setAttribute('data-incremented-today', 'true')` was read as a *read* of
  that attribute rather than as the line that brings it into existence, so nothing in the
  graph ever declared it: the writer in one file and the reader in another were both
  reported as reaching for an element nothing renders. Attribute writes -
  `setAttribute('data-x', …)` and `el.dataset.x = …` - are definitions now, the same
  reasoning the tool already applied to `el.id = 'x'`.
- **Fixed** — **a class JavaScript applies is now evidence for the code that reads it.**
  `el.classList.add('goal-celebrated')` on one line and
  `querySelector('.goal-celebrated')` on another is a file that is its own proof, and the
  reader was reported as reaching for nothing because the matcher only ever consulted
  markup. No template will ever mention a class applied at runtime.
- **Fixed** — **a string inside `<script>` is not markup.** The template scanner read
  `data-…` names out of script bodies, so
  `[['daily_hours_active', 'data-modal-daily-hours']]` - a mapping table - invented an
  attribute at that line and then reported it unused, one screen from the real one.
  Script and style bodies are blanked before the attribute scan (blanked, not removed, so
  every line number after them still lands where it did), and a string that spells an
  attribute name is read as a *reference* to it instead - which is what it is.

- **Fixed** — **a display string is no longer read as an endpoint.**
  `periodsTotalElement.textContent = '/24'` - the "/24" in "period 3/24" - was reported as
  a URL the frontend names, twice on the reference project. A string being written into an
  element is text, and a path has a letter in it; both rules now apply before a literal is
  read as a sighting.

- **Fixed** — **an element with more than one handle is not a dead element.**
  `<button id="lazyConfirmBtn" class="lazy-btn-confirm">` is bound by its id, and the
  class was reported as a label nothing uses - which invites someone to strip an attribute
  off working markup. An attribute nothing reads, on an element something DOES reach
  through another of its attributes, is now `uncertain` with that said plainly. Reading
  all 26 findings of this shape on the reference project by hand produced zero deletions.
  Attributes carry the element they sit on for this, because "same line" is a different
  question: these templates run 400 characters wide and put four unrelated tags on a line.
- **Fixed** — **a BEM modifier of a styled block is a variant, not a missing rule.**
  `ms-ladder--cyan` where `.ms-ladder` is styled usually means the modifier's own rule was
  never written, and the label is the only trace that somebody meant to write it.
- **Fixed** — **an element named by a constant is found.**
  `var COUNTDOWN_ID = 'arena-next-season-countdown'` followed by
  `getElementById(COUNTDOWN_ID)` recorded a lookup of `<runtime value>`, so a plainly
  rendered, plainly used element was reported as one nothing reaches. A string that spells
  a name the markup declares is now evidence that the name is live in that file - bounded
  by what the markup declares, so it can never invent an element, and emitted as evidence,
  so it can never become a claim of its own.

  Measured on the reference project: claims 4,023 → 3,658, `connected` +5,165. Replayed
  against the graded push_arena table and counting only rows whose code still exists,
  **87 of 143 false claims are no longer claims and no real finding was lost** - the three
  that stopped being claims are the ones a hands-on review had already reclassified as
  not-defects.

- **Added** — **unreachable, said once, instead of unreferenced said fourteen times.**
  A guard at the top of a function that returns because an element it looks up is rendered
  nowhere means everything below it has not run since the markup changed. The reference
  project had exactly that: three ids deleted long ago, and **292 lines** below the guard,
  reported as **fourteen separate findings** at the same severity as a one-line typo. It
  is now one `dead_region` finding naming the guard, the missing elements and how many
  lines are stranded, and every finding inside the region points at it rather than being
  raised on its own - because a reference made by code that does not execute is neither
  right nor wrong until the region runs again.

  Deliberately narrow: the guard must be a direct statement of the function body, its
  condition must be one this can read (`!a`, `!a || !b`, `!(a && b)` - never `!a && !b`,
  where one missing element proves nothing), and every element it names must be one
  matching has already decided is missing. A return inside a branch ends the function too,
  and this says nothing about it.

  Found **9 regions, 224 lines** on the reference project. Seven were hand-verified true,
  including a 50-line "Ball Game playground" whose elements exist in no template, and a
  39-line particle routine in a file nobody suspected. One was false and is fixed below.
- **Fixed** — **`json_script` declares an element.**
  `{{ ids|json_script:"purchase-receipt-data" }}` renders
  `<script id="purchase-receipt-data">`, and it is Django's own recommended way to hand
  data to JavaScript - but there is no `id=` in the template text, so the element was
  invisible and every `getElementById` for one read as a query for nothing. The
  dead-region pass then turned that single miss into a claim about **161 unreachable
  lines that run perfectly well**, which is how a good finding kind earns distrust.
- **Added** — **a `/static/…` reference is asked of the filesystem, not the route table.**
  Asked of the routes it could only ever come back `uncertain` - which is what happened to
  every static path on one surface of the reference project, all of them files sitting on
  disk. Resolved against the static directories the scan already knows, the same code path
  becomes a check for the opposite case: **a reference to an asset that is not there**,
  which is a 404 at runtime with its own source line attached. On the reference project:
  103 static references, 91 resolved, and **3 genuinely missing** - the store previews for
  `hot_air_balloon`, `coral_reef` and `castle_builder`, whose directories do not exist
  while every other button has one. Collected copies under `staticfiles/` are deliberately
  not proof: a file that exists only there exists only as a build artefact.
- **Added** — **`seamcheck observe` settles multi-writer findings, which reading never
  could.** Two files writing one element is a *risk*; it is a *defect* when they disagree,
  and disagreement has a runtime signature: a value that changes while nothing is touching
  the page. The run now watches every multi-writer element for twelve seconds of idle,
  fourteen samples, and reports three states rather than one — **moved** (the writers
  disagree, and this is the finding that is real, quoting the two values), **steady** (they
  coexist, often by design), and **not rendered in this state** (untested, not clean,
  which is the state 10 of the reference project's 24 were in). Console output counts the
  three, and `moved` names the element and what it flickered between.
- **Fixed** — **a multi-writer whose second writer never runs is not a fight.** Deleting
  one dead function on the reference project retired two multi-writer reports, because
  one of the two writers had been unreachable all along. Where a writer sits inside a dead
  region and fewer than two live writers remain, the report says so instead of sending
  someone to reconcile a conflict that cannot happen.

- **Added** — **precision is reported per stack and per lens, not just per repository.**
  One number across six frameworks describes none of them: the adapter with the most
  attention carries the mean, and every stack behind it looks fine from there. The report
  now says which stack a repository is, aggregates by it, and adds a per-lens table -
  because "which repository is noisy" and "which extractor is noisy" are different
  questions, and only the second one says what to change. `docs/verifying.md` is the
  protocol, including the four ways a careful person grades output wrongly, all four of
  them made here.
- **Fixed** — **a component file is markup.** In a React, Preact or Solid codebase every
  element is written in JSX, and this read only Django templates as markup - so on those
  projects the entire "does this element exist" side of the DOM lens was blind.
  `id="x"`, `data-…` and `className="x"` written in JSX now declare elements.
  saleor-dashboard's CSS modules query `[data-test-id]`, `[data-state]` and
  `[data-highlighted]`, all written in sibling `.tsx` files: **twelve findings, none of
  them true**.
- **Fixed** — **`data-test-id="x"` was read as `id="x"`.** `\b` matches between the `-`
  and the `id`, so a SELECTOR sitting in a string declared an element. Inventing an
  element is worse than missing one: a real finding about a missing element goes quiet,
  because the scan now believes something declares it.
- **Fixed** — **four links of an Express mount chain, three of which were dropped.** Ghost
  mounts its whole admin API as `backendApp.lazyUse(BASE_API_PATH, require('../api'))` →
  `apiApp.lazyUse('/admin/', …)` → `apiApp.use(routes())` → `router.get('/site')`, and
  every one of those forms was invisible: a mount helper by another name, a prefix held
  in a constant in another module, a `require` of a **TypeScript** file resolving to a
  `.js` that does not exist, and a mount with no path - which adds nothing to the URL and
  everything to the chain. `/ghost/api/admin/site` came out as `/site`, and **all eleven
  claims judged on Ghost were false, every one the same shape**: a real endpoint reported
  as a route the server does not serve. An unresolvable prefix now drops its mount rather
  than mounting at a made-up path, because a wrong prefix does not lose one route - it
  moves every route beneath it.

  Measured on the hand-labelled set: **48% → 55%** overall, `django` 62% → 73%,
  `dom_selector` 31% → 40%, and Ghost's eleven false `fetch_target` claims are simply
  gone. **No true finding was lost by either fix** - the true count is unchanged at 74.
  Across the corpus: routes **2,716 → 2,782** and `uncertain` **246 → 72**.

- **Fixed** — **an opening `<script` in the payload swallowed the rest of the map.**
  Escaping `</script>` is the defence everybody knows and it is not enough: an OPENING
  `<script` inside script data puts the HTML tokenizer into its escaped state, so the next
  `</script>` does not close the block and nothing below it runs. It took one note
  explaining Django's `json_script` filter - which has the word `<script>` in it - to break
  the map on two corpus projects while every unit test passed. Every `<` in embedded JSON
  is now `\u003c`, which cannot interact with the tokenizer at all. Caught by the release
  gate that renders all 46 corpus projects, which is exactly what that gate is for.
- **Fixed** — **a command sent through a pipeline is a command.** `r.pipeline()` returns
  an object whose method calls are the same command set, and the receiver test matched the
  client by *name* - so `pipe.set(...)`, `pipe.hincrby(...)` and `hist_pipe.hset(...)` were
  invisible. Pipelining is not an edge case in the reference project, it is the house
  style: the page-render path alone queues 30+ operations on one pipe, so the lens read the
  cold paths correctly and mis-reported the hottest ones. Followed by **assignment** -
  `pipe = r.pipeline()`, `with r.pipeline() as pipe:` - not by name, so a `pipe` in an
  image-processing module is still not a Redis client. A pipeline is also reported as the
  client it came from: counting it as a second connection made the "touched through more
  than one client" check fire on sixteen keys that only ever had one.

  On the reference project: **185 → 240 keys** seen, and four keys the cleanup pass had
  verified by hand (`admin:config_sync_lock`, `admin:global_stats`,
  `analytics:history:concurrent`, `global:mode_switch_occurred`) went from *"read here and
  written nowhere"* to connected.
- **Fixed** — **several writers of an element that does not exist is dead code, not a
  race.** A multi-writer finding is a flicker risk *when the element exists*; when nothing
  renders it and no script builds it, every branch is unreachable and the surviving one is
  canonical - actionable immediately, which the race version is not.
- **Fixed** — **one row per place.** `itemPurchaseModal` was listed four times in one
  report, on one line: a name read and written on the same line is two symbols and one
  thing to look at. 12% of one surface's rows were repeats. The graph keeps every symbol;
  a list a person reads keeps one row per `(kind, label, file, line)`, worst status first.

Known open, recorded rather than hidden:

- **A multi-writer split needs PAGE scoping to be worth much.** Globally, only 2 of the
  reference project's 91 multi-writer findings name an element nothing declares. The
  valuable case is narrower and this does not catch it: `store.js` carries four branches
  for `.modal-container`, an element that exists in `push_arena.html` and
  `challenges.html` - pages `store.js` never runs on - so a project-wide existence test
  says it exists. Which templates load which scripts is statically knowable, but the walk
  that computes it costs ~13 s and is deliberately kept off the CI path.
- **`redis_key` reported `unused` while an incoming edge says `connected` is NOT a
  contradiction**, and the invariant proposed for it would have been wrong. `unused` on a
  key means *written and never read*; the incoming edges are the writes. Checked on all 32
  such keys in the reference project: **none has an incoming read.** Recorded because the
  rule sounds right and is not.
- **`fastapi` scores 3% and `nextjs` 25% on the labelled set**, against `django` 73%. The
  recorded reasons are consistent: classes a library applies at runtime (highlight.js
  emits `hljs-*`; TipTap sets `data-type`), Tailwind variant classes in Svelte markup, and
  vendor design tokens defined in a package's own stylesheet rather than in the repository.
  All three are the same shape as the CDN-class rule, one layer further out.
- **`celery_schedule` scores 0% on six judged claims.** Sentry registers tasks with
  `@instrumented_task(name=…)` rather than `@shared_task`, so the beat entry pointing at
  one reads as a schedule with no task behind it.
- **The call graph is Python only, and name-keyed.** JavaScript functions carry their
  owner but nothing reads their calls yet, so a JS module's world still stops at its own
  body. And two files that both define `reset` share one entry, because the map's
  function index is keyed by the name a person types.
- **The map index is 413 KB on the reference project**, over the 400 KB the plans set as
  the gate, before this release's 15 KB of new picker. The chunked data is not the
  problem; the script itself is.


- **Store attribution stops at the handler's file.** A view is linked to the keys and
  tables it touches only when both sit in the same file (the nearest view above the
  use). A project whose views delegate to a cache or service module - the reference
  project does, everywhere - lists no page under Redis or Database: all 754 keys sit in
  Every page and none in Push Arena. The layer is still right about the store; it is the
  page column that is empty. Following the call from the view into the module it
  delegates to is the fix, and it is a scan-side change, not a map one.
- **`connected` is not stable between two scans of the same commit** on the reference
  project: 40,168 / 40,169 / 40,171 / 40,175 across five runs of identical code, with
  `unused` (1,485), `unresolved` (2,581) and `uncertain` (3,711) fixed. The total moves
  with it, so a few symbols come and go, not a few statuses. Something in the scan is
  order- or timing-dependent; not yet found.
- **Section attribution follows the file, not the page.** On the reference project, 70
  of Push Arena's 77 sections are `static/admin/regression/plugins/*` - test plugins the
  arena template happens to load under one flag - and they crowd out the seven bundles
  that are the page. The grouping is right (they really are reachable from that
  template); the picker needs a way to say which sections are the page and which are
  passengers. Not yet designed.

## 0.9.0 — 3 Sep 2026

**Measured:** coverage **79%** across 47 projects and 137,054 symbols (Flask 91%, Django
83%, FastAPI 56%, Express 46%, Next.js 38%, NestJS 32%) · precision **46%** (158
hand-labelled claims) · recall **6/6** · render **46/46**.

A release about size. The map used to be one file that grew with the repository and was
parsed in full before the first paint - on a 500k-line game that was 22.6 MB, and opening it
at Retina scale with a trace running took a MacBook down. The file a reader opens is now
386 KB for that same game, and it stays small at any size because everything that grows
with the code - the graph, the notes, the search index, the review lists, the observed
pages, the file tree - is read the moment it is looked at and not before. The scan side got
the same treatment: memory is bounded, every file is parsed once, and a 21,000-file
monorepo that used to lose its entire JavaScript layer keeps it.

- **The map can be written as a folder, so the file a reader opens stays small however
  big the repository is.** `seamcheck map --out map/` (or `--bundle`) writes `index.html`
  plus `data/<chunk>.js`, one JSONP file per page for the graph, one per page for the
  detail, one for the search index and one for the commit history; the page fetches each
  as a classic `<script src>` the first time it is looked at, which is the one on-demand
  loader a `file://` page is allowed. A map whose single file would pass 50 MB is written
  this way on its own and says so; `seamcheck map` alone still prints one file. Served
  and shared maps use the same bundle in memory (`/<token>/data/…`, nothing on disk).
  pointlessbutton: **index.html 2.2 MB** instead of 5.5 MB, 258 data files, 3 requests
  to open and search a page. Synthetic 3.7M symbols across 5,900 pages (about the
  symbol count of a 50-100M-line monorepo): index.html 9 MB, opens in 0.52 s at 31 MB
  heap, biggest page (832k symbols) drawn in 1.6 s at 193 MB, no console errors. The
  55 MB search chunk at that size is the next thing to shard.

- **What the page reads on demand now includes the lists, not only the graph.** The
  rows of every review section (`c<key>`), the boxes of every observed page (`o<i>`) and
  the scanned-file tree with its file-to-page lookup (`files`) are chunks too, read the
  first time a section, a page or the Files view is opened - a panel says "Loading N
  rows…" for the moment it takes, and is drawn again only if the reader is still on it.
  Counts stay in the page, so the menu badges and the overview are as before. Three
  quarters of the game's index was those lists, for views most readers never open:
  pointlessbutton **index.html 386 KB** instead of 2.2 MB (5.5 MB two releases ago),
  first draw in 0.36 s at 10 MB heap. Synthetic 3.7M symbols: index.html 6.3 MB instead
  of 9 MB, of which 6 MB is the manifest's per-page rows and file string table - the
  next cut.

- **A scan of a 21,000-file monorepo no longer loses every JavaScript symbol.** The
  parser writes one JSON line per file to a pipe; on macOS those writes are asynchronous
  and unbounded, and at 2.6 GB of output (n8n) node died with `write ENOBUFS` - the whole
  batch was gone, the report said "no JavaScript symbols" and every route it should have
  found was missing. The parser now waits for the pipe to drain before writing the next
  file, and Python reads the stream as it arrives instead of buffering it whole; when a
  parser does still die part-way, the files that arrived are kept and the message says
  how many. n8n: 0 → 24 routes, 25 views.
- **Generation of the pointlessbutton map: 187 s → 48 s, peak memory 1.9 GB.** Every
  JavaScript file is parsed once per scan (a keyed cache the extractors share, cleared
  when the report is done) instead of once per extractor; the syntax tree carries a
  compact `[start, end]` line pair instead of a full location record (parser output
  1.24 GB → 0.80 GB on n8n); tree walks are iterative; response-field matching reads
  the cached tree instead of spawning node per edge (93 spawns removed); the commit
  history diffs snapshot rows straight from JSON and drops each baseline once its last
  reader is done (13.1 s → 4.7 s, 1.4 GB → 0.3 GB, byte-identical series). Same map.
- **Memory during a scan is now bounded.** Parsed trees are kept for the length of a
  scan only up to a budget - a quarter of physical memory by default,
  `SEAMCHECK_AST_CACHE_MB` to set it - and the extractors read every tree past the budget
  as a stream instead of holding all of them at once. Two places pinned every tree
  regardless: the per-file selector memo kept a reference to each AST it had read
  (+1.8 GB on n8n), and two class-usage readers built a list of every tree before
  walking it (+1.1 GB). n8n, 21,000 files, with a 1 GB budget: 3.7 GB → 1.4 GB peak
  RSS. Above the budget a file is re-parsed once per extractor that needs it, so a
  small budget trades time for memory, never correctness.
- The list of files removed since the last snapshot is now sorted before it is written,
  so two renders of the same repository produce the same `commits` chunk.
- **The map opens in under half a second on a 500k-line codebase, at 6 MB JS heap.** The
  node rows used to sit in one inline `MAPDATA` literal that the browser parsed and
  inflated in full before the first paint (pointlessbutton: 22.6 MB file, ~1 KB of heap
  per node × 51k nodes - enough to take a MacBook down when opened at Retina scale with a
  trace running). The rows now live in inert `<script type="text/plain" data-chunk>`
  blocks, one per page for the graph and one per page for notes/snippets/context, plus
  one for the search index and one for the commit history; blocks over 4 KB are
  gzip + base64 and unpacked in the browser through `DecompressionStream`, so it is still
  a single file that opens from disk. Only the page being looked at is decoded. Same map,
  headless, DPR 1: **22.6 MB → 5.5 MB**, open 0.43 s, heap 6 MB at open and 24 MB with the
  biggest page (18,943 symbols) drawn and its aggregate expanded to 500 cards, search
  index (42,576 rows) 20 ms on first keystroke, `user` 3 ms, no console errors.
- **Measured past any real repository.** `tools/synth_map.py N` builds a graph in the
  shape of the real map - three buckets holding 45%, a dozen heavy bundles, hundreds of
  ordinary pages, fan-in wires, notes and context - and renders it through the same
  `render()`. At 730k symbols (≈10M lines at the measured 73 symbols per 1k lines): 43.7 MB
  file, opens in 0.66 s at 116 MB heap, biggest page (164k symbols) draws in 50 ms, no
  errors. At 2M symbols (≈27M lines): 112 MB file, 0.8 s, 18 MB.
- **Search holds columns, not rows.** The index used to be one JSON array of 7-field
  objects, inflated on the first keystroke - ~330 bytes of heap per symbol, 633 MB at 2M.
  It is now seven columns: ids and labels as two newline-joined strings, kind / status /
  file / line / page as `Int32Array`s over the interned tables. A query is one
  `indexOf` walk over the lowercased label string plus a typed-array compare, and only the
  rows it returns become objects. 2M symbols: index in 0.63 s, a query in 4 ms, **633 →
  225 MB**; 730k: 243 → 83 MB. Parsed chunks are also released once their rows are in
  place (the 450k-symbol bucket was being held twice: 2M heap at open **132 → 18 MB**).
- **The Stripe, Celery and GraphQL layers are pages the renderer builds**, not a union the
  browser assembled from every page on open (which forced every page to be decoded).
  Hidden from the page picker; empty when the scan found nothing for that service.
- `tools/verify_output.py` decodes the chunks the way the page does and fails a map whose
  manifest count disagrees with the rows a chunk actually holds.
- **The map no longer lags while you pan or zoom.** A gesture used to move the `<svg>`
  root's transform, which made Chrome re-lay out every SVG element on every pointer move
  (measured 22 ms Layout + 25 ms compositor Layerize per move on a 3,461-node page). The
  gesture now moves a plain `<div>` above the svg with `contain:paint`, sized to three
  viewports so a drag shows pre-rasterised content instead of blank strips, and the view
  is committed once when the gesture ends. Same page: 60 drag moves 4.65 s → 1.15 s wall,
  ~8 ms main-thread per move; verified headless on the 22 MB pointlessbutton map.
- **A page with tens of thousands of wires now draws in one frame.** The largest
  pointlessbutton page (push-arena-main, 3,461 nodes) used to build 10,040 wire paths on
  open. Now only one kind of node is expanded at a time, an expanded kind shows 500 cards
  per window with a dashed "+N · 1–500 of 1,079 · tap for next" card to page through, and
  the wires between two cards merge into one path whose stroke scales with the count
  (`data-n`). Collapsed: **10,040 → 40 paths**; one kind expanded: 529 cards / 982 paths /
  3,678 DOM nodes. Measured headless on the 22 MB map: 60 drag moves 0.92 s wall, 30 zoom
  ticks 0.90 s, both with **0 long tasks**; search 0.65 s (debounced 120 ms); 26 MB JS heap.
- **A filter combination that empties the canvas now says so.** The status chip plus a
  layer with none of that status drew nothing and stayed silent: two pieces of code wrote
  the empty state (an svg text in `draw()` and the `#nothing` notice), and the one that ran
  first hid the other. `reportEmpty()` is the single writer now, and it names the file
  filter too.
- **Removed a control that never appeared.** The "all" chip in the colour key was skipped
  by the loop that un-hides chips, so it was dead on every page. The way back from a status
  filter is the clear on the filter notice; the notice's button also lost a leftover rule
  from an earlier layout that had been restyling it as an underlined text link.
- The browser tests (`test_map_runs_in_a_browser.py`, 71) were rewritten against the
  current one-menu layout — they had been tapping a lens list that lives behind the menu
  button since that redesign, and asserting a theme control and a pill bar that no longer
  exist. All 71 pass headless.

- Store bands were black: the band stroke read an undefined `--text` token. Now `--ink`,
  with a visible white border.
- Docs: README explains the one-page-per-script map; `docs/the-map.md` has the long form;
  CONTRIBUTING has the tested setup (`pip install -e ".[django,mcp]" pytest`, 859 tests)
  and the pre-push self-scan rule. Two design docs under `docs/plans/`: `observe` riding
  along with an E2E suite, and the map at 1M–10M lines (data out of the HTML, Canvas2D
  with level of detail, bucket trees). Neither is implemented yet.

Known open, recorded rather than hidden:

- 13 tests in `test_renderer_map.py` fail at HEAD and predate this change: they pin
  literal markup/strings from an earlier renderer (rail vs select, `it_loads_nothing_from_
  the_network` — the Google Fonts `<link>` breaks the offline promise it asserts). To be
  rewritten against behaviour, not strings, during the map-at-scale work.
- Dead CSS from earlier layouts is still in the map stylesheet (`.pill`, `.top`,
  `.legendbar`, `.filters`): harmless, ~2 KB, to go in a cleanup pass once the stale
  renderer tests that still assert some of it are rewritten.

## 0.8.2 — 3 Sep 2026

**Measured:** coverage **79%** across 47 projects and 136,181 symbols (Flask 91%, Django
83%, FastAPI 56%, Express 45%, Next.js 36%, NestJS 32%) · precision **45%** · recall
**6/6** · render **46/46**.

The map learned to say what it is looking at. It reads across languages, services and data
stores, and it had never once named any of them.

- **Added** — every node carries the **language of its file** and the **service that owns
  it**. Bands name the languages inside them, and a band with more than one service lays
  out a lane each: `@acme/api · TypeScript`, `web · Python`. A monorepo used to render as
  one anonymous strip.
- **Added** — **the server reaches its store**. Redis emits a use per call site, as
  `db_table_use` always has, and each view links to the store work written inside it. A
  chain used to stop at the handler while THE STORE sat below as an island nothing could
  reach; a path now runs browser → seam → server → store in one line.
- **Changed** — following one path is drawn as a path: **left to right by hop**, straight
  schematic wires with arrowheads, files as pills. It was being drawn with the survey's
  rules, so a four-hop chain came out as a narrow column with four writers stacked on top
  of each other.
- **Fixed** — a multi-writer finding drew **one** writer. The others were basenames that
  nothing resolved to a node, so the panel listed four while the canvas showed one — and
  isolating it showed the least useful subset available.
- **Fixed** — under isolation nothing was ever "on the chain", which switched off the
  schematic wires, the heavier stroke and the lit arrowheads in the one view whose own
  comment says it exists to turn them on.
- **Fixed** — a single service rooted in a **subdirectory** was answered for every file in
  the repository, labelling a whole Django application as the Node service beside it. And a
  Django service in a monorepo carries no packaging manifest, so it was not a service at
  all; `manage.py` is the marker it always has.

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
