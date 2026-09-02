#!/usr/bin/env python3
"""Gate 6: does it still FIND things? Recall, measured against bugs we planted ourselves.

Nothing measured recall until this existed. Gate 1 says the scan ran, gate 4 says the
volume is not absurd, gate 5 says the page renders - and a refactor that quietly stopped
finding half of everything passes all three. The corpus cannot help: nobody knows how many
real bugs are in somebody else's repository, so "found 40" and "found 20" look equally
plausible there.

A planted bug has no such problem. Each fixture below is a tiny project with a known defect
written into it on purpose, and `expected.json` says what the scan must report. If a typo
we put there ourselves stops being found, that is a regression with no ambiguity at all.

`must_not_find` matters as much as `must_find`, and is why the Supabase fixture pair exists.
The 728-false-finding bug was a scan reporting MORE than it should, so a harness that only
counts what was found would have called it a triumph.

Usage:
    python tools/recall.py                # every fixture
    python tools/recall.py supabase-no-schema
"""

from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
FIXTURES = pathlib.Path(__file__).resolve().parent / "recall_fixtures"


def _matches(symbol, rule: dict) -> bool:
    """A rule is a subset match: every key it names must equal the symbol's value."""
    for key, want in rule.items():
        if key == "status":
            if symbol.status.value != want:
                return False
        elif key == "label_contains":
            if want not in symbol.label:
                return False
        elif getattr(symbol, key, None) != want:
            return False
    return True


def check(name: str) -> tuple[bool, list[str]]:
    from seamcheck import api

    directory = FIXTURES / name
    expected = json.loads((directory / "expected.json").read_text(encoding="utf-8"))
    problems: list[str] = []

    try:
        graph = api.scan(str(directory))
    except Exception as error:  # noqa: BLE001 - a crash is the loudest possible failure
        return False, [f"CRASH: {type(error).__name__}: {str(error)[:120]}"]

    symbols = [s for s in graph.symbols if not str(s.file).endswith("expected.json")]

    for rule in expected.get("must_find", []):
        if not any(_matches(s, rule) for s in symbols):
            problems.append(f"NOT FOUND: {rule}")

    for rule in expected.get("must_not_find", []):
        hits = [s for s in symbols if _matches(s, rule)]
        if hits:
            shown = ", ".join(f"{h.kind}:{h.label}" for h in hits[:3])
            problems.append(f"WRONGLY FOUND ({len(hits)}): {rule} -> {shown}")

    return not problems, problems


def main() -> int:
    wanted = [a for a in sys.argv[1:] if not a.startswith("-")]
    names = wanted or sorted(
        p.name for p in FIXTURES.iterdir() if p.is_dir() and (p / "expected.json").is_file()
    )
    if not names:
        print("  no fixtures found")
        return 1

    failed = 0
    for name in names:
        ok, problems = check(name)
        expected = json.loads((FIXTURES / name / "expected.json").read_text(encoding="utf-8"))
        planted = len(expected.get("must_find", []))
        forbidden = len(expected.get("must_not_find", []))
        status = "ok" if ok else f"{len(problems)} FAILURE(S)"
        print(f"  {name:<26} {planted:>2} planted, {forbidden:>2} forbidden   {status}")
        for problem in problems:
            print(f"      - {problem}")
        failed += 0 if ok else 1

    print(f"\n  {len(names) - failed}/{len(names)} fixtures behave as specified.")
    print(f"  {FIXTURES}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
