# Findings from leanos-app

Everything learned about **seamcheck itself** while running 0.15.0 on `leanos-app`
(Next.js 16 App Router, React 19, TypeScript, Supabase, Stripe — plus several hand-rolled
vanilla-JS/HTML "deck" pages under `public/`) on 2026-09-14. This is the Node/React/database
counterpart to `docs/FINDINGS-FROM-POINTLESSBUTTON.md` — same protocol, same split (written
from the project being scanned, implemented and closed here), aimed at the backend the
README's own coverage table scores lowest: **Next.js at 46%**, against Django's 91%.

Purpose here is explicitly **coverage, not cleanup** — nothing in this file is a request to
fix leanos-app. It's the edge-case supply for pushing the Next.js/JS-ecosystem adapters up
the same way the Django ones went from 84%→91%.

One `scan`, all 15 `unused` claims hand-graded (0 `unresolved`), plus a targeted trace of
*why* two of nine dynamically-routed pages showed `uncertain` despite being linked from 34
places. Labels: `tools/labels/leanos-app.json`.

**Read an entry to its end before implementing it** — same rule as the pointlessbutton file.

---

## A. Confirmed bugs

### N1 — a template literal that interpolates the LAST path segment never resolves to its dynamic route · the standard way to link to a Next.js `[param]` route, unmatched

This is the highest-impact item in this file. `<Link href={\`/kaizen/${locale}\`}>` is not
an edge case — it is *the* idiom for linking to a parameterised Next.js route from JSX. It
does not resolve, for a reason confirmed on both sides of the pipeline:

