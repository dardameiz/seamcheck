<!-- Design document, 2026-09-03, from read-only measurement of the 22.6 MB pointlessbutton map.
     Numbers marked measured are measured; the rest are estimates. -->

# Seamcheck map at 1M–10M lines: design document

## Executive summary (12 lines)

1. Measured on the 22.6 MB pointlessbutton map: `MAPDATA` is 20.47 MB of it (90%); nodes+edges are 19.0 MB; the 66-char symbol ids alone cost 3.5 MB in nodes and ~7 MB more in edges (edges carry both ids as strings).
2. The worst page (`push-arena-main`, 3,461 nodes) draws only ~35 cards by default (kinds over 28 collapse to one aggregate card) but 10,040 `<path>` wires, of which 8,591 are the identical line between two aggregate cards. Merging wires by card pair leaves 50. This is Phase 1: ~30 lines in `draw()`, no format change.
3. Second-cheapest wins: `expandedKind` expands every kind, not the clicked one (`map_html.py:1925` checks `!expandedKind`); every search keystroke rebuilds the whole SVG (`:2986-2990`); `.ed{opacity:.38}` plus per-path `marker-end` make each wire expensive.
4. Single-file inline JSON cannot scale: linear extrapolation gives ~430 MB at 700k symbols and ~4.3 GB at 7M; V8 cannot even hold a script literal that large. The data must leave the HTML.
5. Recommended data shape: a directory bundle `map/index.html` + `map/data/*.js` JSONP chunks (classic `<script src>` works from `file://`; `fetch()` and module scripts do not), one core chunk per page with numeric node refs, a lazy detail chunk per page (note/snippet/context = 153 B/symbol, 34% of the payload), a small manifest with per-page counts, and sharded search files. Measured columnar cost: 127 B/symbol core+ids+edges, ~19 B gzipped.
6. A single-file variant remains for shareability: the same chunks embedded as gzip+base64 text blocks inflated with `DecompressionStream` — ~2.5 MB at 37k, ~32 MB at 700k; not offered above that.
7. Rendering: Canvas2D with level-of-detail, viewport culling and a grid hit-test. SVG DOM is ~4 µs per element per commit (the code's own 50 ms/12k measurement at `:2837-2845`); Canvas is ~0.2–5 µs per primitive with no DOM memory. WebGL is not needed because LOD bounds visible primitives to a few thousand by construction; no CDN, no vendored engine.
8. Keep one page per JS entry root. Add progressive disclosure inside a page: band → kind aggregate → (file group when a kind exceeds ~500) → cards, never more than ~3,000 cards on a canvas, and the Not-reached buckets become folder → file → symbol trees with per-file chunks.
9. Search, jump-to, filters, commit view and the detail sheet all work from the manifest plus the currently loaded page chunk; opening a result loads its page chunk first. Served mode adds `/search` over a SQLite index and the existing `/source`.
10. Generation is O(pages × symbols) today (`mapdata.py:211,223`), parses every JS module once per entry root (`api.py:577-583` → `js_extractor.py:271-294`, no AST cache), keeps every commit snapshot graph in memory (`history.py:184-190`), and `json.dumps` one giant dict (`map_html.py:4321-4363`). Fix: index once, parse once, stream per-chunk writes, multiprocess per page.
11. Verify without opening anything headed: a primitive-count unit test over the payload, a graph-level synthetic generator (37k/370k/3.7M), and Playwright headless Chromium with `--js-flags=--max-old-space-size=768` reading `performance.memory` and timing `draw()`.
12. Do not: move pixel layout into Python (it depends on viewport width, `:2078-2085`, and is not the cost), raise `MAX_DRAW`, load anything from a CDN, use ES modules for chunks, keep the raw `MAPDATA` alive after inflate, or promise a 7M-symbol single file.

---

## 1. Where the current design breaks first

All numbers below marked **measured** come from parsing `/Users/balazssimon/dev/pointlessbutton/docs/maps/connectivity-map.html` (22,560,914 bytes) with a read-only Python script; nothing was opened in a browser. Numbers marked **estimate** are extrapolations or platform knowledge, not measurements.

### 1.1 What is inlined (measured)

`/Users/balazssimon/dev/seamcheck/seamcheck/renderers/map_html.py:4575-4591` emits eleven `<script>const X=...</script>` blocks:

| block | bytes | what |
|---|---:|---|
| `MAPDATA` | 20,468,227 | 125 pages, 51,698 node rows, 50,607 edge rows, 29 commits |
| `CONSOLE` | 875,374 | review sections, capped 400 rows / 150 per status (`:4388-4389`) |
| `OBSERVED` | 842,874 | per-page observed DOM boxes |
| `FILES` | 129,772 | 955 file records |
| `SERIES`, `MEANING`, `WHY`, `SHARE`, `OPEN`, `BLIND_SPOTS` | ~20 KB | small |
| CSS + JS source | ~1.1 MB | `_CSS` (`:82-1190`) and `_SCRIPT` (`:1192-4270`) |

Inside `MAPDATA` (the row format is `_payload`, `:4321-4363`, nodes as arrays against string tables for kind/status/file/lang/service):

| field | bytes | note |
|---|---:|---|
| `id` | 3,548,924 | average id length 66.6 chars, max 140; sent as a full string per row |
| edges | 7,405,873 | each edge repeats both ids as strings (`:4345`) |
| `snippet` | 2,438,733 | up to 400 chars (`mapdata.py:186`), 120 in buckets (`:349`) |
| `note` | 2,139,191 | |
| `context` | 1,686,760 | block of source, only for `_CONTEXT_KINDS` (`mapdata.py:22`) |
| `label` | 758,767 | |
| commits | 1,415,504 | 29 commits × up to 300 changes (`api.py:704`) |
| kind/status/file/line/lang | ~440,000 | already interned |

42,686 unique ids across 51,698 rows: a symbol reached from several pages is repeated once per page (21% duplication). Edge rows: 50,607 vs 42,825 unique.

Per-page sizes (measured): `unreached:template` 18,945 nodes (all `dom_attr`), `unreached:js` 8,510, `push-arena-main` 3,461 nodes / 12,875 edges, `store-main` 2,164 / 4,849, `unreached:css` 5,795, `main` 1,316 / 3,628.

### 1.2 How mapdata is built

`/Users/balazssimon/dev/seamcheck/seamcheck/mapdata.py`:

- `build_map` (`:372-419`) loops pages and calls `build_page_map` per page (`:386`). Inside it, `by_id` is rebuilt from **all** graph symbols per page (`:211`), and all symbols are scanned again to find that page's seeds (`:223-229`). That is O(pages × symbols): 125 × 37k today, and would be e.g. 2,000 × 7M = 1.4 × 10^10 dict operations at 10M lines.
- The reach is a 4-hop BFS from seeds (`:274-290`, `_MAX_HOPS = 4`, `:106`) over an adjacency built once (`:364-369`). The per-page reached set is bounded by the bundle, so page sizes grow slowly; the unreached buckets grow linearly (`:327-361`): everything not covered by any page lands in one of five flat pages, and the biggest is already 18,945 nodes.
- `_context` (`:68-86`) reads whole source files into `_source_cache` (`:24`), which is never evicted; at 10M lines that is every line of the repository as a Python `str` (estimate ≥ 1 GB).

`/Users/balazssimon/dev/seamcheck/seamcheck/api.py`:

- `_page_files` (`:566-583`) calls `discover_js_files` once per entry root; `discover_js_files` (`/Users/balazssimon/dev/seamcheck/seamcheck/extractors/js_extractor.py:271-294`) parses every reachable module through the node subprocess (`_parse_files`, `:115-143`) with **no AST cache across calls**, so shared modules are parsed once per root. The docstring already records ~13 s for 125 roots (`api.py:568`). `build_file_tree` (`/Users/balazssimon/dev/seamcheck/seamcheck/filetree.py:78-86`) parses the JS files a third time.
- `_render_map` (`:640-726`) returns one `str`; `commit_series` (`/Users/balazssimon/dev/seamcheck/seamcheck/history.py:179-198`) loads every snapshot graph and keeps them all in `graphs` (`:184-190`).

### 1.3 What the browser holds per symbol (estimate; reasoned from the code, not measured)

`map_html.py:1249-1270` inflates every node of every page into an 11-property object, and every edge into a 3-property object with fresh strings for `source`/`target`. `MAPDATA` is a global `const` (`:4575`) so the raw arrays stay alive too, and V8 keeps the script source (the 20 MB literal) for the lifetime of the page. Then `byId` (`:1400-1401`) maps all 42k ids, `searchIndex()` (`:2096-2109`) builds another 51k-row array with a `hay` string per node on first search.

Rough per node-row: raw JSON share ~396 B (20.47 MB / 51,698) + inflated object ~120 B + its strings (id ~85 B, label ~35, note/snippet/context ~150) + edge objects (~2 per node, each ~200 B with two fresh id strings) + search row ~180 B. Order of **1 KB per node row** → ~50–60 MB JS heap for this map before any SVG. Linear scaling: ~1 GB at 700k, ~10 GB at 7M. Chrome's renderer heap ceiling is a few GB on 64-bit desktop, and V8's maximum string length is about 2^30 characters, so a 4.3 GB inline literal is not "slow", it is unloadable; 430 MB (700k) is likely to OOM on a laptop after inflate (estimate).

### 1.4 What `draw()` does (`map_html.py:2225-2484`)

1. `visible(p)` (`:1700-1734`): `lensed(p)` filters all page nodes (`:1640-1647`), then `capping()` keeps the first `MAX_DRAW = 2000` (`:1691-1698`).
2. `layoutFor(p)` (`:2214-2223`) caches; on a miss `layout()` (`:2069-2086`) runs `place()` nine times (`ROW_CHOICES`, `:1825`) and picks the candidate that fits `svg.clientWidth/Height` best (`:2078-2085`). A kind with more than `AGGREGATE_OVER = 28` members (`:1853`) collapses to one aggregate card (`:1925-1938`) unless `expandedKind` is set.
3. `chainOf(p, lit)` rebuilds adjacency from all page edges on every draw when a node is lit (`:2322`, `:1658-1681`).
4. Wires: every page edge whose endpoints have a position becomes a `<path>` with `marker-end` (`:2385-2410`). The only merge is "both ends on the same aggregate card" (`:2391`). A module → aggregate or aggregate → aggregate edge is emitted once per underlying edge.
5. Cards: one `<g><rect><title><text><text>` per non-aggregated node (`:2449-2480`).
6. `svg.innerHTML = out.join("")` (`:2482`) replaces the whole DOM.

Measured, simulating steps 1, 2 and 4 on the real payload (default view, nothing expanded):

| page | nodes | cards drawn | aggregates | wire `<path>`s | distinct card-pair wires |
|---|---:|---:|---:|---:|---:|
| push-arena-main | 3,461 | 35 | 6 | 10,040 | 50 (largest pair: 8,591 identical paths) |
| store-main | 2,164 | 88 | 2 | 4,409 | 160 |
| main | 1,316 | 42 | 5 | 3,628 | 75 |
| stripe_payment | 992 | – | – | 1,951 | 89 |
| unreached:template | 18,945 | 1 | 1 | 0 | 0 |

So the "~2,000 node cards" case only arises after clicking an aggregate, and then, because `:1925` tests `!expandedKind` rather than `expandedKind === kind`, **every** kind on the page expands at once (the comment at `:1286-1289` says one at a time; the code does not do that). The default cost is dominated by wires that are visually one line drawn thousands of times, each with its own `opacity` (`.ed`, `:618`, a per-element transparency group) and its own marker instance (`:1787-1797`, `:2404-2405`).

The code's own measurement: writing the `#vp` transform on a ~12,000-element page costs "about 50ms a frame on a Retina screen" (`:2837-2845`) — that is why gestures use a compositor transform on `.cvlayer` (`:600`, `:2854-2871`) and commit once at the end. That trick makes panning smooth **during** the gesture regardless of element count; the 50 ms hit lands at gesture end and on every `draw()`.

Other full passes over all pages in memory: `fillPages()` calls `lensed()` for every page (`:1440-1452`) and runs on every status click (`:1480`); `layersPresent()` (`:1342-1350`); `currentPage()` for a service layer unions all pages (`:1361-1380`); `bestPageFor()` (`:3801-3808`); search input calls `draw()` on every keystroke (`:2986-2990`) and `searchEverywhere` is a linear substring scan (`:2113-2129`).

### 1.5 What the selector, search and sheet need in memory today

- Page selector: `PAGES[i].nodes` for counts (`:1445-1448`).
- Search: `searchIndex()` over all nodes.
- Detail sheet: `byId` for every hop label (`:2565-2581`), `routes()` rebuilding adjacency from the current page's edges per click (`:2534-2560`), `n.context`/`n.snippet` for the code box (`:2737-2743`); when served it fetches `/source` (`:2748`, `serve.py:92-117`).
- Files view: `treeHtml()` renders every `FILES` row into one `innerHTML` (`:3536-3585`); at 100k files that is another wall.

### 1.6 Two things in the code that contradict stated assumptions

- The "loads nothing from the network / no `<script src`" promise (`test_renderer_map.py:22-31`) is already broken by the Google Fonts `<link>` tags at `map_html.py:4462-4471`; that test currently fails (run read-only with `-p no:cacheprovider`). The guard needs rewriting anyway, which is convenient for Phase 2.
- "Browser never lays out" as a goal: layout is chosen by measuring the viewport (`:2078-2085`, pinned by `test_the_wrap_is_chosen_by_measuring_not_by_a_constant`, `test_renderer_map.py:436`) and costs a few ms; the DOM is the cost.

---

## 2. Data: getting the graph out of the HTML

### 2.1 Loading mechanisms

| mechanism | `file://` | `http://` | notes |
|---|---|---|---|
| inline `<script>` (today) | yes | yes | whole payload parsed and retained; V8 string cap |
| classic `<script src="data/x.js">` (JSONP: `SC.chunk("p12", {...})`) | **yes** (Chrome, Firefox, Safari) | yes | the only on-demand mechanism that works from a file; scripts are not subject to CORS |
| `<script type="module">` | no in Chrome from `file://` | yes | do not use |
| `fetch()` / XHR | no (Chrome: scheme not supported; Firefox: unique origin) | yes | served mode only |
| `<script type="text/plain">` / `<template>` blobs in one file | yes | yes | parsed as text by the HTML parser, not by JS; inflate with `DecompressionStream("gzip")` (Chrome 80+, Firefox 113+, Safari 16.4+) or a vendored ~8 KB inflate |
| SQLite behind the server | – | yes | `sqlite3` is stdlib; FTS5 availability depends on the Python build (not verified here) |

Recommendation: one chunk format (JSONP `.js`) used by three containers: the bundle directory (default above a size threshold), the single file (chunks embedded as gz+base64 text blocks, `--single-file`, default below the threshold), and served mode (the same bundle on disk, plus dynamic endpoints).

### 2.2 Storage layout

```
map/
  index.html                 shell: CSS, JS, manifest inline (small)
  data/manifest.js           pages[] {id, title, where, chunk, counts by status, counts by kind, bytes}
                             kinds[], statuses[], langs[], services[], columns, bands
                             files -> best page (compact), file counts (today's FILES)
  data/pages/<n>.js          core: ids[], labels[], kind[], status[], file[], line[], edges (src,dst,status as ints)
  data/detail/<n>.js         note[], snippet[], context[] aligned to core ordinals; loaded on first sheet open
  data/buckets/<b>/tree.js   unreached bucket: folder -> file aggregates with status counts
  data/files/<h>.js          symbols of one file (for bucket drill-down and the file filter)
  data/search/<s>.js         token-prefix shards: [labelLower, page, ordinal]
  data/commits.js            commit series + changed sets (loaded when the picker opens)
  data/console.js, observed.js
  map.sqlite                 (served mode only, optional) symbols, edges, detail, FTS
```

Node ids never travel inside edges again; edges are `[srcOrdinal, dstOrdinal, status]` within a page chunk. Ids stay in the page chunk because `seamcheck triage '<id>'` (`:2823`), `jumpTo`, editor links and cross-page identity need them. Detail is separate because it is 153 B/symbol against 59 B for everything the canvas needs.

### 2.3 Measured bytes per unique symbol (this repo, 42,686 symbols)

| part | raw | gzip | per symbol raw / gz |
|---|---:|---:|---|
| core (label, kind, status, file, line; numeric id) | 1,802,986 | 395,336 | 42 / 9.3 B |
| edges (numeric) | 718,703 | 157,073 | 17 / 3.7 B |
| id strings | 2,925,916 | 258,415 | 69 / 6.1 B |
| detail (note, snippet, context) | 6,524,153 | 578,165 | 153 / 13.5 B |
| **today's row format, nodes+edges** | 19,040,096 | (whole MAPDATA gz 1,709,480) | 446 / 40 B |

### 2.4 Size estimates per option (extrapolated linearly from the measured per-symbol costs; the 700k/7M symbol counts are the owner's estimates, i.e. ~73 symbols per 1k lines)

| option | 37k | 700k | 7M | verdict |
|---|---|---|---|---|
| A. status quo, one inline JSON | 22.6 MB (measured) | ~430 MB | ~4.3 GB | 700k likely OOM after inflate; 7M exceeds V8 string limit |
| B. bundle, uncompressed chunks | 5.4 MB core+ids+edges, 6.5 MB detail on disk; **loaded per page ≤ 0.5 MB** | 89 MB + 107 MB on disk | 890 MB + 1.07 GB on disk | fine: only the current page + manifest are in memory |
| C. bundle, gz+base64 chunks (needed if disk matters; not needed for loading) | ~1.8 MB | ~30 MB | ~300 MB | optional; costs an inflate per chunk |
| D. single file, embedded gz+base64 chunks | ~2.5 MB | ~32 MB | ~300 MB | recommended default up to ~700k; refuse above ~50 MB and write a bundle |
| E. served + SQLite (~150–200 B/symbol) | ~8 MB | ~130 MB | ~1.3 GB | same page endpoints as B; adds `/search`, `/symbol/<id>` |
| manifest (per page ~200 B + per file ~25 B) | 125 pages, 955 files: ~50 KB | ~1 MB | ~5 MB (2k pages, 100k files) | inline in `index.html`; treeHtml must become lazy above ~10k files |

Chunk load memory: the biggest page today (3,461 nodes, 12,875 edges) is ~0.45 MB raw core; at 10M lines page sizes are bounded by the 4-hop reach from one bundle (estimate: single-digit MB worst case). The unreached buckets are the exception and are restructured in §4.

---

## 3. Rendering

### 3.1 Cost model per frame (order-of-magnitude; SVG figure derived from the code's own 50 ms/12k measurement at `map_html.py:2837-2845`, the others are platform experience, not measured here)

| visible primitives | SVG DOM (commit/redraw) | SVG memory | Canvas2D full redraw | WebGL instanced |
|---|---|---|---|---|
| 2k | ~8 ms | ~3 MB | ~1–4 ms | <1 ms |
| 20k | ~80 ms (plus ~5 MB innerHTML string, ~0.5 s parse) | ~30 MB | ~10–40 ms | ~1 ms |
| 200k | ~800 ms, 50 MB innerHTML, seconds to parse | ~300 MB+ | ~0.1–0.4 s (text dominates) | ~2 ms + text atlas |

Canvas2D per primitive: `fillRect`/`strokeRect` ~0.1–0.3 µs, a bezier `stroke` ~1–3 µs, `fillText` ~2–5 µs. Text is the expensive primitive on canvas, which is exactly what LOD removes at low zoom (labels already vanish below `k < 0.24`, `:2501`).

Conclusion: at 2k primitives SVG is fine (Phase 1 alone gets the worst page to ~35 cards + 50 wires); at 20k SVG is marginal and Canvas is comfortable; at 200k nothing but WebGL draws it in one frame, and nobody should be shown 200k cards. Design so the visible set is ≤ ~5k primitives and Canvas2D suffices. WebGL would require a vendored text renderer (SDF atlas) to keep the offline promise; it is deferred, not planned.

### 3.2 Level of detail (progressive disclosure inside a page)

- L0 page: bands, one card per (band, lane, kind) aggregate with count and open count, merged wires between cards with counts (the existing `aggregates`, `lanes`, `bands` structures from `place()`, `:1855-1990`).
- L1 kind opened: if ≤ 500 members, cards; else group by file (file cards with counts) → L2 file opened: cards, paginated at 500 ("next 500") rather than capped silently.
- L3 chain: the existing `layoutPath` (`:2005-2066`) for isolate mode, unchanged.
- Zoom-dependent detail: below k≈0.24 no labels (as now); below k≈0.1 cards become status-coloured dots, wires thin lines; aggregate/band text stays.

Rule: never lay out or draw more than ~3,000 cards; the level above always exists to stand in for them.

### 3.3 Culling, hit-testing, edges

- Layout is a grid of rows inside kind columns (`:1939-1943`), so a uniform grid (cell = card width) is the spatial index; quadtree is unnecessary. Store per drawn item `{x, y, w, h, ref}`; culling is a rectangle test per row of cards; hit-test is cell lookup then ≤ a few rects.
- Edge merging: key = (source card, target card, status), draw once with count (and thicker stroke by log count). Under `lit`/isolate, only chain edges are drawn individually.
- Labels: only in-viewport and only above the label zoom; truncate with the existing `fit()` (`:1737`) but cache measured widths per string.
- Virtualisation: redraw on `requestAnimationFrame` only when dirty; during a gesture keep the existing compositor-transform trick (`:2854-2871`) applied to the `<canvas>` element and redraw crisp on settle, exactly as SVG does now (`settleSoon`, `:2893-2896`).
- Worker layout: not needed while a level is ≤ 3k items (`place()` × 9 is ~ms). Keep layout on the main thread; keep the viewport-fit selection.
- Python-side layout: precompute *structure* per page (kind order, per-kind counts, file groups, service lanes, merged wire counts) so the browser can draw L0 from the manifest before the chunk finishes; do not precompute pixels.

---

## 4. Interaction model at scale

- **One page per JS entry root stays** (`api.py:566-583` → `mapdata.build_page_map`). Pages become chunks; the selector reads titles and counts from the manifest, so `fillPages` (`:1440-1452`) stops touching nodes. Status/layer/commit counts per page are precomputed per (status, kind) in the manifest; the commit picker's per-page counts (`:3095`) come from `commits.js` changed sets intersected with page id lists lazily (per-page id sets are in the chunk; show "…" until loaded, or precompute per commit × page counts at generation, which is cheap).
- **Opening a page**: load `pages/<n>.js` → inflate to typed arrays → L0 draw. Detail loads on first sheet open for that page (one chunk, ≤ ~0.5 MB for a 3.5k-node page).
- **Search**: file mode uses token-prefix shards (split labels and file basenames on camelCase/`_`/`-`/`/`, index by the first two lowercase characters; each shard ≤ ~1 MB, split further by third character when larger). A query loads 1–3 shards, ranks with the existing scoring (`:2118-2125`), shows 60 results with page name. Served mode calls `/search?q=` (SQLite, FTS5 when available, `LIKE` fallback). Substring-anywhere search over 7M labels in file mode is not offered; word-prefix search is, and that matches what the ranking already prefers.
- **Open this symbol / deep links**: `jumpTo(ref)` (`:2132-2147`) becomes `async`: ensure the page chunk, then `lit = ordinal`, draw, `show()`. URL hash `#p=<page>&s=<ordinal>` (or `&id=` for stability across regenerations) makes a finding shareable.
- **Detail sheet**: `routes()` (`:2534-2560`) uses the page's edge arrays (present in the core chunk) — build the per-page adjacency once per chunk load, not per click. Hop labels come from the chunk's label array; cross-page hops (a symbol on several pages) resolve via the manifest's `id → (page, ordinal)` only in served mode or through the search shard; in file mode the sheet shows the page-local route, which is what it shows today.
- **Filters**: status, layer and section filters operate on the loaded chunk's typed arrays (a `Uint8Array` mask) and the L0 aggregate counts recompute from the mask — no object allocation.
- **Not-reached buckets** (`mapdata.py:327-361`): a bucket page is a tree (folder → file → symbols) instead of a 19k-node flat list. Its chunk carries only folder/file aggregates with status counts; a file card loads `files/<h>.js`. The file filter (`:1707-1709`) and Files view "on map" (`:3835-3848`) use the same file chunk, so `bestPageFor` (`:3801-3808`) is replaced by the manifest's file → page map computed at generation.
- **Service layers** (`:1358-1380`): generate `layer:stripe` etc. as ordinary page chunks at build time instead of unioning all pages in the browser.
- **Nobody sees a hairball**: L0 is the only thing drawn on page open; a kind opens on click; a bucket opens folder by folder; the cap message ("Showing 2,000 of 18,945", `:2250-2253`) disappears because nothing is silently cut — the next level is always a click away.

---

## 5. Generation-time cost in Python for 7M symbols

Where time and memory go today, and the bound:

| step | today | at 7M (estimate) | fix |
|---|---|---|---|
| `_page_files` (`api.py:566-583`) | node parse of every reachable module per root, no cache (`js_extractor.py:271-294`) | roots × modules parses: days | parse each file once, build an import graph, BFS per root in Python; reuse the same ASTs in `build_file_tree` (`filetree.py:78-86`) |
| `build_page_map` `by_id`/seed scan (`mapdata.py:211,223`) | O(P × S) | 10^10 ops | build `by_id` and `seeds_by_file` once in `build_map`; per page O(files in bundle + reached) |
| `_context` (`mapdata.py:24,68-86`) | whole-repo source cache, never evicted | ≥ 1 GB | compute context while writing each detail chunk; LRU of ~256 files; or defer to served `/source` |
| `build_unreached_pages` (`:327-361`) | 5 passes over all edges | fine | group by folder/file while streaming |
| `_payload` + `json.dumps` (`map_html.py:4321-4363`) | one dict, one string | multi-GB string, ×2 on encode | write each chunk with `json.dump` to its own file as soon as the page is built; never hold two pages of rows |
| `commit_series` (`history.py:184-190`) | all snapshot graphs retained | 29 × GB | keep only the current pair; cap the series; write `commits.js` separately |
| `save_snapshot` (`snapshot.py:29`, `indent=2`) | pretty JSON of the whole graph | GBs per commit | out of scope for the map but flagged: `separators=(",",":")` halves it |
| page BFS per page | serial | 2k pages × BFS over a shared adjacency | `multiprocessing` with `fork` (explicit `get_context("fork")`; macOS defaults to `spawn`, which would pickle the graph per worker); each worker writes its own chunk files; parent only collects manifest rows |

Memory floor: the scan's own `Graph` (7M `Symbol` dataclasses, `graph.py:23-33`) is ~3–4 GB in CPython regardless of the map (estimate). The map should add at most ~1× that transiently (one page's rows at a time), not 2–3× as now (MapNode copies for every page plus the full JSON string).

