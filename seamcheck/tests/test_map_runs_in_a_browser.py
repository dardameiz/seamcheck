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
    """A console section set, so the panel, its filters and the nav render too.

    Built without a try/except on purpose: swallowing the failure here produced a page
    with no navigation at all, and three tests that failed looking for a nav item rather
    than telling us the console never built.
    """
    from seamcheck.console import build_console
    from seamcheck.report import build_report

    return build_console(graph, build_report(
        graph=graph, diff=None, entries=[], git_sha="0" * 12,
    ))


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

    def _render(self, series=None) -> str:
        from seamcheck.mapdata import build_map
        from seamcheck.renderers.map_html import render

        graph = _fixture_graph()
        pages = {"orders-main": {s.id for s in graph.symbols}}
        connectivity = build_map(graph, pages, git_sha="0" * 12)
        html = render(connectivity, console=_console_for(graph), series=series)
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


class TrendChartRenders(MapRunsInABrowser):
    """The series is the sentence no tool in this category can say; it has to draw."""

    @staticmethod
    def _series(*counts):
        from seamcheck.trend import Entry, trend
        return trend([
            Entry(sha=str(i) * 40, at=f"2026-0{i + 1}-01T00:00:00", symbols=100,
                  findings=c, by_status={"unused": c}, by_kind={"css_selector": c})
            for i, c in enumerate(counts)
        ])

    def _open(self, series):
        from playwright.sync_api import sync_playwright

        url = self._render(series)
        with sync_playwright() as playwright:
            try:
                browser = playwright.chromium.launch()
            except Exception as error:  # pragma: no cover
                raise unittest.SkipTest(f"no chromium: {error}") from None
            page = browser.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(url, wait_until="load")
            page.wait_for_timeout(250)
            page.click('#nav .nv[data-key="changes"]')
            page.wait_for_timeout(200)
            state = page.evaluate("""() => ({
                dots: document.querySelectorAll('.trend .dot').length,
                line: document.querySelectorAll('.trend .line').length,
                headline: (document.querySelector('.headline') || {}).textContent || '',
                movers: document.querySelectorAll('.movers .m').length,
                body: document.querySelector('#panel').textContent,
            })""")
            browser.close()
        self.assertEqual(errors, [], f"the trend view raised: {errors}")
        return state

    def test_a_series_draws_a_point_per_scan(self):
        state = self._open(self._series(40, 31, 24, 12))
        self.assertEqual(state["dots"], 4)
        self.assertEqual(state["line"], 1)
        self.assertIn("28 fewer", state["headline"])
        self.assertEqual(state["movers"], 1)

    def test_a_rise_is_reported_as_a_rise(self):
        self.assertIn("15 more", self._open(self._series(10, 25))["headline"])

    def test_one_scan_says_so_instead_of_drawing_a_line(self):
        state = self._open(self._series(7))
        self.assertEqual(state["dots"], 0)
        self.assertIn("1 scan recorded", state["body"])


class SyntaxHighlighting(MapRunsInABrowser):
    """The highlighter must never print its own markup as if it were source.

    The first version chained regexes over the ESCAPED line: escaping turned an apostrophe
    into `&#x27;`, the string rule wrapped that, and then the number rule matched the 39
    inside the entity while the keyword rule matched the `class` in the tag it had just
    written. A line of JavaScript rendered as a soup of `&class="c">#class="n">39;`.
    """

    def _highlight(self, lines: list[str]) -> list[str]:
        from playwright.sync_api import sync_playwright

        url = self._render()
        with sync_playwright() as playwright:
            try:
                browser = playwright.chromium.launch()
            except Exception as error:  # pragma: no cover
                raise unittest.SkipTest(f"no chromium: {error}") from None
            page = browser.new_page()
            page.goto(url, wait_until="load")
            page.wait_for_timeout(200)
            out = page.evaluate("lines => lines.map(l => highlight(l))", lines)
            browser.close()
        return out

    def test_markup_never_leaks_into_the_rendered_code(self):
        source = [
            "const banner = document.getElementById('cookieConsentBanner');",
            'banner.classList.add("translate-y-0");',
            "# a python comment with 'quotes' and 39",
            "const n = 39; // trailing comment",
            "const t = `template ${x} literal`;",
        ]
        for rendered in self._highlight(source):
            with self.subTest(rendered=rendered[:60]):
                # The only markup allowed is the highlighter's own one-letter spans.
                self.assertNotIn('&class', rendered)
                self.assertNotIn('#class', rendered)
                stripped = rendered.replace('<i class="s">', "").replace('<i class="c">', "")
                stripped = stripped.replace('<i class="n">', "").replace('<i class="k">', "")
                stripped = stripped.replace("</i>", "")
                self.assertNotIn("<i", stripped, "an unexpected tag was emitted")
                self.assertNotIn("class=", stripped, "markup rendered as source text")

    def test_source_characters_that_are_html_are_escaped(self):
        rendered = self._highlight(["if (a < b && c > d) { return '<script>'; }"])[0]
        self.assertIn("&lt;", rendered)
        self.assertIn("&amp;", rendered)
        self.assertNotIn("<script>", rendered)

    def test_a_string_is_marked_and_a_keyword_outside_it_is_too(self):
        rendered = self._highlight(["const x = 'const';"])[0]
        self.assertIn('<i class="k">const</i>', rendered)
        self.assertIn('<i class="s">', rendered)
        # The keyword inside the string belongs to the string, not to the keyword rule.
        self.assertEqual(rendered.count('<i class="k">const</i>'), 1)
