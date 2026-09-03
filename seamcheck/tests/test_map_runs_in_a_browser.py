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



def _open_lens(page, key: str) -> None:
    """Pick a lens. Overview opens first and the lens list lives inside the one menu, on
    every width, so the menu opens before anything in it can be tapped."""
    if page.get_attribute("#menubtn", "aria-expanded") != "true":
        page.click("#menubtn")
    page.click(f'#nav .nv[data-key="{key}"]')


def _goto_page_with(page, status: str) -> None:
    """Open the first page holding a node of this status. A status the open page has
    none of is hidden from the colour key rather than offered as a chip that filters
    nothing, so a test that taps a chip has to stand on a page where it exists."""
    page.evaluate("""(status) => {
        const sel = document.getElementById('pg');
        const i = [...sel.options].findIndex(o =>
            (PAGES[Number(o.value)].st[status] || 0) > 0);
        if (i < 0) throw new Error('no page holds a ' + status + ' node');
        sel.selectedIndex = i;
        sel.dispatchEvent(new Event('change', {bubbles: true}));
    }""", status)
    # The page's rows are decoded on demand; wait until they are in.
    page.wait_for_function("() => !!PAGES[Number(document.getElementById('pg').value)].nodes")
    page.wait_for_timeout(200)


class MapRunsInABrowser(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        try:
            from playwright.sync_api import sync_playwright  # noqa: F401
        except ImportError:  # pragma: no cover - depends on the optional extra
            raise unittest.SkipTest("playwright is not installed (the observe extra)") from None

    def _document(self, series=None):
        from seamcheck.mapdata import build_map
        from seamcheck.renderers.map_html import render_document

        graph = _fixture_graph()
        pages = {"orders-main": {s.id for s in graph.symbols}}
        connectivity = build_map(graph, pages, git_sha="0" * 12)
        return render_document(connectivity, console=_console_for(graph), series=series)

    def _render(self, series=None) -> str:
        path = pathlib.Path(tempfile.mkdtemp()) / "map.html"
        path.write_text(self._document(series).single_file(), encoding="utf-8")
        return path.as_uri()

    def _render_bundle(self) -> str:
        from seamcheck.api import write_map_document

        folder = pathlib.Path(tempfile.mkdtemp()) / "map"
        written, _ = write_map_document(self._document(), str(folder) + "/")
        return pathlib.Path(written).as_uri()

    def _drawn(self, url: str) -> dict:
        """Open a map and report what it drew, plus which data it went to fetch."""
        from playwright.sync_api import sync_playwright

        errors: list[str] = []
        fetched: list[str] = []
        with sync_playwright() as playwright:
            try:
                browser = playwright.chromium.launch()
            except Exception as error:  # pragma: no cover - no browser downloaded
                raise unittest.SkipTest(f"no chromium: {error}") from None
            page = browser.new_page()
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
            page.on("request", lambda r: fetched.append(r.url.rsplit("/", 1)[-1])
                    if "/data/" in r.url else None)
            page.goto(url, wait_until="load")
            page.wait_for_function("() => !!PAGES[Number(document.getElementById('pg').value)].nodes")
            page.wait_for_timeout(300)
            page.fill("#q", "orders")
            page.wait_for_timeout(300)
            state = page.evaluate("""() => ({
                nodes: document.querySelectorAll('#cv g[data-id]').length,
                pages: document.querySelectorAll('#pg option').length,
                hits: document.getElementById('qn').textContent.trim(),
                leftovers: document.querySelectorAll('script[src*="data/"]').length,
            })""")
            browser.close()
        state["errors"] = errors
        state["fetched"] = sorted(set(fetched))
        return state

    def test_the_bundle_opened_from_a_file_url_draws_the_same_map(self):
        """The folder form fetches its rows as classic scripts - the one loader a
        `file://` page is allowed. It must draw exactly what the single file draws, fetch
        only the chunks it looked at, and leave no script tags behind."""
        single = self._drawn(self._render())
        bundle = self._drawn(self._render_bundle())

        self.assertEqual(bundle["errors"], [])
        self.assertEqual(single["errors"], [])
        self.assertGreater(bundle["nodes"], 0, "the bundle drew nothing")
        self.assertEqual((bundle["nodes"], bundle["pages"], bundle["hits"]),
                         (single["nodes"], single["pages"], single["hits"]))
        self.assertEqual(single["fetched"], [], "the single file went to the network")
        self.assertIn("p0.js", bundle["fetched"])
        self.assertIn("search.js", bundle["fetched"])
        self.assertEqual(bundle["leftovers"], 0, "loader script tags were not removed")

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
        # Four statuses. The way back to everything is the clear on the filter notice.
        self.assertEqual(populated["key"], 4, "the status filter is missing")
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
            # draws is selected. Overview opens first, and the lens list lives inside the
            # one menu, so the menu opens before anything in it can be tapped.
            _open_lens(page, "map")
            page.wait_for_timeout(200)
            # A filter can only change a page that mixes statuses, and a status the page
            # has none of is hidden rather than offered as a chip that filters nothing.
            # So: go to the first page with two statuses, then tap the first chip shown.
            mixed = page.evaluate("""() => {
                const pages = [...document.querySelectorAll('#pg option')].map(o => o.value);
                return pages.findIndex(v =>
                    Object.keys(PAGES[Number(v)].st).length > 1);
            }""")
            page.select_option("#pg", index=mixed)
            page.wait_for_timeout(200)
            before = page.eval_on_selector("#pg option:checked", "el => el.textContent")
            chip = '#colourkey .seg button[data-status]:not([data-status=""]):not([hidden])'
            page.click(chip)
            page.wait_for_timeout(200)
            after = page.eval_on_selector("#pg option:checked", "el => el.textContent")
            pressed = page.get_attribute(chip + '[aria-pressed="true"]', "aria-pressed")
            browser.close()

        self.assertNotEqual(before, after, "filtering changed nothing")
        self.assertEqual(pressed, "true")


if __name__ == "__main__":
    unittest.main()


class RowsArriveWhenOpened(SimpleTestCase):
    """A section's rows are a chunk, read on first look.

    Its own class: the harness above is inherited by every group of browser tests, and
    a test defined on it runs once per group.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        try:
            from playwright.sync_api import sync_playwright  # noqa: F401
        except ImportError:  # pragma: no cover - depends on the optional extra
            raise unittest.SkipTest("playwright is not installed (the observe extra)") from None

    def _lazy_document(self):
        """A findings section too long to ride inline."""
        from seamcheck.console import Console, Row, Section
        from seamcheck.mapdata import build_map
        from seamcheck.renderers.map_html import render_document

        graph = _fixture_graph()
        connectivity = build_map(graph, {"orders-main": {s.id for s in graph.symbols}},
                                 git_sha="0" * 12)
        rows = [Row(id=f"f{i}", label=f"lazy-row-{i}", kind="url", status="unresolved",
                    file="app/views.py", line=i, note="n", snippet="") for i in range(80)]
        console = Console(git_sha="0" * 12, generated_at="", baseline_sha=None, backend={},
                          frontend={}, counts={}, groups=[],
                          sections=[Section("findings", "Findings", "b", rows)])
        files = [{"path": f"app/mod_{i}/views.py", "counts": {"connected": 1},
                  "declarations": 2, "known": 1} for i in range(120)]
        return render_document(connectivity, console=console, files=files)

    def _opened_lazily(self, url: str) -> dict:
        """Open the findings list, then the files, and report what each drew."""
        from playwright.sync_api import sync_playwright

        errors: list[str] = []
        with sync_playwright() as playwright:
            try:
                browser = playwright.chromium.launch()
            except Exception as error:  # pragma: no cover - no browser downloaded
                raise unittest.SkipTest(f"no chromium: {error}") from None
            page = browser.new_page()
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(url, wait_until="load")
            page.wait_for_timeout(250)
            page.evaluate("() => { if (window.setSheet) setSheet(true); }")
            _open_lens(page, "findings")
            page.wait_for_function("() => document.querySelectorAll('#panel .row').length > 0")
            # The list pages at 60 and says how many there are; the count is the proof
            # the whole chunk arrived, not the rows on screen.
            rows = page.evaluate("() => document.querySelectorAll('#panel .row').length")
            more = page.evaluate("() => (document.getElementById('cmore') || {}).textContent || ''")
            head = page.evaluate("() => document.querySelector('#panel h2').textContent")
            _open_lens(page, "files")
            page.wait_for_function("() => document.querySelectorAll('#panel .tree .fl').length > 0")
            files = page.evaluate("() => document.querySelectorAll('#panel .tree .fl').length")
            # A click on a file draws it on the map; the page it lands on comes from the
            # same chunk as the list, so the lookup must be there by now.
            # The tree opens folded, so the row is reached by script rather than by eye.
            page.evaluate("() => document.querySelector('#panel .tree .fl').click()")
            page.wait_for_timeout(300)
            crumb = page.evaluate("() => document.getElementById('crumb').textContent")
            browser.close()
        return {"rows": rows, "more": more, "head": head, "files": files,
                "crumb": crumb, "errors": errors}

    def test_a_section_arrives_when_opened(self):
        """Its rows are a chunk now. Both forms of the map must still draw them, after a
        moment that says it is loading rather than a panel that stays empty."""
        from seamcheck.api import write_map_document

        document = self._lazy_document()
        index, _ = document.bundle()
        self.assertNotIn("lazy-row-79", index)
        self.assertNotIn("mod_119", index)

        folder = pathlib.Path(tempfile.mkdtemp())
        single = folder / "map.html"
        single.write_text(document.single_file(), encoding="utf-8")
        bundled, _ = write_map_document(document, str(folder / "bundle") + "/")

        for url in (single.as_uri(), pathlib.Path(bundled).as_uri()):
            with self.subTest(url=url):
                state = self._opened_lazily(url)
                self.assertEqual(state["errors"], [])
                self.assertEqual(state["head"], "Findings")
                self.assertEqual(state["rows"], 60)
                self.assertIn("60 of 80", state["more"])
                self.assertEqual(state["files"], 120)
                self.assertIn("app/mod_", state["crumb"])


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
            _open_lens(page, "changes")
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


class AppearancePicker(MapRunsInABrowser):
    """Appearance is a pack, not a switch.

    The map is read on a light phone as often as on a dark desk, and a reader may hate
    the colour the author liked. The control offers named packs; the ones that read on
    either ground also offer the ground, and the choice survives a reload.
    """

    def _pick(self, steps, reload=False):
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
            _open_lens(page, "map")
            if steps:
                page.click("#tmode")
            for step in steps:
                page.click(step)
                page.wait_for_timeout(100)
            if reload:
                page.reload(wait_until="load")
                page.wait_for_timeout(200)
            stamped = page.evaluate("""() => ({
                pack: document.documentElement.getAttribute('data-pack'),
                mode: document.documentElement.getAttribute('data-mode'),
                modesOffered: !document.querySelector('#packmenu .modes').hidden,
            })""")
            browser.close()
        return stamped

    def test_the_default_is_the_dark_aurora_and_offers_no_ground(self):
        """A light Aurora is not Aurora, so the ground row is withheld for it."""
        state = self._pick([])
        self.assertEqual(state["pack"], "aurora")
        self.assertIsNone(state["mode"], "a single-world pack must not be stamped a mode")
        self.assertFalse(state["modesOffered"])

    def test_a_two_ground_pack_offers_light_and_dark(self):
        state = self._pick(['#packmenu [data-pk="slate"]'])
        self.assertEqual(state["pack"], "slate")
        self.assertTrue(state["modesOffered"])
        self.assertEqual(state["mode"], "light", "light is the ground a phone is read on")
        state = self._pick(['#packmenu [data-pk="slate"]', '#packmenu [data-md="dark"]'])
        self.assertEqual(state["mode"], "dark")

    def test_the_choice_survives_a_reload(self):
        state = self._pick(['#packmenu [data-pk="slate"]', '#packmenu [data-md="dark"]'],
                           reload=True)
        self.assertEqual((state["pack"], state["mode"]), ("slate", "dark"))


class DirectionAOnAPhone(MapRunsInABrowser):
    """One menu over the canvas, on every width, and the canvas gets the rest.

    The header was six stacked rows - brand, view, commit, page, crumb, colour key - and
    the map got roughly a third of a 390px screen. Every control now lives in the one
    dropdown behind the menu button, except the colour key, which is the filter a reader
    reaches for most and floats over the canvas on its own.
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
            _open_lens(page, "map")
            page.wait_for_timeout(300)
            state = page.evaluate("""() => ({
                menu: !!document.querySelector('#mapsheet'),
                page: !!document.querySelector('.hud #pg'),
                layer: !!document.querySelector('#mapsheet #ly'),
                lenses: !!document.querySelector('#mapsheet #nav .nv'),
                search: !!document.querySelector('#mapsheet #q'),
                status: !!document.querySelector('.hud #colourkey'),
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

    def test_every_control_is_in_the_one_menu(self):
        state = self._open()
        self.assertTrue(state["menu"], "no menu was built")
        for control in ("layer", "lenses", "search"):
            self.assertTrue(state[control], f"the {control} control is not in the menu")
        # The two filters a reader reaches for most float over the canvas on their own.
        self.assertTrue(state["page"], "the page picker must sit in a corner, not a menu")
        self.assertTrue(state["status"], "the colour key must float over the canvas")

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

    def test_the_desktop_gets_the_same_menu(self):
        """One layout, not a phone one and a desk one that drift apart."""
        state = self._open(width=1200)
        self.assertTrue(state["menu"])
        self.assertTrue(state["page"] and state["layer"] and state["lenses"])


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
            _open_lens(page, "map")
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
            lambda p: _goto_page_with(p, "unresolved"),
            lambda p: p.click('#colourkey .seg button[data-status="unresolved"]'),
            lambda p: p.click("#fnote button"),
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
            _open_lens(page, "map")
            page.wait_for_timeout(300)
            page.evaluate("() => setSheet(true)")
            page.wait_for_timeout(200)
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
            page.evaluate("() => { if (window.setSheet) setSheet(true); }")
            page.wait_for_timeout(150)
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
            lambda p: _open_lens(p, "map"),
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
            lambda p: _open_lens(p, "map"),
            lambda p: _goto_page_with(p, "unresolved"),
            lambda p: p.click('#colourkey .seg button[data-status="unresolved"]'),
            lambda p: p.select_option("#ly", "stripe"),
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
            _open_lens(page, "map")
            _goto_page_with(page, "unresolved")
            page.wait_for_timeout(350)
            page.evaluate("() => setSheet(true)")
            page.wait_for_timeout(200)
            page.evaluate("() => setSheet(false)")
            page.wait_for_timeout(200)
            state = page.evaluate("""() => {
              const main = document.querySelector('.main').getBoundingClientRect();
              // The chrome is the corners: what they cover, top to bottom, is what the
              // reader cannot see map through.
              const huds = [...document.querySelectorAll('.hud')].map(h => h.getBoundingClientRect());
              const covered = huds.reduce((a, r) => a + r.height, 0);
              const between = [...document.querySelectorAll('.hud')].map(h => {
                const r = h.getBoundingClientRect();
                const y = r.top > window.innerHeight / 2 ? r.top - 40 : r.bottom + 40;
                const el = document.elementFromPoint(r.left + r.width / 2, y);
                return el && el.closest('#cv, .main') ? 'map' : (el && el.className);
              });
              return {
                canvasShare: main.height / window.innerHeight,
                clearShare: (window.innerHeight - covered) / window.innerHeight,
                pickers: document.querySelectorAll('#pg').length,
                hudsFloat: [...document.querySelectorAll('.hud')].every(h =>
                  getComputedStyle(h).position === 'absolute'),
                mapUnderChrome: between.every(b => b === 'map'),
                noBounce: getComputedStyle(document.body).overscrollBehaviorY === 'none',
              };
            }""")
            page.click('#colourkey .seg button[data-status="unresolved"]')
            page.wait_for_timeout(300)
            state["filtering"] = page.evaluate(
                "() => document.querySelector('#colourkey').classList.contains('filtering')")
            state["note"] = page.evaluate(
                "() => (document.getElementById('fnote') || {}).textContent || ''")
            page.click("#fnote button")
            page.wait_for_timeout(300)
            state["clearedNote"] = page.evaluate(
                "() => !document.getElementById('fnote').hidden")
            state["clearedFiltering"] = page.evaluate(
                "() => document.querySelector('#colourkey').classList.contains('filtering')")
            browser.close()
        return state

    def test_the_canvas_fills_the_screen(self):
        self.assertGreaterEqual(self._measure()["canvasShare"], 0.99)

    def test_most_of_the_screen_is_unobstructed_map(self):
        self.assertGreater(self._measure()["clearShare"], 0.65)

    def test_the_chrome_floats_and_does_not_swallow_the_drag(self):
        state = self._measure()
        self.assertTrue(state["hudsFloat"], "the chrome takes layout instead of overlaying")
        self.assertTrue(state["mapUnderChrome"], "something other than map sits under a corner")
        self.assertTrue(state["noBounce"], "the page will rubber-band mid-pan on iOS")

    def test_the_page_picker_is_not_built_twice(self):
        """Two copies of the page select is two answers to which page is open."""
        self.assertEqual(self._measure()["pickers"], 1)

    def test_an_active_filter_is_obvious_and_clearable(self):
        """A reader sets a filter, pans for a minute, and comes back to a partial map."""
        state = self._measure()
        self.assertTrue(state["filtering"], "nothing marks the map as filtered")
        self.assertIn("unresolved", state["note"])
        self.assertFalse(state["clearedNote"], "clear did not remove the notice")
        self.assertFalse(state["clearedFiltering"])


class GesturesAndCommits(DirectionAOnAPhone):
    """Two phone reports: a pinch zoomed the PAGE, and a commit drew a blank canvas."""

    def test_webkit_pinch_over_the_canvas_is_refused(self):
        """iOS ignores touch-action AND user-scalable, and fires its own gesture events.

        Without refusing those, two fingers on the map zoom the whole document - which is
        what the screenshot showed: the header at three times its size and the canvas
        untouched underneath.
        """
        from seamcheck.renderers.map_html import render

        out = render(self._map_for_source())
        for event in ("gesturestart", "gesturechange", "gestureend"):
            self.assertIn(event, out, f"{event} is not refused, so iOS will zoom the page")
        self.assertIn('svg.addEventListener("dblclick"', out)

    @staticmethod
    def _map_for_source():
        from seamcheck.mapdata import build_map
        graph = GesturesAndCommits._phone_graph()
        return build_map(graph, {"orders-main": {s.id for s in graph.symbols}}, git_sha="0" * 12)

    def test_a_commit_that_changed_nothing_says_so_in_the_list(self):
        """Most commits touch docs, config or tests - none of which the scan reads."""
        from seamcheck.renderers.map_html import render

        out = render(self._map_for_source())
        self.assertIn('" · no change"', out)
        self.assertIn('changed`', out)

    def test_an_empty_commit_explains_itself_on_the_canvas(self):
        from seamcheck.renderers.map_html import render

        out = render(self._map_for_source())
        self.assertIn("This commit changed nothing the scan reads", out)
        self.assertIn("Documentation, config and tests are not in the graph", out)