---

## 6. Recommended architecture and phased plan

**Target**: `seamcheck map` writes `docs/maps/map/` (bundle) or a single `connectivity-map.html` when the payload is under a threshold (`--single-file` forces it; above ~50 MB the CLI refuses and says why). The same `index.html` runs from `file://` and from the server; the server serves bundle files plus `/source`, `/inventory`, and `/search`. The canvas is a `<canvas>` with LOD; SVG stays for one release behind a flag.

### Phase 1 — draw-side fixes, no format change (1–2 days)

Files: `map_html.py` (`draw()` `:2373-2410`, `place()` `:1925`, search input `:2986-2990`, `.ed` CSS `:618`), `test_renderer_map.py`, `test_mapdata.py`.

- Merge wires by (source card, target card, status) with a count; expected 10,040 → 50 on `push-arena-main` (measured above).
- `:1925`: `items.length > AGGREGATE_OVER && expandedKind !== kind` so one kind opens at a time; when an opened kind exceeds ~500, page it ("next 500") instead of drawing 2,000.
- Search keystrokes: toggle a `faded` class per card (or a `hit` mask) instead of `draw()`; debounce 80 ms.
- `.ed { stroke-opacity: .38 }` instead of `opacity`; keep markers only on lit/chain wires, draw plain wires without `marker-end`.
- Drop `MAPDATA` after inflate (`MAPDATA = null` is impossible for a `const`; rename to `let` or wrap the inflate so the literal is not referenced globally).

