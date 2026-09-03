# Plan: `observe` rides along with the E2E suite

*Target: seamcheck 0.9.0. Written 2026-09-03 against `seamcheck` HEAD `1725c797c` (0.8.2). Executor: Opus, step by step. Owner rules apply: no unit tests unless asked; verify against real projects; never publish findings against a named public repo; Conventional Commits; no `Co-Authored-By`/`Claude-Session` trailers.*

*Working-tree note: the seamcheck checkout has uncommitted edits to `README.md`, `seamcheck/renderers/map_html.py`, `seamcheck/tests/test_server_adapters.py` and untracked `docs/*.md` + `docs/images/*.png`. Do not sweep them into the commits below; leave them or ask the owner.*

## 1. Current state, verified from the code

### 1.1 The probe

`seamcheck/observe.py:30-113` — `PROBE`, one JS IIFE injected via `context.add_init_script()` (`seamcheck/browser.py:68`) before any page script. It patches, additively (original always called, value returned untouched):

| patched | bucket | what is stored (`observe.py:33-39`) |
|---|---|---|
| `Document/Element.prototype.querySelector/querySelectorAll`, `Element.prototype.closest` (`:42-55`) | `selectors` | `{count, hits}` keyed by the selector string; a hit = non-null / non-empty NodeList |
| `Document.prototype.getElementById` (`:56-61`) | `selectors` | stored as `"#" + id` |
| `window.fetch` (`:64-71`), `XMLHttpRequest.prototype.open` (`:72-76`) | `fetches` | keyed by the raw URL argument (string or `Request.url`); `hits` always incremented; no method |
| `DOMTokenList.prototype.add` (`:79-83`), `Element.prototype.className` setter (`:84-93`) | `classes` | class name → `{count, hits}` |

It also exposes `window.__seamcheck.observed` and `window.__seamcheck.boxes()` (`:96-111`) — bounding boxes of every `[id],[class],[data-page],[data-stat]` element, for the map's "page a browser saw" panel.

Not recorded: which call site made the query (no file/line), HTTP method, navigations/`pushState`, `classList.toggle/replace`, `setAttribute('class')`, `sendBeacon`, `WebSocket`, `EventSource`, form submits, link navigations, iframes' own buffers (init scripts do run in frames, but only the top frame is read back).

### 1.2 The driver (standalone crawler)

`seamcheck/browser.py:38-103` `observe_pages(urls, ...)`: one Chromium context, one `page` per URL, `goto(wait_until="domcontentloaded", timeout=30s)` (`:72`), fixed `1500 ms` settle (`:21`, `:73`), then one `page.evaluate` (`:74-77`) reads the buffer and boxes; a page that fails to load is stored with empty buckets and `screenshot="error: ..."` (`:92-99`). No login, no cookies, no fixtures, no clicks, no multi-step flows, one page per URL, sequential.

URL list: `seamcheck/management/commands/seamcheck.py:224-235` `_page_urls()` — every `url` symbol without `<`, not starting with `^`, not under `api/` or `admin/`, not ending in `.txt/.xml/.js`, prefixed with `--base-url` (default `http://127.0.0.1:8080`, `:93-96`).

### 1.3 The evidence file

`seamcheck/observe.py:25,130-140` — `OTHER/seamcheck/observed/<git HEAD sha>.json`, a JSON **list** of `Observation` dicts (`:116-127`): `{page, selectors, fetches, classes, boxes, screenshot}`. No schema version, no timestamp, no probe version, no framework, no run id. One file per SHA, **overwritten** by every run (`save()` is `write_text`), so two runs at one commit do not accumulate; parallel writers would clobber each other. `load()` (`:143-166`) reads only the file for the exact SHA asked; `merge()` (`:169-180`) folds pages into `bucket -> key -> {count, hits, pages[]}`.

### 1.4 How the scan consumes it

`seamcheck/api.py:273` — `scan()` ends with `_with_observations(scanned, repo_root)` (`:317-343`): loads `observe.load(repo_root, current_git_sha())` — **HEAD only**, silently nothing otherwise — and calls `provenance.apply_observations(graph, merge(observations))`. This runs *after* classification, so it overrides final statuses, and *before* `write_map()` saves the snapshot (`api.py:562`), so promoted statuses are baked into `OTHER/seamcheck/scans/<sha>.json` and into what `check` later diffs against.

The map panel uses a different rule: `api.py:291-314` `_observations()` falls back to the **newest evidence file of any SHA** (mtime order) and tells the panel `current: false` (`map_html.py:3675-3677` renders "Recorded at X, not at this commit"). So the panel and the classifier can be looking at different evidence.

### 1.5 The provenance labelling

`seamcheck/provenance.py:41-60` `apply_observations()` touches only `dom_selector` and `fetch_target` (`:63-68`). Everything it promotes gets `Status.CONNECTED` and a `note` beginning with `OBSERVED_NOTE` (`:22-26`: "Seen happening in a browser on {pages}. ... a page nobody visited leaves no trace here"). **The label lives only in the free-text note**: `Symbol` (`graph.py:23-33`) has no evidence field, renderers cannot filter on it, and JSON consumers must string-match the note. `NOT_OBSERVED_NOTE` (`:27-30`) is defined and never used anywhere.

