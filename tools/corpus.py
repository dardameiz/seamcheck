#!/usr/bin/env python3
"""Clone real repositories and scan them, so a claim about accuracy has a corpus behind it.

Doing this by hand is a day that leaves no artefact. The point of a script is that every
future adapter is a re-run rather than a fresh day, and that the numbers in the README come
from something anyone can reproduce.

Four gates, in order, per `docs/specs/validation.md`. A repository that fails an early gate
tells us more than one that sails through - gate 1 failing is a project shape never seen
before, which is the entire reason to stop polishing against a single codebase.

  1. It runs.        Detection picks an adapter; the scan finishes without an exception.
  2. It finds routes. Compared against a hand count, or against import mode where it installs.
  3. Findings are plausible.  Hand-check a sample - the protocol that took the reference
                              project from 73% to 98.3%.
  4. Volume is sane.  4,000 findings on a 5,000-line project means an extractor misfires,
                      whatever the sample precision says.

## Which repositories are worth adding

The corpus is being grown towards ~100-150 projects, and the selection rule is not "popular"
or "big". It is: **does this repository contain BOTH SIDES of a seam?**

Seamcheck checks boundaries between languages. A repository that is only a JSON API has no
boundary in it - the other half lives in some frontend repo nobody cloned - and scanning it
measures the route reader and nothing else. Measured: paperless-ngx, a Django REST backend
with its Angular client in-tree but built separately, yields 522 symbols. Pretix, a Django
app that renders its own templates, yields 16,133 from a comparable codebase.

So, in priority order:

  1. **Full-stack monoliths.** Server-rendered templates plus their own CSS and JS in one
     tree - Django, Flask, Rails-shaped Express, Next.js used as a full application. These
     are where every cross-language check has something to check.
  2. **Full-stack monorepos.** Backend and frontend in one repository, even in separate
     packages. The seam is real and crossing it is the interesting case.
  3. **API-only services** - a few per backend, to keep the route readers honest, and no
     more. They cannot exercise the DOM, CSS or template halves at all.
  4. **Frontend-only apps** - a few, for the same reason in reverse.

A backend is not "covered" because its adapter runs. It is covered when the corpus holds
enough full-stack projects in it that coverage and precision both stop moving when another
one is added.

Nothing here publishes a repository's findings. Aggregate numbers are fine, naming what was
scanned is fine, naming a repository beside its findings is not - at 98.3% precision roughly
one finding in sixty is wrong, and a wrong finding published against a named project is a
public accusation about working code.

Usage:
    python tools/corpus.py clone          # clone or update every repo in the list
    python tools/corpus.py scan           # scan them all, print the table
    python tools/corpus.py scan --only dispatch
    python tools/corpus.py entries        # full scan + page entries on the phase-1 set
    python tools/corpus.py entries --code ../base --out before.json   # the same, on other code
    python tools/corpus.py entries --path ../some-project             # a project outside the corpus
    python tools/corpus.py entries-compare before.json after.json
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import pathlib
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
# OUTSIDE the repository, deliberately. Cloned third-party code inside the project is
# scanned by the project's own tests - nine million lines of somebody else's source turned
# a 75-second suite into four minutes - and it is not part of this codebase in any sense.
CORPUS = pathlib.Path(
    os.environ.get("SEAMCHECK_CORPUS", ROOT.parent / "seamcheck-corpus")
)

# Chosen for SHAPES not for stars: a template, a production app, a tutorial-shaped app.
# Every entry is permissively licensed so quoting a line in a bug report is uncontroversial.
REPOS = [
    # --- Django, the focus backend -------------------------------------------------
    # Ordered small to large on purpose. The working method is one repository at a
    # time: scan it, hand-check what it claims, fix the rule the mistake belongs to,
    # only then move on. Starting on a 400k-line codebase means never finishing the
    # first one.
    {
        "name": "healthchecks",
        "url": "https://github.com/healthchecks/healthchecks",
        "adapter": "django",
        "why": "small, clean, classic Django templates and vanilla JS - the shape every"
               " rule should handle before any exotic one",
    },
    {
        "name": "mezzanine",
        "url": "https://github.com/stephenmcd/mezzanine",
        "adapter": "django",
        "why": "older template-heavy CMS - the idioms a 2012 Django app still uses",
    },
    {
        "name": "django-cms",
        "url": "https://github.com/django-cms/django-cms",
        "adapter": "django",
        "why": "templates assembled at runtime from placeholders - the hardest case for"
               " deciding what a template actually renders",
    },
    {
        "name": "netbox",
        "url": "https://github.com/netbox-community/netbox",
        "adapter": "django",
        "why": "Django templates plus HTMX - attributes drive behaviour, so a dead"
               " data-attribute is a dead feature",
    },
    {
        "name": "readthedocs.org",
        "url": "https://github.com/readthedocs/readthedocs.org",
        "adapter": "django",
        "why": "Django templates with a separate built frontend - the two-world case",
    },
    {
        "name": "weblate",
        "url": "https://github.com/WeblateOrg/weblate",
        "adapter": "django",
        "why": "large, heavily internationalised - templates where most strings are tags",
    },
    {
        "name": "pretix",
        "url": "https://github.com/pretix/pretix",
        "adapter": "django",
        "why": "large Django with plugins - routes and templates contributed by installed"
               " apps rather than declared in one place",
    },
    {
        "name": "paperless-ngx",
        "url": "https://github.com/paperless-ngx/paperless-ngx",
        "adapter": "django",
        "why": "Django REST plus an Angular SPA - the contrast case, where there are"
               " almost no templates and the seam is entirely API-to-client",
    },
    {
        "name": "djangoproject.com",
        "url": "https://github.com/django/djangoproject.com",
        "adapter": "django",
        "why": "the Django project's own site - idiomatic by definition, and small enough"
               " to hand-check end to end",
    },
    {
        "name": "bookwyrm",
        "url": "https://github.com/bookwyrm-social/bookwyrm",
        "adapter": "django",
        "why": "templates plus progressive-enhancement JS - data attributes doing real work",
    },
    {
        "name": "wger",
        "url": "https://github.com/wger-project/wger",
        "adapter": "django",
        "why": "templates, HTMX and web components together in one app",
    },
    {
        "name": "misago",
        "url": "https://github.com/rafalp/Misago",
        "adapter": "django",
        "why": "Django forum with a heavy JS frontend in the same tree - the seam is inside"
               " one repository",
    },
    {
        "name": "inventree",
        "url": "https://github.com/inventree/InvenTree",
        "adapter": "django",
        "why": "large Django with templates and a big hand-written JS layer",
    },
    {
        "name": "django-debug-toolbar",
        "url": "https://github.com/django-commons/django-debug-toolbar",
        "adapter": "django",
        "why": "small, and its whole product IS templates plus CSS plus JS - dense seams"
               " per line of code",
    },
    # --- queued: full-stack projects for the backends after Django ----------------
    # Listed, not yet cloned. Django is being finished first; these are chosen already so
    # the next backend starts with a corpus rather than a search.
    {
        "name": "searxng",
        "url": "https://github.com/searxng/searxng",
        "adapter": "flask",
        "why": "Flask rendering Jinja templates with its own CSS and JS - the Flask"
               " equivalent of the Django projects above",
    },
    {
        "name": "indico",
        "url": "https://github.com/indico/indico",
        "adapter": "flask",
        "why": "large Flask, templates plus a big hand-written frontend in the same tree",
    },
    {
        "name": "etherpad-lite",
        "url": "https://github.com/ether/etherpad-lite",
        "adapter": "express",
        "why": "Express serving its own frontend - both halves in one repository, which is"
               " rare enough among Node projects to be worth having",
    },
    {
        "name": "plane",
        "url": "https://github.com/makeplane/plane",
        "adapter": "nextjs",
        "why": "Next.js frontend with a Django backend in the same repository - the"
               " cross-LANGUAGE seam, which is the case this tool exists for",
    },
    {
        "name": "formbricks",
        "url": "https://github.com/formbricks/formbricks",
        "adapter": "nextjs",
        "why": "Next.js full-stack: routes and the pages calling them in one tree",
    },
    {
        "name": "openstatus",
        "url": "https://github.com/openstatusHQ/openstatus",
        "adapter": "nextjs",
        "why": "Next.js monorepo, small enough to hand-check completely",
    },
    {
        "name": "twenty",
        "url": "https://github.com/twentyhq/twenty",
        "adapter": "nestjs",
        "why": "NestJS server and a React client in one repository - NestJS is the weakest"
               " backend here and has no full-stack project in the corpus at all",
    },
    {
        "name": "vendure",
        "url": "https://github.com/vendure-ecommerce/vendure",
        "adapter": "nestjs",
        "why": "NestJS with an admin UI in-tree - a second shape for the weakest reader",
    },
    # --- deliberately large, to find what only shows up at scale -------------------
    {
        "name": "sentry",
        "url": "https://github.com/getsentry/sentry",
        "adapter": "django",
        "why": "one of the largest Django apps in the open - thousands of routes",
    },
    {
        "name": "saleor",
        "url": "https://github.com/saleor/saleor",
        "adapter": "django",
        "why": "large Django, GraphQL-first - few REST routes by design",
    },
    {
        "name": "saleor-dashboard",
        "url": "https://github.com/saleor/saleor-dashboard",
        "adapter": "django",
        "why": "a React SPA with NO backend - the GraphQL client half, and the case where "
               "no adapter fits and the fallback is honest about finding nothing",
    },
    {
        "name": "cal.com",
        "url": "https://github.com/calcom/cal.com",
        "adapter": "nextjs",
        "why": "a very large Next.js monorepo - the hardest routing tree in the corpus",
    },
    {
        "name": "immich",
        "url": "https://github.com/immich-app/immich",
        "adapter": "nestjs",
        "why": "large production NestJS, TypeScript throughout",
    },
    {
        "name": "n8n",
        "url": "https://github.com/n8n-io/n8n",
        "adapter": "express",
        "why": "a very large TypeScript monorepo on Express",
    },
    {
        "name": "open-webui",
        "url": "https://github.com/open-webui/open-webui",
        "adapter": "fastapi",
        "why": "large FastAPI backend with a Svelte front end",
    },
    {
        "name": "redash",
        "url": "https://github.com/getredash/redash",
        "adapter": "flask",
        "why": "a large production Flask app - blueprints and Flask-RESTful resources",
    },
    {
        "name": "ctfd",
        "url": "https://github.com/CTFd/CTFd",
        "adapter": "flask",
        "why": "Flask with many blueprints registered from a factory function",
    },
    {
        "name": "flaskbb",
        "url": "https://github.com/flaskbb/flaskbb",
        "adapter": "flask",
        "why": "Flask the conventional way - the layout a tutorial produces",
    },
    {
        "name": "full-stack-fastapi-template",
        "url": "https://github.com/fastapi/full-stack-fastapi-template",
        "adapter": "fastapi",
        "why": "the official template - the layout most new FastAPI projects start from",
    },
    {
        "name": "dispatch",
        "url": "https://github.com/Netflix/dispatch",
        "adapter": "fastapi",
        "why": "a large production FastAPI app with deeply nested routers",
    },
    {
        "name": "fastapi-realworld",
        "url": "https://github.com/nsidnev/fastapi-realworld-example-app",
        "adapter": "fastapi",
        "why": "the RealWorld spec - a different idiom again, routers by feature",
    },
    {
        "name": "parse-server",
        "url": "https://github.com/parse-community/parse-server",
        "adapter": "express",
        "why": "a large production Express app, JavaScript rather than TypeScript",
    },
    {
        "name": "ghost",
        "url": "https://github.com/TryGhost/Ghost",
        "adapter": "express",
        "why": "a very large Express monorepo - the hardest shape to detect correctly",
    },
    {
        "name": "dub",
        "url": "https://github.com/dubinc/dub",
        "adapter": "nextjs",
        "why": "a real Next.js App Router product - route groups and dynamic segments",
    },
    {
        "name": "documenso",
        "url": "https://github.com/documenso/documenso",
        "adapter": "nextjs",
        "why": "a Next.js monorepo - apps/ workspaces, the harder detection shape",
    },
    {
        "name": "excalidraw",
        "url": "https://github.com/excalidraw/excalidraw",
        "adapter": "nextjs",
        "why": "React+TS; its only Next app is an EXAMPLE - the detection edge case",
    },
    {
        "name": "nestjs-realworld",
        "url": "https://github.com/lujakob/nestjs-realworld-example-app",
        "adapter": "nestjs",
        "why": "NestJS decorators - impossible to read before the parser work",
    },
    {
        "name": "nodebb",
        "url": "https://github.com/NodeBB/NodeBB",
        "adapter": "express",
        "why": "Express with routes registered through helper functions, not decorators",
    },
]


_VENDORED = {"node_modules", ".git", "dist", "build", "coverage", ".next", "vendor",
             "site-packages", "__pycache__", ".venv", "venv"}


# Phase 1 of docs/plans/2026-09-15-entry-sources-design.md, measured where `scan` cannot
# look. The repositories that design doc measured, minus the ones whose full scan is too
# slow or too large for a before-and-after run on one machine (sentry, n8n, saleor, ghost,
# misago, netbox, weblate, pretix). Folder names under CORPUS, not REPOS entries: three of
# the Next.js ones are cloned there without being in REPOS.
ENTRY_REPOS = (
    "cal.com", "dub", "commerce", "documenso", "nextjs-subscription-payments", "platforms",
    "healthchecks", "djangoproject.com", "wger", "django-debug-toolbar", "paperless-ngx", "bookwyrm",
    "full-stack-fastapi-template", "fastapi-realworld", "dispatch", "open-webui",
    "node-express-boilerplate", "node-express-realworld-example-app", "parse-server", "nodebb",
)


def entries_row(target: pathlib.Path) -> dict:
    """One repository: a full scan, its entries, what they reach, and the buckets.

    Works on code from before seamcheck/entries/ existed too - there the pages are
    `page_files()`'s keys - so one command measures both sides of the change.
    """
    import seamcheck
    from seamcheck import api
    from seamcheck.mapdata import build_map
    from seamcheck.progress import null

    row: dict = {"name": target.name, "code": os.path.dirname(os.path.dirname(seamcheck.__file__))}
    started = time.time()
    try:
        graph = api.scan(str(target), null())
        if hasattr(api, "page_entries"):
            found = api.page_entries(str(target), graph)
            pages = api._page_files_for(str(target), graph, found)
            names = api._names_from_entries(found)
            row["kinds"] = dict(collections.Counter(entry.kind for entry in found))
            row["pages"] = sorted(entry.key for entry in found if entry.kind == "page")
        else:
            from seamcheck.pagenames import page_names

            pages = api.page_files(str(target))
            names = page_names(str(target), api._config(), graph)
            row["kinds"] = {"page": len(pages)}
            row["pages"] = sorted(pages)
        built = build_map(graph, pages, git_sha="corpus", names=names)
        reached = set().union(*pages.values()) if pages else set()
        row["symbols"] = len(graph.symbols)
        row["on_page"] = sum(1 for symbol in graph.symbols if symbol.file in reached)
        # Every page that is not a bucket. Literal prefixes, not mapdata.BUCKET_PREFIXES: code
        # from before the change has no such name, and only "unreached:" buckets.
        row["drawn"] = sum(1 for page in built.pages
                           if not page.page.startswith(("unreached:", "undrawn:")))
        row["buckets"] = {page.page: len(page.nodes) for page in built.pages
                          if page.page.startswith(("unreached:", "undrawn:"))}
        row["gate"] = "ok"
    except Exception as error:  # noqa: BLE001 - a crash IS the result
        row["gate"] = f"CRASH: {type(error).__name__}: {str(error)[:90]}"
    row["seconds"] = round(time.time() - started, 1)
    return row


def _count_lines(root: pathlib.Path, patterns: tuple[str, ...]) -> int:
    total = 0
    for pattern in patterns:
        for path in root.rglob(pattern):
            if any(part in _VENDORED for part in path.parts):
                continue
            try:
                with path.open("rb") as handle:
                    total += sum(1 for _ in handle)
            except OSError:
                continue
    return total


def _run(command: list[str], cwd: pathlib.Path | None = None, timeout: int = 600):
    return subprocess.run(
        command, cwd=cwd, capture_output=True, text=True, timeout=timeout, check=False
    )


def clone(only: str | None = None) -> None:
    CORPUS.mkdir(exist_ok=True)
    for repo in REPOS:
        if only and only != repo["name"]:
            continue
        target = CORPUS / repo["name"]
        if target.exists():
            print(f"  {repo['name']:<32} already cloned")
            continue
        print(f"  {repo['name']:<32} cloning ...", flush=True)
        # Shallow: history is a separate oracle and costs bandwidth we do not need yet.
        result = _run(["git", "clone", "--depth", "1", repo["url"], str(target)])
        print(f"  {repo['name']:<32} {'ok' if result.returncode == 0 else 'FAILED'}")
        if result.returncode != 0:
            print("    " + (result.stderr or "").strip().splitlines()[-1:][0][:120])


def scan_one(repo: dict) -> dict:
    """Gates 1, 2 and 4. Gate 3 is a human reading a sample and cannot be automated."""
    target = CORPUS / repo["name"]
    if not target.is_dir():
        return {"name": repo["name"], "gate1": "not cloned"}

    sys.path.insert(0, str(ROOT))
    from seamcheck.adapters import select_all
    from seamcheck.graph import Status
    from seamcheck.progress import null

    row: dict = {"name": repo["name"], "expected": repo["adapter"]}
    started = time.time()
    try:
        # Every adapter that confidently fits, because a large monorepo is not one
        # application: cal.com serves a Next.js front end and a NestJS API, and immich
        # pairs a NestJS server with a FastAPI machine-learning service.
        chosen = select_all(str(target), {})
        row["detected"] = "+".join(adapter.name for adapter, _ in chosen)
        row["confidence"] = chosen[0][1]
        symbols = []
        seen = set()
        for adapter, _ in chosen:
            for symbol in adapter.scan(str(target), {"static_urls": True}, null()).symbols:
                if symbol.id not in seen:
                    seen.add(symbol.id)
                    symbols.append(symbol)
        # The transports, which no route list contains and which are the reason a repo
        # can be "fully read" and still have a whole API invisible.
        from seamcheck.extractors.celery_extractor import extract_celery
        from seamcheck.extractors.graphql_extractor import extract_graphql
        from seamcheck.extractors.stripe_extractor import extract_stripe

        for extract in (extract_graphql, extract_celery, extract_stripe):
            try:
                extra, _ = extract(str(target))
            except Exception:  # noqa: BLE001 - a transport must not fail the scan
                extra = []
            for symbol in extra:
                if symbol.id not in seen:
                    seen.add(symbol.id)
                    symbols.append(symbol)
        row["graphql"] = sum(1 for s in symbols if s.kind.startswith("graphql"))
        row["celery"] = sum(1 for s in symbols if s.kind.startswith("celery"))
        row["stripe"] = sum(1 for s in symbols if s.kind.startswith("stripe"))
        # The data layer, which nothing here measured: the ORM lens landed on 17 Django
        # repositories in this corpus and the totals did not move, because every column
        # counted routes. A lens no column watches is a lens that can regress silently.
        row["tables"] = sum(1 for s in symbols if s.kind == "db_table")
        row["queries"] = sum(1 for s in symbols
                             if s.kind in ("db_table_use", "db_column_use"))
        row["badcols"] = sum(1 for s in symbols
                             if s.kind == "db_column_use" and s.status is Status.UNRESOLVED)

        routes = [s for s in symbols if s.kind == "url"]
        row["routes"] = len(routes)
        row["views"] = sum(1 for s in symbols if s.kind == "view")
        # A route the adapter could not place is the honest outcome, not a failure: it
        # means the path is decided at runtime. Tracked because a RISING share of these
        # across a corpus is the signal that an adapter is missing a mounting idiom.
        row["uncertain"] = sum(1 for s in routes if s.status is Status.UNCERTAIN)
        row["gate1"] = "ok"
        row["gate2"] = "ok" if row["routes"] else "NO ROUTES"
        counts = collections.Counter(s.status.value for s in symbols)
        row["by_status"] = dict(counts)
        # Count source files in the language the adapter actually reads. Counting only
        # *.py made every JavaScript repo look like it had zero source and tripped the
        # implausibility gate on a correct scan - the gate was measuring the harness.
        primary = row["detected"].split("+")[0]
        patterns = {
            "django": ("*.py",), "fastapi": ("*.py",),
            "express": ("*.js", "*.mjs", "*.cjs", "*.ts"),
            "nextjs": ("*.ts", "*.tsx", "*.js", "*.jsx"),
        }.get(primary, ("*.py", "*.js", "*.ts", "*.tsx"))
        row["files"] = sum(
            1 for pattern in patterns for path in target.rglob(pattern)
            if "node_modules" not in path.parts
        )
        row["gate4"] = "ok" if row["routes"] < max(row["files"] * 20, 100) else "IMPLAUSIBLE"
        # Lines of source, so a claim about scale has a number behind it rather than an
        # adjective. Counted in the languages the adapter reads, excluding vendored trees.
        row["lines"] = _count_lines(target, patterns)
    except Exception as error:  # noqa: BLE001 - a crash IS the result of gate 1
        row["gate1"] = f"CRASH: {type(error).__name__}: {str(error)[:90]}"
    row["seconds"] = round(time.time() - started, 1)
    return row


def scan(only: str | None = None) -> None:
    rows = [scan_one(repo) for repo in REPOS if not only or only == repo["name"]]
    width = max((len(row["name"]) for row in rows), default=10)
    detected_width = max((len(row.get("detected", "")) for row in rows), default=10)
    print(f"\n  {'repo':<{width}}  {'detected':<{detected_width}} {'routes':>7} {'views':>6} "
          f"{'unsure':>7} {'gql':>6} {'celery':>7} {'stripe':>7} {'tables':>7} {'queries':>8} "
          f"{'badcol':>7} {'lines':>10} {'sec':>6}  gates")
    print("  " + "-" * (width + 100))
    for row in rows:
        if row.get("gate1") != "ok":
            print(f"  {row['name']:<{width}}  {row['gate1']}")
            continue
        mark = "1" + ("2" if row["gate2"] == "ok" else "-") + ("4" if row["gate4"] == "ok" else "-")
        flag = "" if row["expected"] in row["detected"] else f"  (expected {row['expected']})"
        print(f"  {row['name']:<{width}}  {row['detected']:<{detected_width}} {row['routes']:>7,} "
              f"{row['views']:>6,} {row['uncertain']:>7,} {row.get('graphql', 0):>6,} "
              f"{row.get('celery', 0):>7,} {row.get('stripe', 0):>7,} "
              f"{row.get('tables', 0):>7,} {row.get('queries', 0):>8,} "
              f"{row.get('badcols', 0):>7,} "
              f"{row['lines']:>10,} {row['seconds']:>6}  {mark}{flag}")
    done = [row for row in rows if row.get("gate1") == "ok"]
    if done:
        print("  " + "-" * (width + 100))
        print(f"  {'TOTAL':<{width}}  {len(done):<10} "
              f"{sum(r['routes'] for r in done):>7,} {sum(r['views'] for r in done):>6,} "
              f"{sum(r['uncertain'] for r in done):>7,} "
              f"{sum(r.get('graphql', 0) for r in done):>6,} "
              f"{sum(r.get('celery', 0) for r in done):>7,} "
              f"{sum(r.get('stripe', 0) for r in done):>7,} "
              f"{sum(r.get('tables', 0) for r in done):>7,} "
              f"{sum(r.get('queries', 0) for r in done):>8,} "
              f"{sum(r.get('badcols', 0) for r in done):>7,} "
              f"{sum(r['lines'] for r in done):>10,}")
    (CORPUS / "results.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
    print(f"\n  wrote {CORPUS / 'results.json'}")
    print("  Gate 3 - are the findings plausible - is a human reading a sample.")


def entries(names, code: pathlib.Path, out: pathlib.Path, path: pathlib.Path | None = None) -> None:
    """`entries_row` for each repository, each in its own process on the seamcheck at `code`.

    A process per repository because a full scan of cal.com or dub holds a lot of parse
    cache, and one that dies costs its own row rather than the run. PYTHONPATH decides
    which seamcheck the child imports, so `--code` pointing at a snapshot of main measures
    main; each row records the directory it actually imported from.
    """
    targets = [path] if path else [CORPUS / name for name in names]
    rows = []
    for target in targets:
        if not target.is_dir():
            rows.append({"name": target.name, "gate": "not cloned"})
            print(f"  {target.name:<36} not cloned")
            continue
        print(f"  {target.name:<36} scanning ...", end="", flush=True)
        done = subprocess.run(
            [sys.executable, str(pathlib.Path(__file__).resolve()), "entries-one", str(target)],
            env={**os.environ, "PYTHONPATH": str(code)}, capture_output=True, text=True, check=False,
        )
        lines = done.stdout.strip().splitlines()
        try:
            row = json.loads(lines[-1])
        except (IndexError, ValueError):
            tail = (done.stderr or "").strip().splitlines()[-1:] or [f"exit {done.returncode}"]
            row = {"name": target.name, "gate": f"CRASH: {tail[0][:90]}"}
        rows.append(row)
        kinds = " ".join(f"{kind} {count}" for kind, count in sorted(row.get("kinds", {}).items()))
        print(f"\r  {target.name:<36} {row['gate'][:40]:<12} {kinds:<32} "
              f"on page {row.get('on_page', 0):>7,} / {row.get('symbols', 0):>7,}  "
              f"{row.get('seconds', '')}s")
    out.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    print(f"\n  wrote {out}")


def entries_compare(before_file: pathlib.Path, after_file: pathlib.Path) -> None:
    """What moved between two `entries` runs, per repository."""
    before = {row["name"]: row for row in json.loads(before_file.read_text(encoding="utf-8"))}
    after = {row["name"]: row for row in json.loads(after_file.read_text(encoding="utf-8"))}
    totals = [0, 0, 0, 0]  # on page before, symbols before, on page after, symbols after
    for name, now in after.items():
        was = before.get(name, {})
        if now.get("gate") != "ok" or was.get("gate") != "ok":
            print(f"  {name:<36} before: {was.get('gate', 'absent')}   after: {now.get('gate')}")
            continue
        gone = sorted(set(was["pages"]) - set(now["pages"]))
        new = sorted(set(now["pages"]) - set(was["pages"]))
        pages = "pages identical" if not gone and not new else f"pages -{len(gone)} +{len(new)}"
        kinds = " ".join(f"{kind} {count}" for kind, count in sorted(now["kinds"].items()))
        print(f"  {name:<36} {pages:<18} entries: {kinds}")
        print(f"      drawn {was['drawn']} -> {now['drawn']}   "
              f"on page {was['on_page']:,}/{was['symbols']:,} -> {now['on_page']:,}/{now['symbols']:,}")
        keys = sorted(set(was["buckets"]) | set(now["buckets"]))
        print("      buckets " + ", ".join(
            f"{key} {was['buckets'].get(key, 0):,} -> {now['buckets'].get(key, 0):,}" for key in keys))
        if gone:
            print(f"      pages gone: {', '.join(gone[:6])}{' ...' if len(gone) > 6 else ''}")
        if new:
            print(f"      pages new:  {', '.join(new[:6])}{' ...' if len(new) > 6 else ''}")
        totals = [totals[0] + was["on_page"], totals[1] + was["symbols"],
                  totals[2] + now["on_page"], totals[3] + now["symbols"]]
    if totals[1] and totals[3]:
        print(f"\n  symbols on a page, all repositories: {100 * totals[0] / totals[1]:.1f}% -> "
              f"{100 * totals[2] / totals[3]:.1f}%")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command",
                        choices=["clone", "scan", "list", "entries", "entries-one", "entries-compare"])
    parser.add_argument("targets", nargs="*",
                        help="entries-one: a repository directory; entries-compare: BEFORE.json AFTER.json")
    parser.add_argument("--only", help="just this repo")
    parser.add_argument("--path", help="entries: measure this directory instead of the corpus set")
    parser.add_argument("--code", default=str(ROOT), help="entries: the seamcheck checkout to measure with")
    parser.add_argument("--out", help="entries: where to write the rows (default: <corpus>/entries.json)")
    args = parser.parse_args()
    if args.command == "list":
        for repo in REPOS:
            print(f"  {repo['name']:<32} {repo['adapter']:<10} {repo['why']}")
    elif args.command == "clone":
        clone(args.only)
    elif args.command == "entries":
        names = [args.only] if args.only else list(ENTRY_REPOS)
        out = pathlib.Path(args.out) if args.out else CORPUS / "entries.json"
        entries(names, pathlib.Path(args.code).resolve(), out,
                pathlib.Path(args.path).resolve() if args.path else None)
    elif args.command == "entries-one":
        print(json.dumps(entries_row(pathlib.Path(args.targets[0]).resolve())))
    elif args.command == "entries-compare":
        entries_compare(pathlib.Path(args.targets[0]), pathlib.Path(args.targets[1]))
    else:
        scan(args.only)


if __name__ == "__main__":
    main()