Verify: a Python unit test that replays the simulation above over `_payload` output and asserts "no page's default view emits more than N wires or cards"; Playwright headless (pattern in `test_map_runs_in_a_browser.py:60-105`) on the real pointlessbutton map with `chromium.launch(args=["--js-flags=--max-old-space-size=768", "--enable-precise-memory-info"])`, `page.evaluate("performance.memory.usedJSHeapSize")` and `performance.now()` around `draw()`. Headless only.

### Phase 2 — data out of the HTML (1–2 weeks)

Files: new `seamcheck/renderers/map_bundle.py` (chunk writer, manifest), `map_html.py` (loader `SC.chunk()`, inflate to typed arrays, async `jumpTo`, `fillPages` from manifest), `api.py` (`_render_map` returns a bundle object; `report(fmt="map")` keeps returning the single-file string when under threshold), `cli.py:543-550` and `management/commands/seamcheck.py:358-393` (write a directory; `--single-file`), `serve.py` (serve the bundle directory under the token path; keep `/source`), tests (`test_renderer_map.py:22-31` becomes "no cross-origin `src`/`href`"; also resolve the Google Fonts `<link>` at `:4462-4471`, which already fails that test).

- Chunk encoding per §2.2; detail lazy; `commits.js`, `console.js`, `observed.js` split out.
- Single-file variant: chunks as `<script type="text/plain" data-chunk="p12">` gz+base64, inflated with `DecompressionStream`; fall back to uncompressed embedding if the API is absent (feature-detect at generation is impossible, so emit uncompressed when `--single-file --no-gzip`, and in the page show a clear message instead of a blank canvas if `DecompressionStream` is missing).

