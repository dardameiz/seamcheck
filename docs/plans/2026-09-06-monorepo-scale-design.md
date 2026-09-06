<!-- Design document, 2026-09-06. Decisions taken with the owner are marked DECIDED.
     Numbers marked measured come from tools/memory_ladder.py, tools/memory_budget.py and
     a timing of detect_services on the corpus, all on 2026-09-06. Everything else that
     carries a number is the measured rate extended, and says so. -->

# Seamcheck from 10k to 100M lines: the service as the unit of a scan

## Executive summary

1. **The service, not the repository, becomes the unit of a scan.** Each deployable is
   scanned, held and rendered on its own, so peak memory is the cost of the largest single
   service rather than of the whole tree. Every figure in
   [the memory document](2026-09-06-memory-at-scale.md) then applies per job unchanged.
2. **A second, cheap pass builds the cross-service map from surface files alone** - what
   each service offers and what it reaches for - so the seams between services are checked
   without any process ever holding two services at once.
3. **DECIDED**: per service plus a seam layer, over one out-of-core graph or per-service
   with no whole-repo view. **DECIDED**: CI builds it and developers read it; local runs
   scan the one service being worked on.
4. **A project with one service sees none of this.** Detection already returns a single
   root when nothing says otherwise, and it costs **0.01 s** on a 500k-line project
   (measured). No new folder, no new flags, no seam pass, no manifest. The architecture
   appears only when a repository has more than one deployable.
5. **Incremental is a consequence, not a separate feature.** A commit dirties the services
   whose files it touched; the rest are reused from a content-addressed cache; the seam
   pass always re-runs because it is seconds and it is what catches a route deleted in one
   service while another still calls it.
6. **Agents get a delta, not a map**: a kilobyte-scale JSON of which symbols appeared and
   vanished and which seams changed status, so "what did this commit break" needs no bundle.
7. **What this does not solve**: a single service that is itself 10M lines. One graph still
   has to fit in one process. That case needs the out-of-core store described in §11 and is
   deliberately out of scope here.

## 1. The sizes this has to cover

Measured wall time for `seamcheck map`, and the same figures extended past 2M at the
measured rate of 5,000-20,000 lines per second. The spread is depth of coverage: a Django
project with every extractor running is the slow end, a monorepo the tool only skims is the
fast end.

| lines | today, one process | with this design, 10 parallel jobs |
|---|---|---|
| 10k | 1 s (measured) | 1 s, unchanged - one service |
| 100k | 7-22 s (measured) | unchanged - one service |
| 500k | 30-106 s (measured) | unchanged - one service |
| 2M | 2-4 min (measured at 1.7M: 164 s) | 30 s - 1 min |
| 10M | 11-33 min (extended) | 2-5 min |
| 100M | 2-5.5 hours (extended) | 15-40 min |

The parallel column assumes the largest single service is about a tenth of the repository.
That assumption is the whole design's weak point and §11 says what to do when it is false.

## 2. What a service is, and who decides

`detect_services` (`seamcheck/services.py:212`) walks the tree with a depth limit, skipping
vendor directories, and records every directory holding `package.json`, `pyproject.toml`,
`go.mod`, `Cargo.toml`, `pom.xml`, `manage.py`, or a file named like a Dockerfile
(`services.py:155-181`). That is the **declared** set, and it is mostly libraries.

**Deployable** is the stricter test at `services.py:115-134`: a Dockerfile, or a location
under `apps`, `services`, `cmd`, `backend`, `frontend`, `server`, `web`, or a start script
that launches a long-running process (`next start`, `uvicorn`, `gunicorn`, `nest start`, and
the rest of `_SERVER_HINTS`), or a Python or Go project directory with no `package.json`.
Compose files corroborate that a repository is multi-service but never invent a root
(`services.py:183-210`), because compose points at images rather than at source.

Measured on the corpus:

| repo | declared | deployable | detection |
|---|---:|---:|---:|
| pointlessbutton | 1 | 0 | 0.01 s |
| cal.com | 113 | 6 | 0.11 s |
| ghost | 47 | 20 | 0.05 s |
| n8n | 84 | 8 | 0.17 s |

So a real monorepo splits into six to twenty jobs, not two hundred. These are deployment
units, not an architectural claim: the test is "something you run", which covers a
frontend, a worker, an API or a monolith equally.

**The split is too important at 100M lines to leave to a heuristic**, so:

