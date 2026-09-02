#!/usr/bin/env python3
"""Gate 7: is it RIGHT? Precision, measured against findings a person has verified.

Recall is answerable with planted bugs. Precision is not: it needs somebody to open the
file and decide whether a claim is true, and that judgement has to be recorded or it is
made again from scratch every time.

Nothing measured precision before this. Every automated gate asked whether the scan ran,
whether the volume was plausible, whether the page rendered - and a matcher recognising
Stripe events by their SHAPE passed all of them while scoring roughly one true positive in
eighteen. It was caught by hand-reading eighteen findings, which is luck rather than
process. This is the process.

Only `unresolved` and `unused` are labelled. Those are the two statuses that make a CLAIM
about the code - something is missing, or something is dead - and a wrong one sends a
person to change working code. `connected` is evidenced by construction and `uncertain`
asserts nothing, so neither can be false in the way that matters.

Labels live in tools/labels/<repo>.json, keyed by symbol id:

    {"url:/api/x": {"verdict": "true", "why": "no handler; grep finds no other caller"},
     "db_table_use:foo:a.js:3": {"verdict": "false", "why": "created by a seed script"}}

Usage:
    python tools/precision.py                      # score every labelled repo
    python tools/precision.py --sample dub 25      # print 25 unlabelled claims to judge
"""

from __future__ import annotations

import json
import pathlib
import random
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
LABELS = pathlib.Path(__file__).resolve().parent / "labels"
CORPUS = pathlib.Path(
    __import__("os").environ.get("SEAMCHECK_CORPUS", ROOT.parent / "seamcheck-corpus")
)

# The statuses that assert something. See the module docstring.
CLAIMS = ("unresolved", "unused")


def _root(repo: str) -> str:
    """Where the repository is. Corpus by default; a label file may say otherwise."""
    override = _labels(repo).get("_root")
    return override if isinstance(override, str) and override else str(CORPUS / repo)


def _claims(repo: str) -> list:
    from seamcheck import api

    graph = api.scan(_root(repo))
    return [s for s in graph.symbols if s.status.value in CLAIMS]


def _labels(repo: str) -> dict:
    path = LABELS / f"{repo}.json"
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def sample(repo: str, count: int) -> int:
    """Print unlabelled claims for a person to judge, in a form easy to paste back."""
    labelled = _labels(repo)
    claims = [s for s in _claims(repo) if s.id not in labelled]
    if not claims:
        print(f"  {repo}: every claim is already labelled ({len(labelled)} of them)")
        return 0
    random.seed(0)  # reproducible: the same sample every run, so a partial pass resumes
    chosen = random.sample(claims, min(count, len(claims)))
    print(f"\n  {len(claims)} unlabelled claims in {repo}; showing {len(chosen)}.")
    print(f"  Judge each, then write tools/labels/{repo}.json:\n")
    out = {}
    for symbol in chosen:
        print(f"  {symbol.status.value.upper():<10} {symbol.kind}")
        print(f"    {symbol.label}")
        print(f"    {symbol.file}:{symbol.line}")
        if symbol.note:
            print(f"    note: {symbol.note[:150]}")
        print(f"    id:   {symbol.id}")
        print()
        out[symbol.id] = {"verdict": "TODO", "why": ""}
    print("  Skeleton:")
    print(json.dumps(out, indent=2))
    return 0


def score(repo: str) -> tuple[int, int, list]:
    """(true, false, the findings judged wrong) for one repository."""
    labelled = _labels(repo)
    if not labelled:
        return 0, 0, []
    by_id = {s.id: s for s in _claims(repo)}
    true = false = 0
    wrong = []
    for symbol_id, entry in labelled.items():
        # `_why` and any other underscore key is a note to whoever reads the file, not a
        # judgement about a symbol.
        if symbol_id.startswith("_") or not isinstance(entry, dict):
            continue
        verdict = str(entry.get("verdict", "")).lower()
        if verdict not in ("true", "false"):
            continue
        # A claim that has DISAPPEARED since it was labelled is not scored: it is neither
        # right nor wrong any more, and counting it either way would let a change that
        # simply stopped reporting things improve the number.
        if symbol_id not in by_id:
            continue
        if verdict == "true":
            true += 1
        else:
            false += 1
            wrong.append((symbol_id, entry.get("why", "")))
    return true, false, wrong


def main() -> int:
    if "--sample" in sys.argv:
        index = sys.argv.index("--sample")
        repo = sys.argv[index + 1]
        count = int(sys.argv[index + 2]) if len(sys.argv) > index + 2 else 20
        return sample(repo, count)

    LABELS.mkdir(exist_ok=True)
    files = sorted(LABELS.glob("*.json"))
    if not files:
        print("  No labels yet. Start with:  python tools/precision.py --sample <repo> 25")
        return 0

    total_true = total_false = 0
    print(f"\n  {'repo':<28} {'true':>6} {'false':>6} {'precision':>10}")
    print("  " + "-" * 54)
    for path in files:
        repo = path.stem
        true, false, wrong = score(repo)
        judged = true + false
        if not judged:
            print(f"  {repo:<28} {'—':>6} {'—':>6} {'unjudged':>10}")
            continue
        total_true += true
        total_false += false
        print(f"  {repo:<28} {true:>6} {false:>6} {true * 100 // judged:>9}%")
        for symbol_id, why in wrong:
            print(f"      FALSE  {symbol_id[:70]}")
            if why:
                print(f"             {why[:100]}")

    judged = total_true + total_false
    if judged:
        print("  " + "-" * 54)
        print(f"  {'ALL':<28} {total_true:>6} {total_false:>6} "
              f"{total_true * 100 // judged:>9}%")
        print(f"\n  {judged} claims judged by hand. Precision is the share of `unresolved`")
        print("  and `unused` findings that were real. It does not move on its own -")
        print("  a change that stops reporting something drops it out of the count.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