Verify: `tools/synth_map.py` that builds a `Graph` directly (no source files) with the measured shape — a few hundred pages, dom_selector → dom_attr fan-in like `push-arena-main`, bucket sizes ×N — at 37k, 370k, 3.7M symbols; assert chunk sizes and manifest sizes against the §2.4 table; headless Chromium opens the 370k bundle from `file://` and reports heap < 300 MB after opening the biggest page.

### Phase 3 — Canvas2D renderer with LOD (1–2 weeks)

Files: `map_html.py` (a `Renderer` object with `svg` and `canvas` implementations sharing `layout()`; grid hit-test; `applyView` on canvas; `.cvlayer` gesture transform reused), CSS (`:600-622`, `:783-797`), `test_renderer_map.py` tests pinned to SVG markup (`:421-495`) rewritten against the layout structure rather than `<path>` strings.

Verify: headless timing of a full redraw at 3k cards + 500 wires < 16 ms; hit-test correctness tests via `page.mouse.click` and the sheet's `<h2>`.

### Phase 4 — buckets, files, search at scale (1 week)

Files: `mapdata.py:327-361` (bucket trees), `map_bundle.py` (file chunks, search shards, file → page map), `map_html.py` (bucket drill-down, `fileFilter` via file chunk, sharded search, `treeHtml` lazy folders `:3536-3585`), `serve.py` (`/search`).

