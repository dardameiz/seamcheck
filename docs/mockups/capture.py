"""Render the README's screenshots from the real renderer, on a fictional project.

Every app screenshot here is `map_html.render()` output driven in a real browser - not a
drawing of the UI, and not the private project the tool was built against. The two panels
that are NOT the app (the CI gate and the assistant transcript) are marked as mockups in
their own captions, because a picture of a terminal is a claim about output and should not
be mistaken for a recording of one.

    python docs/mockups/capture.py
"""

from __future__ import annotations

import http.server
import pathlib
import socketserver
import sys
import threading

import django
from django.conf import settings

HERE = pathlib.Path(__file__).resolve().parent
OUT = HERE.parent / "images"
WIDTH, HEIGHT = 1440, 900


def _boot():
    if not settings.configured:
        settings.configure(INSTALLED_APPS=["seamcheck"], DATABASES={}, SEAMCHECK_CONFIG={})
        django.setup()
    sys.path.insert(0, str(HERE))


def _map_html() -> str:
    from demo_graph import build

    from seamcheck.console import build_console
    from seamcheck.filetree import build_file_tree
    from seamcheck.mapdata import build_map
    from seamcheck.renderers import map_html
    from seamcheck.report import build_report

    graph, pages = build()
    report = build_report(graph=graph, diff=None, entries=[], git_sha="a1b2c3d4e5f6")
    files = [
        {"path": r.path, "counts": r.counts, "declarations": r.declarations, "known": r.known}
        for r in build_file_tree(graph, [])
    ]
    return map_html.render(
        build_map(graph, pages, git_sha="a1b2c3d4e5f6"),
        console=build_console(graph, report),
        files=files,
        repo_root="/Users/you/bookshop",
        editor="vscode",
    )


def _serve(directory: pathlib.Path):
    handler = lambda *a, **k: http.server.SimpleHTTPRequestHandler(  # noqa: E731
        *a, directory=str(directory), **k
    )
    server = socketserver.TCPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def main() -> int:
    _boot()
    from playwright.sync_api import sync_playwright

    OUT.mkdir(parents=True, exist_ok=True)
    scratch = HERE / "_build"
    scratch.mkdir(exist_ok=True)
    (scratch / "map.html").write_text(_map_html(), encoding="utf-8")
    for panel in HERE.glob("panel_*.html"):
        (scratch / panel.name).write_text(panel.read_text(encoding="utf-8"), encoding="utf-8")

    server, base = _serve(scratch)
    shots = []
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page(viewport={"width": WIDTH, "height": HEIGHT},
                                    device_scale_factor=2)

            def shot(name):
                path = OUT / f"{name}.png"
                page.screenshot(path=str(path))
                shots.append((name, path.stat().st_size))

            page.goto(f"{base}/map.html")
            page.wait_for_timeout(1200)

            # 1. Overview - the numbers, in shares.
            shot("overview")

            # 2. The map, on the page with the most findings on it.
            page.evaluate("""() => {
              const vw = document.getElementById('vw');
              vw.value = 'map'; vw.dispatchEvent(new Event('change'));
            }""")
            page.wait_for_timeout(700)
            page.evaluate("""() => {
              // Whichever page carries the most red - the picture is about the damage.
              const pg = document.getElementById('pg');
              let best = 0, most = -1;
              [...pg.options].forEach((o, i) => {
                const n = parseInt((o.textContent.match(/(\\d+) nodes/) || [0, 0])[1], 10);
                if (n > most) { most = n; best = i; }
              });
              pg.value = pg.options[best].value;
              pg.dispatchEvent(new Event('change'));
            }""")
            page.wait_for_timeout(900)
            for _ in range(2):
                page.click("#zi")
                page.wait_for_timeout(140)
            shot("map")

            # 3. A node clicked: the chain that reaches it, hop by hop. An UNRESOLVED one -
            # a healthy node's panel says "nothing to do", which is a poor advertisement
            # for a tool whose job is finding the ones that are not healthy.
            page.evaluate("""() => {
              const nodes = [...document.querySelectorAll('#cv .nd')];
              const red = nodes.find(g => {
                const r = g.querySelector('rect[stroke]');
                const t = (g.querySelector('title')||{}).textContent || '';
                return r && /wishlist/.test(t);
              });
              (red || nodes[8]).dispatchEvent(new MouseEvent('click', {bubbles: true}));
            }""")
            page.wait_for_timeout(700)
            shot("chain")

            # 4. Findings, each saying what it means and what to check.
            page.evaluate("""() => {
              const vw = document.getElementById('vw');
              vw.value = 'findings'; vw.dispatchEvent(new Event('change'));
            }""")
            page.wait_for_timeout(700)
            shot("findings")

            # 5 & 6. The two panels that are mockups, and say so.
            for name in ("panel_ci", "panel_agent"):
                if (scratch / f"{name}.html").exists():
                    page.goto(f"{base}/{name}.html")
                    page.wait_for_timeout(400)
                    shot(name.replace("panel_", ""))

            browser.close()
    finally:
        server.shutdown()

    for name, size in shots:
        print(f"  {name + '.png':<18}{size / 1024:>8.0f} KB")
    print(f"\n{len(shots)} images -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
