# The function is the unit — 4 Sep 2026

Approved design. The map is organised around pages and elements; a developer is organised
around **functions**. They open the editor at `def submit_push` and everything they need to
know is "what does this touch, and what touches it". Today the map can only answer that by
hand, and a card names the variable and the selector but never the function they live in.

Four commits, in this order. Each passes the usual gates: `ruff check seamcheck tools`, the
non-map suite, the three map suites (13 known-open in `test_renderer_map.py`), self-scan
(`connected 0 unused 0 unresolved 0 uncertain 1`), `tools/verify_output.py --self`, corpus
TOTAL unchanged except where a number is expected to move, and the game map under 50 s.

## 0. What already exists, so nothing is rebuilt

- **JavaScript already knows its function.** `_walk(node, enclosing)` in `js_extractor.py`
  carries the name of the function each node sits in, and `dom_js_extractor` already passes
  it into `Symbol.chain` as `[basename, enclosing]`. The information is extracted; it is
  only never shown, and never indexed.
- **Python does not.** `redis_extractor._scan_python`, and every other Python extractor,
  walks with `ast.walk`, which is flat — a `pipe.set("user:{id}:stats")` hit knows its file
  and line but not that it is inside `def submit_push`. This is the one real gap.
- **The search index is already the right shape**: one lowercase string of labels plus
  typed-array columns, `indexOf`, 3.7 M rows searched in 6 ms. A function index is the same
  construction over a set ~50× smaller.
- `chainOf` already walks a node's ancestors, so lighting a neighbourhood needs no new
  graph analysis — only a different seed.

## 1. `Symbol.owner` — the function a line lives in

A new optional field on `Symbol`, defaulting to `""`:

```python
owner: str = ""   # "submit_push", "StoreManager.apply" - the def/class.method this line
                  # sits in. Empty at module level, which is a fact, not a gap.
```

- **Python.** One shared walker in a new `seamcheck/pyscope.py`:
  `owners(tree) -> dict[lineno, str]` — one pre-order pass recording, for every line in a
  `FunctionDef` / `AsyncFunctionDef` body, the dotted `Class.method` or bare `function`
  name. Extractors look their `node.lineno` up in it instead of tracking scope themselves.
  Wired into every extractor that reads Python: `redis_extractor`, `django_extractor`,
  `django_models_extractor`, `celery_extractor`, `sql_schema_extractor`, `env_extractor`,
  `job_queue_extractor`, `stripe_extractor`, `supabase_extractor`, `url_reference_extractor`.
- **JavaScript.** `enclosing` is already threaded; assign it to `owner` at every
  construction site in `dom_js_extractor` and `js_extractor` rather than only appending it
  to `chain`.
- **Templates and CSS** have no functions. `owner` stays empty and nothing pretends
  otherwise.

`owner` is carried into `MapNode`, into the search columns, and into the JSON of
`check()`. It is descriptive only — no status depends on it, so a wrong owner can never
turn into a wrong finding.

## 2. The card reads top-down

Today a card leads with the label and then the file. It becomes four lines, in the order a
developer thinks in:

```
pbitsCost                        the thing            (label + kind pill)
def submit_push                  who owns this line   (owner, omitted when empty)
pointless/views/push_views.py:412  where               (existing file link)
querySelector('[data-stat="pbits"]')  how              (existing snippet)
```

The findings list gets the owner as a muted suffix on the row (`… · submit_push`), so a
list of forty findings can be read as "eleven of these are in one function".

## 3. The Function picker — a third filter, instant

The header becomes **Page · Section · Function**.

- Function is a text input with a results list under it. Typing `sub` lists every function
  whose name starts with `sub`, then those that contain it, each with its file and a count
  of the symbols it owns.
- **Index:** one more chunk, `functions`, built at write time — `names` (one newline-joined
  lowercase string), `file`, and `rows` (the symbol row ids each function owns, as a flat
  typed array plus offsets). Prefix search is `indexOf` over one string, exactly as
  `searchEverywhere` does. Functions are tens of thousands where symbols are millions, so
  this is sub-millisecond and needs no worker and no debounce.
- **Picking one draws that function's world**, replacing the page drawing:
  - **What it touches** — every symbol whose `owner` is this function, laid out in the
    existing lanes: the seam (fetches it makes), the server, the store (Redis keys, the
    LUA scripts it evaluates, Postgres tables and models, Celery tasks it enqueues), the
    browser (DOM elements it writes).
  - **What touches it** — every symbol one edge away pointing at those: the route that
    dispatches to it, the fetch in JavaScript that calls that route, the Celery beat entry
    that enqueues it, the template attribute it feeds.
  - **One hop each way by default**, with a "widen" control for a second hop. Unbounded
    reachability from a hot function is the whole application, which is the page view again.
- Function composes with Page and Section (`function ∩ page` when both are set), and the
  choice rides in the URL hash, so "submit_push on push_arena" is a link.

## 4. The cost line — why this function is slow

With a function picked, a line above the drawing counts round-trips **per call**, by lane,
from the symbols it owns:

```
Redis 3  (1 LUA)  ·  Postgres 1  ·  Celery 1  ·  HTTP out 0
```

Counting only, no runtime, no estimate: each number is how many distinct store operations
the scan read inside that function. The value is the shape, not the magnitude — a view
that should be Redis-only showing `Postgres 1` is the entire diagnosis, and at 20–30 k
concurrent users that one row is the difference between a cache read and a connection from
a pool of 45. A count that is inflated by a loop is not claimed to be otherwise: the line
says "operations in the source", and the card for each one names its line.

## 5. What this makes possible, and is worth saying in the README

Building a feature: filter to the function being written, and the holes are the drawing.
An unresolved fetch means the backend is not there yet; an unused route means the frontend
is not calling it yet; a Redis key written and never read means nobody consumes it. These
are the statuses that already exist — the function filter is what makes them read as a
checklist for the thing being built rather than as a list of complaints about a page.

## Order and gates

1. `owner` on `Symbol` + `pyscope.py` + every extractor + tests that a Redis write inside
   `def submit_push` reports that owner, and that module-level stays empty.
2. The card and the findings row.
3. The `functions` chunk, the picker, the neighbourhood drawing, one hop, the hash.
4. The cost line.

Each commit: the gates above, plus a browser test for anything drawn. The push_arena
precision work (`docs/plans/2026-09-04-precision-pusharena.md`) follows this batch.