- `SEAMCHECK_CONFIG["services"]`, a list of roots, **wins outright** when present. Detection
  is not consulted, and the map says the split was declared rather than detected.
- When detection finds no split, the run is today's single-service run. This is the
  fallback that keeps small projects free, and it is also what a Bazel-style monorepo with
  no per-directory manifests gets: correct, just not parallel, and it says so.

## 3. What a run writes

```
maps/
  manifest.json              services, git sha, per-service timings, failures, cache hits
  services/<name>/           index.html + data/   - today's bundle, unchanged
  surfaces/<name>.json       what that service offers and reaches for
  seams/                     the cross-service map, built from surfaces alone
  delta.json                 what changed since the previous run (§9)
```

A single-service project writes what it writes today and nothing else. `manifest.json`,
`seams/` and `delta.json` exist only when there is more than one service.

## 4. The per-service run

`seamcheck map --service apps/web` scans one service. Discovery is rooted at that
directory; a file outside it enters the scan only by being imported from inside it, which
the resolver already follows today. Peak memory is that service's cost.

**The run records the file list it actually read**, into the manifest. That is not
bookkeeping for its own sake: it is the only way the next run can compute a dirty set
without scanning anything (§8). A shared package is "imported by web" precisely because it
is in web's recorded file list.

`seamcheck services --json` lists the roots so CI can fan out one job per service with no
coordination between them.

**The cost we accept**: a shared package imported by eight services is read eight times.
That is duplicated work bought in exchange for jobs that never share state, which is what
makes the fan-out possible at all. It also means the shared package's symbols appear in
eight service maps, which is correct - it really is in all eight - and the seam map is where
the sharing is visible as one thing.

## 5. The surface file

Two lists and a header. Nothing here is new analysis: a scan already computes both, and
today it discards everything that leaves the repository boundary.

```json
{
  "service": "api", "root": "apps/api", "language": "TypeScript",
  "sha": "…", "seamcheck": "0.11.0", "scanned_at": "…",
  "offers":  [{"kind": "route",  "key": "GET /api/orders/{}", "id": "url:api/orders/<id>/",
               "file": "apps/api/src/orders.ts", "line": 31}],
  "reaches": [{"kind": "request", "key": "GET /api/orders/{}", "id": "fetch_target:…",
               "file": "apps/web/src/cart.ts", "line": 88, "status": "unresolved"}]
}
```

- **Offers**: routes served, queues consumed, tables and keys declared, events handled.
- **Reaches**: requests made, queues published to, tables and keys touched.
- **`key`** is the normalised matching string and is the only field the seam pass compares:
  method plus path template with parameter names erased for HTTP, the queue name for jobs,
  the table or key pattern for stores. **It is produced by the normaliser the matcher
  already uses inside one repository**, not by a second implementation - if the two ever
  disagree, a call that resolves within a service would stop resolving across services, and
  the difference would be invisible.
- **`id`** is that service's own node id, so the seam map links straight into the service
  map at the right card.

Sized from the reference project's own counts (208 routes, 191 handlers, 297 request
targets, 570 key uses): a few thousand entries, a few hundred kilobytes. Two hundred
services is tens of megabytes, which the seam pass reads in seconds.

## 6. The seam pass

Reads every surface file, indexes all offers by key, and walks every reach. One hash lookup
per reach, so it is linear in the number of reaches and holds no service's graph.

| outcome | meaning |
|---|---|
| connected | a reach matched an offer in another service |
| unresolved | a reach matched nothing anywhere - **the finding this whole design exists for** |
| unused | an offer nobody reaches, with the standing caveat that an external client may call it |
| uncertain | the key was built at runtime, or no service claims the file |

**Honesty rules, and they are not optional:**

- A service that failed to scan is drawn as a grey container marked *not scanned*, and every
  seam touching it is `uncertain`, never `unresolved`. A missing surface must never be
  reported as a missing route.
- If any service failed, no `unused` verdict is issued at all: the caller may be in the part
  that did not scan.
- The manifest records every failure, and the map says how many services it is missing
  before it says anything else.

## 7. The reader's landing page

The seam map is what opens. Services are containers, the crossings between them are the
wires, red where a call resolves to nothing. Clicking a service opens that service's own
map, which is the map that exists today. This is the same shape as the containers we
already draw for stores and languages, one level further out.

A single-service project opens its own map exactly as now.

## 8. Incremental

