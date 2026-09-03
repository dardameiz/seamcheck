"""Render a map of a codebase that does not exist, at a size no corpus repo has.

The pointlessbutton map (511k lines, 42,686 symbols) is the largest real input the
renderer has been measured on. The owner's target is ten million lines - twenty times
that - and there is no such repository to hand, so this builds the graph directly, in
the shape the real one has, and renders it through the same `render()` the CLI uses:

- a few hundred ordinary pages of 40-400 symbols, a dozen heavy bundles of 2-4k, and
  three "unreached" buckets that together hold ~45% of everything (that is what the
  real map looks like: pointlessbutton's biggest page is a bucket of 18,943);
- dom_selector -> dom_attr fan-in like push-arena-main, ~0.5 edges per node;
- a snippet on every node, a note on a third, three lines of context on a fifth;
- five commits with changed sets.

    python tools/synth_map.py 37000            # ~pointlessbutton
    python tools/synth_map.py 730000           # ~10M lines at 73 symbols per 1k lines
    python tools/synth_map.py 730000 --open    # then open it headless and report heap
    python tools/synth_map.py 730000 --bundle --open   # the folder form: index.html + data/

Prints render wall time, peak RSS, file size, and the size of the largest chunk. With
--open, drives the file in headless Chromium (DPR 1, 768 MB heap cap so a runaway kills
the tab and not the machine) and reports open time and heap after the biggest page.
"""
from __future__ import annotations

import argparse
import pathlib
import random
import re
import resource
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from seamcheck.mapdata import ConnectivityMap, MapEdge, MapNode, PageMap  # noqa: E402
from seamcheck.renderers.map_html import render_document  # noqa: E402

KINDS = ["dom_selector", "dom_attr", "api_call", "route", "view", "template_var",
         "js_export", "js_import", "css_class", "celery_task", "stripe_event",
         "graphql_field", "ws_event", "env_var"]
STATUSES = ["connected", "connected", "connected", "unused", "unresolved", "uncertain"]
WORDS = ("user", "push", "arena", "store", "level", "stats", "badge", "season", "lobby",
         "invite", "reset", "hourly", "daily", "bonus", "streak", "team", "boost", "pbits")


