# From "a Django tool" to a seam tool that reads Django

**Status:** plan. Nothing here is claimed publicly until the code earns it.

## The observation this rests on

Of the 36,735 symbols in a real scan, roughly **1,078 are server-side** — routes, views,
models, response fields, admin actions, signal receivers, template tags. Everything else —
around **97%** — is JavaScript, CSS, DOM attributes and template markup, and none of that
reads Django at all.

The runtime probe is stronger still: it patches `fetch`, `XMLHttpRequest`, `querySelector`
and `classList` in a browser. It has no idea what served the page.

**So the tool is already framework-agnostic except for one component: the route reader.**
Calling it a Django tool undersells it to everyone who isn't using Django, and — worse —
tells Django users it is a niche curiosity rather than the thing that finds the bug no
linter can see.

## The claim boundary, which is the whole discipline

Seamcheck's one house rule is that it never asserts more than its evidence. Applying that to
its own marketing:

- ✅ **"A seam tool. Reads Django today."** True now.
- ✅ **"The architecture is framework-agnostic; adapters are small."** True and demonstrable.
- ❌ **"Universal."** Not until a second adapter exists and has been run against real repos.

The word `universal` is earned by the second adapter, not by the plan for one. Shipping the
claim first would be exactly the failure the `uncertain` status exists to prevent — and the
first person to try it on Rails and find nothing would be right to never come back.

## The architecture change

Today `run_scan()` calls `extract_django_urls_views()` directly. That is the only knot.

```python
class ServerAdapter(Protocol):
    name: str                       # "django", "rails", "laravel", "fastapi", "express"

    def detect(self, repo_root: str) -> float:
        """0.0-1.0 confidence that this adapter fits the repo. Highest wins."""

    def routes(self, repo_root: str, config: dict) -> tuple[list[Symbol], list[Edge]]:
        """url + view symbols, and the routing edges between them."""

    def route_names(self, repo_root: str, config: dict) -> dict[str, str]:
        """name -> path, for reverse()/{% url %}-style references."""

    def references(self, repo_root: str, config: dict) -> tuple[list[Symbol], list[Edge]]:
        """Server-side and template-side references TO routes."""

    def templates(self, repo_root: str, config: dict) -> list[str]:
        """Files to scan for DOM attributes and inline script/style."""
```

Everything downstream — the matcher, the classifier, the DOM/CSS extractors, the map, the
probe, provenance — is untouched. `UrlIndex` already resolves a concrete path against
parameterised routes, and `<int:pk>` versus `:id` versus `{id}` is a converter table, not a
redesign.

Detection is by artefact, not by asking:

| adapter | detected by |
|---|---|
| django | `manage.py`, `ROOT_URLCONF`, a `urls.py` |
| rails | `config/routes.rb` |
| laravel | `routes/web.php`, `artisan` |
| fastapi | `@app.get(` / `@router.get(` |
| express | `app.get(` / `router.get(` on an express import |

## What each adapter actually costs

The Django adapter is two files and about 500 lines, and most of that is Django's own
oddities — the URLconf tree, converters, `include()`, namespaces. The next ones are smaller
because the machinery exists:

- **Rails** — `config/routes.rb` is a DSL; `resources :books` expands to seven routes by a
  known table. ERB templates go straight into the existing template scanner.
- **Laravel** — `Route::get('/x', [C::class, 'm'])` is a flat, greppable list. Blade
  templates likewise.
- **FastAPI** — decorators on functions, readable with the `ast` module already in use.
- **Express** — `app.get('/x', handler)`, readable with the acorn parser already bundled.

**FastAPI is the cheapest first move** — same language, same `ast`, and it validates the
adapter seam without a new parser. Rails is the most convincing second, because it proves
the idea is not "Python tooling."

## The public repositioning

### Name and tagline

Keep `seamcheck`. It never said Django.

> **Find the code your project no longer connects to — and the connections it only thinks
> it has.**

stays true and framework-neutral. What changes is the line under it and the first paragraph.

### The README's first screen

Lead with the bug, not the framework. Today's opening — *"Your AI wrote 400 lines. Which of
them are actually wired to anything?"* — is already framework-free and should stay. Add,
immediately below, a plain statement of scope so nobody has to guess:

> Reads **Django** today. The frontend half — JavaScript, CSS, DOM, templates — is
> framework-agnostic already, and so is the runtime probe. Adding a backend is one adapter.

That sentence does three jobs: it tells a Rails user why it does not work yet, it tells a
Django user this is not a toy, and it is true.

### GitHub metadata

- **About:** "Find what your code no longer connects to — and the connections it only thinks
  it has. A frontend↔backend seam checker with a visual map."
- **Topics:** `static-analysis`, `dead-code`, `code-map`, `codebase-visualization`,
  `django`, `javascript`, `css`, `mcp`, `ai-code-review`, `developer-tools`,
  `code-quality`, `technical-debt`
- **pyproject keywords:** drop the implication that this is Django-only; keep `django` as
  one entry among several rather than the headline.

### What must NOT change

The `uncertain` explanation, the measured precision, and the "what it can't do" section.
Those are the reason anyone would believe the rest, and a repositioning that quietly drops
the limitations is how a good tool becomes an untrusted one.

## Order of work

1. **Land the runtime probe** (built, needs a live run). Framework-agnostic, closes the
   statically-unreachable third, and pays regardless of what happens next.
2. **Extract the `ServerAdapter` seam** with Django as the only implementation. No new
   behaviour, no new claims — a refactor with the existing 600 tests as the guard.
3. **FastAPI adapter.** Cheapest possible proof the seam is real.
4. **Reposition publicly**, once (2) and (3) are true. Not before.
5. **Rails or Laravel**, the convincing one.
6. **The corpus** — now multi-framework, which is what makes it worth building at all:
   *"N repos across 3 frameworks, M symbols, X% precision."*

## The corpus, revised

The earlier corpus plan assumed Django. Multi-framework changes what it proves:

- **Per-framework precision**, so a Rails user sees a Rails number rather than a Django one.
- **Adapter detection accuracy** — how often the right adapter is picked with no config.
- The two oracles stay: git history as a self-labelling answer key (*did the maintainers
  later delete what we called unused?*) and mutation testing for recall.

Static mode already makes cloning viable for Django. Each new adapter needs its own static
reader, because none of them can be imported either.
