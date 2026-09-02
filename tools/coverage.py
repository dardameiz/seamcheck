#!/usr/bin/env python3
"""How much of a codebase seamcheck actually has an opinion about, per backend.

The number this project used to quote was precision: of the claims it makes, how many are
true. That is the right number for "can I trust a finding", and it is the wrong number for
"is this tool any use on my stack", because it is silent about everything the tool declined
to judge. A backend where seamcheck answers `uncertain` for every symbol has perfect
precision and is worthless.

The two are separate axes with different denominators, and reporting one as if it were the
other is exactly the kind of claim-without-evidence this codebase exists to stop:

    coverage  = judged / all symbols        <- this file
    precision = true claims / all claims    <- tools/precision.py

Coverage is the honest headline per backend, and it is the one that says what to build
next: the top uncertainty cause for a backend IS the missing extractor, named.

Nothing here publishes a repository's findings - aggregate rates per backend only, per
`docs/specs/validation.md`.

Usage:
    python tools/coverage.py                 # every repo in the corpus
    python tools/coverage.py --only dub      # one repo
    python tools/coverage.py --json out.json
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
CORPUS = pathlib.Path(os.environ.get("SEAMCHECK_CORPUS", ROOT.parent / "seamcheck-corpus"))

sys.path.insert(0, str(ROOT))

# Order matters: a monorepo detects several adapters and is attributed to the first match,
# so the backend that owns the ROUTES is listed before the one that owns the pages.
BACKENDS = ("django", "nestjs", "fastapi", "flask", "express", "nextjs",
            "supabase", "firebase")


def external_roots() -> dict[str, pathlib.Path]:
    """Repositories that are not in the corpus, named by a label file.

    The reference Django project is private and lives outside the corpus, but it is the
    codebase every Django rule was actually written against - leaving it out of the
    coverage table would report a worse Django number than the one the work produced, and
    hide the only repository whose findings anyone has checked line by line.

    Same `_root` key tools/precision.py already honours, so there is one convention.
    """
    found: dict[str, pathlib.Path] = {}
    labels = ROOT / "tools" / "labels"
    for path in sorted(labels.glob("*.json")) if labels.is_dir() else []:
        try:
            root = json.loads(path.read_text(encoding="utf-8")).get("_root")
        except (OSError, ValueError):
            continue
        if isinstance(root, str) and root and pathlib.Path(root).is_dir():
            found[path.stem] = pathlib.Path(root)
    return found


def backend_of(adapters: str) -> str:
    for name in BACKENDS:
        if name in (adapters or ""):
            return name
    return "none"


# Symbols that exist to CARRY EVIDENCE, never to be judged. `class:apply` is a class some
# JavaScript adds to an element: it proves a rule of that name is live, and it is marked
# uncertain by design because there is nothing about that line to claim. Counting these as
# "things the scan failed to judge" is a denominator error, and a large one - a React
# project applies thousands of classes in JS, which dragged Next.js to a reported 2%
# coverage while nothing was actually wrong. Coverage must measure the symbols that were
# ELIGIBLE for a verdict.
EVIDENCE_SUBS = ("class:apply", "class:stem")


# Uncertainty splits in two, and only one half is a to-do list.
#
# INHERENT: the evidence is not in the repository and no amount of work here will put it
# there. A project whose entire stylesheet is a CDN <link>, or whose Bootstrap lives in an
# uncommitted node_modules, cannot have its classes checked by anything that reads source.
# Measured: NetBox 12% and microblog 5% are both at their honest ceiling.
#
# FIXABLE: the evidence IS in the repository and seamcheck cannot read it yet. This is the
# roadmap, and it is the only half worth reporting as a gap.
_INHERENT_MARKERS = (
    "No stylesheet in this repository",
    "loaded from a CDN",
    "framework's own stylesheet",
    "no schema present",
    "not an inventory",
    "renders this id from a form widget",
)


def _is_inherent(note: str) -> bool:
    return any(marker in (note or "") for marker in _INHERENT_MARKERS)


def _is_evidence(symbol) -> bool:
    return symbol.sub.endswith(":evidence") or symbol.sub.startswith(EVIDENCE_SUBS)


def scan_one(path: pathlib.Path) -> dict:
    from seamcheck import api
    from seamcheck.pipeline import LAST_ADAPTERS

    started = time.time()
    try:
        graph = api.scan(str(path))
    except Exception as exc:  # noqa: BLE001 - a crash is a result, not an abort
        return {"name": path.name, "error": f"{type(exc).__name__}: {exc}"[:200],
                "seconds": round(time.time() - started, 1)}

    adapters = "+".join(f"{a.get('name','?')}" for a in LAST_ADAPTERS) or "none"
    by_status: collections.Counter = collections.Counter()
    causes: collections.Counter = collections.Counter()
    evidence = 0
    inherent = 0
    for symbol in graph.symbols:
        if _is_evidence(symbol):
            evidence += 1
            continue
        by_status[symbol.status.value] += 1
        if symbol.status.value == "uncertain":
            if _is_inherent(symbol.note):
                inherent += 1
            words = " ".join((symbol.note or "").split()[:9]) or "(no note recorded)"
            causes[f"{symbol.kind} :: {words}"] += 1
    return {
        "name": path.name,
        "adapters": adapters,
        "backend": backend_of(adapters),
        "symbols": len(graph.symbols) - evidence,
        "evidence_only": evidence,
        "inherent_uncertain": inherent,
        "all_symbols": len(graph.symbols),
        "by_status": dict(by_status),
        "top_causes": dict(causes.most_common(3)),
        "seconds": round(time.time() - started, 1),
    }


def report(rows: list[dict]) -> None:
    ok = [r for r in rows if "error" not in r]
    bad = [r for r in rows if "error" in r]

    agg: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    causes: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    repos: dict[str, list[str]] = collections.defaultdict(list)
    for row in ok:
        b = row["backend"]
        repos[b].append(row["name"])
        agg[b]["symbols"] += row["symbols"]
        agg[b]["inherent"] += row.get("inherent_uncertain", 0)
        for status, n in row["by_status"].items():
            agg[b][status] += n
        for cause, n in row["top_causes"].items():
            causes[b][cause] += n

    print(f"\n  {'backend':<10}{'repos':>6}{'symbols':>9}{'judged':>8}"
          f"{'no oracle':>11}{'fixable':>9}{'ceiling':>9}{'now':>6}")
    print("  " + "-" * 67)
    total: collections.Counter = collections.Counter()
    for b in sorted(agg, key=lambda x: -agg[x]["symbols"]):
        c = agg[b]
        symbols, uncertain, inherent = c["symbols"], c["uncertain"], c["inherent"]
        judged = symbols - uncertain
        fixable = uncertain - inherent
        total["symbols"] += symbols
        total["uncertain"] += uncertain
        total["inherent"] += inherent
        # The ceiling is what coverage could reach if every missing reader were written.
        ceiling = (symbols - inherent)
        print(f"  {b:<10}{len(repos[b]):>6}{symbols:>9,}{judged:>8,}{inherent:>11,}"
              f"{fixable:>9,}{judged * 100 // max(ceiling, 1):>8}%"
              f"{judged * 100 // max(symbols, 1):>5}%")
    print("  " + "-" * 67)
    judged = total["symbols"] - total["uncertain"]
    ceiling = total["symbols"] - total["inherent"]
    print(f"  {'ALL':<10}{len(ok):>6}{total['symbols']:>9,}{judged:>8,}"
          f"{total['inherent']:>11,}{total['uncertain'] - total['inherent']:>9,}"
          f"{judged * 100 // max(ceiling, 1):>8}%{judged * 100 // max(total['symbols'], 1):>5}%")
    print("\n  ceiling = coverage if every missing READER were written. `no oracle` is"
          "\n  evidence that is not in the repository at all, so it can never be judged"
          "\n  by anything that reads source - it is not a gap, it is the shape of the"
          "\n  project. `fixable` is the to-do list.")

    silent = [r for r in ok if r["symbols"] and
              r["by_status"].get("uncertain", 0) == r["symbols"]]
    if silent:
        print(f"\n  {len(silent)} repo(s) where seamcheck had NO opinion at all:")
        for r in sorted(silent, key=lambda r: -r["symbols"]):
            print(f"    {r['name']:<34}{r['symbols']:>7,} symbols   {r['adapters']}")

    print("\n  What to build next, per backend - the top uncertainty cause IS the")
    print("  missing extractor:")
    for b in sorted(causes, key=lambda x: -agg[x]["uncertain"]):
        if not agg[b]["uncertain"]:
            continue
        cause, n = causes[b].most_common(1)[0]
        share = n * 100 // max(agg[b]["uncertain"], 1)
        print(f"    {b:<10}{share:>3}% of its uncertain: {cause}")

    if bad:
        print(f"\n  {len(bad)} repo(s) failed to scan:")
        for r in bad:
            print(f"    {r['name']:<34}{r['error']}")


def markdown(rows: list[dict]) -> None:
    """The README's table, generated. A number nobody can regenerate rots silently."""
    ok = [r for r in rows if "error" not in r]
    agg: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    repos: dict[str, list[str]] = collections.defaultdict(list)
    for row in ok:
        agg[row["backend"]]["symbols"] += row["symbols"]
        agg[row["backend"]]["uncertain"] += row["by_status"].get("uncertain", 0)
        repos[row["backend"]].append(row["name"])

    print("\n--- paste into README ---\n")
    print("| backend | repos scanned | symbols | judged | coverage |")
    print("|---|---:|---:|---:|---:|")
    for b in sorted(agg, key=lambda x: -(agg[x]["symbols"] - agg[x]["uncertain"])):
        c = agg[b]
        judged = c["symbols"] - c["uncertain"]
        print(f"| **{b}** | {len(repos[b])} | {c['symbols']:,} | {judged:,} | "
              f"**{judged * 100 // max(c['symbols'], 1)}%** |")
    total_s = sum(c["symbols"] for c in agg.values())
    total_j = total_s - sum(c["uncertain"] for c in agg.values())
    print(f"| | **{len(ok)}** | **{total_s:,}** | **{total_j:,}** | "
          f"**{total_j * 100 // max(total_s, 1)}%** |")
    print()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", help="just this repo")
    parser.add_argument("--json", help="also write the rows here")
    parser.add_argument("--markdown", action="store_true",
                        help="emit the per-backend table for the README")
    args = parser.parse_args()

    targets = [p for p in sorted(CORPUS.iterdir()) if p.is_dir()]
    names = {p.name for p in targets}
    targets += [p for name, p in external_roots().items() if name not in names]
    targets = [p for p in sorted(targets, key=lambda p: p.name)
               if not args.only or p.name == args.only]
    if not targets:
        print(f"no repos in {CORPUS}", file=sys.stderr)
        return 1

    rows = []
    for path in targets:
        print(f"  {path.name} ...", end="", flush=True)
        row = scan_one(path)
        rows.append(row)
        print(f" {row.get('symbols', 0):>7,} symbols  {row['seconds']:>6}s"
              f"{'  ERROR' if 'error' in row else ''}")
    report(rows)
    if args.markdown:
        markdown(rows)

    out = pathlib.Path(args.json) if args.json else CORPUS / "coverage.json"
    out.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    print(f"\n  wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
