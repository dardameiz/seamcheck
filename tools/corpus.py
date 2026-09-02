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
          f"{'unsure':>7} {'gql':>6} {'celery':>7} {'stripe':>7} {'lines':>10} {'sec':>6}  gates")
    print("  " + "-" * (width + 76))
    for row in rows:
        if row.get("gate1") != "ok":
            print(f"  {row['name']:<{width}}  {row['gate1']}")
            continue
        mark = "1" + ("2" if row["gate2"] == "ok" else "-") + ("4" if row["gate4"] == "ok" else "-")
        flag = "" if row["expected"] in row["detected"] else f"  (expected {row['expected']})"
        print(f"  {row['name']:<{width}}  {row['detected']:<{detected_width}} {row['routes']:>7,} "
              f"{row['views']:>6,} {row['uncertain']:>7,} {row.get('graphql', 0):>6,} "
              f"{row.get('celery', 0):>7,} {row.get('stripe', 0):>7,} "
              f"{row['lines']:>10,} {row['seconds']:>6}  {mark}{flag}")
    done = [row for row in rows if row.get("gate1") == "ok"]
    if done:
        print("  " + "-" * (width + 76))
        print(f"  {'TOTAL':<{width}}  {len(done):<10} "
              f"{sum(r['routes'] for r in done):>7,} {sum(r['views'] for r in done):>6,} "
              f"{sum(r['uncertain'] for r in done):>7,} "
              f"{sum(r.get('graphql', 0) for r in done):>6,} "
              f"{sum(r.get('celery', 0) for r in done):>7,} "
              f"{sum(r.get('stripe', 0) for r in done):>7,} "
              f"{sum(r['lines'] for r in done):>10,}")
    (CORPUS / "results.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
    print(f"\n  wrote {CORPUS / 'results.json'}")
    print("  Gate 3 - are the findings plausible - is a human reading a sample.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["clone", "scan", "list"])
    parser.add_argument("--only", help="just this repo")
    args = parser.parse_args()
    if args.command == "list":
        for repo in REPOS:
            print(f"  {repo['name']:<32} {repo['adapter']:<10} {repo['why']}")
    elif args.command == "clone":
        clone(args.only)
    else:
        scan(args.only)


if __name__ == "__main__":
    main()
