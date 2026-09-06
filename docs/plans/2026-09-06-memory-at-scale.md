<!-- Findings, 2026-09-06. Every number under "Measured" was measured on this machine with
     /usr/bin/time -l, one child process per row, on real repositories from the corpus.
     Nothing here is an estimate unless it says so. -->

# What seamcheck costs in memory, and where that stops working

## The short version

1. **Peak RSS is roughly 200 MB per 100,000 lines**, across nine real repositories from
   3,800 to 3.8M lines. The spread is 110–610, and the driver of the spread is how much
   JavaScript there is to parse, not the line count.
2. **The single biggest lever already exists and is set wrong.** The JavaScript AST cache
   defaults to **a quarter of physical memory** — 6 GB on this 24 GB machine. Capping it
   at 128 MB cut peak RSS on the reference project by **45%** and cost **11%** more time.
   The default buys nothing above ~600 MB on that project, and it is the reason a scan
   competes with everything else running.
3. **A quarter of PHYSICAL memory is the wrong quantity.** It cannot see the other 7 GB
   process on the machine. On 2026-09-06 the map server was killed twice by the OS while
   another session's test run held 6.9 GB — the budget had no way to know.
4. **At 10M lines the trend line says ~20 GB**, which does not fit on an ordinary machine.
   Three unbounded structures make the real number worse than the trend: every Python AST
   in the project held at once, a source-line cache that is never cleared, and one MapNode
   per (symbol × page) carrying its own copy of the source context.
5. Nothing here is about the browser. The rendered map was analysed separately in
   [2026-09-03-map-at-scale.md](2026-09-03-map-at-scale.md); this document is the
   generating process.

## Measured: the ladder

`seamcheck scan` (graph only) and `seamcheck map --no-serve` (graph, document, write), one
process per row, peak resident set size from `/usr/bin/time -l`.

| repository | lines | scan | map | wall (map) | MB per 100k lines |
|---|---:|---:|---:|---:|---:|
| fastapi-realworld | 3.8k | 46 MB | 49 MB | 0.4 s | — (baseline) |
| django-debug-toolbar | 12k | 71 MB | 75 MB | 1.4 s | 258 |
| redash | 72k | 194 MB | 197 MB | 6.7 s | 214 |
| open-webui | 114k | 737 MB | 759 MB | 22 s | 611 |
| immich | 170k | 464 MB | 464 MB | 11 s | 249 |
| documenso | 268k | 599 MB | 603 MB | 13 s | 208 |
| **pointlessbutton** | **~500k** | **1,818 MB** | **2,650 MB** | **106 s** | **356 / 522** |
| cal.com | 550k | 1,035 MB | 1,047 MB | 28 s | 181 |
| ghost | 765k | 1,394 MB | 1,599 MB | 35 s | 177 |
| sentry | 1.7M | 3,804 MB | 3,811 MB | 164 s | 220 |
| n8n | 3.8M | 4,259 MB | 4,271 MB | 59 s | 110 |

The per-100k column subtracts a 40 MB interpreter baseline. bookwyrm and saleor are absent
because they fail to import in this environment, which is a dependency problem, not memory.

Two things this table says that the line count alone does not:

- **The reference project is the most expensive per line of anything measured**, and the
  only one where the map phase costs materially more than the scan (**+832 MB**). It is a
  Django project seamcheck imports and reads with every extractor it has — ORM, Redis,
  Celery, templates, CSS, DOM — and it has 125 pages, so the per-page duplication below
  applies to it more than to a JavaScript monorepo.
- **n8n is the cheapest per line** at 3.8M lines. That is not efficiency; it is the AST
  cache refusing new files after the budget and a monorepo whose bulk seamcheck never
  reads deeply. Cost per line falls as coverage falls.

## Measured: what the AST budget actually buys

`seamcheck scan` on the reference project, everything else identical, only
`SEAMCHECK_AST_CACHE_MB` changed:

| budget | peak RSS | wall | vs default |
|---|---:|---:|---|
| default (RAM ÷ 4 = 6 GB) | 1,820 MB | 89.6 s | — |
| 4,096 MB | 1,816 MB | 91.3 s | nothing |
| 1,024 MB | 1,818 MB | 90.7 s | nothing |
| 512 MB | 1,344 MB | 92.6 s | **−26% RSS, +3% time** |
| 256 MB | 1,149 MB | 96.9 s | **−37% RSS, +8% time** |
| 128 MB | 1,005 MB | 99.3 s | **−45% RSS, +11% time** |

The cache holds ~815 MB on this project and the last 5 GB of headroom is never used, so
the default's only effect is to leave the memory unavailable to everything else. Note also
that at a 128 MB budget the floor is still **1 GB**: over half the peak is not the cache at
all, and no budget touches it.

