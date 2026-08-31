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
import pathlib
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
CORPUS = ROOT / "corpus"

# Chosen for SHAPES not for stars: a template, a production app, a tutorial-shaped app.
# Every entry is permissively licensed so quoting a line in a bug report is uncontroversial.
REPOS = [
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
    from seamcheck.adapters import select
    from seamcheck.graph import Status
    from seamcheck.progress import null

    row: dict = {"name": repo["name"], "expected": repo["adapter"]}
    started = time.time()
    try:
        adapter, confidence = select(str(target), {})
        row["detected"] = adapter.name
        row["confidence"] = confidence
        server = adapter.scan(str(target), {"static_urls": True}, null())
        routes = [s for s in server.symbols if s.kind == "url"]
        row["routes"] = len(routes)
        row["views"] = sum(1 for s in server.symbols if s.kind == "view")
        # A route the adapter could not place is the honest outcome, not a failure: it
        # means the path is decided at runtime. Tracked because a RISING share of these
        # across a corpus is the signal that an adapter is missing a mounting idiom.
        row["uncertain"] = sum(1 for s in routes if s.status is Status.UNCERTAIN)
        row["gate1"] = "ok"
        row["gate2"] = "ok" if row["routes"] else "NO ROUTES"
        counts = collections.Counter(s.status.value for s in server.symbols)
        row["by_status"] = dict(counts)
        # Count source files in the language the adapter actually reads. Counting only
        # *.py made every JavaScript repo look like it had zero source and tripped the
        # implausibility gate on a correct scan - the gate was measuring the harness.
        patterns = {
            "django": ("*.py",), "fastapi": ("*.py",),
            "express": ("*.js", "*.mjs", "*.cjs", "*.ts"),
            "nextjs": ("*.ts", "*.tsx", "*.js", "*.jsx"),
        }.get(row["detected"], ("*.py", "*.js", "*.ts"))
        row["files"] = sum(
            1 for pattern in patterns for path in target.rglob(pattern)
            if "node_modules" not in path.parts
        )
        row["gate4"] = "ok" if row["routes"] < max(row["files"] * 20, 100) else "IMPLAUSIBLE"
    except Exception as error:  # noqa: BLE001 - a crash IS the result of gate 1
        row["gate1"] = f"CRASH: {type(error).__name__}: {str(error)[:90]}"
    row["seconds"] = round(time.time() - started, 1)
    return row


def scan(only: str | None = None) -> None:
    rows = [scan_one(repo) for repo in REPOS if not only or only == repo["name"]]
    width = max((len(row["name"]) for row in rows), default=10)
    print(f"\n  {'repo':<{width}}  {'detected':<10} {'routes':>7} {'views':>6} "
          f"{'unsure':>7} {'py':>6} {'sec':>5}  gates")
    print("  " + "-" * (width + 60))
    for row in rows:
        if row.get("gate1") != "ok":
            print(f"  {row['name']:<{width}}  {row['gate1']}")
            continue
        mark = "1" + ("2" if row["gate2"] == "ok" else "-") + ("4" if row["gate4"] == "ok" else "-")
        flag = "" if row["detected"] == row["expected"] else f"  (expected {row['expected']})"
        print(f"  {row['name']:<{width}}  {row['detected']:<10} {row['routes']:>7,} "
              f"{row['views']:>6,} {row['uncertain']:>7,} {row['files']:>6,} "
              f"{row['seconds']:>5}  {mark}{flag}")
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