Verify: synthetic 3.7M graph — bucket page opens in < 1 s from `file://`, a search returns in < 200 ms loading ≤ 3 shards.

### Phase 5 — generation bounds (1 week)

Files: `api.py:566-583` (parse-once page attribution; share ASTs with `filetree.py`), `mapdata.py:211-229` (indexes once), `:24` (context in detail chunks, bounded cache), `history.py:184-190` (bounded), `map_bundle.py` (multiprocessing per page, streaming writes), `progress.py` steps.

Verify: `tools/synth_map.py` at 3.7M symbols under `/usr/bin/time -l` (macOS) or a Linux container with `--memory=8g`; wall time and peak RSS recorded in the commit message per the house rule.

### Phase 6 — optional served extras

SQLite index (`map.sqlite`), `/symbol/<id>` cross-page resolution, FTS. WebGL only if a real page needs > 20k visible primitives after LOD, which the design is built to prevent.

---

## 7. Risks and what not to do

- **Do not move pixel layout to Python.** Layout is picked by viewport fit (`map_html.py:2078-2085`) and a test pins that (`test_renderer_map.py:436`); it is not the bottleneck. Precompute structure and counts, not coordinates.
- **Do not raise `MAX_DRAW`** or let an opened kind draw 2,000 cards; replace the cap with levels.
- **No CDN, no vendored engine.** The offline promise is tested (`test_renderer_map.py:22-31`) and PixiJS/regl would add hundreds of KB for a problem LOD removes.
- **Chunks must be classic scripts**, same directory, relative `src`; no `type="module"`, no `fetch()` in file mode, no `Content-Encoding` reliance (a `file://` load does not decompress `.gz`).
- **Do not keep the raw payload alive** after inflate, and never ship ids inside edges or duplicate detail per page.
- **Do not merge pages to save bytes**; the per-entry page is the product. Per-page duplication (21%) is the price and it is fine.
- **Do not promise a 7M single file.** Above ~50 MB write a bundle and say so in the CLI output.
- **Do not change the graph JSON/snapshot format** as part of this; diff, history and triage read it.
- **Never open a generated large map headed** during development; headless with heap flags only, and keep the pointlessbutton map out of any test that runs without `--js-flags`.
- **Uncertain, stated as such**: the 700k/7M symbol counts are linear extrapolations from one repo (73 symbols per 1k lines, 51% of them `dom_attr`); browser heap and per-primitive timings above are estimates except the code's own 50 ms/12k figure; FTS5 presence in the stdlib `sqlite3` build is unverified; V8's exact string limit varies by version but is on the order of 1 GB; the cause of the crash on the owner's machine is not established from source (the most likely candidates are the inline parse plus an expanded-kind redraw, but that is inference).