### 1.6 Limitations and bugs verified in the code (not just the README)

1. **The `[observe]` extra does not exist.** `pyproject.toml:57-67` defines `django`, `models`, `mcp`, `node`. README:91, `browser.py:52` and `cli.py:203` all say `pip install 'seamcheck[observe]'`; pip warns and installs nothing. Playwright is never pulled in by seamcheck.
2. **Blanket promotion of every runtime-built selector.** `provenance.py:86-93`: a `<dynamic>` `dom_selector` becomes CONNECTED if `found` is non-empty — i.e. if *any* selector anywhere hit. Worse, the probe records **its own** `boxes()` query (`observe.py:100` calls the patched `querySelectorAll`), so every observed page contributes `'[id],[class],[data-page],[data-stat]': {hits: 1}` — verified in `~/dev/pointlessbutton/OTHER/seamcheck/observed/6c552a39….json` (all 169 pages carry it). Consequence: once any page is observed, **all 261 `<dynamic>` selectors in pointlessbutton are promoted to connected** with no evidence about any of them. Same shape for fetch: `provenance.py:122-127` promotes a `<dynamic>` fetch_target if *any* fetch fired (this branch is unreachable on real graphs — `js_extractor.py:406-413` records a fully dynamic fetch as a `js_call` labelled `fetch(<runtime value>)`, never as a `fetch_target` — so it is test-only code).
3. **Substring URL matching.** `provenance.py:129`: `url.endswith(label) or label in url` — `/api/x` "observed" when `/api/xyz/` or `/static/api/x.js` was requested.
4. **Live-null demotion overrides a static connected.** `provenance.py:107-114`: a selector queried and never found on *the pages visited* becomes UNRESOLVED even when a template renders it on a page the run did not visit. The stored pointlessbutton evidence shows why this is not hypothetical: 88 of 169 crawled URLs were the admin login redirect (identical signature `1 selector / 0 fetches / 12 boxes`), recorded under the admin page's URL. A shared bundle querying `#leaderboard` on those pages returns null 88 times.
5. **Evidence is HEAD-only and never accumulates.** Any commit invalidates everything (`api.py:332`); the stored pointlessbutton file is at `6c552a3`, HEAD is `9e5307f`, so today the classifier applies nothing while the map panel shows the old run.
6. **`seamcheck observe` is Django-only.** `cli.py:625-681` `_plain_args` has no `--observe`; on a non-Django repo `seamcheck observe` falls into `_run_without_django` and prints the default report, exit 0.
7. **No coverage number.** Nothing reports how many pages/routes/files the run touched versus how many the graph knows; the "silence proves nothing" caveat is prose only (`api.py:339-341`, `provenance.py:22-26`).
8. **Snapshot and triage interaction.** Because promotions are applied before `save_snapshot`, and `triage.fingerprint_for_symbol` is `kind:snippet:status` (`triage.py:91`), a triage mark placed on a static verdict expires when evidence arrives and revives when it does not — flip-flop across machines with and without evidence.
9. **`url`/`view` never promoted.** 264 `url` + 226 `view` symbols are uncertain on pointlessbutton for "no caller found in source"; a request or navigation actually observed would settle them, and provenance does not look.
10. The docs are otherwise accurate: README:84-104 and `docs/field-notes.md:47-61` describe the mechanism, the commit keying and the caveat correctly. CHANGELOG has no entry for `observe` at all (it landed in `9e6fc3ba4`).

Baseline numbers on pointlessbutton HEAD scan (`OTHER/seamcheck/scans/e8478907….json`): 37,247 symbols; uncertain by cause — url/view "no caller found" 489, `class:apply` 483, URL literal sighting 324, `<dynamic>` selector 261, dynamic fetch 175, css prefix 122, class stem 112. Those are the rows this feature is for.

## 2. Design: the ride-along

### 2.1 Shape

Three parts, framework-agnostic at the core:

1. **`probe.js`** — one file, shipped inside the wheel at `seamcheck/probe/probe.js`, read by Python (`seamcheck.probe.probe_js()`) and inlined into the Node shims. It patches as today plus: records `sites` (caller script URL:line:col) for the first 3 distinct callers of each key; records `navigations` (`location.pathname` at load, `pushState/replaceState/popstate`); records the fetch `method`; persists its buffer to `sessionStorage['__seamcheck_v1']` on `pagehide` and every 2 s, and re-seeds from it on the next load of the same origin; exposes `window.__seamcheck.drain()` returning `{probe: 1, pages: [...]}` and clearing both; calls the *original* `querySelectorAll` inside `boxes()`; ignores requests to its own sink (none in this design) and keys longer than 512 chars.
2. **A sink** — a function in the test process that receives the drained object and writes one JSON file per (test, worker), never rewriting. Python: `seamcheck.probe.sink.write_run(...)`. Node: 20 lines of `fs.writeFileSync` in the shim.
3. **Thin adapters** — install (init script) + drain-at-test-end:

