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
                key: document.querySelectorAll('#colourkey .seg button[data-status]').length,
                theme: document.querySelectorAll('#tmode').length,
            })""")
            browser.close()

        self.assertEqual(errors, [], f"the map raised in the browser: {errors}")
        self.assertGreater(populated["nav"], 0, "navigation never rendered")
        self.assertGreater(populated["pages"], 0, "page selector never filled")
        # Four statuses plus "all", which is the way back to everything.
        self.assertEqual(populated["key"], 5, "the status filter is missing")
        self.assertEqual(populated["theme"], 1, "the theme control is missing")

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
            page.click('#colourkey .seg button[data-status="unresolved"]')
            page.wait_for_timeout(200)
            after = page.eval_on_selector("#pg option", "el => el.textContent")
            pressed = page.get_attribute(
                '#colourkey .seg button[data-status="unresolved"]', "aria-pressed")
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


class ThemeControl(MapRunsInABrowser):
    """The map is read on a phone in a light OS as often as on a dark desktop.

    It followed prefers-color-scheme and offered no way to disagree, so a reader whose
    system is light had no way to see the dark design at all.
    """

    def _cycle(self, clicks: int) -> str:
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
            page.click('#nav .nv[data-key="map"]')
            for _ in range(clicks):
                page.click("#tmode")
            stamped = page.get_attribute("html", "data-theme")
            browser.close()
        return stamped

    def test_it_follows_the_system_until_told_otherwise(self):
        self.assertIsNone(self._cycle(0), "nothing is stamped before a reader chooses")

    def test_one_press_forces_dark(self):
        self.assertEqual(self._cycle(1), "dark")

    def test_two_presses_force_light(self):
        self.assertEqual(self._cycle(2), "light")

    def test_three_presses_return_to_the_system(self):
        self.assertIsNone(self._cycle(3))


class DirectionAOnAPhone(MapRunsInABrowser):
    """The phone layout: one pill at the bottom, and the canvas gets the rest.

    The header was six stacked rows - brand, view, commit, page, crumb, colour key - and
    the map got roughly a third of a 390px screen. Every control belongs in one container
    over the canvas instead, within reach of the thumb already holding the phone.
    """

    @staticmethod
    def _phone_graph():
        from seamcheck.graph import Graph, Status, Symbol

        def symbol(kind, label, status=Status.CONNECTED, file="a.py"):
            return Symbol(id=f"{kind}:{label}", kind=kind, label=label, sub="", file=file,
                          line=1, status=status, snippet="x", chain=[label], note="")

        return Graph(symbols=[
            symbol("url", "api/orders/"), symbol("view", "orders"),
            symbol("module", "orders.js", file="s/orders.js"),
            symbol("fetch_target", "/api/gone/", Status.UNRESOLVED, "s/orders.js"),
            symbol("css_selector", "cart", Status.UNUSED, "a.css"),
            symbol("stripe_webhook", "webhook"), symbol("stripe_event", "charge.refunded"),
            symbol("celery_task", "billing.send_receipt"),
        ], edges=[])

    def _open(self, width=390):
        from playwright.sync_api import sync_playwright

        from seamcheck.console import build_console
        from seamcheck.mapdata import build_map
        from seamcheck.renderers.map_html import render
        from seamcheck.report import build_report
        from seamcheck.trend import Entry, trend

        graph = self._phone_graph()
        series = trend([
            Entry(sha=str(i) * 40, at=f"2026-0{i + 1}-01T00:00:00", symbols=8, findings=f,
                  by_status={}, by_kind={"css_selector": f}) for i, f in enumerate((40, 22, 12))
        ])
        html = render(
            build_map(graph, {"orders-main": {s.id for s in graph.symbols}}, git_sha="0" * 12),
            console=build_console(graph, build_report(
                graph=graph, diff=None, entries=[], git_sha="0" * 12)),
            series=series)
        path = pathlib.Path(tempfile.mkdtemp()) / "m.html"
        path.write_text(html, encoding="utf-8")

        with sync_playwright() as playwright:
            try:
                browser = playwright.chromium.launch()
            except Exception as error:  # pragma: no cover
                raise unittest.SkipTest(f"no chromium: {error}") from None
            page = browser.new_page(viewport={"width": width, "height": 780})
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(path.as_uri(), wait_until="load")
            page.wait_for_timeout(300)
            # The phone gets a View select; the desktop keeps the rail. Same destination,
            # two controls, because the rail does not fit on 390px.
            if width <= 720:
                page.select_option("#vw", "map")
            else:
                page.click('#nav .nv[data-key="map"]')
            page.wait_for_timeout(300)
            state = page.evaluate("""() => ({
                pill: !!document.querySelector('.pill'),
                page: !!document.querySelector('.pill #pg'),
                layer: !!document.querySelector('.pill #ly'),
                status: !!document.querySelector('.pill #colourkey'),
                reading: !document.getElementById('reading').hidden,
                big: (document.getElementById('bignum') || {}).textContent,
                spark: document.querySelectorAll('#spark polyline').length,
                layers: [...document.querySelectorAll('#ly option')].map(o => o.value),
                canvasShare: document.querySelector('.main').getBoundingClientRect().height / 780,
                selects: document.querySelectorAll('#pg').length,
            })""")
            browser.close()
        self.assertEqual(errors, [], f"the phone layout raised: {errors}")
        return state

    def test_every_control_is_in_one_pill(self):
        state = self._open()
        self.assertTrue(state["pill"], "no pill was built")
        for control in ("page", "layer", "status"):
            self.assertTrue(state[control], f"the {control} control is not in the pill")

    def test_the_controls_are_moved_not_duplicated(self):
        """Two copies of the page select is two answers to which page is open."""
        self.assertEqual(self._open()["selects"], 1)

    def test_the_canvas_gets_most_of_the_screen(self):
        self.assertGreater(self._open()["canvasShare"], 0.6)

    def test_the_reading_shows_the_count_and_its_trend(self):
        state = self._open()
        self.assertTrue(state["reading"])
        self.assertTrue(state["big"])
        self.assertEqual(state["spark"], 1, "three scans should draw a sparkline")

    def test_each_service_is_its_own_layer(self):
        """"Show me Stripe" is the question; "does this talk to anything" is not."""
        layers = self._open()["layers"]
        self.assertIn("stripe", layers)
        self.assertIn("celery", layers)
        self.assertNotIn("graphql", layers, "a service the scan did not find is not offered")
        self.assertIn("", layers, "there must be a way back to everything")

    def test_the_desktop_keeps_its_header(self):
        state = self._open(width=1200)
        self.assertFalse(state["pill"], "the pill is a phone layout, not a redesign of both")


class FiltersReachTheCanvas(DirectionAOnAPhone):
    """A filter has to change the picture, not only the number beside it.

    The layout is memoised because it is the most expensive thing on the page, and its key
    listed every input to visible() EXCEPT the two newest: the layer and the status filter.
    So choosing "Stripe" or "unresolved" recomputed the page count and reused the cached
    drawing - the dropdown said 35 nodes over a canvas still showing all 392. Nothing threw,
    nothing looked broken in isolation, and the tool was lying about its own view.
    """

    def _drive(self, steps, width=390):
        from playwright.sync_api import sync_playwright

        from seamcheck.console import build_console
        from seamcheck.mapdata import build_map
        from seamcheck.renderers.map_html import render
        from seamcheck.report import build_report

        graph = self._phone_graph()
        html = render(
            build_map(graph, {"orders-main": {s.id for s in graph.symbols}}, git_sha="0" * 12),
            console=build_console(graph, build_report(
                graph=graph, diff=None, entries=[], git_sha="0" * 12)))
        path = pathlib.Path(tempfile.mkdtemp()) / "m.html"
        path.write_text(html, encoding="utf-8")

        with sync_playwright() as playwright:
            try:
                browser = playwright.chromium.launch()
            except Exception as error:  # pragma: no cover
                raise unittest.SkipTest(f"no chromium: {error}") from None
            page = browser.new_page(viewport={"width": width, "height": 780})
            page.goto(path.as_uri(), wait_until="load")
            page.wait_for_timeout(250)
            page.select_option("#vw", "map") if width <= 720 else page.click(
                '#nav .nv[data-key="map"]')
            page.wait_for_timeout(300)
            counts = []
            for step in steps:
                step(page)
                page.wait_for_timeout(300)
                counts.append(page.evaluate(
                    "() => document.querySelectorAll('#cv g[data-id]').length"
                    " || document.querySelectorAll('#cv rect').length"))
            browser.close()
        return counts

    def test_a_status_filter_redraws_the_canvas(self):
        base, filtered, restored = self._drive([
            lambda p: None,
            lambda p: p.click('#colourkey .seg button[data-status="unresolved"]'),
            lambda p: p.click('#colourkey .seg button[data-status=""]'),
        ])
        self.assertLess(filtered, base, "the canvas kept drawing everything")
        self.assertEqual(restored, base)

    def test_a_layer_redraws_the_canvas(self):
        """Asserted on WHAT is drawn, not how much.

        A count alone cannot tell a redrawn canvas from a stale one that happens to hold
        the same number of nodes - which is exactly what a small fixture produces.
        """
        labels = self._labels([
            lambda p: None,
            lambda p: p.select_option("#ly", "stripe"),
            lambda p: p.select_option("#ly", ""),
        ])
        base, stripe, restored = labels
        self.assertIn("api/orders/", " ".join(base), "the full map should hold the route")
        self.assertNotIn("api/orders/", " ".join(stripe), "the layer kept drawing everything")
        self.assertTrue(any("webhook" in text or "charge" in text for text in stripe),
                        f"the Stripe layer drew none of Stripe: {stripe}")
        self.assertIn("api/orders/", " ".join(restored), "resetting did not restore the map")

    def _labels(self, steps, width=390):
        """The text actually on the canvas after each step."""
        from playwright.sync_api import sync_playwright

        from seamcheck.console import build_console
        from seamcheck.mapdata import build_map
        from seamcheck.renderers.map_html import render
        from seamcheck.report import build_report

        graph = self._phone_graph()
        html = render(
            build_map(graph, {"orders-main": {s.id for s in graph.symbols}}, git_sha="0" * 12),
            console=build_console(graph, build_report(
                graph=graph, diff=None, entries=[], git_sha="0" * 12)))
        path = pathlib.Path(tempfile.mkdtemp()) / "m.html"
        path.write_text(html, encoding="utf-8")
        out = []
        with sync_playwright() as playwright:
            try:
                browser = playwright.chromium.launch()
            except Exception as error:  # pragma: no cover
                raise unittest.SkipTest(f"no chromium: {error}") from None
            page = browser.new_page(viewport={"width": width, "height": 780})
            page.goto(path.as_uri(), wait_until="load")
            page.wait_for_timeout(250)
            page.select_option("#vw", "map")
            page.wait_for_timeout(300)
            for step in steps:
                step(page)
                page.wait_for_timeout(300)
                out.append(page.evaluate(
                    "() => [...document.querySelectorAll('#cv text')].map(t => t.textContent)"))
            browser.close()
        return out

    def test_a_service_layer_is_not_scoped_to_the_open_page(self):
        """A Stripe webhook hangs off no page entry - it is reached by Stripe."""
        base, stripe = self._drive([
            lambda p: None,
            lambda p: p.select_option("#ly", "stripe"),
        ])
        self.assertGreater(stripe, 0, "picking a service drew an empty canvas")


class PanelBehaviour(DirectionAOnAPhone):
    """Two things the phone review caught, and one measure of the screen it caught them on."""

    def _panel(self, steps, width=390):
        from playwright.sync_api import sync_playwright

        from seamcheck.console import build_console
        from seamcheck.mapdata import build_map
        from seamcheck.renderers.map_html import render
        from seamcheck.report import build_report

        graph = self._phone_graph()
        html = render(
            build_map(graph, {"orders-main": {s.id for s in graph.symbols}}, git_sha="0" * 12),
            console=build_console(graph, build_report(
                graph=graph, diff=None, entries=[], git_sha="0" * 12)))
        path = pathlib.Path(tempfile.mkdtemp()) / "m.html"
        path.write_text(html, encoding="utf-8")
        with sync_playwright() as playwright:
            try:
                browser = playwright.chromium.launch()
            except Exception as error:  # pragma: no cover
                raise unittest.SkipTest(f"no chromium: {error}") from None
            page = browser.new_page(viewport={"width": width, "height": 780})
            page.goto(path.as_uri(), wait_until="load")
            page.wait_for_timeout(250)
            out = [step(page) or page.wait_for_timeout(250) for step in steps]
            state = page.evaluate("""() => {
              const panel = document.querySelector('#panel');
              const seen = el => getComputedStyle(el).display !== 'none';
              let text = '';
              const walk = el => { if (!seen(el)) return;
                if (el.tagName === 'DETAILS' && !el.open) {
                  text += el.querySelector('summary').textContent + ' '; return; }
                if (!el.children.length) { text += el.textContent + ' '; return; }
                [...el.children].forEach(walk); };
              walk(panel);
              return {visible: text.trim().replace(/\\s+/g, ' ').length,
                      folds: panel.querySelectorAll('details.explain').length,
                      rows: panel.querySelectorAll('.row').length,
                      head: (panel.querySelector('h2') || {}).textContent || '',
                      empty: (() => { const e = document.getElementById('nothing');
                                      return e && !e.hidden ? e.textContent : ""; })()};
            }""")
            browser.close()
        del out
        return state

    def test_the_overview_leads_with_numbers_not_prose(self):
        """Six hundred words stood in front of the counts a reader opened the page for."""
        state = self._panel([])
        self.assertGreaterEqual(state["folds"], 2, "the prose is not folded")
        self.assertLess(state["visible"], 1200, "still a wall of text on first sight")

    def test_show_as_list_stays_on_the_map(self):
        """No section is keyed "map", so renderPanel returned and left the Overview up."""
        state = self._panel([
            lambda p: p.select_option("#vw", "map"),
            lambda p: p.click("#aslist"),
        ])
        self.assertNotEqual(state["head"], "Overview", "the list navigated away from the map")
        self.assertGreater(state["rows"], 0, "the map list is empty")

    def test_an_empty_filter_combination_explains_itself(self):
        """Stripe has no unresolved symbols, so the pair is legitimately empty.

        An empty canvas is indistinguishable from a broken one, and the reader set the two
        filters one at a time and cannot see the combination.
        """
        state = self._panel([
            lambda p: p.select_option("#vw", "map"),
            lambda p: p.select_option("#ly", "stripe"),
            lambda p: p.click('#colourkey .seg button[data-status="unresolved"]'),
        ])
        self.assertIn("Nothing is both", state["empty"])


class ThePhoneCanvas(DirectionAOnAPhone):
    """On a 15 Pro Max the map got about 40% of the screen. It is the page; it gets it all.

    The header was a BLOCK above the canvas - brand line, reading, view picker, crumb row -
    and with the pill at the bottom the drawing was squeezed into what was left. Overlaying
    the chrome costs nothing, because map under a control is still map and a reader pans it
    out from under.
    """

    def _measure(self, width=430, height=839):
        from playwright.sync_api import sync_playwright

        from seamcheck.console import build_console
        from seamcheck.mapdata import build_map
        from seamcheck.renderers.map_html import render
        from seamcheck.report import build_report

        graph = self._phone_graph()
        html = render(
            build_map(graph, {"orders-main": {s.id for s in graph.symbols}}, git_sha="0" * 12),
            console=build_console(graph, build_report(
                graph=graph, diff=None, entries=[], git_sha="0" * 12)))
        path = pathlib.Path(tempfile.mkdtemp()) / "m.html"
        path.write_text(html, encoding="utf-8")
        with sync_playwright() as playwright:
            try:
                browser = playwright.chromium.launch()
            except Exception as error:  # pragma: no cover
                raise unittest.SkipTest(f"no chromium: {error}") from None
            page = browser.new_page(viewport={"width": width, "height": height},
                                    is_mobile=True, has_touch=True)
            page.goto(path.as_uri(), wait_until="load")
            page.wait_for_timeout(250)
            page.select_option("#vw", "map")
            page.wait_for_timeout(350)
            state = page.evaluate("""() => {
              const top = document.querySelector('.top').getBoundingClientRect();
              const pill = document.querySelector('.pill');
              const pr = pill ? pill.getBoundingClientRect() : null;
              const main = document.querySelector('.main').getBoundingClientRect();
              return {
                canvasShare: main.height / window.innerHeight,
                clearShare: (window.innerHeight - top.height - (pr ? pr.height : 0))
                            / window.innerHeight,
                viewInPill: !!(pill && pill.querySelector('#vw')),
                topSelects: document.querySelectorAll('.top select').length,
                headerOverlays: getComputedStyle(document.querySelector('.top')).position,
                canDragUnderHeader:
                  getComputedStyle(document.querySelector('.top')).pointerEvents === 'none',
                noBounce: getComputedStyle(document.body).overscrollBehaviorY === 'none',
              };
            }""")
            page.click('#colourkey .seg button[data-status="unresolved"]')
            page.wait_for_timeout(300)
            state["filtering"] = page.evaluate(
                "() => document.querySelector('.pill').classList.contains('filtering')")
            state["note"] = page.evaluate(
                "() => (document.getElementById('fnote') || {}).textContent || ''")
            page.click("#fnote button")
            page.wait_for_timeout(300)
            state["clearedNote"] = page.evaluate("() => !!document.getElementById('fnote')")
            state["clearedFiltering"] = page.evaluate(
                "() => document.querySelector('.pill').classList.contains('filtering')")
            browser.close()
        return state

    def test_the_canvas_fills_the_screen(self):
        self.assertGreaterEqual(self._measure()["canvasShare"], 0.99)

    def test_most_of_the_screen_is_unobstructed_map(self):
        self.assertGreater(self._measure()["clearShare"], 0.65)

    def test_the_chrome_floats_and_does_not_swallow_the_drag(self):
        state = self._measure()
        self.assertEqual(state["headerOverlays"], "absolute")
        self.assertTrue(state["canDragUnderHeader"], "the header eats gestures over the map")
        self.assertTrue(state["noBounce"], "the page will rubber-band mid-pan on iOS")

    def test_the_view_picker_is_not_built_twice(self):
        state = self._measure()
        self.assertTrue(state["viewInPill"])
        self.assertEqual(state["topSelects"], 0, "two rows of selects for one control surface")

    def test_an_active_filter_is_obvious_and_clearable(self):
        """A reader sets a filter, pans for a minute, and comes back to a partial map."""
        state = self._measure()
        self.assertTrue(state["filtering"], "nothing marks the map as filtered")
        self.assertIn("unresolved", state["note"])
        self.assertFalse(state["clearedNote"], "clear did not remove the notice")
        self.assertFalse(state["clearedFiltering"])
