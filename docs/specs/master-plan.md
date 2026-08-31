# Master plan — from a tool that works to a tool people find

**Status:** plan. Supersedes nothing; it sequences `roadmap.md`, `universal.md`,
`validation.md` and `page-capture.md` into one order, and adds the part none of them cover —
how this actually reaches anybody.

**The rule that governs both halves:** *never make a claim the scan cannot evidence.* It is
the reason the tool is worth anything, and it binds the marketing exactly as hard as the
code. A tagline is a claim.

---

## Part 0 — Where we actually are

### What is true and measured

| | |
|---|---|
| Reference project | one real Django + vanilla-JS app, **511,000 lines**, 1,505 files |
| Precision | **~73% → 98.3%**, hand-assigned root cause on every finding, twice |
| `uncertain` | 5,632 → **2,077**, with **zero unexplained** |
| Static mode | **95% recall** on routes declared in a `urls.py` |
| Config auto-detection | reproduces 9 hand-written keys, and found a *better* CSS root |
| Template scanner | identical output on Django, Twig, **Blade**, ERB, Handlebars |
| Node backend | `express.js` **parses today** — the AST is already built |
| Tests | **604 passing** |
| Shipped | `seamcheck 0.5.0` on PyPI, verified from a clean venv |

### What is built but unproven

- **The runtime probe.** Unit-tested, never driven a real page. Zero live evidence.
- **`observe` provenance merge.** Same.

### What is claimed but stale

**The README's numbers are from an earlier measurement pass and no longer match the tool.**
It says 38,010 symbols · 1,455 findings · ~97% precision. Later work measured ~36,753
symbols · ~4,105 findings · 98.3% · 2,077 uncertain — different because the extractors
changed underneath.

> **Nothing publishable exists until every number is re-measured in one run, on one commit,
> and every document quotes that single run.** Two documents disagreeing about our own
> accuracy is the fastest way to lose the only thing this project has.

### What is a known hole

- **TypeScript: zero coverage.** Not discovered, not parseable. Takes Next.js and NestJS with it.
- **React:** JSX unparsed, and the DOM/CSS half is structured differently regardless.
- **One backend.** Django only.
- **Transports untraced:** Stripe, Celery, Redis, WebSockets — quoted in every `unused` note.
- **Recall: completely unmeasured.** A false `connected` is invisible by construction.

---

## Part 1 — Engineering, in order

Each phase names the gate that must be true before the next begins.

### P1 · Live-run the runtime probe
The only thing built and unverified. Backend-agnostic, so it pays back on every future
adapter — and a recorded `fetch` returning 404 **is** a finding with nothing parsed, which
makes the dynamic half framework-free today.
**Gate:** uncertain drops measurably on the reference project, and the number is recorded.

### P2 · Extract the `ServerAdapter` seam
Django as the sole implementation. No new behaviour, no new claims — a refactor with 604
tests as the guard. Doing it while nothing else moves is the whole point.
**Gate:** 604 still green, scan output byte-identical.

### P3 · FastAPI adapter
Cheapest possible proof the seam is real: same language, same `ast`, no new parser.
**Gate:** a FastAPI sample scans end-to-end. **This is what earns the word "multi-framework".**

### P4 · Stripe + Celery, static half
The least contested ground in the plan. Nothing else looks at string dispatch, and both are
real outage classes — a payment webhook silently dropping an event, a beat entry naming a
task that does not exist.
**Gate:** `BLIND_SPOTS` shrinks for the first time. That constant is quoted in the UI, the
README and every finding's note, so shrinking it is the most visible proof the tool improved.

### P5 · The TypeScript gate
`acorn-jsx` (pure-JS official plugin) for syntax, then a type-stripper (`sucrase`, pure JS)
feeding acorn. **This is the gate on everything modern** — every mainstream frontend and both
JS backends worth having.
**Gate:** a `.ts` and a `.tsx` file produce symbols, and the parse-failure reporter stays silent.

### P6 · Node route adapter
Express and Fastify are pattern-matching over an AST we already build. **Next.js App Router
needs no parser at all** — its routes are the filesystem. Cheap, and unblocked only by P5.
**Gate:** an Express app and a Next app both resolve their routes.

### P7 · Public-repo validation
Per `validation.md`: four gates, 10–15 repos, chosen for **unfamiliar shapes** not famous
names. Now spans 3–4 frameworks, which is what makes it worth doing at all.
*Run a 5-repo Django-only smoke pass right after P2* — early precision signal costs half a
day and de-risks everything after it.
**Gate:** precision published per framework, however low it comes out.

### P8 · Re-measure and rewrite
One run, one commit, one set of numbers. Then Part 2.

### Deferred, deliberately
**React DOM extractors** (a project, not a task — component graph, CSS Modules, props),
**Rails/Laravel** (Laravel is cheaper than Django was; Blade already works), **Redis + WS**,
and **the clickable page** (`page-capture.md`) — the human half, once the graph is worth
pointing at.

---

## Part 2 — Entering the market

### The message, in three beats

**1. The bug nobody else can see.**

> **The bug lives between your files.**

`catalogue.js` is valid JavaScript. `urls.py` is valid Python. Every linter says both files
are fine — and they are. The defect is that one names a route the other does not serve, and
it exists *only between them*. Every tool in this space works inside one language and stops
at its boundary. **That boundary is where this bug lives.**

**2. Why it matters now.** Keep the current opener; it is the best line we have:

