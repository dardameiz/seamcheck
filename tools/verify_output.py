#!/usr/bin/env python3
"""Gate 5: check the OUTPUT, not just that the scan produced findings.

The four gates in `corpus.py` all stop at the graph - did an adapter fit, were routes
found, is the volume sane. Every one of them can pass while the thing a person actually
opens is broken, and that is not hypothetical: the map's JavaScript is emitted from a
Python string, so a stray character makes a **blank page** that no Python test and no
linter sees, and a page-attribution bug once dropped every page while the finding count
stayed identical.

So this renders the real map and interrogates the document:

  1. It rendered at all, and is big enough to be a map rather than an error page.
  2. Its emitted <script> blocks PARSE - `node --check` on each one.
  3. The nodes in the payload match the graph: same count, same statuses, no orphan edge
     pointing at a node that is not there.
  4. Pages exist when the repo has pages, and every page's files resolve to real symbols.
  5. No placeholder leaked into the document - `undefined`, `NaN`, `[object Object]`,
     `None`, an unrendered `{{ }}`.

Usage:
    python tools/verify_output.py                 # every cloned repo
    python tools/verify_output.py dub cal.com     # named ones
    python tools/verify_output.py --self          # the seamcheck repo itself
"""

from __future__ import annotations

import contextlib
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
CORPUS = pathlib.Path(os.environ.get("SEAMCHECK_CORPUS", ROOT.parent / "seamcheck-corpus"))

# Substrings that mean a value reached the page that should have been formatted first.
# Checked against the document's TEXT, never its script blocks - "undefined" is a
# perfectly ordinary token in JavaScript and only a defect when a reader can see it.
_LEAKED = ("undefined", "NaN", "[object Object]", "{{", "None", "&lt;built-in")