Snapshots already exist per commit (`snapshot.py:22-38`), as does a graph diff
(`diff.py:25`) and the commit series (`history.py`). Two things change.

**Snapshots are keyed by service and commit**, not by commit alone: `_snapshot_path` becomes
`.seamcheck/scans/<service>/<sha>.json`.

**A run computes a dirty set:**

1. Map the commit's changed files to services (`ServiceMap.of`, `services.py:310`).
2. A changed file outside every service root dirties each service whose **recorded file
   list** from the previous run contains it (§4). No import graph has to be recomputed to
   know this, and a file nothing recorded dirties nothing - correctly, because nothing read
   it.
3. Dirty services are rescanned. Clean ones are reused from cache.
4. **The seam pass always re-runs.** It costs seconds and it is what catches a route deleted
   in `api` while `web` still calls it - a breakage in a service whose own files did not
   change and which is therefore never rescanned.

**The cache key is a hash of: every file's content under the service root, the seamcheck
version, and the effective config.** All three, because a cache entry that survives a tool
upgrade is a lie about what the current tool would say. When the key cannot be computed -
no git, a dirty tree, an unreadable file - the run falls back to a full scan and the
manifest records why.

Expected cost at 100M lines for a commit touching one service: **one to two minutes**,
against two to five hours for a full build.

## 9. The delta, for programs rather than people

Every incremental run also writes `delta.json`:

```json
{"since": "abc123", "head": "def456",
 "services": {"api": {"added": 3, "removed": 1, "rescanned": true}},
 "seams": [{"key": "GET /api/orders/{}", "was": "connected", "now": "unresolved",
            "caller": "web", "callee": "api", "at": "apps/web/src/cart.ts:88"}]}
```

The `seams` list with `was` and `now` is the whole point: it is the answer to "what did this
commit break" in a form an agent can read without loading a bundle, and it is kilobytes.

## 10. What a small project sees

Nothing. One service means: one scan, today's output path, no manifest, no seam pass, no
`services/` folder, no new flags. The measured additional cost is the 0.01 s detection that
already runs today, plus a surface file of a few kilobytes that nothing forces you to open.

This is the constraint the design is held to, not an aspiration: if a change makes a
single-service run slower or noisier, the change is wrong.

## 11. Where this design ends

A single service of 10M lines is not helped by any of the above, because one graph still has
to fit in one process, and the trend line puts that at roughly 20 GB. When that case is
real, the answer is the store the seam pass lets us avoid for now: the graph moves out of
Python objects into an on-disk append-only store, extractors stream into it, matching
becomes a join rather than a dict lookup, and the renderer streams per page. That is a
change to every layer of the tool, which is exactly why it is worth avoiding until a
repository forces it.

The cheap mitigations from the memory document apply to that case meanwhile and are worth
doing regardless: an absolute AST cache default instead of a quarter of physical RAM,
releasing the parse trees before the map is built, and a ceiling on the source-line cache.

## 12. Order of work

This is three implementation plans, not one, and each is useful on its own:

1. **The service as a unit.** `--service`, `seamcheck services --json`, the manifest with
   its recorded file lists, the output layout, and the single-service path proven unchanged.
   Ships value immediately: a monorepo becomes scannable at all.
2. **The seam layer.** Surface files, the seam pass, the honesty rules, the landing page.
   This is the part that finds cross-service breakage.
3. **Incremental and the delta.** Per-service snapshot keys, the content-addressed cache,
   the always-re-run seam pass, `delta.json`.

Phase 2 needs Phase 1's file lists; Phase 3 needs Phase 2's surfaces. In that order, each
phase leaves the tool in a state worth shipping.

## 13. How we will know it works

- **Memory**: `tools/memory_ladder.py` per service on a real monorepo; the assertion is that
  peak RSS tracks the largest service, not the repository.
- **Correctness of the split**: on cal.com, ghost and n8n, every file the whole-repo scan
  attributed to a service must land in that service's per-service run. A file that appears
  in neither is a hole and fails the gate.
- **The seam pass**: a fixture monorepo where `web` calls a route `api` does not serve; the
  assertion is one `unresolved` seam naming both sides and the caller's file and line.
- **Honesty**: the same fixture with `api` failing to scan; the assertion is `uncertain`,
  no `unused` verdicts anywhere, and a manifest that names the failure.
- **The small case**: a single-service project's run before and after this work must produce
  the same files and the same wall time within noise.