| runner | install | drain | ceremony |
|---|---|---|---|
| Playwright, Node | auto fixture in an extended `test` (`context.addInitScript(PROBE)`) | fixture teardown: `for page of context.pages(): page.evaluate(drain)` (+ every frame) | one line: `export const test = base.extend(seamcheck)` in the suite's fixtures file, or `import { test, expect } from './seamcheck-probe.mjs'` |
| Playwright, Python (pytest-playwright) | pytest plugin (`pytest11` entry point) with an autouse fixture on `context`, **active only when `SEAMCHECK_PROBE=1` or `--seamcheck-probe`** | same fixture, teardown | zero lines: `SEAMCHECK_PROBE=1 pytest tests/e2e` |
| Playwright, Python, hand-rolled contexts | `seamcheck.probe.playwright.install(context)` | `seamcheck.probe.playwright.drain(context, test="...")` | two lines |
| Cypress | `Cypress.on('window:before:load', win => win.eval(PROBE))` | global `afterEach`: `cy.window().then(w => w.__seamcheck?.drain())` → `cy.writeFile` | one line in `cypress/support/e2e.js`: `import './seamcheck-probe.cy.js'` |
| Selenium (Python, Chrome/Chromium) | `driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {"source": PROBE})` | `driver.execute_script("return window.__seamcheck && window.__seamcheck.drain()")` | two lines: `install(driver)` in `setUpClass`, `drain(driver)` in `tearDown`. Firefox: not in this iteration |

`seamcheck probe init` detects which of these the repo has (`playwright.config.*`, `cypress.config.*`/`cypress/`, `pytest-playwright` / `selenium` in `requirements*.txt`/`pyproject.toml`) and prints the exact lines; `--write` writes the Node shims (`seamcheck-probe.mjs`, `seamcheck-probe.cy.js`) next to the runner config with the probe inlined and a header naming the seamcheck version that generated them.

### 2.2 Getting evidence out of the browser — mechanisms compared

| mechanism | works in PW-Node / PW-Py / Cypress / Selenium | survives navigation | parallel-safe | changes app behaviour | needs a sidecar |
|---|---|---|---|---|---|
| A. In-page buffer, `page.evaluate` at test end (today) | yes / yes / yes / yes | **no** — lost on every navigation unless re-read | yes if each adapter writes its own file | no | no |
| B. `context.exposeBinding` / CDP `Runtime.addBinding` | yes / yes / **no** / partial | yes | yes | no | no |
| C. HTTP collector + `sendBeacon` from the probe | yes / yes / yes / yes | yes | yes | **yes** — extra requests the test may intercept or count; beacons are killed by `page.route('**')` and appear in HAR/network assertions | **yes** |
| D. Browser extension, content script at `document_start` | Chrome only, not in Playwright's default headless shell | yes | yes | no | no, but fragile |
| **E. In-page buffer persisted to `sessionStorage` across same-origin navigations, drained by the adapter at test end** | **yes / yes / yes / yes** | **yes** (same origin) | yes — one file per test per worker | one storage key (documented) | **no** |

**Pick E.** It is A with the one hole (navigations) closed, uses the page handle every runner already has, produces no network traffic, and needs no process the team has to start. Its known losses are stated rather than hidden: a buffer on a page that is closed without `pagehide` since the last 2 s flush, and anything on a different origin (an OAuth hop). B is kept in mind for Playwright-only precision later.

### 2.3 Parallel workers and file naming

`OTHER/seamcheck/observed/<sha>/<utc-ts>-<run>-<worker>-<seq>.json`, where `run` = `SEAMCHECK_RUN_ID` or 8 random hex chosen once per test process, `worker` = `TEST_PARALLEL_INDEX` (Playwright) / `PYTEST_XDIST_WORKER` / `pid`, `seq` = per-process counter. Written with `O_EXCL` semantics (write to a temp name then `rename`). Nothing is ever appended or rewritten, so there is no lock. `<sha>` is `git rev-parse HEAD` of the checkout the tests run from, overridable with `SEAMCHECK_SHA`; the evidence root is overridable with `SEAMCHECK_EVIDENCE_DIR` (CI writes to a fresh dir and uploads it).

### 2.4 CI story

```yaml
# e2e job
- run: SEAMCHECK_PROBE=1 SEAMCHECK_EVIDENCE_DIR=$RUNNER_TEMP/evidence npx playwright test
- uses: actions/upload-artifact@v4
  with: { name: seamcheck-evidence, path: ${{ runner.temp }}/evidence }
# seamcheck job (needs: e2e)
- uses: actions/download-artifact@v4
  with: { name: seamcheck-evidence, path: evidence }
- run: seamcheck check --evidence evidence --since $BASE_SHA
```

`--evidence PATH` adds a file or directory to what is read from `OTHER/seamcheck/observed/`; `--no-evidence` reads none. Evidence recorded at a SHA that is not an ancestor/descendant known to the checkout is listed and ignored.

## 3. Evidence model

### 3.1 Run file (schema 1)

