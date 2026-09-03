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

Found by installing 0.9.0 from PyPI into a clean virtualenv and pointing it at the
reference project - the first time a release was tested the way a user meets it.

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

Known open, recorded rather than hidden:

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
