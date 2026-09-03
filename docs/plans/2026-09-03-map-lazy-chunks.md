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
