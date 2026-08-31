"""The map's JavaScript has to actually run, and nothing else in the suite checks that.

A `let` declared after its first use is valid JavaScript right up until it executes, so a
temporal-dead-zone error shipped a page whose navigation, page selector and canvas were all
empty - with 716 tests green. Every other test here reads the HTML as text, which cannot
tell a working page from a blank one.

Playwright is optional (it is the observe extra), so this skips rather than fails when the
browser is not installed. A skipped guard is worth having: the day the browser IS there,
this is the only thing standing between a typo and a dead page.
"""

from __future__ import annotations

import pathlib
import tempfile
import unittest

from django.test import SimpleTestCase

from seamcheck.graph import Edge, Graph, Status, Symbol


def _console_for(graph):
    """A console section set, so the panel and its filters render too."""
    from seamcheck.console import build_console
    from seamcheck.report import build_report

    try:
        return build_console(build_report(graph), graph)
    except Exception:  # pragma: no cover - the panel is optional to this test
        return None


def _fixture_graph() -> Graph:
    def symbol(kind, label, status=Status.CONNECTED, file="app/thing.py"):
        return Symbol(id=f"{kind}:{label}", kind=kind, label=label, sub="", file=file,
                      line=1, status=status, snippet=f"{kind} {label}", chain=[label], note="")

    symbols = [
        symbol("url", "api/orders/"),
        symbol("view", "orders"),
        symbol("module", "orders.js", file="static/js/orders.js"),
        symbol("fetch_target", "/api/orders/", file="static/js/orders.js"),
        symbol("fetch_target", "/api/gone/", Status.UNRESOLVED, "static/js/orders.js"),
        symbol("css_selector", "cart", Status.UNUSED, "static/css/app.css"),
        symbol("dom_attr", "cart-id", Status.UNCERTAIN, "templates/page.html"),
    ]
    edges = [Edge(from_id="url:api/orders/", to_id="view:orders", status=Status.CONNECTED)]
    return Graph(symbols=symbols, edges=edges)


class MapRunsInABrowser(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        try:
            from playwright.sync_api import sync_playwright  # noqa: F401
        except ImportError:  # pragma: no cover - depends on the optional extra
            raise unittest.SkipTest("playwright is not installed (the observe extra)") from None

    def _render(self) -> str:
        from seamcheck.mapdata import build_map
        from seamcheck.renderers.map_html import render

        graph = _fixture_graph()
        pages = {"orders-main": {s.id for s in graph.symbols}}
        connectivity = build_map(graph, pages, git_sha="0" * 12)
        html = render(connectivity, console=_console_for(graph))
        path = pathlib.Path(tempfile.mkdtemp()) / "map.html"
        path.write_text(html, encoding="utf-8")
        return path.as_uri()

    def test_the_page_runs_without_a_single_console_error(self):
        from playwright.sync_api import sync_playwright

        url = self._render()
        errors: list[str] = []
        with sync_playwright() as playwright:
            try:
                browser = playwright.chromium.launch()
            except Exception as error:  # pragma: no cover - no browser downloaded
                raise unittest.SkipTest(f"no chromium: {error}") from None
            page = browser.new_page()
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
            page.goto(url, wait_until="load")
            page.wait_for_timeout(400)

            # The three things a dead page leaves empty. Asserting on them rather than only
            # on the error list means a silent failure is caught too.
            populated = page.evaluate("""() => ({
                nav: document.querySelectorAll('#nav .nv').length,
                pages: document.querySelectorAll('#pg option').length,
                key: document.querySelectorAll('#colourkey button[data-status]').length,
            })""")
            browser.close()

        self.assertEqual(errors, [], f"the map raised in the browser: {errors}")
        self.assertGreater(populated["nav"], 0, "navigation never rendered")
        self.assertGreater(populated["pages"], 0, "page selector never filled")
        self.assertEqual(populated["key"], 4, "the status filter chips are missing")

    def test_clicking_a_status_chip_filters_the_page_counts(self):
        """The filter the key promises has to change what the canvas claims to hold."""
        from playwright.sync_api import sync_playwright

        url = self._render()
        with sync_playwright() as playwright:
            try:
                browser = playwright.chromium.launch()
            except Exception as error:  # pragma: no cover
                raise unittest.SkipTest(f"no chromium: {error}") from None
            page = browser.new_page()
            page.goto(url, wait_until="load")
            page.wait_for_timeout(300)
            # The key belongs to the canvas, and the canvas is hidden until a lens that
            # draws is selected. Overview opens first.
            page.click('#nav .nv[data-key="map"]')
            page.wait_for_timeout(200)
            before = page.eval_on_selector("#pg option", "el => el.textContent")
            page.click('#colourkey button[data-status="unresolved"]')
            page.wait_for_timeout(200)
            after = page.eval_on_selector("#pg option", "el => el.textContent")
            pressed = page.get_attribute('#colourkey button[data-status="unresolved"]', "aria-pressed")
            browser.close()

        self.assertNotEqual(before, after, "filtering changed nothing")
        self.assertEqual(pressed, "true")


if __name__ == "__main__":
    unittest.main()