def build(total: int, seed: int = 1) -> ConnectivityMap:
    rnd = random.Random(seed)
    # Page sizes: three buckets share 45%, twelve bundles share 20%, the rest is spread
    # over ordinary pages of 40-400 until the total is reached.
    # Buckets last, as the real map orders them: the page a reader opens on is an
    # ordinary one, and the 45% that is unreached is decoded only if they go there.
    sizes = []
    bucket = int(total * 0.45)
    buckets = [bucket // 2, bucket // 3, bucket - bucket // 2 - bucket // 3]
    heavy = int(total * 0.20)
    sizes += [heavy // 12] * 12
    left = total - sum(sizes) - bucket
    while left > 0:
        n = min(left, rnd.randint(40, 400))
        sizes.append(n)
        left -= n
    sizes += buckets
    pages, sym = [], 0
    for index, n in enumerate(sizes):
        which = index - (len(sizes) - 3)
        name = (["unreached:template", "unreached:js", "unreached:css"][which] if which >= 0
                else f"bundle-{index}" if index < 12 else f"page-{index}")
        index = which if which >= 0 else 3 + index  # bucket kind for the folder/ext below
        nodes, edges = [], []
        page_node = MapNode(id=f"page:{name}", label=name, kind="page", status="connected")
        nodes.append(page_node)
        folder = ("templates", "static/js", "static/css")[index] if index < 3 else "modules"
        ext = "html" if index == 0 else "js"
        files = [f"app/{folder}/{name}/{rnd.choice(WORDS)}_{k}.{ext}"
                 for k in range(max(1, n // 60))]
        selectors = []
        for k in range(n):
            sym += 1
            kind = KINDS[k % len(KINDS)] if index >= 3 else rnd.choice(("dom_selector", "dom_attr", "css_class"))
            status = rnd.choice(STATUSES) if index >= 3 else rnd.choice(("unused", "unused", "uncertain"))
            label = f"{rnd.choice(WORDS)}-{rnd.choice(WORDS)}-{k}"
            node = MapNode(
                id=f"{kind}:{name}:{k}", label=label, kind=kind, status=status,
                file=rnd.choice(files), line=rnd.randint(1, 4000),
                snippet=f'<div class="{label}" data-stat="{rnd.choice(WORDS)}">',
                note=("reached from nothing the scan reads" if k % 3 == 0 else ""),
                context=(f"  const el = q('.{label}');\n  el.textContent = fmt(v);\n  return el;"
                         if k % 5 == 0 else ""),
                lang="js" if index != 0 else "html",
                service=("stripe" if kind == "stripe_event" else "celery" if kind == "celery_task"
                         else "graphql" if kind == "graphql_field" else ""),
            )
            nodes.append(node)
            if kind == "dom_selector":
                selectors.append(node.id)
            if k % 2 == 0 and k:
                # Fan-in: attrs and calls hang off the selectors the way a page's DOM
                # writers do; everything else chains to the page node or a neighbour.
                src = rnd.choice(selectors) if selectors and kind == "dom_attr" else page_node.id
                edges.append(MapEdge(source=src, target=node.id, status=status))
            elif k % 7 == 0 and k:
                edges.append(MapEdge(source=nodes[-2].id, target=node.id, status=status))
        pages.append(PageMap(page=name, nodes=nodes, edges=edges, title=name,
                             where=f"app/{name}.html"))
    commits = []
    changed_all: dict[str, str] = {}
    for c in range(5):
        changed = {}
        for p in rnd.sample(pages, min(len(pages), 6)):
            for node in rnd.sample(p.nodes, min(len(p.nodes), 30)):
                changed[node.id] = rnd.choice(("added", "removed", "status"))
        changed_all.update(changed)
        commits.append({
            "sha": f"{c:040x}", "subject": f"synthetic commit {c}", "date": "2026-09-03",
            "symbols": total, "baseline": f"{c + 1:040x}", "head": f"{c:040x}",
            "changed": changed,
            "changes": [{"id": i, "change": v} for i, v in changed.items()],
        })
    return ConnectivityMap(git_sha="synthetic" + "0" * 32, generated_at="2026-09-03T00:00:00Z",
                           pages=pages, commits=commits, changed=changed_all)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("symbols", type=int)
    ap.add_argument("--out", default="")
    ap.add_argument("--open", action="store_true")
    ap.add_argument("--bundle", action="store_true",
                    help="write the folder form (index.html + data/*.js) instead of one file")
    args = ap.parse_args()
    suffix = "" if args.bundle else ".html"
    out = pathlib.Path(args.out or f"/tmp/synth_{args.symbols}{suffix}")

    t0 = time.time()
    cm = build(args.symbols)
    built = time.time() - t0
    t0 = time.time()
    document = render_document(cm)
    if args.bundle:
        html, assets = document.bundle()
        (out / "data").mkdir(parents=True, exist_ok=True)
        for relative, body in assets.items():
            (out / relative).write_bytes(body)
        (out / "index.html").write_text(html)
        chunks = [(name, enc, text) for name, enc, text in document.chunks]
        total = len(html) + sum(len(b) for b in assets.values())
        opened = out / "index.html"
    else:
        html = document.single_file()
        out.write_text(html)
        chunks = re.findall(r'data-chunk="([^"]+)" data-enc="(\w+)">(.*?)</script>', html, re.S)
        total = len(html)
        opened = out
    rendered = time.time() - t0
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024 ** 2)
    biggest = max(chunks, key=lambda c: len(c[2]))
    inline = len(re.search(r"const MAPDATA=(.*?);</script>", html, re.S).group(1))
    print(f"{args.symbols:,} symbols across {len(cm.pages)} pages: build {built:.1f}s, "
          f"render {rendered:.1f}s, peak RSS {rss:.0f} MB")
    print(f"  {out} = {total / 1e6:.1f} MB"
          + (f" (index.html {len(html) / 1e6:.1f} MB)" if args.bundle else "")
          + f"; MAPDATA {inline / 1e6:.2f} MB; "
          f"{len(chunks)} chunks; biggest {biggest[0]} {biggest[1]} {len(biggest[2]) / 1e6:.2f} MB")
    if not args.open:
        return 0

    from playwright.sync_api import sync_playwright  # noqa: PLC0415

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=["--js-flags=--max-old-space-size=768"])
        page = browser.new_page(viewport={"width": 1400, "height": 900}, device_scale_factor=1)
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        cdp = page.context.new_cdp_session(page)
        cdp.send("Performance.enable")

        def heap():
            # After a collection: what the page HOLDS, not what parsing left behind.
            cdp.send("HeapProfiler.collectGarbage")
            m = {x["name"]: x["value"] for x in cdp.send("Performance.getMetrics")["metrics"]}
            return f"{m['JSHeapUsedSize'] / 1048576:.0f} MB"

        t0 = time.time()
        page.goto(opened.as_uri())
        page.wait_for_function("typeof PAGES !== 'undefined' && PAGES.some(p => p.nodes)", timeout=120000)
        print(f"  open {time.time() - t0:.2f}s, heap {heap()}")
        page.evaluate("switchTo('map')")
        big = page.evaluate("PAGES.map((p,i)=>[i,p.n]).sort((a,b)=>b[1]-a[1])[0]")
        t0 = time.time()
        page.evaluate(f"current={big[0]}; draw()")
        page.wait_for_function(f"PAGES[{big[0]}].nodes && _drawWaiting === -1", timeout=120000)
        print(f"  biggest page ({big[1]:,} symbols) drawn in {time.time() - t0:.2f}s, heap {heap()}")
        t0 = time.time()
        page.evaluate("(() => { const a = document.querySelector('#cv .nd.agg');"
                      " if (a) a.dispatchEvent(new MouseEvent('click', {bubbles: true})); })()")
        page.wait_for_timeout(500)
        cards = page.evaluate("document.querySelectorAll('#cv .nd').length")
        print(f"  expanded to {cards} cards in {time.time() - t0:.2f}s, heap {heap()}")
        t0 = time.time()
        page.evaluate("searchIndex(() => window._idxDone = true)")
        page.wait_for_function("window._idxDone", timeout=120000)
        n = page.evaluate("_index.n")
        t1 = time.time()
        hits = page.evaluate("searchEverywhere('user').length")
        print(f"  search index {n:,} rows in {t1 - t0:.2f}s, 'user' {hits} hits in "
              f"{(time.time() - t1) * 1000:.0f} ms, heap {heap()}")
        print("  errors:", errors[:3] or "none")
        browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