## Where the rest of it goes

From reading the code, with the memory consequence of each:

**Bounded, and already deliberate.** The AST cache (`js_extractor.py:122-148`, admit until
full, never evict, because every extractor reads files in the same order and an LRU would
evict each file just before the next reader asks for it). The NDJSON parser output streams
line by line (`nodetools.py:67-112`, a comment records 2.6 GB held as one string before).
Commit snapshots are dropped as soon as their last reader is done (`history.py:230-235`,
1.8 GB before). Unreached buckets carry no source context at all (`mapdata.py:359`).

**Unbounded, and the reason the trend line bends the wrong way past ~1M lines:**

| what | where | consequence |
|---|---|---|
| Every Python AST in the project, alive at once | `callgraph.py:112-127` | parses all `.py` into one list before processing any; runs during map generation; no cap of any kind |
| Source lines, never released | `mapdata.py:24,72-79` | every context-bearing file split into one string per line; no budget, no eviction, no `clear()` anywhere |
| One MapNode per symbol per page | `mapdata.py:220-302,397-402` | a shared helper on 20 pages is 20 nodes, each with its own `context` string of up to ~9.6 KB (`mapdata.py:86` joins a fresh string per call) |
| Group unions, service layers, shared layer | `map_html.py:6222-6279` | three more full copies of matching nodes, on top of the per-page ones |
| The whole document as one string | `map_html.py:6133-6135`, `api.py:753` | `single_file()` holds chunks + wrappers + the join ≈ 3× the payload; the write adds the UTF-8 encoding as a fourth |
| Four copies inside every chunk | `map_html.py:6067-6079` | data → JSON string → UTF-8 bytes → gzip, plus `_json`'s `.replace()` allocating a second complete JSON string (`map_html.py:6049-6058`) |
| No `__slots__` on the graph | `graph.py:22-62` | ~9 live Python objects per symbol; an instance dict per Symbol and a `chain` list allocated even when empty |

## What this means at 1M, 10M and beyond

At the measured ~200 MB per 100k lines, and taking the reference project's map phase as the
expensive case rather than the cheap one:

| lines | trend | verdict |
|---|---:|---|
| 500k | 1.8–2.7 GB | measured; fine alone, killed by the OS when something else wants 7 GB |
| 1M | ~2–4 GB | measured at sentry's 1.7M: 3.8 GB. Works, with nothing else running |
| 10M | ~20 GB | does not fit on an ordinary machine, and the three unbounded structures above are not in that number |
| 100M | — | out of the question for one process; needs sharding by service or by page, and per-shard output |

The honest summary is that **there is no cliff, only a slide**, and the slide starts being
felt at about 1M lines on a 16 GB machine.

## The levers, in the order their measurements justify

1. **Change the AST cache default from "a quarter of physical RAM" to an absolute figure
   near 512 MB**, or better, a share of *available* memory read at start-up. Measured:
   −26% to −45% peak for +3% to +11% time. It is one function
   (`js_extractor.py:138-148`) and it is the difference between a scan that survives
   alongside a test run and one that does not.
2. **Release the JavaScript ASTs before building the map.** `clear_parse_cache()` runs only
   in the `finally` of the whole command (`api.py:448,714`), so the +832 MB map phase on
   the reference project runs while ~815 MB of parse trees are still held for a phase that
   has finished.
3. **Give `_source_cache` a budget and clear it** (`mapdata.py:24`). It is the one cache in
   the codebase with no ceiling at all.
4. **Stream `callgraph._walk_project`** (`callgraph.py:112-127`) instead of accumulating
   every Python AST, which is the structure most likely to end a 10M-line Python scan.
5. **`@dataclass(slots=True)` on `Symbol`, `Edge` and `MapNode`.** One line each; removes an
   instance dict per object across the largest population in the process.
6. **Stop duplicating nodes per page.** One node table plus per-page id lists, which is also
   what [the map-at-scale document](2026-09-03-map-at-scale.md) asks for on the browser
   side, so both halves want the same change.
7. **Write chunks as they are produced** rather than holding `chunks` and joining
   (`map_html.py:6123-6135`).

Items 1 to 3 are small and would move the number today. Items 4 to 7 are what a 10M-line
project needs.

## How to reproduce

```bash
python3 tools/memory_ladder.py   # one process per repo per phase, /usr/bin/time -l
python3 tools/memory_budget.py   # peak vs wall at six AST budgets on one project
```

Both read the cloned corpus at `~/dev/seamcheck-corpus` and the reference project beside
it, the same way `tools/corpus.py` does. Each row is its own child process, measured with
`/usr/bin/time -l`, because `getrusage(RUSAGE_CHILDREN)` is a running maximum over every
child ever reaped - once one big repository has been measured, every later row reports that
repository's peak instead of its own.