**Side 1 — the extractor produces a truncated prefix**, correctly per its own docstring
(`extractors/js_extractor.py::_static_url`, "a prefix that is known, and a tail that is
not"):

```pycon
>>> _static_url(<AST for `/kaizen/${locale}`>)
('/kaizen/', False)
```

**Side 2 — the resolver can never match that prefix against a dynamic route**, because
`_normalize()` strips *both* leading and trailing slashes before compiling/matching:

```pycon
>>> from seamcheck.matcher import UrlIndex
>>> index = UrlIndex([<Symbol for url:/kaizen/[locale]>])
>>> index.resolve("/kaizen/")
None                              # "/kaizen/" -> normalised to "kaizen" -> no segment left
                                   #  to satisfy the pattern `kaizen/[^/]+\Z`
>>> index.resolve("/kaizen/en")
Symbol(id='url:/kaizen/[locale]', ...)   # a COMPLETE value matches fine
```

So a prefix ending right where a required dynamic segment begins can *only* ever fail to
resolve — not sometimes, always — for any Next.js route with exactly one more required
segment after the known prefix. `fetch()`'s own inexact-prefix case avoids this because its
truncation point is usually inside a *query string*, after every required path segment is
already literal; a nav `href` truncates *inside the path itself*, which is the one place
this resolver cannot use a prefix at all.

**Measured blast radius, one project:** 9 routes in `leanos-app` are single-`[param]`
dynamic pages linked exclusively (no other component ever imports/serves them) via this
idiom — `/landing/[locale]`, `/pricing/[locale]`, `/privacy/[locale]`, `/cookies/[locale]`,
`/terms/[locale]`, `/legal/[locale]`, `/samec-terms/[locale]`, `/kaizen/[locale]`,
`/formacion/[locale]` — reached from **34 separate `href={` sites** across 5 files. **7 of
the 9 show `connected` anyway, and it is an accident**: those 7 each happen to also have a
sibling bare route (`app/landing/page.tsx`, etc.) whose body calls
`redirect("/landing/en")` — a fully literal string, which resolves fine and is the *only*
reason those seven aren't `uncertain` too. `/kaizen/[locale]` and `/formacion/[locale]` have
no such sibling (no `app/kaizen/page.tsx` exists at all) and are the two left holding
`uncertain`, despite being linked exactly as many times as the other seven. The tool's own
verdict changes not because the evidence is different, but because of an unrelated file that
happens to exist next to two of them and not the other two.

**Regression test that fails today** (isolates the resolver half, no JS parsing needed):

```python
def test_a_prefix_ending_at_a_required_dynamic_segment_resolves_to_that_route():
    route = Symbol(id="url:/kaizen/[locale]", kind="url", label="/kaizen/[locale]",
                    sub="GET", file="app/kaizen/[locale]/page.tsx", line=1,
                    status=Status.CONNECTED, snippet="", chain=[], note="")
    index = UrlIndex([route])
    # `href={`/kaizen/${locale}`}` truncates to exactly this prefix - and it should count
    # as reaching the route, the same class of evidence a fetch() prefix already gets.
    assert index.resolve("/kaizen/") is not None
```

**Suggested fix — a prefix-aware resolve, not a change to what `_static_url` returns.**
`_as_pattern()` already builds one regex per parameterised route; the missing piece is a
second method, something like `UrlIndex.resolve_prefix(self, prefix: str) -> Symbol | None`,
that checks whether `prefix` (stripped of only its *trailing* slash, never `_normalize`'s
symmetric strip) equals a parameterised route's **literal segments up to and including the
one immediately before its first dynamic segment**. That reuses `_PARAMETER`'s own
segmentation instead of re-deriving it. Call it from `_read_js_references` exactly where a
full `resolve()` currently returns `None` and the target came from an inexact `_static_url`
prefix — mirroring the fetch side's own "record but don't claim which route" caution, so a
route reached this way stays visibly *inexact* evidence rather than being promoted to the
same confidence as `resolve("/kaizen/en")`.

**Judge it twice, per this project's own rule:** the risk of this fix is a prefix like
`/kaiz` (an actual partial word, not a full segment) getting credited against `/kaizen/...`
- guard on the prefix ending in `/` (a complete segment boundary), which every real
`href={`/x/${y}`}` site in this project satisfies, before matching it against a route's
literal prefix.

### N2 — `window.location.href` / `.replace()` / `.assign()` is not recognised as navigation at all, even with a fully literal argument

`_NAV_RECEIVERS` (`extractors/url_reference_extractor.py`) is
`{"router", "history", "navigation", "nav", "$router"}`; `_NAV_METHODS` is
`{"push", "replace", "prefetch"}`. `window.location` matches neither the receiver list (its
`object` is itself a `MemberExpression`, `window.location`, not a bare `Identifier` the
receiver check can name) nor, for `.assign()` and the property-assignment form
(`location.href = "/x"`), the method list.

**Concrete case in this project:** `components/Sidebar.tsx:293` —
`window.location.assign("/admin")` — is the **only** internal reference to `/admin`
anywhere in the repository (grepped: no `<Link href="/admin">`, no `router.push`
anywhere). `seamcheck symbols --search "url:/admin"` reports it `uncertain`. The string is
completely literal and completely unambiguous; nothing about it is a case for `uncertain`
to exist for.

Full-page navigation (`window.location.*`) is common specifically for the cases a project
does *on purpose* to bypass the client router — an admin console mounted as "a separate
shell" per this project's own comments, a hard reload after sign-out, breaking out of an
iframe. That is exactly the kind of intentional, load-bearing navigation this class of
finding exists to protect.

**Fix:** recognise `window.location` (and bare `location`, both valid) as a receiver whose
property access — `.href = "..."` (an `AssignmentExpression`, a different node shape than
the calls this file currently walks) or a call to `.assign()` / `.replace()` — carries a
navigation target the same way `router.push(...)` does.

---

## B. False-positive classes, measured

Fifteen `unused` claims, hand-graded by reading the code (0 `unresolved` on this project;
nothing to grade there). **5 true / 15 = 33%.**

### B1 — `content: attr(data-X)` is a read, and it isn't checked · 5 of 15

Two components set a data attribute purely so a *shared, separately-loaded* stylesheet can
render it via CSS `attr()`. Nothing reads the attribute back in JS, which is exactly the
shape that makes a `dom_attr` check call it unread — except it is read, just not by
`getAttribute`/`dataset`/an attribute *selector*.

```css
/* app/globals.css:10-14 */
.lc-tip:hover::after { content: attr(data-tip); ... }
```
```tsx
// components/Vsm.tsx:1995 and :2144
<span className={...} data-tip={metricHint(metric, t) || undefined} ...>
```

Same shape with an attribute *selector* on the CSS side:

```css
/* app/globals.css:353-354 */
.lc-compare-table td[data-label]::before { content: attr(data-label); }
```
```tsx
// app/landing/LandingClient.tsx:468, 475, 481
<td data-label={compareHeaders.leanos}>
```

Findings: `dom_attr:data:tip:components/Vsm.tsx:1995`, `...:2144`,
`dom_attr:data:label:app/landing/LandingClient.tsx:468/475/481` — all 5 false.

**Fix:** wherever the CSS extractor parses a `content:` value, recognise `attr(NAME)` and
credit `NAME` as a read on the matching attribute — same status as an attribute-selector
read; it's the same evidence (a stylesheet names the attribute literally).

### B2 — a class name built by JS string concatenation/template literal, then assigned via `innerHTML`, is invisible · 4 of 15

Not a template engine — plain JS building an HTML string. Two sites, three selectors, one
file:

```js
// public/comercial/leanos-comercial.html:386
el.className = "slide" + (s.kind === "cover" ? " dark" : "");
// :333                    `<div class="kpi ${k[2]?'g':''}">`
// :337                    `<span class="hp ${p[4]}">`             // p[4] = "ok" | "risk"
```

Findings: `css_selector:class:dark`, `class:g`, `class:ok`, `class:risk` — all 4 false, all
in the same file, all `el.innerHTML = <template string>` construction.

**Fix:** this is the tool's own already-documented F11/F4 class ("built at runtime") in a
context that class detection doesn't yet seem to reach — plain string concatenation
(`"a" + (cond ? " b" : "")`) and a template literal with a ternary/variable directly inside
a `class="..."` position, as opposed to `querySelector`/`classList.add` (which the CSS
`attr()` gap in B1 shows is a separate code path from this one).

### B3 — `element.style.setProperty("--token", …)` isn't linked to the token's `var()` reads · 1 of 15

```css
/* public/comercial/leanos-comercial.html:14 */
:root { --accent: #2551d6; ... }        /* + 9 more `var(--accent)` reads, same file */
```
```js
// public/comercial/leanos-comercial.html:387
if (s.accent) el.style.setProperty("--accent", s.accent);
```

`css_token_def:token:--accent` came back `unused` ("defined at runtime by JavaScript, not
in any stylesheet") — true of *this* definition site in isolation, false of the token: the
same name is declared at `:root` and read via `var(--accent)` nine-plus times in the same
file. The runtime write is a real, working per-slide override, not dead code.

**Fix:** check a `css_token_def` site against **every** `var(NAME)` read for that name
anywhere in a reachable stylesheet, not only reads the definition's own origin can see —
whoever defines a token that is read anywhere is used, independent of whose value wins.

---

## C. True positives — confirmed dead, safe to remove

The other 5 of 15, checked by grepping the *whole* file for every spelling before calling
each dead:

- `css_selector:class:h4l`, `:h5l` — `public/formacion/plantillas.html` declares a
  `.writebox.h1l`…`.h5l` size scale; only h1l/h2l/h3l are ever applied.
- `css_selector:class:three` — same file, a 3-column grid utility; its media-query siblings
  `.two`/`.bones`/`.vsm-tot` **are** used elsewhere in the file, so this isn't a blind spot
  on the whole family, just this one class.
- `css_selector:class:num` — `public/formacion/guia-formador.html` styles `ol.num` twice
  (base + print) but no `<ol>` in the file carries that class.
- `dom_attr:data:retry:components/EssentialCookieGate.tsx:87` — written, never read anywhere
  (checked every spelling: `dataset.retry`, `getAttribute`, `[data-retry]`,
  `attr(data-retry)`). Does real work as a React re-render key; the DOM attribute carrying
  it out has no consumer.

---

## D. Checks it does not have, ranked by what they would have found here

**D1 — no model of Next.js middleware / this environment's renamed `proxy.ts` convention
at all.** `adapters/nextjs_adapter.py` never references `middleware.ts` or any equivalent,
in a project or a stock Next.js install. leanos-app's `proxy.ts` (`export function
proxy(request)`, `export const config = { matcher: "/" }`) intercepts every request to `/`
and 307-redirects anonymous visitors to `` `/landing/${locale}` `` before `app/page.tsx`
ever runs. Two consequences worth a lens: (a) a route can be `connected` by the filesystem
reader while middleware unconditionally redirects a whole class of request away from it —
the route "exists" in a sense a server operator would not recognise; (b) a rewrite target
(not present in this project, but standard Next.js) can be the *real* destination of traffic
that no application code ever names, and would show as unreferenced with nothing to say why.
Not asking for full request-flow simulation — just recognising the file and its `matcher`
as a first-class symbol, the way `stripe_extractor.py` gives a webhook its own
externally-reached status rather than judging it by internal callers alone.

**D2 — schema kept as prose SQL in a markdown file, not `supabase/migrations/`, zeroes the
entire DB lens.** `scan` correctly reported 1012 `uncertain` and named the fix (`supabase db
pull`) rather than guessing — this is not a bug. Recording it because it's probably a common
real-world shape (schema tracked in running notes next to a migrations folder that was never
started) and because it disables exactly the lens this project's own history most needed:
its notes separately describe a real incident where a table had RLS **on** with zero
policies, silently failing every read and write — precisely what `docs/data-layer.md`'s
`db_policy` check exists to catch. Not proposing markdown-SQL parsing (too heuristic to
trust) — just naming where the ceiling sits for a project shaped like this one, since `db
pull` needs credentials no static reader can have.

---

## Precision on this project

**5 true / 15 = 33%**, scored via `python tools/precision.py` against
`tools/labels/leanos-app.json` (repo root pinned there with `_root`, since this is a private
project, not in the corpus).

| Class | Count |
|---|---:|
| true (genuinely dead) | 5 |
| false — B1: CSS `attr()` read not traced | 5 |
| false — B2: class name built by JS string concat / template literal | 4 |
| false — B3: `--token` via `setProperty()` not linked to its `var()` reads | 1 |

N1 and N2 are not in this table — both are `uncertain`/reference-extraction gaps, not
`unused`/`unresolved` claims, so they sit outside the precision protocol by its own rule
(`docs/verifying.md`: only `unresolved` and `unused` are claims). They are very likely the
larger lever for the Next.js coverage number, since N1 alone affects every parameterised
route in this project linked the idiomatic way.

---

# Update · implemented here, re-measured on the same project

Every entry in A and B, worked from this side, following this file's own rule (read each
one to its end before implementing it — N1 especially, whose regression-test snippet and
its own "Suggested fix" section disagree on purpose). Re-scanned with `--refresh` against
the same `leanos-app` checkout, no source in that project touched.

## Verified fixed

| # | Item | Evidence on re-scan |
|---|---|---|
| **N1** | `href={`/kaizen/${locale}`}` never resolves | `url:/kaizen/[locale]` and `url:/formacion/[locale]` → **connected**, same as their seven siblings; both now carry an explicit reference symbol whose `note` says the match came from a literal prefix, not a complete value |
| **N2** | `window.location.assign/replace/href=` not read as navigation | `url:/admin` → **connected** |
| **B3** | `--token` via `setProperty()` not linked to its `var()` reads | `css_token_def:token:--accent` → **connected** (was `unused`) |
| **B2** | class built by JS string concat / template literal | `css_selector:class:dark` and `class:g` → **connected**. `class:ok`/`class:risk` are **not fixed** — see below, this one was only partially closed |
| **B1** | CSS `attr()` read not traced | `dom_attr:data:tip:components/Vsm.tsx:1995`/`:2144` and `dom_attr:data:label:app/landing/LandingClient.tsx:468`/`475`/`481` → **connected** (was `unused`) — but only after N3, below; see why |

**Control held.** The four genuine true positives from section C —
`css_selector:class:h4l`, `:h5l`, `:three`, `:num` — are **still `unused`** after every fix
above. Nothing here blanket-downgrades a real finding to make the numbers look better.

**B2 is partially closed, on purpose, not by oversight.** `dark` and `g` are ternaries
between two LITERAL branches (`cond ? " dark" : ""`, `` k[2]?'g':'' ``) — genuinely static,
just two possible values instead of one, so `_literal_strings` now expands them. `ok`/`risk`
(`` `<span class="hp ${p[4]}">` ``, `p[4]` an array index into a table defined elsewhere in
the same file) are not: naming which string `p[4]` holds needs actual data-flow analysis
across an array literal, not string-literal resolution, and that is a different and much
larger feature than "a ternary is not a runtime unknown." Left `unused`, correctly, by this
tool's own rule against claiming more than the evidence supports.

## N3, N4, N5 — new: one chain of gaps, found only by checking B1 against this project

**B1's fix alone did nothing on this project when first checked** — `dom_attr:data:tip`
stayed `unused` after the `content: attr()` reader was added. Chasing why surfaced three
separate, independent bugs stacked on top of each other, none of them named in sections A
or B because nothing in this file's original scan could see far enough to name them.

**N3 — `css_source_root` autoconfig has no entry for a Next.js App Router's own `app/`.**
`app/globals.css` — the file B1's own evidence is quoted from — was **never discovered as
a stylesheet at all**. `autoconfig.py`'s non-Django `css_source_root` guess only ever
looked inside `_STATIC_DIRS` (`public`, `static`, `assets`, …), which is where
Django/Express/Flask-shaped projects keep their CSS; a Next.js App Router keeps its global
stylesheet at `app/globals.css`, imported directly into the root layout, never linked with
`<link>` and never sitting under a `public/`-style directory. `public/` exists in this
project (so detection did not fail loudly), but holds zero `.css` files — the deck pages
under it are `.html` with inline `<style>`, not `.css` — so `css_source_root` silently came
back empty and **the entire file was invisible**: not one of its 47 class selectors, its
design tokens, or its attribute-selector rules existed as a symbol, regardless of what B1
does once it can see the text. **Fix:** `_detect_without_django` now also checks `app/`,
`components/` and `pages/` for `.css` files (`_CSS_EXTRA_DIRS`), in addition to
`_STATIC_DIRS`, never instead of it — a project with a real static CSS root keeps picking
it by file count exactly as before (`NonDjangoCssRootTests`,
`seamcheck/tests/test_autoconfig.py`). `app/globals.css` now contributes 47
`css_selector` symbols where it contributed zero before.

**N4 — `styles_are_local` asked `bool(css_files)`, the STANDALONE `.css` list, not
whether the project has styles at all.** Fixing N3 flipped `styles_are_local` true for
this project as a side effect (`css_files` went from empty to one file) and, doing so,
exposed that the flag was never asking the right question: `match_css_selectors`'s own
comment says its job is deciding whether "this project HAS stylesheets of its own", but a
project whose ENTIRE stylesheet lives in templates' own `<style>` blocks — leanos-app's
`public/*.html` deck pages, ~1,000 selectors, zero standalone `.css` files, the majority
of this project's CSS by far — answered `bool(css_files)` with `False` regardless of N3,
and every class or id with no matching rule anywhere got the CDN-style benefit of the
doubt (`uncertain`, "no local CSS was found at all") instead of the honest `unresolved`
its own evidence supported. **Fix:** `pipeline.py` now passes `bool(selectors)` — the
already-computed, already-template-merged selector list — instead of `bool(css_files)`
(`StylesAreLocalFromTemplateOnlyCssTests`,
`seamcheck/tests/test_pipeline_dom_css_regression.py`).

**N5 — `wired_by_markup` only ever read the `dom_selectors` parameter, and
`scan_templates()`'s matching evidence lives in `dom_attrs`.** N4 fixing `styles_are_local`
had its own side effect: every id in this project with no CSS rule now got JUDGED rather
than excused — including 13 ids across 4 near-identical guide pages
(`public/formacion/guia-formador*.html`) that are pure in-page navigation targets
(`<a href="#m0">…</a>` pointing at `id="m0"`), which have no business being asked whether
a stylesheet defines them. `match_css_selectors` has an exemption for exactly this shape —
`wired_by_markup`, built from any `id:evidence` symbol — and `scan_templates()` does emit
one for every such `href="#x"` reference (`template_scanner.py`, `_ID_REF_RE`). But
`scan_templates()` returns ONE mixed list, dom_attr declarations and these dom_selector-
kind evidence symbols together, and `pipeline.py` assigns the whole thing to its
`dom_attrs` variable without ever splitting the two kinds apart — so the evidence has sat
in `dom_attrs`, not `dom_selectors`, for every project this ever ran on, and
`wired_by_markup` — reading `dom_selectors` alone — never once saw it. Not something N3 or
N4 introduced; both only removed the leniency that had been hiding it. **Fix:**
`wired_by_markup` (`dom_matcher.py`) now reads both parameters
(`WiredByMarkupTests.test_the_reference_still_wires_up_when_it_arrives_through_dom_attrs`,
`seamcheck/tests/test_dom_matcher.py`) — the safer fix of the two available, since
reshaping what `pipeline.py` routes into `dom_attrs` risks changing what several *other*
consumers of that list see (multi-writer detection's `declared` set among them), un-verified
by this pass.

**One honest side effect, not a bug, left in place.** Two genuine new findings survive
every fix above: `css_token_use:token:--font-geist-sans`/`--font-geist-mono` in
`app/globals.css` are `unresolved` because no `--font-geist-*` definition appears as text
anywhere seamcheck reads — `next/font`'s loader defines them by adding a generated class to
the document, a mechanism this scan does not model at all, and a `D`-shaped gap of its own
(a Next.js font-loader lens) rather than a defect in any fix here. And
`dom_attr:class:noprint:…:289` (four copies, one per guide-page locale) is `unresolved`
because nothing in the project styles `.noprint` and nothing references it as markup
evidence either — real, in the same sense the font tokens are: the tool is not wrong to
report it, whatever the truth turns out to be on inspection.

## Numbers on the same project

| | before | after | |
|---|---:|---:|---|
| connected | 5,116 | **5,349** | +233 |
| unresolved | 0 | **6** | +6 — 2 genuine `next/font` findings (N3's side note) + 4 copies of one genuine unstyled class (N5's side note); every id N5 exists for is now correctly `connected` instead |
| unused | 15 | **22** | +7 — genuine new findings in newly-readable CSS (dead `--define`/`--measure`/`--analyze`/`--improve`/`--control` tokens in a deck page, `ok`/`risk` left open above), not a regression in the 15 already graded |
| uncertain | 1,012 | **891** | −121 |
| total symbols | 6,143 | **6,268** | +125 |

Precision, re-derived by hand rather than re-run through `tools/precision.py` (that tool
scores `tools/labels/leanos-app.json` against whatever the CURRENT scan claims `unused`,
and the label file itself is unchanged — it still grades all 15 original findings, several
of which are no longer `unused` claims at all, which is the point): of the original 15,
**8 are no longer claimed `unused`** (B1's 5, B3's 1, B2's `dark`/`g`) — corrected, not
just reclassified — leaving **7 still claimed `unused`**: the 5 genuine true positives from
C, plus the 2 not fixed here (`ok`/`risk`). Precision on what the tool still claims from
this sample is **5 true / 7 = 71%**, up from 33%, with the other 8 moved out of the claim
entirely rather than papered over.

**Not re-run:** `python tools/corpus.py scan` (needs the 47-repo corpus cloned, not present
in this environment) and `./build_parsers.sh` (no `.mjs` parser source touched by any of
these fixes — B1 reads raw CSS text with a Python regex, same mechanism the existing
`[data-x]` attribute selector already used, not the postcss AST). `python -m seamcheck.cli
check` on this repository itself still reports `connected 0 unused 0 unresolved 0
uncertain 1`, unchanged. `ruff check seamcheck/` is clean. The full test suite
(`seamcheck/tests`, 1,591 tests, browser test excluded) passes.
