# Map: the data leaves the page (Phase 2 of map-at-scale)

**Problem.** One `const MAPDATA = {…}` literal is 20 of the 22.6 MB pointlessbutton
map, and the browser parses and inflates all of it before the first paint, for a reader
who will look at one page. At 10M lines (≈730k symbols) that is ~300 MB of literal and
the tab dies before the menu opens.

**Shape.** The output stays ONE HTML file — the CLI, `serve`, and the MCP all return one
string and nothing about that changes. Inside it, the map data is split:

| block | inline? | loaded when |
|---|---|---|
| `MAPDATA` meta: columns, tables, per-page counts, file→page, commit counts | yes, small | at parse |
| `p<i>` node+edge rows for page *i* | `<script type="text/plain">` chunk | the page is drawn |
| `d<i>` note / snippet / context for page *i* | chunk | a sheet or code box opens on it |
| `search` one row per unique symbol | chunk | first keystroke in the box |
| `commits` per-commit changed maps + change lists | chunk | a commit is picked |
| `CONSOLE`, `FILES`, `OBSERVED`, `SERIES`, … | yes (bounded) | at parse |

A chunk is raw JSON under ~32 KB (fixtures stay greppable) and gzip+base64 above it.
Decoding is `fetch("data:…;base64,…")` → `DecompressionStream("gzip")` → `JSON.parse`,
which works from `file://` — the one loader that does. `<script type="text/plain">` is
inert to the parser, so nothing is evaluated until asked, and the base64 text is dropped
from the DOM once decoded.

**Service layers** (Stripe / Celery / GraphQL) were a union computed in the browser over
every page's nodes. They become pages built in Python (`layer:stripe`), hidden from the
picker, so they load like any other page.

**JS contract.** `PAGES[i].nodes === null` until `ensurePage(i)` resolves. `draw()`
loads its own page and redraws, so every existing caller keeps working. `show()` /
`showCode()` / `hop()` wait on `ensureDetail(i)`. `fillPages`, `layersPresent`,
`syncChrome` counts, `bestPageFor`, and the commit picker read the manifest.

**Not in this phase.** Canvas2D LOD (Phase 3), a directory bundle (only past ~7M
symbols), observed at scale.

**Gate.** pointlessbutton map: file ≤ 6 MB, heap after opening the biggest page under
300 MB, no console errors, 71 browser tests green, `tools/verify_output.py` reads the
new format, corpus + self-scan unchanged.

**Measured (2026-09-03, headless Chromium, DPR 1, `--max-old-space-size=768`).**
pointlessbutton map: 22.6 MB → **5.5 MB** (inline scripts 2.22 MB, of which `CONSOLE`
0.91 MB and `OBSERVED` 0.84 MB are the next candidates; chunks 3.20 MB in 256 blocks,
inline limit 4 KB). Open 0.43 s, heap 6 MB. Biggest page (`unreached:template`, 18,943
symbols) decodes in 10 ms and draws in 0.14 s; aggregate expanded to 500 cards: 1.5 s,
heap 24 MB, 2,552 elements. Sheet 20 ms. Search index 42,576 rows in 20 ms on first
keystroke; `user` 3 ms. No page or console errors. verify_output --self ok.

**Synthetic scale (`tools/synth_map.py`, same shape as the real map, same headless setup).**

| symbols (≈ lines) | pages | render | Python peak RSS | file | open | heap open | biggest page drawn | search index (rows, load, heap) |
|---|---|---|---|---|---|---|---|---|
| 37k (≈ 0.5M) | 76 | 0.2 s | 65 MB | 2.5 MB | 0.40 s | 8 MB | 8,326 in 0.01 s | 37k · 20 ms · 14 MB |
| 730k (≈ 10M) | 1,180 | 5.4 s | 774 MB | 40.7 MB | 0.48 s | 7 MB | 164,251 in 0.26 s | 730k · 0.24 s · 83 MB |
| 2M (≈ 27M) | 3,188 | 18.7 s | 2.0 GB | 112 MB | 0.84 s | 18 MB | 450,001 in 0.68 s | 2M · 0.63 s · 225 MB |

(Before the columnar search index and chunk release: 730k opened at 116 MB with search at
243 MB; 2M at 218 MB / 633 MB. The synthetic map now also puts its three unreached buckets
last, as the real map does, so "open" lands on an ordinary page.)

No console errors at any size. The search index is now seven columns - two newline-joined
strings for ids and labels, five `Int32Array`s - scanned with `indexOf`; rows exist only
for hits. What is left of it is the label string itself, ~100 bytes a symbol; sharding
by label prefix would take it further if a repository ever needs that.
Second is Python peak RSS at render (≈1 KB/symbol); Phase 5.