> *Your AI wrote 400 lines. Which of them are actually wired to anything?*

Generated code is fluent, plausible, and wrong at exactly the seams — a `fetch` to a route
that was renamed, a selector for an element the template no longer has. It compiles. It
lints. It is broken.

**3. Why you can trust the answer.** This is the differentiator competitors structurally
cannot copy, because copying it costs them their own numbers:

- **A fourth answer.** `uncertain` means *no evidence either way* — not a claim it's dead.
- **It reports its own coverage.** How much of each file it actually reasoned about.
- **It publishes its own false-positive rate**, measured by hand, and republishes it when it
  gets worse.

### Where we sit, said honestly

| tool | what it does | where it stops |
|---|---|---|
| **Knip** | dead JS/TS files, exports, deps. Free, ISC, excellent | inside JavaScript |
| **Vulture / ruff F401** | dead Python | inside Python |
| **PurgeCSS** | unused CSS | inside CSS |
| **Seamcheck** | **the seam between them** | one backend, for now |

**Do not position against Knip.** It is free, good, and beloved; picking that fight loses.
Position *beside* it: Knip cleans inside a language, Seamcheck checks across the boundary.
Recommending Knip in our own README is cheap and buys credibility.

### The image set — 10 assets, all from the fictional bookshop

Never a real repository, per `validation.md`. Sizes are for where they render.

| # | asset | size | why it exists |
|---|---|---|---|
| 1 | **Social preview / OG card** | 1280×640 | **Highest leverage and currently missing.** Renders on every GitHub, HN, Reddit and X share. One line of message + the map. |
| 2 | Hero: the map | 2× retina | Regenerate — the UI changed (Overview first, percentages, new palette) |
| 3 | **Click a red node → chain** | 2× | The aha. Numbered hops, real source, file:line |
| 4 | **Animated map GIF** | ≤3 MB, ~8 s | **The single highest-converting asset for a visual tool.** Click → everything recedes → chain lights up |
| 5 | **Terminal cast** | SVG | `pip install seamcheck` → `seamcheck map` → clickable link. SVG stays crisp and tiny; shows the 30-second path |
| 6 | The four answers | graphic | Currently a table. It is the differentiator; draw it |
| 7 | Coverage self-report | graphic | "No other tool tells you where it wasn't looking" |
| 8 | CI panel | refresh | Exists |
| 9 | Agent / LLM panel | refresh | Exists |
| 10 | **Precision before/after** | graphic | 73% → 98.3%, and *why* each 27% was wrong. A trust asset, not a brag |

All regenerated by `python docs/mockups/capture.py`, which already renders the real UI
against `demo_graph.py`. **Assets 1, 4, 5, 6, 7 and 10 are new work.**

### The texts — all of them, rewritten not patched

| text | note |
|---|---|
| **README** | Restructure. 394 lines with stale numbers. Lead with the three beats |
| **PyPI long_description** | Must not drift from README — same source |
| **GitHub About** (350 char) + **Topics** | Values already drafted in `universal.md` |
| **Social preview image** | Repo setting; asset #1 |
| **CHANGELOG / release notes** | Per version, plainly |
| **Show HN** post + prepared first comment | One shot. See sequence below |
| **r/django · r/Python · r/webdev** | Three *different* posts — Reddit punishes copy-paste, correctly |
| **X thread**, 6–8 posts | Asset #4 carries it |
| **Long-form: "I measured my own false-positive rate"** | The strongest piece available. Nobody in this category publishes one, and it doubles as the accuracy documentation |
| Sponsor tiers, issue templates | Exist; review only |

### Launch sequence — quiet first, loud once

| stage | what | gate |
|---|---|---|
| **0 · now** | Silent PyPI releases. No announcements | — |
| **1** | P1–P7 engineering + validation | precision published per framework |
| **2** | Re-measure once; rewrite every text and asset | all numbers from one run |
| **3 · soft** | Long-form post + X thread. Collect issues, fix, iterate | someone who is not the author runs it successfully |
| **4 · loud** | **Show HN — one shot** | everything above true |
| **5** | Sustained: answer every issue, publish every correction | — |

**Show HN is not repeatable.** It fires once, and it fires into a README that either
withstands a skeptical reader or does not. The single most likely top comment is *"how many
false positives?"* — and being the only project in the category with a measured answer is
precisely why the honesty work was worth doing.

---

## Part 3 — Risks worth naming now

- **Precision will drop below 98.3% on unfamiliar code.** That number was earned by nine
  extractor fixes against *one* codebase's habits. **Publish the drop.** A number that only
  ever rises is a number nobody should believe — and volunteering it is the cheapest
  credibility available.
- **The TypeScript gate (P5) is the biggest single unknown.** Everything modern is behind it.
- **Solo maintainer.** A successful launch means issues arriving faster than one person
  answers them. Better to launch late than to launch into silence.
- **Fixing repo A can regress repo B.** Every validation fix needs a fixture built from the
  shape that caused it.
- **The real cost is hand-judgement**, not compute: 15 repos × 20 findings = 300 decisions.
  It is also exactly the work that produced every improvement so far.

## Part 4 — What is not written until it is earned

- The word **"universal"** — waits for P3.
- Any **named repository beside its findings** — never, per `validation.md`.
- Any **screenshot that is not the fictional demo**.
- Any **accuracy number** not produced by the single re-measurement run in P8.