```json
{
  "schema": 1,
  "probe": 1,
  "seamcheck": "0.9.0",
  "framework": "playwright-node | playwright-python | cypress | selenium-chrome | seamcheck-observe",
  "sha": "9e5307f6…",
  "recorded_at": "2026-09-03T10:12:44Z",
  "run": "a1b2c3d4", "worker": "1", "test": "arena > push increments the counter",
  "origin": "http://localhost:8080",
  "pages": [
    {
      "page": "/push_arena/",
      "navigations": ["/push_arena/", "/store/"],
      "selectors": {"#combo-42": {"count": 3, "hits": 3, "sites": ["http://localhost:8080/static/js/arena.js:118:21"]}},
      "fetches":   {"/api/push/": {"count": 1, "hits": 1, "methods": ["POST"], "sites": ["…/arena.js:204:9"]}},
      "classes":   {"pb-badge-success": {"count": 2, "hits": 2, "sites": ["…"]}},
      "boxes": [ {"x":0,"y":0,"w":1440,"h":80,"id":"navbar","cls":["nav"]} ]
    }
  ]
}
```

`sites` holds raw script locations as the browser reported them (first 3 distinct callers per key); resolution to repo files happens at scan time where the config is known. Fetch keys are stored path-only (origin and query string stripped) when the URL is same-origin; cross-origin URLs are kept whole and never matched to routes. `boxes` capped at 300 per page at capture. Schema 0 (today's bare list at `observed/<sha>.json`) stays readable and is treated as `framework: seamcheck-observe`, `sites: []`.

### 3.2 Merge across runs

`seamcheck/evidence.py: load_all(repo_root, extra_paths) -> list[Run]`, `merge(runs) -> Merged` with `Merged.items[bucket][key] = {count, hits, pages: set, sites: set, shas: set, first_seen, last_seen, runs: int}` and `Merged.runs: [ {sha, recorded_at, framework, test_count, page_count} ]`. Union; counts summed; `first_seen`/`last_seen` are `recorded_at` extremes. Nothing is discarded at merge time — staleness is applied per symbol at apply time (below), so a file is never "partially stale".

### 3.3 Staleness rule — per file, via git

An item supports a claim about symbol `S` only if, for at least one SHA in the item's `shas`, `S.file` is byte-identical between that SHA and HEAD: `git diff --name-only <sha> HEAD` (one call per distinct evidence SHA, cached for the scan). Evidence at HEAD passes trivially. A SHA git does not know (`rev-parse` fails) contributes nothing and is reported.

Per file rather than per source line, deliberately: a `<dynamic>` selector is identified by `file:line` and its value depends on code around it (the variable that feeds it), so an unchanged line in a changed file is not evidence that the same thing still runs; and per-file is exact, free, and needs no hashing scheme of its own. It errs towards "uncertain", which is the side the house rule wants. Second-order staleness (the *template* that rendered `#x` changed while the JS did not) is handled by the asymmetry in 3.5: promotions survive across commits under this rule; the strongest negative claim (a live null) requires evidence at HEAD exactly.

### 3.4 Labelling a promoted symbol

Add `evidence: str = "static"` to `Symbol` (`graph.py`), values `"static"`, `"observed"`, `"observed-name"` (see 3.5); bump `SCHEMA_VERSION` to `"1.1"`. `graph_from_dict` tolerates its absence (default). Every promotion also sets the note:

> Observed: `#combo-42` found on /push_arena/ (run 2026-09-03, at 9e5307f, this commit). A page the run did not visit leaves no trace here.

or "…at 6c552a3, 14 commits behind; this file is unchanged since." The word `observed` appears as a fixed token in the terminal (`connected · observed`), markdown (`*observed*`), JSON (`"evidence"`), and the map (badge + filter chip). Statuses stay the same four words.

### 3.5 What may be promoted, by what evidence

| symbol | evidence | result | attribution required |
|---|---|---|---|
| `dom_selector` `<dynamic>` (`dynamic:*`) | a hit whose `sites` resolve to the symbol's file, same line (±2 if unique) | connected, `observed` | **by site only** — never by name |
| `dom_selector` literal, static uncertain | hit by name | connected, `observed-name`; if a site resolves to the same file → `observed` | by name allowed |
| `dom_selector` literal, static **unresolved** | hit by name only | unchanged status; note "found by name on /p from another call site" | overriding a static finding needs `observed` (site in same file) |
| `dom_selector` literal, static connected | queried, all null on visited pages | unchanged; note "queried on N pages and not found there" | never demoted (1.6 #4) |
| `dom_selector` literal, static unresolved | queried, all null, evidence at HEAD | stays unresolved, note "live null: the page asked and it was not there", `observed` | confirmation only |
| `dom_selector` `class:apply` / `class:stem` | class observed applied (stem: any class with that prefix) | connected, `observed-name` | by name |
| `css_selector` uncertain or unused, `sub` class/id | class observed applied / id selector hit | connected, `observed-name` | by name (a rule matched at runtime is not dead) |
| `js_call` `fetch(<runtime value>)` | a fetch whose site resolves to file+line | connected, `observed` | by site only |
| `fetch_target`, exact label | fetch path equal | connected, `observed` | by name (paths are global) |
| `fetch_target`, prefix (`_PREFIX_NOTE`) | fetch path `startswith(label)` | connected, `observed` | by name |
| `fetch_target` literal sighting (`sub == "literal"`) | fetch path equal | connected, `observed`; the sighting is no longer a sighting | by name |
| `url` uncertain | any fetch path or navigation resolved by `matcher.UrlIndex(urls).resolve(path)` | connected, `observed` | by name; regex (`^`) routes exact only |
| `view` uncertain | one of its urls promoted (edge url→view) | connected, `observed` | cascade |
| everything else | — | untouched | — |

Site resolution: strip origin; if the path starts with the configured static prefix (Django `STATIC_URL`, or `/static/`), map into the static candidates `api._static_candidates()`; else unique suffix match against `{symbol.file}`; ambiguous or inline-in-HTML → unattributed. Runner-origin sites (`/__cypress/`, `cypress_runner`, `<anonymous>`, `eval`, `pptr:`) are never app sites; an item whose *only* sites are runner-origin is ignored for selectors (see 5 #7).

### 3.6 Observation coverage

Computed in `evidence.coverage(graph, merged)` and carried on `Report`:

- **routes**: `url` symbols reached by any observed navigation or fetch via `UrlIndex.resolve` ÷ all `url` symbols → "routes observed 62 of 169".
- **js files**: files with ≥1 resolved site ÷ files that produced `dom_selector`/`js_call` symbols → "js files 14 of 41" (which code actually ran).
- **pages**: count of distinct navigation paths, and the run count, SHAs, oldest/newest.
- **ignored**: items skipped as stale (file changed) and runs from unknown SHAs.

Reported in: the terminal and markdown counts block (one line under the four counts), `seamcheck check` output, `seamcheck probe status`, the map's observed panel header and the `OBSERVED.coverage` payload, and JSON (`"evidence": {...}` top-level key in `seamcheck json`).

## 4. CLI surface

New command, in the `cli.py` prose style:

```
seamcheck probe - Put the browser probe into the tests you already run.

The standalone `observe` is a crawler: no login, no fixtures, no clicks, and a page it
cannot reach looks exactly like a page that is broken. Your end-to-end suite already has
the login, the seeded data and the team's own idea of which pages matter. This installs
the same probe into that suite, so every run it makes leaves evidence behind.

`probe init` looks at the repository and prints the one line each runner needs -
Playwright (Node and pytest), Cypress, Selenium on Chrome. `--write` writes the two
Node shims next to your runner config. Nothing else is touched: the probe is additive,
every patched call runs the original and returns its result, and it is off unless the
run asks for it (SEAMCHECK_PROBE=1 for pytest; the import for Node).

`probe status` says what evidence is on disk: runs, commits, how many routes and files
were observed against how many the graph knows, and what was ignored because the file it
describes has changed since. That line is the honest half of a falling `uncertain`.

examples:
  seamcheck probe init              # what to add, per runner found here
  seamcheck probe init --write      # ...and write the Node shims
  seamcheck probe js                # print the probe, for a setup not listed
  seamcheck probe status            # evidence on disk, and what it covers
  SEAMCHECK_PROBE=1 pytest tests/e2e          # pytest-playwright: no code change
  SEAMCHECK_EVIDENCE_DIR=/tmp/ev npx playwright test   # write evidence somewhere CI can upload
```

Changed:

- `scan`, `check`, `report`, `map`, `json`, `explain` gain `--evidence PATH` (repeatable; file or directory) and `--no-evidence`, in both the management command and `_plain_args`; env `SEAMCHECK_EVIDENCE` is the CI spelling. `seamcheck help check` gets one paragraph: "`--evidence` reads what an E2E run recorded... promotions are labelled observed and the counts line says how much of the project the run touched."
- `observe` keeps its flags, writes schema-1 files into `observed/<sha>/`, records `framework: seamcheck-observe`, and its help gains one paragraph pointing at `probe` for logged-in and multi-step flows. It is also routed on non-Django repos (`_plain_args`) — or, if that is more than a step, prints "not available on this backend yet" instead of the default report (1.6 #6).
- `pyproject.toml`: `observe = ["playwright>=1.40"]` (the extra the README already names), `[project.entry-points.pytest11] seamcheck_probe = "seamcheck.probe.pytest_plugin"`.

## 5. Honesty checks — every way this can lie, and the rule that stops it

1. **Selector observed on one page, symbol belongs to another call site.** Rule: `<dynamic>` symbols promote by site only; a static *finding* (unresolved/unused) is overridden only by site-attributed evidence in the same file; by-name promotion is limited to uncertain rows and is labelled `observed-name` with the page named.
2. **Null on the pages visited, element on a page not visited** (the 88-login-pages case). Rule: a null never demotes a static connected; it only confirms an existing unresolved, only from HEAD evidence, and the note says "on the pages visited".
3. **The probe observes itself / the runner.** Rule: `boxes()` uses the saved originals; sites are captured for the first 3 callers of each key and items whose sites are all runner-origin or anonymous (Cypress's jQuery `cy.get`, Selenium's atoms, `page.evaluate` bodies) are dropped for selectors. Playwright's own selectors run in an isolated world and are not seen.
4. **A test stubs `fetch` / intercepts routes.** The probe patches at document start so a later `window.fetch = fake` bypasses it (a miss, not a claim); a `page.route`/`cy.intercept` stub still shows the client *calling* the URL, which is what `fetch_target`/`js_call` mean, and `url`/`view` promote only when a real route in the graph resolves the path — a stub cannot invent a route.
5. **Evidence from an older SHA.** Per-file git rule (3.3), the "N commits behind, file unchanged" note, and unknown SHAs ignored and counted.
6. **Evidence recorded against a different deployment than the checkout** (tests pointed at staging). Rule: `sha` is the checkout's HEAD, `SEAMCHECK_SHA` overrides, `origin` is stored and shown in `probe status`; the plan does not claim to detect a mismatch (scope cut: app-exposed build SHA).
7. **Bundled/minified JS** — stack lines do not map to source. Rule: no site resolution → no by-site promotion; `<dynamic>` rows stay uncertain with the note "observed from this file, but line numbers do not map to source (bundled)".
8. **Inline `<script>` in templates.** Rendered-HTML line numbers differ from template lines. Rule: sites whose URL is the page itself are unattributed; literal selectors still match by name.
9. **DOM inserted after the query** (dynamically built UI). Rule: a query that ran and missed, then ran and hit, has `hits > 0` → found; a query that never ran leaves silence and silence changes nothing.
10. **Cross-origin and third-party.** Fetch keys with a foreign origin are kept whole and never matched; classes applied by a third-party widget can promote a `css_selector` (the rule *did* match at runtime — that is the truthful claim).
11. **Coverage gaps read as all-clear.** Rule: the coverage line is printed wherever counts are printed; `check` prints it; the map panel header carries it; a promoted symbol's note names the page.
12. **The CI gate flips with evidence presence.** Rule: promotions only move rows *out of* findings; the one new finding class (live null) needs HEAD evidence; `check` prints which evidence was used so a green/red difference between two machines is explainable. The triage flip-flop (1.6 #8) is fixed by excluding `evidence` from the fingerprint — fingerprint stays `kind:snippet:status` and status only changes when evidence arrives, which is the intended expiry; document it.
13. **Multiple SHAs disagree** (an item hit at A, null at B). Rule: promotion needs a hit from a SHA where the file is unchanged; nulls never demote; the note lists the SHAs.
14. **Probe/schema version drift.** Rule: files carry `probe` and `schema`; a newer schema than the reader knows is skipped with a message, never half-read.

## 6. Execution steps

Every step: one Conventional Commit, `ruff check seamcheck/`, `python -m seamcheck.cli check` (must stay 0/0/0, no crash), and the corpus gate `python tools/corpus.py scan` (no CRASH, no new NO ROUTES) before committing. pointlessbutton (private) is the primary bench: `cd ~/dev/pointlessbutton && venv/bin/pip install -e ~/dev/seamcheck`, app running on `http://127.0.0.1:8080` (`./local-dev.sh`), admin credentials as in its `conftest.py`. Scratch files go under the session scratchpad, never into either repo.

**Step 0 — baseline.** Record `venv/bin/seamcheck scan` counts on pointlessbutton and `venv/bin/seamcheck json | python3 -c 'import json,sys,collections;g=json.load(sys.stdin);print(collections.Counter((s["kind"],s["status"]) for s in g["symbols"]))'`. Note the stale evidence file `OTHER/seamcheck/observed/6c552a3….json`. Done: numbers written into the commit message of step 1.

**Step 1 — stop the false claims that exist today** (`fix(observe): …`). Files: `seamcheck/observe.py` (boxes uses originals), `seamcheck/provenance.py` (delete the blanket `<dynamic>` branches; exact/prefix fetch match; null never demotes connected; live-null only confirms unresolved), `seamcheck/graph.py` (`evidence` field, schema 1.1), `pyproject.toml` (`observe` extra), `seamcheck/tests/test_observe.py` (update the two tests that encode the old behaviour — `test_a_runtime_built_selector_is_resolved_by_observation`, `test_a_selector_that_returned_nothing_becomes_a_finding` — to the new rules; this is maintenance of existing tests, not new ones). Verify on pointlessbutton: copy the stale file to `observed/<HEAD sha>.json` temporarily, `seamcheck scan`, confirm no `<dynamic>` row is promoted, no static-connected row changed, `seamcheck json` shows `"evidence": "observed"` only on rows whose note names a page; then delete the copy. Done: `pip install 'seamcheck[observe]'` resolves; the counts differ from step 0 only in uncertain→connected rows that a person can check by opening the file:line.

**Step 2 — evidence store v1** (`feat(evidence): …`). New `seamcheck/evidence.py` (schema-1 reader/writer, schema-0 reader, `load_all`, `merge`, `changed_files(sha, repo_root)` via git, `coverage()`); `observe.py` becomes a thin shim re-exporting `PROBE`/`Observation` and writing v1 through `evidence.write_run`. `api._with_observations` → `_with_evidence(graph, repo_root, extra, disabled)`; `_observations()` (map panel) reads the same merged store. Verify on pointlessbutton: `seamcheck scan` with the stale schema-0 file present prints "1 run at 6c552a3 (N commits behind): X items applied where the file is unchanged, Y ignored"; `seamcheck json | jq .evidence` shows coverage. Done: old file loads; nothing crashes with an empty/missing dir; a scratch dir passed by `SEAMCHECK_EVIDENCE` is read.

**Step 3 — probe v1** (`feat(probe): …`). New `seamcheck/probe/__init__.py`, `seamcheck/probe/probe.js` (sessionStorage persistence, `drain()`, sites, navigations, methods, caps, self-exclusion, `PROBE_VERSION`); `browser.py` uses `drain()` and writes v1. Verify: `venv/bin/seamcheck observe http://127.0.0.1:8080/ http://127.0.0.1:8080/about` → files under `observed/<sha>/`, `sites` present with `/static/...js:line:col`, no `[id],[class]...` key; `node --check` on the probe; run the wheel build (`python -m build && unzip -l dist/*.whl | grep probe`) to confirm the JS ships. Done: standalone crawler works end-to-end on the new store.

**Step 4 — Python adapters** (`feat(probe): pytest and Selenium ride-along`). New `seamcheck/probe/sink.py`, `seamcheck/probe/playwright.py` (`install(context)`, `drain(context, test=...)`), `seamcheck/probe/pytest_plugin.py` (gated autouse fixture registered only when `pytest_playwright` is loaded), `seamcheck/probe/selenium.py` (`install(driver)`, `drain(driver, test=...)`; Chrome only, says so otherwise); `pyproject.toml` entry point. Verify (a) on pointlessbutton with a scratch `test_ride.py` that uses its `conftest.py` `shared_browser_context` fixture (explicit `install`/`drain`, because a session-scoped custom context bypasses the plugin — document this) to log in as admin, visit `/push_arena/`, click the button, open three `/asd/...` admin pages: `SEAMCHECK_PROBE=1 venv/bin/pytest <scratch>/test_ride.py -p no:cacheprovider`; then `seamcheck scan` — admin-only JS under `pointless/static/admin/` gains observed rows the crawler could never reach; hand-check five. (b) Selenium on `~/dev/seamcheck-corpus/django-debug-toolbar` (real Selenium suite, LiveServer, no docker): temporarily add `install`/`drain` to its `tests/test_integration.py` setUp/tearDown, run its selenium tests with Chrome, confirm files, `git checkout -- tests/` to revert. Internal verification only; nothing about that repo is published. Done: both adapters produce schema-1 files; parallel `pytest -n 2` (if xdist is available) yields distinct filenames.

**Step 5 — Node adapters and `probe init`** (`feat(probe): Playwright and Cypress shims, probe init`). New `seamcheck/probe/playwright.mjs`, `seamcheck/probe/cypress.js` (templates with `__SEAMCHECK_PROBE__` placeholder), `seamcheck/probe/init.py` (detection, printing, `--write`), `cli.py` (`probe` handled before the Django bootstrap like `share`, with `_setup_django_if_any()` for `status`), `COMMANDS["probe"]`. Verify on pointlessbutton (it has `@playwright/test` in `node_modules` and `playwright.config.js` with `baseURL http://localhost:8080`): `venv/bin/seamcheck probe init` prints Playwright-Node + pytest-playwright + Cypress-absent; `--write` puts `seamcheck-probe.mjs` beside the config (delete afterwards, or keep untracked); a scratch spec importing it, `npx playwright test --config <scratch>/pw.config.mjs --workers 4 --project chromium` → 4 workers, distinct files, `seamcheck probe status` merges them. Cypress: a scratch project with `npx cypress run` against pointlessbutton if the binary downloads in this environment; if it cannot, mark the Cypress shim **unverified** in the CHANGELOG line rather than claiming it works. Done: `seamcheck help probe` reads well; `probe init` on `~/dev/seamcheck-corpus/django-cms` detects its Playwright suite (detection only; not run).

**Step 6 — provenance v2** (`feat(provenance): promote by site, by name, and to routes`). `seamcheck/provenance.py` rewritten to the table in 3.5 with site resolution (`api._static_candidates`, suffix match), `UrlIndex` reuse for `url`/`view`, class stems, `css_selector`, `js_call` dynamic fetch; runner-origin filtering; notes as in 3.4. Verify on pointlessbutton with the step-4/5 evidence: for each promoted kind pick two rows from `seamcheck json`, open `file:line`, confirm the selector/URL/class is what the note says; confirm zero rows changed *from* connected; confirm `<dynamic>` promotions all carry a site in the same file. Record before/after uncertain per kind in the commit message. Done: the uncertain drop is fully explained by named pages and sites.

**Step 7 — reporting and the gate** (`feat(report): observation coverage, --evidence`). `report.py` (`Report.evidence`), `renderers/terminal.py`, `renderers/markdown.py` (coverage line, `observed` marker on rows), `mapdata.py`/`renderers/map_html.py` (node badge, filter chip, panel header with coverage, panel pages from the merged store with SHA), `management/commands/seamcheck.py` + `cli._plain_args` (`--evidence`, `--no-evidence`), `api.check`/`api.report`/`mcp_server` pass-through, `triage.py` comment on fingerprint. Verify: `git worktree add /tmp/pb-ci HEAD` of pointlessbutton, copy the evidence dir elsewhere, `seamcheck check --evidence <dir>` there prints the coverage line and exits by findings; `--no-evidence` restores step-0 counts exactly; `seamcheck map --no-serve`, open it, the badge and chip work and `node --check` passes on the emitted script. Remove the worktree. Done: same graph, evidence on/off, differences all labelled.

**Step 8 — docs and release notes** (`docs: …`). README (section 8 below), `docs/commands.md`, `docs/field-notes.md` (one line: the crawler hit the login wall 88 times), CHANGELOG 0.9.0 entry with the measured before/after on the private bench described in aggregate only, `llms.txt` one line, `seamcheck help observe` paragraph. Verify: `seamcheck help probe`, `seamcheck help check`, self-gate, corpus gate. Done: the owner reads the README diff and it sounds like them.

## 7. Scope cuts (not in this iteration)

- Login/auth for the standalone crawler (ranked 4): the ride-along makes it less urgent; revisit after two teams have adopted the probe.
- npm package `seamcheck-probe`: the shim *is* the package's source; publish once the file format has survived one release.
- Source maps for bundled JS; Firefox/WebKit under Selenium; Puppeteer; WebSocket/EventSource/`sendBeacon` observation; iframe drains under Cypress.
- Observed *edges* in the map (js_call → url from a dynamic fetch): symbols only for now.
- Live-null demotion of static-connected rows and per-template page coverage: both need url→template attribution (`pagenames.urls_by_template` is Django-only and heuristic).
- Evidence compaction / a merged cache file; an app-exposed build SHA; a Django template tag that injects the probe (would cover Firefox and the REG-harness iframes; touches the app's markup).
- MCP tool for evidence status; `share` counting observed rows.
- Any change to the four statuses.

## 8. README / docs changes (short)

README, "Turning `uncertain` into evidence": keep the paragraphs; fix the install line (the extra now exists); replace the single crawler command with:

```bash
pip install 'seamcheck[observe]'
seamcheck observe                     # a crawler: the pages the graph knows about, no login
SEAMCHECK_PROBE=1 pytest tests/e2e    # better: ride along with the tests you already run
seamcheck probe init                  # the one line for Playwright, Cypress or Selenium
```

and one sentence after the caveat: "Every report now says how much was observed — `routes 62 of 169, js files 14 of 41` — so a falling `uncertain` comes with the number that earned it." `docs/commands.md`: add `seamcheck probe` to the list and `--evidence PATH` / `--no-evidence` to the flags line. CHANGELOG: an `observe` entry finally, under 0.9.0, listing the four fixes from step 1 plainly as bugs.

---

## Summary

1. Today's `observe` is a Playwright crawler (`browser.py`) with a 90-line probe (`observe.py`) recording selectors/fetches/classes into one overwritten `OTHER/seamcheck/observed/<sha>.json`, applied HEAD-only in `api.scan()` via `provenance.py`, labelled only in free-text notes.
2. Verified bugs: the `[observe]` extra does not exist; the probe records its own `boxes()` query, which makes every `<dynamic>` selector in the project promote to connected; substring URL matching; live nulls demote static-connected rows (88 of 169 crawled pointlessbutton pages were the login wall).
3. Design: one `probe.js` shipped in the wheel + a file sink + thin adapters (Playwright Node fixture, pytest plugin gated by `SEAMCHECK_PROBE=1`, Cypress support import, Selenium CDP helper); `seamcheck probe init` prints/writes the one line per runner.
4. Egress: in-page buffer persisted to `sessionStorage` across navigations, drained at test end; chosen over an HTTP collector (changes the app's network traffic) and `exposeBinding` (Playwright-only).
5. Evidence schema 1: one immutable file per test per worker under `observed/<sha>/`, with call sites, navigations, methods; schema 0 still readable; union merge with first/last seen.
6. Staleness: per-file via `git diff --name-only <sha> HEAD`; promotions survive across commits when the symbol's file is unchanged; the live-null claim needs HEAD evidence.
7. Labelling: `Symbol.evidence = static | observed | observed-name`, schema 1.1, fixed token in every renderer; statuses unchanged.
8. Coverage: routes observed ÷ known and js files observed ÷ known, printed with the counts, in `check`, `probe status`, and the map.
9. Honesty rules: `<dynamic>` promotes by call site only; static findings overridden only by site-attributed evidence; nulls never demote connected; runner-origin queries (Cypress/Selenium atoms) filtered; stubs cannot invent routes.
10. Eight ordered steps, each verified on pointlessbutton (admin-only pages reachable for the first time) and Selenium on django-debug-toolbar internally; scope cuts include crawler login, npm publish, source maps, observed edges.

**File not written:** this session was read-only, so `~/dev/seamcheck/docs/plans/2026-09-03-observe-rides-along.md` must be created by the parent from the document above (`mkdir -p ~/dev/seamcheck/docs/plans` first).