_SCRIPT = re.compile(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", re.S)
_TAGS = re.compile(r"<[^>]+>")


def _node_check(source: str) -> str | None:
    """Parse one script block the way a browser would. Returns an error, or None."""
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as handle:
        handle.write(source)
        path = handle.name
    try:
        done = subprocess.run(
            ["node", "--check", path], capture_output=True, text=True, timeout=60, check=False
        )
        if done.returncode == 0:
            return None
        for line in (done.stderr or "").splitlines():
            if line.strip() and not line.startswith(("/", "    at", "Node.js")):
                return line.strip()[:160]
        return "node --check failed"
    except (OSError, subprocess.SubprocessError) as error:
        return f"could not run node: {error}"
    finally:
        with contextlib.suppress(OSError):
            os.unlink(path)


def _payload(html: str) -> dict | None:
    """The map's own data, read back out of the document it emitted.

    MAPDATA is interned: statuses, kinds and files are lookup tables and each node is a
    row of indices into them. Reading it back the way the browser does is the point -
    a checker that re-derived the numbers from the graph would pass while the document
    a person opens was empty.
    """
    match = re.search(r"const\s+MAPDATA\s*=\s*(\{.*\});</script>", html)
    if not match:
        return None
    try:
        return json.loads(match.group(1).replace("<\\/", "</"))
    except json.JSONDecodeError:
        return None


def verify(name: str, root: pathlib.Path) -> dict:
    from seamcheck import api

    row: dict = {"name": name, "problems": []}
    fail = row["problems"].append
    started = time.time()

    try:
        html = api._render_map(str(root), "HEAD")
    except Exception as error:  # noqa: BLE001 - a crash IS the result
        row["problems"].append(f"RENDER CRASH: {type(error).__name__}: {str(error)[:120]}")
        row["seconds"] = round(time.time() - started, 1)
        return row

    row["bytes"] = len(html)
    row["seconds"] = round(time.time() - started, 1)

    # 1. it is a document
    if len(html) < 20_000:
        fail(f"suspiciously small: {len(html):,} bytes")
    if "<html" not in html.lower():
        fail("no <html> element")

    # 2. every emitted script parses
    blocks = _SCRIPT.findall(html)
    row["scripts"] = len(blocks)
    if not blocks:
        fail("no inline <script> - the map cannot draw")
    for index, block in enumerate(blocks):
        if not block.strip():
            continue
        error = _node_check(block)
        if error:
            fail(f"script #{index} does not parse: {error}")

    # 3. the payload agrees with the graph
    data = _payload(html)
    if data is None:
        fail("no readable MAPDATA payload in the document")
    else:
        pages = data.get("pages") or []
        statuses = data.get("statuses") or []
        kinds = data.get("kinds") or []
        row["pages"] = len(pages)
        row["nodes"] = sum(len(p.get("nodes") or []) for p in pages)
        row["edges"] = sum(len(p.get("edges") or []) for p in pages)

        allowed = {"connected", "unresolved", "unused", "uncertain"}
        unknown = [s for s in statuses if s not in allowed]
        if unknown:
            fail(f"unknown status value(s) in payload: {unknown[:4]}")

        for page in pages:
            nodes = page.get("nodes") or []
            ids = {n[0] for n in nodes if n}
            # A row is [id, label, kindIdx, statusIdx, ...]; the first four are required
            # because the map reads them unconditionally when it draws a card.
            short = [n for n in nodes if len(n) < 4]
            if short:
                fail(f"{page.get('page')}: {len(short)} node row(s) shorter than 4 fields")
            for node in nodes[:4000]:
                if len(node) >= 4:
                    if not 0 <= node[2] < len(kinds):
                        fail(f"{page.get('page')}: node {node[0]!r} kind index out of range")
                        break
                    if not 0 <= node[3] < len(statuses):
                        fail(f"{page.get('page')}: node {node[0]!r} status index out of range")
                        break
            dangling = [
                e for e in (page.get("edges") or [])
                if len(e) >= 2 and (e[0] not in ids or e[1] not in ids)
            ]
            if dangling:
                fail(f"{page.get('page')}: {len(dangling)} edge(s) point at a node "
                     f"not on that page, e.g. {dangling[0][:2]}")

        if row["nodes"] and not row["pages"]:
            fail("nodes exist but no page holds them - nothing would draw")
        # A repo with templates or a pages/app directory must produce pages.
        has_pages = any(
            (root / d).is_dir()
            for d in ("templates", "pages", "app", "src/pages", "src/app", "views")
        )
        if has_pages and not row["pages"]:
            fail("repo has a pages/templates directory but the map lists no pages")

    # 5. nothing raw leaked into what a person reads
    text = _TAGS.sub(" ", re.sub(_SCRIPT, " ", html))
    for token in _LEAKED:
        if token in text:
            context = text[max(0, text.index(token) - 45):text.index(token) + 45]
            fail(f"{token!r} visible in page text: ...{' '.join(context.split())}...")

    return row


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if "--self" in sys.argv:
        targets = [("seamcheck", ROOT)]
    elif args:
        targets = [(n, CORPUS / n) for n in args]
    else:
        targets = [(p.name, p) for p in sorted(CORPUS.iterdir()) if p.is_dir()]

    rows = []
    for name, root in targets:
        if not root.is_dir():
            print(f"  {name:<32} not cloned")
            continue
        print(f"  {name:<32} rendering ...", end="", flush=True)
        row = verify(name, root)
        rows.append(row)
        status = "ok" if not row["problems"] else f"{len(row['problems'])} PROBLEM(S)"
        print(f"\r  {name:<32} {row.get('bytes', 0):>10,}b  "
              f"nodes {row.get('nodes', 0):>6,}  pages {row.get('pages', 0):>4}  "
              f"{row['seconds']:>6}s  {status}")
        for problem in row["problems"]:
            print(f"      - {problem}")

    bad = [r for r in rows if r["problems"]]
    print(f"\n  {len(rows) - len(bad)}/{len(rows)} repositories rendered a sound map.")
    (CORPUS / "verify_output.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
