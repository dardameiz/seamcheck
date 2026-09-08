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


def _open_map_list(page) -> None:
    """The map, shown as a list. The Findings view was a SECOND list of the same graph with
    a second set of filters to keep in step, so it is gone; every reader of a list of rows
    is this one now."""
    _open_lens(page, "map")
    page.evaluate("() => { if (!asList) { asList = true; switchTo('map'); } }")


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
            _open_map_list(page)
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

    def test_a_chunk_arrives_when_opened(self):
        """Rows and files ride in chunks now. Both forms of the map must still draw them,
        after a moment that says it is loading rather than a panel that stays empty.

        It used to open the Findings view, which was a second list of the same graph beside
        the map's own; that view is gone, so this stands on the list the map itself draws.
        The subject was never the view - it is that a chunk arrives and gets rendered."""
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
                # The rows are there at all: the chunk was fetched, decoded and drawn.
                self.assertGreater(state["rows"], 0)
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
                layerInFilters: !!document.querySelector('#filtersheet #ly'),
                pickersInFilters: !!document.querySelector('#filtersheet #pg'),
                // On the glass means a direct child of the corner - the sheets are
                // inside that corner too, so `.hud #pg` cannot tell them apart.
                pageOnGlass: !!document.querySelector('.hud > #pgwrap #pg'),
                lenses: !!document.querySelector('#mapsheet #nav .nv'),
                searchInMenu: !!document.querySelector('#mapsheet #q'),
                searchInFilters: !!document.querySelector('#filtersheet #q'),
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

    def test_every_control_is_behind_one_of_the_two_buttons(self):
        """Two buttons, two questions. Menu answers "which view"; Filter answers
        "narrowed to what". The menu used to answer both - it carried the view list AND
        the emphasis filter AND the search - so one intention sat behind two buttons and
        the five words naming the views were the top fifth of a panel."""
        state = self._open()
        self.assertTrue(state["menu"], "no menu was built")
        self.assertTrue(state["lenses"], "the view list is not in the menu")
        # ...and NOTHING else is in the menu.
        self.assertFalse(state["layer"], "emphasis belongs behind Filter, not Menu")
        self.assertFalse(state["searchInMenu"], "the search belongs behind Filter")
        # Everything that narrows the map is behind the one button that says so.
        self.assertTrue(state["layerInFilters"], "emphasis is behind neither button")
        self.assertTrue(state["pickersInFilters"], "the page picker is behind neither button")
        self.assertTrue(state["searchInFilters"], "the search is behind neither button")
        # Still the one filter a reader reaches for most, still on the glass.
        self.assertTrue(state["status"], "the colour key must float over the canvas")

    def test_a_phone_puts_the_filters_behind_the_filter_button(self):
        state = self._open()
        self.assertTrue(state["layerInFilters"], "emphasis belongs in the filter sheet")
        self.assertTrue(state["pickersInFilters"], "the pickers belong in the filter sheet")
        self.assertFalse(state["pageOnGlass"],
                         "nothing but the two buttons on the glass")

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
        """One layout, not a phone one and a desk one that drift apart.

        It used to be two: the desk monitor kept the pickers on the glass and half the
        filters in the menu, and only a phone got the two-button split. The phone shape
        was the better one at every width - on the glass the pickers competed with the
        map for the top of the screen and truncated anyway."""
        state = self._open(width=1200)
        self.assertTrue(state["menu"] and state["lenses"])
        self.assertTrue(state["layerInFilters"] and state["pickersInFilters"]
                        and state["searchInFilters"],
                        "a desk monitor gets the same two buttons as a phone")
        self.assertFalse(state["pageOnGlass"], "nothing but the two buttons on the glass")


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


class PageThenSection(SimpleTestCase):
    """Two pickers: the page a person recognises, then the bundle inside it.

    Own class for the reason RowsArriveWhenOpened is: the harness above runs its tests
    once per group that inherits it.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        try:
            from playwright.sync_api import sync_playwright  # noqa: F401
        except ImportError:  # pragma: no cover - depends on the optional extra
            raise unittest.SkipTest("playwright is not installed (the observe extra)") from None

    def _url(self) -> str:
        from seamcheck.mapdata import build_map
        from seamcheck.pagenames import PageName
        from seamcheck.renderers.map_html import render_document

        graph = _fixture_graph()
        # A page is seeded by FILES; two bundles on one template, one bundle on another.
        every = {s.file for s in graph.symbols}
        pages = {"orders-main": every, "orders-side": every, "home-main": every}
        names = {"orders-main": PageName("Orders", "/orders/", "orders-main"),
                 "orders-side": PageName("Orders", "/orders/", "orders-side"),
                 "home-main": PageName("Home", "/", "home-main")}
        connectivity = build_map(graph, pages, git_sha="0" * 12, names=names)
        document = render_document(connectivity, console=_console_for(graph))
        path = pathlib.Path(tempfile.mkdtemp()) / "map.html"
        path.write_text(document.single_file(), encoding="utf-8")
        return path.as_uri()

    def test_the_page_list_is_by_name_and_the_section_list_appears_only_when_there_is_one(self):
        from playwright.sync_api import sync_playwright

        errors: list[str] = []
        with sync_playwright() as playwright:
            try:
                browser = playwright.chromium.launch()
            except Exception as error:  # pragma: no cover - no browser downloaded
                raise unittest.SkipTest(f"no chromium: {error}") from None
            page = browser.new_page()
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(page_url := self._url(), wait_until="load")
            page.wait_for_timeout(250)
            _open_lens(page, "map")
            page.wait_for_timeout(200)
            read = "() => ({" \
                "pages: [...document.querySelectorAll('#pg option')].map(o => o.textContent)," \
                "sections: [...document.querySelectorAll('#sec option')].map(o => o.textContent)," \
                "sectionShown: !document.getElementById('secwrap').hidden," \
                "current: PAGES[current].page, crumb: document.getElementById('crumb').textContent," \
                "clear: (() => { const r = document.getElementById('readout').getBoundingClientRect();" \
                "  const l = document.querySelector('.hud.tl').getBoundingClientRect();" \
                "  const t = document.querySelector('.hud.tr').getBoundingClientRect();" \
                "  return r.width === 0 || (r.left >= l.right && r.right <= t.left); })()})"
            first = page.evaluate(read)
            # The other page, then one section inside it.
            page.select_option("#pg", index=1)
            page.wait_for_timeout(200)
            third = page.evaluate(read)
            page.select_option("#sec", index=1)
            page.wait_for_timeout(200)
            section = page.evaluate(read)
            browser.close()

        self.assertEqual(errors, [], page_url)
        self.assertEqual([p.split(" — ")[0] for p in first["pages"]][:2],
                         ["Home · /", "Orders · /orders/"],
                         "one row per page a person recognises, not per bundle")
        self.assertEqual(len(first["pages"]), 2 + 4, "then the not-reached buckets, as before")
        # The map opens on the first page: Home, which has one bundle and so no sections.
        self.assertEqual(first["current"], "home-main")
        self.assertFalse(first["sectionShown"])
        self.assertEqual(first["sections"], [])
        # Orders has two, so it opens on the whole page with both bundles offered.
        self.assertEqual(third["current"], "group:1")
        self.assertTrue(third["sectionShown"])
        self.assertEqual([s.split(" — ")[0] for s in third["sections"]],
                         ["Whole page", "orders-main", "orders-side"])
        # The readout does not repeat what the pickers say; it names what they do not.
        self.assertTrue(third["crumb"].startswith("Orders — "), third["crumb"])
        self.assertEqual(section["current"], "orders-main")
        self.assertTrue(section["crumb"].startswith("orders-main — "), section["crumb"])
        self.assertEqual(section["pages"], first["pages"],
                         "picking a section leaves the page list alone")
        # Two pickers reach past the centre of the screen, where the readout used to sit;
        # it starts after them now, and the right corner's buttons end it.
        for shot in (first, third, section):
            self.assertTrue(shot["clear"], "the readout sits under a corner's controls")


class StoreLayerInTheBrowser(SimpleTestCase):
    """Redis as a layer over the whole map, narrowed by the Page picker.

    The picker stays while a store is on - "Every page" first, then each page with how
    many of the store's nodes it reaches. Keys are parked by namespace, not by kind, and
    a key's card says which pages touch it, each a jump.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        try:
            from playwright.sync_api import sync_playwright  # noqa: F401
        except ImportError:  # pragma: no cover - depends on the optional extra
            raise unittest.SkipTest("playwright is not installed (the observe extra)") from None

    def _url(self) -> str:
        from seamcheck.mapdata import build_map
        from seamcheck.pagenames import PageName
        from seamcheck.renderers.map_html import render_document

        def sym(kind, label, file, status=Status.CONNECTED):
            return Symbol(id=f"{kind}:{label}", kind=kind, label=label, sub="", file=file, line=1,
                          status=status, snippet=label, chain=[label], note="")

        # A page is seeded by the fetches in its files and walked fetch -> url -> view ->
        # key, so each page gets that chain, and the keys hang off the view.
        symbols, edges = [], []
        def page_chain(name, keys):
            fetch = sym("fetch_target", f"/api/{name}/", f"static/{name}.js")
            url = sym("url", f"api/{name}/", "app/urls.py")
            view = sym("view", name, "app/views.py")
            symbols.extend([fetch, url, view])
            edges.append(Edge(from_id=fetch.id, to_id=url.id, status=Status.CONNECTED))
            edges.append(Edge(from_id=url.id, to_id=view.id, status=Status.CONNECTED))
            for k in keys:
                edges.append(Edge(from_id=view.id, to_id=k.id, status=Status.CONNECTED))

        arena = [sym("redis_key", f"user:{i}:stats", "app/arena.py") for i in range(30)]
        boards = [sym("redis_key", f"leaderboard:{n}", "app/home.py") for n in ("hourly", "daily", "season")]
        shared = sym("redis_key", "season:active", "app/shared.py")
        stray = [sym("redis_key", "cursor", "app/home.py"), sym("redis_key", "lock", "app/home.py")]
        orphan = sym("redis_key", "orphan:key", "app/worker.py", Status.UNUSED)
        symbols.extend(arena + boards + [shared] + stray + [orphan])
        page_chain("arena", arena + [shared])
        page_chain("home", boards + [shared] + stray)
        graph = Graph(symbols=symbols, edges=edges)
        pages = {"arena-main": {"static/arena.js"}, "home-main": {"static/home.js"}}
        names = {"arena-main": PageName("Arena", "/arena/", "arena-main"),
                 "home-main": PageName("Home", "/", "home-main")}
        connectivity = build_map(graph, pages, git_sha="0" * 12, names=names)
        document = render_document(connectivity, console=_console_for(graph))
        path = pathlib.Path(tempfile.mkdtemp()) / "map.html"
        path.write_text(document.single_file(), encoding="utf-8")
        return path.as_uri()

    def test_every_page_then_one_page_and_a_key_says_where_it_is_touched(self):
        from playwright.sync_api import sync_playwright

        errors: list[str] = []
        with sync_playwright() as playwright:
            try:
                browser = playwright.chromium.launch()
            except Exception as error:  # pragma: no cover - no browser downloaded
                raise unittest.SkipTest(f"no chromium: {error}") from None
            page = browser.new_page()
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(page_url := self._url(), wait_until="load")
            page.wait_for_timeout(250)
            _open_lens(page, "map")
            page.wait_for_timeout(200)
            read = "() => ({" \
                "pages: [...document.querySelectorAll('#pg option')].map(o => o.textContent)," \
                "picked: document.getElementById('pg').value," \
                "pickerShown: !document.getElementById('pgwrap').hidden," \
                "drawn: document.querySelectorAll('#cv .nd:not(.agg)').length," \
                "aggs: [...document.querySelectorAll('#cv .nd.agg')].map(g =>" \
                "  [g.dataset.key, g.querySelector('.big').textContent])," \
                "openCards: [...document.querySelectorAll('#cv .nd.agg.opened')]" \
                "  .map(g => g.dataset.key)," \
                "labels: [...document.querySelectorAll('#cv .nd:not(.agg) .lbl, #cv .nd:not(.agg) text')]" \
                "  .map(t => t.textContent)})"
            page.select_option("#ly", "redis")
            page.wait_for_function("() => !!PAGES[currentPageIndex()].nodes")
            page.wait_for_timeout(200)
            every = page.evaluate(read)
            home = page.evaluate("() => [...document.querySelectorAll('#pg option')]"
                                 ".find(o => o.textContent.startsWith('Home')).value")
            page.select_option("#pg", home)
            page.wait_for_timeout(200)
            narrowed = page.evaluate(read)
            # Back to the whole store, open the user:* namespace.
            page.select_option("#pg", "all")
            page.wait_for_timeout(200)
            page.click('#cv .nd.agg[data-key="redis_key/user"]')
            page.wait_for_timeout(200)
            opened = page.evaluate(read)
            # A key on two pages says so, and the first one is a jump.
            page.evaluate("() => show('redis_key:season:active')")
            page.wait_for_timeout(200)
            sheet = page.evaluate("() => [...document.querySelectorAll('#dbody [data-go]')]"
                                  ".map(b => [b.textContent, b.dataset.page])")
            page.click('#dbody [data-go]')
            page.wait_for_timeout(300)
            landed = page.evaluate("() => ({page: PAGES[current].page, layer,"
                                   " pickerShown: !document.getElementById('pgwrap').hidden,"
                                   " picked: document.getElementById('pg').value})")
            browser.close()

        self.assertEqual(errors, [], page_url)
        # The whole store: every key, the orphan included, with the picker offering pages.
        self.assertTrue(every["pickerShown"])
        self.assertEqual(every["picked"], "all")
        self.assertEqual(every["pages"][0], "Every page — 37 nodes")
        self.assertEqual([p.split(" — ")[0] for p in every["pages"][1:3]], ["Arena · /arena/", "Home · /"])
        self.assertIn("Home · / — 6 nodes", every["pages"], "how many of the store's nodes Home reaches")
        self.assertEqual(len(every["pages"]), 3, "a page reaching none of the store is not offered")
        # Thirty-seven keys are parked by namespace, the two lone ones under "other".
        self.assertEqual(dict(every["aggs"]), {"redis_key/user": "30", "redis_key/leaderboard": "3",
                                               "redis_key/": "4"})
        # Home: its three boards, the shared key and the two strays; no user:* card.
        self.assertEqual(narrowed["picked"], home)
        self.assertEqual(narrowed["aggs"], [])
        self.assertEqual(narrowed["drawn"], 6)
        self.assertEqual(narrowed["pages"][0], "Every page — 37 nodes", "the whole store's count holds")
        # Opening user:* lays out its thirty keys and leaves the other namespaces parked -
        # and the opened namespace keeps a card of its own, because a group with no card
        # can only be closed by opening a different one, which is how two wires into two
        # namespaces could never be seen at the same time.
        self.assertEqual(opened["drawn"], 30)
        self.assertEqual(dict(opened["aggs"]), {"redis_key/user": "30",
                                                "redis_key/leaderboard": "3",
                                                "redis_key/": "4"})
        self.assertEqual(opened["openCards"], ["redis_key/user"],
                         "the open group says so on its card")
        self.assertEqual([b[0] for b in sheet], ["Arena · /arena/ › arena-main", "Home · / › home-main"])
        # The jump lands on the page, with the layer off and the picker back.
        self.assertEqual(landed, {"page": "arena-main", "layer": "", "pickerShown": True,
                                  "picked": str(sheet[0][1])})


    def test_two_groups_can_be_open_at_once(self):
        """Opening a second group used to close the first, silently.

        The owner's words: "when I collapse one container or open one container, the other
        collapses, so sometimes I cannot see the big picture" - one wire ran into one
        namespace and another into a different one, and the two could never be on screen
        together. The only way through was "Show only this chain", which answers a
        different question.
        """
        from playwright.sync_api import sync_playwright

        errors: list[str] = []
        with sync_playwright() as playwright:
            try:
                browser = playwright.chromium.launch()
            except Exception as error:  # pragma: no cover - no browser downloaded
                raise unittest.SkipTest(f"no chromium: {error}") from None
            page = browser.new_page()
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(page_url := self._url(), wait_until="load")
            page.wait_for_timeout(250)
            _open_lens(page, "map")
            page.wait_for_timeout(200)
            page.select_option("#ly", "redis")
            page.wait_for_function("() => !!PAGES[currentPageIndex()].nodes")
            page.wait_for_timeout(250)

            def tap(key):
                page.evaluate("""(key) => {
                    const card = [...document.querySelectorAll('#cv .nd.agg')]
                        .find(g => g.dataset.key === key);
                    card.dispatchEvent(new MouseEvent('click', {bubbles: true}));
                }""", key)
                page.wait_for_timeout(200)

            def state():
                return page.evaluate("""() => ({
                    open: [...document.querySelectorAll('#cv .nd.agg.opened')]
                        .map(g => g.dataset.key).sort(),
                    drawn: document.querySelectorAll('#cv .nd:not(.agg)').length,
                })""")

            tap("redis_key/user")
            one = state()
            tap("redis_key/leaderboard")
            both = state()
            tap("redis_key/user")
            closed_one = state()
            browser.close()

        self.assertEqual(errors, [], page_url)
        self.assertEqual(one["open"], ["redis_key/user"])
        # The second stays open WITH the first - the whole point.
        self.assertEqual(both["open"], ["redis_key/leaderboard", "redis_key/user"])
        self.assertGreater(both["drawn"], one["drawn"], "both namespaces are laid out")
        # ...and tapping an open one closes only itself.
        self.assertEqual(closed_one["open"], ["redis_key/leaderboard"])

    def test_the_shared_layer_holds_what_two_pages_reach_and_the_card_says_so(self):
        from playwright.sync_api import sync_playwright

        errors: list[str] = []
        with sync_playwright() as playwright:
            try:
                browser = playwright.chromium.launch()
            except Exception as error:  # pragma: no cover - no browser downloaded
                raise unittest.SkipTest(f"no chromium: {error}") from None
            page = browser.new_page()
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(page_url := self._url(), wait_until="load")
            page.wait_for_timeout(250)
            _open_lens(page, "map")
            page.wait_for_timeout(200)
            read = "() => ({" \
                "pages: [...document.querySelectorAll('#pg option')].map(o => o.textContent)," \
                "pickerShown: !document.getElementById('pgwrap').hidden," \
                "cards: [...document.querySelectorAll('#cv .nd:not(.agg)')].map(g =>" \
                "  [g.dataset.id, g.querySelector('text.on') ? g.querySelector('text.on').textContent : '']" \
                ").filter(([id]) => id)})"
            offered = page.evaluate("() => [...document.querySelectorAll('#ly option')].map(o => o.value)")
            page.select_option("#ly", "shared")
            page.wait_for_function("() => !!PAGES[currentPageIndex()].nodes")
            page.wait_for_timeout(200)
            shared = page.evaluate(read)
            # Back on an ordinary page, the same card carries the same tag - and only it.
            page.select_option("#ly", "")
            page.wait_for_timeout(200)
            page.evaluate("() => pickPage(PAGES.findIndex(p => p.page === 'home-main'))")
            page.wait_for_function("() => !!PAGES[current].nodes")
            page.wait_for_timeout(200)
            page.evaluate("() => { focus = 'view:home'; draw(); }")
            page.wait_for_timeout(200)
            home = page.evaluate(read)
            browser.close()

        self.assertEqual(errors, [], page_url)
        self.assertIn("shared", offered)
        self.assertTrue(shared["pickerShown"])
        self.assertEqual(shared["pages"][0], "Every page — 1 node")
        # The one key both pages' views touch; the page-chains are each page's own.
        self.assertEqual(shared["cards"], [["redis_key:season:active", "on 2 pages"]])
        tagged = {id_: tag for id_, tag in home["cards"] if tag}
        self.assertEqual(tagged, {"redis_key:season:active": "on 2 pages"})
        self.assertGreater(len(home["cards"]), 1, "the rest of home's chain is drawn untagged")


class MarksInTheBrowser(SimpleTestCase):
    """A mark a person made is on the card, and one the code outgrew says RETURNED.

    The finding is back in the list either way; what the map adds is who said it was
    fine, when, and an Undo that puts the command on the clipboard - so a returned
    finding is picked up where the last person left it rather than judged cold.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        try:
            from playwright.sync_api import sync_playwright  # noqa: F401
        except ImportError:  # pragma: no cover - depends on the optional extra
            raise unittest.SkipTest("playwright is not installed (the observe extra)") from None

    def _url(self) -> str:
        from seamcheck.console import build_console
        from seamcheck.mapdata import build_map
        from seamcheck.renderers.map_html import render_document
        from seamcheck.report import build_report
        from seamcheck.triage import TriageEntry, TriageStatus, fingerprint_for_symbol

        graph = _fixture_graph()
        by_id = {s.id: s for s in graph.symbols}
        entries = [
            # Marked when the evidence was different: the fingerprint no longer matches.
            TriageEntry(symbol_id="fetch_target:/api/gone/", fingerprint="older-evidence",
                        status=TriageStatus.APPROVED, who="alice", when="2026-08-20",
                        reason="feature-flagged", why="consumed-by-dependency", expired="2026-09-01"),
            # Still holds.
            TriageEntry(symbol_id="css_selector:cart", fingerprint=fingerprint_for_symbol(by_id["css_selector:cart"]),
                        status=TriageStatus.APPROVED, who="bob", when="2026-08-25",
                        reason="", why="js-applied"),
        ]
        report = build_report(graph=graph, diff=None, entries=entries, git_sha="0" * 12)
        connectivity = build_map(graph, {"orders-main": {s.id for s in graph.symbols}}, git_sha="0" * 12)
        document = render_document(connectivity, console=build_console(graph, report))
        path = pathlib.Path(tempfile.mkdtemp()) / "map.html"
        path.write_text(document.single_file(), encoding="utf-8")
        return path.as_uri()

    def test_the_card_and_the_list_carry_the_mark_and_undo_copies_the_command(self):
        from playwright.sync_api import sync_playwright

        errors: list[str] = []
        with sync_playwright() as playwright:
            try:
                browser = playwright.chromium.launch()
            except Exception as error:  # pragma: no cover - no browser downloaded
                raise unittest.SkipTest(f"no chromium: {error}") from None
            page = browser.new_page()
            page.on("pageerror", lambda e: errors.append(str(e)))
            # The clipboard is a permission in a real browser; here it is a mailbox.
            page.add_init_script("Object.defineProperty(navigator, 'clipboard', {value: "
                                 "{writeText: t => { window.__copied = t; return Promise.resolve(); }}});")
            page.goto(page_url := self._url(), wait_until="load")
            page.wait_for_timeout(250)
            hero = page.evaluate("() => (document.querySelector('#panel .returned-note') || {}).textContent || ''")
            _goto_page_with(page, "unresolved")
            _open_map_list(page)
            page.wait_for_function("() => document.querySelectorAll('#panel .row').length > 0")
            pills = page.evaluate("() => [...document.querySelectorAll('#panel .row')].map(r => ["
                                  "r.querySelector('.t').textContent, "
                                  "(r.querySelector('.pill') || {}).textContent || ''])")
            # A card is shown from the page that holds the node.
            _goto_page_with(page, "unresolved")
            page.evaluate("() => show('fetch_target:/api/gone/')")
            page.wait_for_function("() => !!document.getElementById('undo')")
            card = page.evaluate("() => ({"
                                 "mark: (document.querySelector('#dbody .mark') || {}).textContent || '',"
                                 "returned: !!document.querySelector('#dbody .mark.returned'),"
                                 "wrong: (document.getElementById('wrong') || {}).textContent || ''})")
            page.click("#undo")
            copied = page.evaluate("() => window.__copied")
            label = page.evaluate("() => document.getElementById('undo').textContent")
            _goto_page_with(page, "unused")
            page.evaluate("() => show('css_selector:cart')")
            page.wait_for_function("() => document.querySelector('#dbody h2').textContent === 'cart'")
            held = page.evaluate("() => ({"
                                 "mark: (document.querySelector('#dbody .mark') || {}).textContent || '',"
                                 "returned: !!document.querySelector('#dbody .mark.returned')})")
            browser.close()

        self.assertEqual(errors, [], page_url)
        self.assertIn("1", hero)
        self.assertIn("evidence has changed", hero)
        # One page's list, because that is what a list is now: `cart`'s mark rides on
        # `cart`'s own page. The mark travelling with the row is the thing under test, and
        # it does - the row whose evidence moved says so, in the list, without opening it.
        self.assertEqual(dict(pills)["/api/gone/"], "returned")
        self.assertTrue(card["returned"])
        for word in ("alice", "2026-08-20", "feature-flagged", "consumed-by-dependency", "2026-09-01", "unresolved"):
            self.assertIn(word, card["mark"])
        self.assertEqual(card["wrong"], "Mark it again")
        self.assertEqual(copied, "seamcheck triage 'fetch_target:/api/gone/' --undo")
        self.assertIn("copied", label)
        self.assertFalse(held["returned"])
        self.assertIn("bob", held["mark"])
        self.assertIn("js-applied", held["mark"])


class TheFunctionOnTheCard(SimpleTestCase):
    """A card names the function the line sits in, in that language's own keyword.

    The reader already has `submit_push` open in an editor. A card that says the variable
    and the file, and never the function, makes them find that themselves - which is the
    one step they did not need help with.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        try:
            from playwright.sync_api import sync_playwright  # noqa: F401
        except ImportError:  # pragma: no cover - depends on the optional extra
            raise unittest.SkipTest("playwright is not installed (the observe extra)") from None

    def _url(self) -> str:
        from seamcheck.console import build_console
        from seamcheck.mapdata import build_map
        from seamcheck.renderers.map_html import render_document
        from seamcheck.report import build_report

        def symbol(kind, label, status=Status.CONNECTED, file="app/views.py", owner=""):
            return Symbol(id=f"{kind}:{label}", kind=kind, label=label, sub="", file=file,
                          line=12, status=status, snippet=f"{kind} {label}", chain=[label],
                          note="", owner=owner)

        graph = Graph(symbols=[
            symbol("url", "api/orders/"),
            symbol("view", "orders", owner="orders"),
            symbol("redis_key_use", "cart:{user}:items", owner="submit_push"),
            symbol("module", "orders.js", file="static/js/orders.js"),
            symbol("fetch_target", "/api/gone/", Status.UNRESOLVED, "static/js/orders.js",
                   owner="loadOrders"),
        ], edges=[])
        report = build_report(graph=graph, diff=None, entries=[], git_sha="0" * 12)
        connectivity = build_map(graph, {"orders-main": {s.id for s in graph.symbols}},
                                 git_sha="0" * 12)
        document = render_document(connectivity, console=build_console(graph, report))
        path = pathlib.Path(tempfile.mkdtemp()) / "map.html"
        path.write_text(document.single_file(), encoding="utf-8")
        return path.as_uri()

    def test_the_card_says_def_for_python_and_function_for_javascript(self):
        from playwright.sync_api import sync_playwright

        errors: list[str] = []
        with sync_playwright() as playwright:
            try:
                browser = playwright.chromium.launch()
            except Exception as error:  # pragma: no cover - no browser downloaded
                raise unittest.SkipTest(f"no chromium: {error}") from None
            page = browser.new_page()
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(page_url := self._url(), wait_until="load")
            page.wait_for_timeout(250)
            _goto_page_with(page, "unresolved")
            _open_map_list(page)
            page.wait_for_function("() => document.querySelectorAll('#panel .row').length > 0")
            listed = page.evaluate(
                "() => [...document.querySelectorAll('#panel .row')].map(r => "
                "(r.querySelector('.w') || {}).textContent || '')")
            _goto_page_with(page, "unresolved")
            page.evaluate("() => show('fetch_target:/api/gone/')")
            page.wait_for_function("() => !!document.querySelector('#dbody .row.owner')")
            js = page.evaluate("() => document.querySelector('#dbody .row.owner').textContent")
            # The Redis touch lives on the store layer, not on this page: `byId` holds
            # the open page only, so the card has to be opened where the node is.
            page.evaluate("""() => {
                const i = PAGES.findIndex(p => p.page === 'layer:redis');
                return jumpTo('redis_key_use:cart:{user}:items', i);
            }""")
            page.wait_for_function(
                "() => (document.querySelector('#dbody .row.owner') || {}).textContent"
                ".includes('submit_push')")
            py = page.evaluate("() => document.querySelector('#dbody .row.owner').textContent")
            order = page.evaluate("""() => {
                const rows = [...document.querySelectorAll('#dbody .row')];
                return {
                  owner: rows.findIndex(r => r.classList.contains('owner')),
                  file: rows.findIndex(r => !!r.querySelector('.loc')),
                };
            }""")
            browser.close()

        self.assertEqual(errors, [], page_url)
        self.assertIn("function loadOrders", " ".join(js.split()))
        self.assertIn("def submit_push", " ".join(py.split()))
        # ...and the function is above the file, which is the whole point of the order:
        # the thing, then who owns it, then where it is.
        self.assertGreater(order["file"], order["owner"] , order)
        self.assertNotEqual(order["owner"], -1, order)
        self.assertTrue(any("loadOrders" in text for text in listed),
                        "the list names the function too")


class TheFunctionFilter(SimpleTestCase):
    """Type three letters, get the function, and see everything it touches.

    The page and section pickers answer "where am I looking". This one answers "what am I
    working on" - and the reason it earns a place on the glass is the cost line: a handler
    that should be Redis-only, showing a Postgres write, is the whole diagnosis.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        try:
            from playwright.sync_api import sync_playwright  # noqa: F401
        except ImportError:  # pragma: no cover - depends on the optional extra
            raise unittest.SkipTest("playwright is not installed (the observe extra)") from None

    def _url(self) -> str:
        from seamcheck.console import build_console
        from seamcheck.mapdata import build_map
        from seamcheck.renderers.map_html import render_document
        from seamcheck.report import build_report

        def symbol(kind, label, status=Status.CONNECTED, file="app/views.py", owner=""):
            return Symbol(id=f"{kind}:{label}", kind=kind, label=label, sub="", file=file,
                          line=12, status=status, snippet=f"{kind} {label}", chain=[label],
                          note="", owner=owner)

        js = "static/js/orders.js"
        symbols = [
            symbol("fetch_target", "/api/push/", file=js, owner="sendPush"),
            symbol("url", "api/push/", file="app/urls.py"),
            symbol("view", "submit_push", owner="submit_push"),
            symbol("redis_key_use", "user:{id}:pushes", owner="submit_push"),
            symbol("redis_key_use", "user:{id}:stats", owner="submit_push"),
            # The accident: one Postgres write in a handler that should be Redis-only.
            symbol("db_table_use", "pointless_push", owner="submit_push"),
            symbol("celery_task", "tasks.settle", owner="submit_push"),
            # Owned by a helper, two calls down: invisible to a view that stops at the
            # handler's own body, and the reason the call graph exists.
            symbol("redis_key_use", "user:{id}:streak", file="app/services.py",
                   owner="touch"),
            # Another function entirely, so the filter has something to exclude.
            symbol("redis_key_use", "leaderboard:global", owner="get_user_stats"),
        ]
        edges = [
            Edge(from_id="fetch_target:/api/push/", to_id="url:api/push/",
                 status=Status.CONNECTED),
            Edge(from_id="url:api/push/", to_id="view:submit_push", status=Status.CONNECTED),
            Edge(from_id="view:submit_push", to_id="redis_key_use:user:{id}:pushes",
                 status=Status.CONNECTED),
        ]
        graph = Graph(symbols=symbols, edges=edges)
        report = build_report(graph=graph, diff=None, entries=[], git_sha="0" * 12)
        connectivity = build_map(graph, {"orders-main": {js, "app/views.py", "app/urls.py"}},
                                 git_sha="0" * 12,
                                 # The shape the feature exists for: the handler delegates,
                                 # and a helper two calls down writes the third key.
                                 calls={"submit_push": ["record"], "record": ["touch"]},
                                 defined={"submit_push": "app/views.py",
                                          "record": "app/services.py",
                                          "touch": "app/services.py"})
        document = render_document(connectivity, console=build_console(graph, report))
        path = pathlib.Path(tempfile.mkdtemp()) / "map.html"
        path.write_text(document.single_file(), encoding="utf-8")
        return path.as_uri()

    def test_typing_three_letters_offers_the_function_and_draws_what_it_touches(self):
        from playwright.sync_api import sync_playwright

        errors: list[str] = []
        with sync_playwright() as playwright:
            try:
                browser = playwright.chromium.launch()
            except Exception as error:  # pragma: no cover - no browser downloaded
                raise unittest.SkipTest(f"no chromium: {error}") from None
            page = browser.new_page(viewport={"width": 1280, "height": 800})
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(page_url := self._url(), wait_until="load")
            # The file opens on Overview; the pickers belong to the map.
            _open_lens(page, "map")
            page.wait_for_function(
                "() => !!PAGES[Number(document.getElementById('pg').value)].nodes")
            page.wait_for_timeout(200)
            # The search lives behind the Filter button now, with everything else that
            # narrows the map. A closed sheet is `pointer-events:none`, so a row in it is
            # not clickable - which is the point of it being closed.
            page.click("#filterbtn")
            page.fill("#q", "sub")
            page.wait_for_function("() => document.querySelectorAll('#fnlist .fnrow').length > 0")
            offered = page.evaluate(
                "() => [...document.querySelectorAll('#fnlist .fnrow')]"
                ".map(r => r.dataset.go + ':' + r.dataset.name)")
            page.click("#fnlist .fnrow")
            page.wait_for_function("() => funcFilter === 'submit_push'")
            page.wait_for_timeout(300)
            drawn = page.evaluate(
                "() => [...document.querySelectorAll('#cv g[data-id]')].map(g => g.dataset.id)")
            crumb = page.evaluate("() => document.getElementById('crumb').textContent")
            note = page.evaluate("() => document.getElementById('callers').textContent")
            # One hop out reaches the route; two reaches the fetch that calls it.
            page.click("#widen")
            page.wait_for_timeout(300)
            wider = page.evaluate(
                "() => [...document.querySelectorAll('#cv g[data-id]')].map(g => g.dataset.id)")
            page.evaluate("() => pickFunction('record')")
            page.wait_for_function("() => funcFilter === 'record'")
            page.wait_for_timeout(300)
            called_by = page.evaluate("() => document.getElementById('callers').textContent")
            # Picking a result closes the sheet - it sits over the canvas it just changed.
            self.assertFalse(page.evaluate(
                "() => document.getElementById('filtersheet').classList.contains('open')"))
            page.click("#filterbtn")
            page.click("#fnoff")
            page.wait_for_timeout(300)
            cleared = page.evaluate("() => funcFilter")
            self.assertNotEqual(page.evaluate("() => current"),
                                page.evaluate("() => FN_PAGE"))
            after = page.evaluate(
                "() => [...document.querySelectorAll('#cv g[data-id]')].map(g => g.dataset.id)")
            browser.close()

        self.assertEqual(errors, [], page_url)
        # One box, three kinds of answer. It offered functions and nothing else before,
        # so a filename typed into it answered "No function is called that" - while the
        # box that DID know about files sat two clicks away inside the menu.
        self.assertIn("function:submit_push", offered)
        self.assertEqual([o for o in offered if o.startswith("function:")],
                         ["function:submit_push"], offered)
        # What it touches: its own symbols, plus one hop - the route that dispatches to it.
        self.assertIn("redis_key_use:user:{id}:stats", drawn)
        self.assertIn("db_table_use:pointless_push", drawn)
        self.assertIn("url:api/push/", drawn)
        # ...and nothing owned by another function.
        self.assertNotIn("redis_key_use:leaderboard:global", drawn)
        # The cost line is the point: one Postgres write where there should be none.
        self.assertIn("submit_push()", crumb)
        self.assertIn("through helpers", crumb)
        # The lanes go under the canvas, where a long line is not ellipsised away.
        self.assertIn("Redis 3", note)
        self.assertIn("redis_key_use:user:{id}:streak", drawn)
        self.assertIn("Postgres 1", note)
        self.assertIn("Celery 1", note)
        # The request ARRIVING is drawn without widening. This used to assert the
        # opposite - that the fetch appeared only after "widen by one hop" - and that was
        # the defect: filtering the reference project on `submit_push` drew THE SERVER and
        # THE STORE and nothing above them, so the view built to answer "what is this
        # handler" could not show that a push reaches it at all. Widening still reaches
        # further; the request path is not what it is for.
        self.assertIn("fetch_target:/api/push/", drawn)
        self.assertIn("fetch_target:/api/push/", wider)
        self.assertIsNone(cleared)
        # Cleared means cleared: another function's key is drawable again, and the reader
        # is off the synthetic page.
        # ...and the canvas is a real page again, drawing what the function view hid.
        self.assertIn("module:static/js/orders.js", after)
        # Who calls it: the reverse of the same map, named under the canvas.
        self.assertIn("submit_push", called_by)


class TracingAWireTests(SimpleTestCase):
    """Which wire connects what, and which way it runs.

    Reported from use: "on the full map it is not visible which the wire is connecting".
    The only way to trace one was to CLICK a card, which isolates its whole chain - so
    scanning a dense page meant committing to a click per guess, and nothing at all
    responded to the pointer.

    And direction was drawn correctly and rendered unreadably: an SVG marker defaults to
    `markerUnits="strokeWidth"`, so the arrowhead is multiplied by the wire's thickness -
    which is already the edge-COUNT channel, thickening with log10(n). The common case, a
    single edge at 1.1px, therefore got the smallest head on the canvas, while a merged
    bundle got a giant one. On the reference project's store page 30 of 106 wires run
    both ways and not one of them could be seen doing it.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        try:
            from playwright.sync_api import sync_playwright  # noqa: F401
        except ImportError:
            raise unittest.SkipTest("playwright is not installed (the observe extra)") from None

    def _url(self) -> str:
        from seamcheck.mapdata import build_map
        from seamcheck.renderers.map_html import render_document

        graph = _fixture_graph()
        pages = {"orders-main": {s.id for s in graph.symbols}}
        document = render_document(build_map(graph, pages, git_sha="0" * 12),
                                   console=_console_for(graph))
        path = pathlib.Path(tempfile.mkdtemp()) / "map.html"
        path.write_text(document.single_file(), encoding="utf-8")
        return path.as_uri()

    def _on_canvas(self, script: str):
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            try:
                browser = playwright.chromium.launch()
            except Exception as error:  # pragma: no cover - no browser downloaded
                raise unittest.SkipTest(f"no chromium: {error}") from None
            page = browser.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(self._url(), wait_until="load")
            _open_lens(page, "map")
            # `attached`, not the default `visible`: Playwright calls an element visible
            # when it has a non-empty bounding box, and a wire between two cards on the
            # same row is a horizontal line - width 38, height 0. A straight edge is a
            # correct drawing, so waiting for it to have area waits forever.
            page.wait_for_selector("#cv .ed", state="attached")
            page.wait_for_timeout(200)
            result = page.evaluate(script)
            browser.close()
        self.assertEqual(errors, [], "the canvas must not throw")
        return result

    def test_an_arrowhead_is_the_same_size_whatever_the_wire_weighs(self):
        sizes = self._on_canvas("""() => [...document.querySelectorAll('#cv marker')]
            .map(m => m.getAttribute('markerUnits'))""")
        self.assertTrue(sizes, "the canvas must define arrowhead markers")
        self.assertEqual(set(sizes), {"userSpaceOnUse"},
                         "a head scaled by stroke-width is scaled by the EDGE COUNT")

    def test_every_wire_says_which_two_cards_it_joins(self):
        # Without this the pointer has nothing to match a wire against, so tracing one
        # cannot be done at all.
        ends = self._on_canvas("""() => [...document.querySelectorAll('#cv .ed')]
            .map(e => [e.dataset.a, e.dataset.b])""")
        self.assertTrue(ends)
        for a, b in ends:
            self.assertTrue(a and b, "every wire carries both of its endpoints")

    def test_hovering_a_card_lights_its_own_wires_and_dims_the_rest(self):
        state = self._on_canvas("""() => {
            const card = document.querySelector('#cv .nd[data-p]');
            card.dispatchEvent(new PointerEvent('pointerenter', {bubbles: true}));
            const wires = [...document.querySelectorAll('#cv .ed')];
            const mine = wires.filter(w => w.dataset.a === card.dataset.p
                                        || w.dataset.b === card.dataset.p);
            return {
                tracing: document.getElementById('cv').classList.contains('tracing'),
                mine: mine.length,
                hot: wires.filter(w => w.classList.contains('hot')).length,
                minehot: mine.every(w => w.classList.contains('hot')),
            };
        }""")
        self.assertTrue(state["tracing"], "the canvas enters a tracing state")
        self.assertGreater(state["mine"], 0, "the hovered card must have wires to light")
        self.assertTrue(state["minehot"], "every wire of the hovered card is lit")
        self.assertEqual(state["hot"], state["mine"], "and nothing else is")

    def test_leaving_the_card_puts_everything_back(self):
        state = self._on_canvas("""() => {
            const card = document.querySelector('#cv .nd[data-p]');
            card.dispatchEvent(new PointerEvent('pointerenter', {bubbles: true}));
            card.dispatchEvent(new PointerEvent('pointerleave', {bubbles: true}));
            return {
                tracing: document.getElementById('cv').classList.contains('tracing'),
                hot: document.querySelectorAll('#cv .ed.hot').length,
            };
        }""")
        self.assertFalse(state["tracing"])
        self.assertEqual(state["hot"], 0)

    def test_hovering_does_not_redraw_the_canvas(self):
        # A redraw per pointer move on a ten-thousand-node page is a frozen tab. The
        # highlight is class toggling on wires that are already there.
        same = self._on_canvas("""() => {
            const svg = document.getElementById('cv');
            const before = svg.querySelector('#vp');
            svg.querySelector('.nd[data-p]')
               .dispatchEvent(new PointerEvent('pointerenter', {bubbles: true}));
            return svg.querySelector('#vp') === before;
        }""")
        self.assertTrue(same, "hover must not rebuild the canvas")

    def test_changing_the_page_does_not_carry_the_isolated_node_with_it(self):
        # Found while checking the wires: with a node isolated, picking another page drew
        # "Nothing to draw here. Try another page." on a page holding 3,273 nodes. The
        # isolated node is not ON the new page, so its chain there is empty - and an
        # empty canvas is indistinguishable from a broken one.
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            try:
                browser = playwright.chromium.launch()
            except Exception as error:  # pragma: no cover - no browser downloaded
                raise unittest.SkipTest(f"no chromium: {error}") from None
            page = browser.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(self._url(), wait_until="load")
            _open_lens(page, "map")
            page.wait_for_selector("#cv .nd[data-id]")
            page.click("#cv .nd[data-id]")
            page.wait_for_selector("#iso")
            page.click("#iso")
            page.wait_for_timeout(200)
            isolated = page.evaluate("() => document.querySelectorAll('#cv .nd').length")
            page.evaluate("""() => {
                const sel = document.getElementById('pg');
                sel.selectedIndex = (sel.selectedIndex + 1) % sel.options.length;
                sel.dispatchEvent(new Event('change', {bubbles: true}));
            }""")
            page.wait_for_timeout(400)
            after = page.evaluate("""() => ({
                nodes: document.querySelectorAll('#cv .nd').length,
                empty: !document.getElementById('nothing').hidden,
            })""")
            browser.close()

        self.assertEqual(errors, [])
        self.assertGreater(isolated, 0, "isolating must draw the chain it was asked for")
        self.assertGreater(after["nodes"], 0, "the next page must draw its own nodes")
        self.assertFalse(after["empty"], "and must not report itself empty")

    def test_a_redraw_does_not_leave_the_highlight_stuck(self):
        # `trace()` returns early when the place has not changed, and a redraw replaces
        # every wire in the DOM - so the state variable outlived the elements it
        # described, and hovering the SAME card after a redraw lit nothing at all while
        # the canvas stayed dimmed. Caught on the real map, not in a unit test: the
        # first pointer worked and the second did not.
        state = self._on_canvas("""() => {
            const svg = document.getElementById('cv');
            const wired = new Set([...svg.querySelectorAll('.ed')]
                .flatMap(e => [e.dataset.a, e.dataset.b]));
            const card = [...svg.querySelectorAll('.nd[data-p]')]
                .find(n => wired.has(n.dataset.p));
            const place = card.dataset.p;
            card.dispatchEvent(new PointerEvent('pointerover', {bubbles: true}));
            const first = svg.querySelectorAll('.ed.hot').length;
            // Whatever the reader does next that redraws. The section picker is the
            // plainest one: it calls draw() and replaces every wire in the DOM.
            const sel = document.getElementById('pg');
            sel.dispatchEvent(new Event('change', {bubbles: true}));
            const again = [...svg.querySelectorAll('.nd[data-p]')]
                .find(n => n.dataset.p === place);
            if (again) again.dispatchEvent(new PointerEvent('pointerover', {bubbles: true}));
            const hot = svg.querySelectorAll('.ed.hot').length;
            return {first: first, second: hot, stillThere: !!again,
                    dimmedWithNothingLit: svg.classList.contains('tracing') && hot === 0};
        }""")
        self.assertGreater(state["first"], 0)
        self.assertTrue(state["stillThere"], "the card must survive the redraw")
        self.assertGreater(state["second"], 0,
                           "hovering the same card after a redraw must light it again")
        self.assertFalse(state["dimmedWithNothingLit"],
                         "and a redraw must never leave the canvas dimmed for nothing")


class FunctionReachesTheBrowserTests(SimpleTestCase):
    """A handler's page must show the request arriving, not only where it goes.

    Filtering the reference project on `submit_push` drew THE SERVER and THE STORE and
    nothing above them: the page walks what the function REACHES, and the browser is on
    the other side - the fetch reaches IN. So the one view built for "I am working on
    submit_push, show me everything" could not answer *does a push actually arrive here*,
    which is the first thing a reader asks of it.

    Widening by hops is the wrong lever: the seam is three or four hops from a store row
    and widening that far drags in half the project. The request has a shape - js call →
    fetch → route → handler - and it is followed as a shape.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        try:
            from playwright.sync_api import sync_playwright  # noqa: F401
        except ImportError:
            raise unittest.SkipTest("playwright is not installed (the observe extra)") from None

    def test_a_function_page_shows_the_request_arriving(self):
        from playwright.sync_api import sync_playwright

        from seamcheck.mapdata import build_map
        from seamcheck.renderers.map_html import render_document

        # A whole request, and a store row the handler's own work owns - which is what
        # makes the handler a seed on its own function page.
        def sym(kind, label, owner="", file="app/thing.py", sub=""):
            return Symbol(id=f"{kind}:{label}", kind=kind, label=label, sub=sub,
                          file=file, line=1, status=Status.CONNECTED,
                          snippet=f"{kind} {label}", chain=[label], note="", owner=owner)

        graph = Graph(
            symbols=[
                sym("js_call", "/api/orders/", file="static/js/orders.js"),
                sym("fetch_target", "/api/orders/", file="static/js/orders.js"),
                sym("url", "api/orders/"),
                sym("view", "orders"),
                sym("redis_key_use", "orders:count", owner="orders", sub="writes"),
                sym("redis_key", "orders:count"),
            ],
            edges=[
                Edge(from_id="js_call:/api/orders/", to_id="fetch_target:/api/orders/",
                     status=Status.CONNECTED),
                Edge(from_id="fetch_target:/api/orders/", to_id="url:api/orders/",
                     status=Status.CONNECTED),
                Edge(from_id="url:api/orders/", to_id="view:orders",
                     status=Status.CONNECTED),
                Edge(from_id="view:orders", to_id="redis_key_use:orders:count",
                     status=Status.CONNECTED),
                Edge(from_id="redis_key_use:orders:count", to_id="redis_key:orders:count",
                     status=Status.CONNECTED),
            ],
        )
        # build_map takes the FILES a page is built from, not symbol ids - passing ids
        # puts every symbol on an "unreached" page and drops the edges between bands,
        # which is the whole chain this test is about.
        pages = {"orders-main": {"app/thing.py", "static/js/orders.js"}}
        document = render_document(build_map(graph, pages, git_sha="0" * 12,
                                             calls={"orders": []},
                                             defined={"orders": "app/thing.py"}),
                                   console=_console_for(graph))
        path = pathlib.Path(tempfile.mkdtemp()) / "map.html"
        path.write_text(document.single_file(), encoding="utf-8")

        with sync_playwright() as playwright:
            try:
                browser = playwright.chromium.launch()
            except Exception as error:  # pragma: no cover - no browser downloaded
                raise unittest.SkipTest(f"no chromium: {error}") from None
            page = browser.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(path.as_uri(), wait_until="load")
            _open_lens(page, "map")
            page.wait_for_selector("#cv .nd")
            kinds = page.evaluate("""async () => {
                const fn = document.getElementById('q');
                fn.value = 'orders';
                fn.dispatchEvent(new Event('input', {bubbles: true}));
                await new Promise(r => setTimeout(r, 400));
                const row = document.querySelector('.fnrow');
                if (!row) return {error: 'no function row'};
                row.click();
                await new Promise(r => setTimeout(r, 800));
                return {kinds: PAGES[FN_PAGE].nodes.map(n => n.kind),
                        drawn: [...document.querySelectorAll('#cv .nd')].length,
                        bands: [...document.querySelectorAll('#cv .bandbig')]
                                 .map(b => b.textContent)};
            }""")
            browser.close()

        self.assertEqual(errors, [])
        self.assertNotIn("error", kinds, kinds)
        # The handler is what was asked for; the route and the fetch are how the request
        # gets to it, and both belong on the page that answers "what is this function".
        self.assertIn("view", kinds["kinds"])
        self.assertIn("url", kinds["kinds"])
        self.assertIn("fetch_target", kinds["kinds"])
        # ...and DRAWN, not merely present in the page's data. `visible()` used to re-run
        # the same one-hop walk from owner-matched seeds and throw the request path away
        # again - two implementations of one thing, disagreeing, with the weaker one last.
        self.assertIn("THE BROWSER", kinds["bands"])
        self.assertIn("THE SEAM", kinds["bands"])


class LanguageContainersTests(SimpleTestCase):
    """A band holds more than one language, and said so only in a corner label.

    Reported from use: "each section actually holds multiple languages, multiple
    backends, microservices — inside the main sections there should be separate
    containers per language, so the .py files are together and any JS is together, in a
    different coloured container."

    The band knew: it collects every language it contains and prints them top-right as
    `CSS · JavaScript · Template`. The cards themselves sat intermixed, so the one thing
    the label promised - that this strip crosses a language boundary - could not be seen
    anywhere on the canvas.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        try:
            from playwright.sync_api import sync_playwright  # noqa: F401
        except ImportError:
            raise unittest.SkipTest("playwright is not installed (the observe extra)") from None

    def _url(self):
        from seamcheck.mapdata import build_map
        from seamcheck.renderers.map_html import render_document

        def sym(kind, label, file, sub=""):
            return Symbol(id=f"{kind}:{label}", kind=kind, label=label, sub=sub,
                          file=file, line=1, status=Status.CONNECTED,
                          snippet=f"{kind} {label}", chain=[label], note="")

        graph = Graph(
            symbols=[
                # One band, two languages: a JS module and a TypeScript one.
                sym("module", "orders.js", "static/js/orders.js"),
                sym("js_call", "/api/orders/", "static/js/orders.js"),
                sym("module", "cart.ts", "static/ts/cart.ts"),
                sym("js_call", "/api/cart/", "static/ts/cart.ts"),
                sym("dom_selector", "cart-total", "templates/page.html"),
            ],
            edges=[],
        )
        pages = {"orders-main": {"static/js/orders.js", "static/ts/cart.ts",
                                 "templates/page.html"}}
        document = render_document(build_map(graph, pages, git_sha="0" * 12),
                                   console=_console_for(graph))
        path = pathlib.Path(tempfile.mkdtemp()) / "map.html"
        path.write_text(document.single_file(), encoding="utf-8")
        return path.as_uri()

    def _drawn(self):
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            try:
                browser = playwright.chromium.launch()
            except Exception as error:  # pragma: no cover - no browser downloaded
                raise unittest.SkipTest(f"no chromium: {error}") from None
            page = browser.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(self._url(), wait_until="load")
            _open_lens(page, "map")
            page.wait_for_selector("#cv .nd")
            page.wait_for_timeout(300)
            state = page.evaluate("""() => ({
                boxes: [...document.querySelectorAll('#cv .langbox')].map(b => ({
                    stroke: b.getAttribute('stroke'),
                    w: Number(b.getAttribute('width')),
                    h: Number(b.getAttribute('height')),
                })),
                names: [...document.querySelectorAll('#cv .lanename')]
                         .map(t => t.textContent),
            })""")
            browser.close()
        self.assertEqual(errors, [])
        return state

    def test_each_language_gets_its_own_container(self):
        state = self._drawn()
        self.assertGreaterEqual(len(state["boxes"]), 2,
                                f"one container per language, got {state['names']}")

    def test_the_containers_are_coloured_per_language(self):
        state = self._drawn()
        strokes = {b["stroke"] for b in state["boxes"]}
        self.assertGreaterEqual(len(strokes), 2,
                                "JavaScript and TypeScript must not share a colour")
        self.assertTrue(all(s and s != "none" for s in strokes), strokes)

    def test_a_container_is_a_box_around_its_cards(self):
        # Not a heading: a box, the way the band's own border is a box.
        state = self._drawn()
        for box in state["boxes"]:
            self.assertGreater(box["w"], 100, box)
            self.assertGreater(box["h"], 40, box)

    def test_the_language_is_named_on_its_container(self):
        state = self._drawn()
        named = " ".join(state["names"])
        self.assertIn("JavaScript", named)
        self.assertIn("TypeScript", named)


class SectionsSideBySideTests(SimpleTestCase):
    """Reported from use, filtering the reference project on one function: "the sections
    are below each other, I am getting lost".

    Every kind started a fresh row whatever its width, so a filtered page - four headings
    of two cards each - came out as four screens of scrolling with the answer spread down
    all of them. They flow along the row now and wrap when it is full.

    And the same session: "when moving the canvas with my fingers it is selecting as long
    as I am moving". Two halves - the browser's own text selection over the labels, and
    the hover highlight, which a touch turns on with the pointerdown and a pan never turns
    off, because hit-testing is disabled for the duration of the drag.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        try:
            from playwright.sync_api import sync_playwright  # noqa: F401
        except ImportError:
            raise unittest.SkipTest("playwright is not installed (the observe extra)") from None

    def _url(self) -> str:
        from seamcheck.graph import Edge, Graph, Status, Symbol
        from seamcheck.mapdata import build_map
        from seamcheck.renderers.map_html import render_document

        def symbol(kind, label, file):
            return Symbol(id=f"{kind}:{label}", kind=kind, label=label, sub="", file=file,
                          line=1, status=Status.CONNECTED, snippet=label, chain=[label],
                          note="")

        # Four kinds of one band, two cards each: the shape of a page filtered to one
        # function, and the shape that used to cost four rows and four screens.
        symbols = [
            symbol("url", "api/orders/", "app/urls.py"),
            symbol("url", "api/carts/", "app/urls.py"),
            symbol("view", "orders", "app/views.py"),
            symbol("view", "carts", "app/views.py"),
            symbol("signal_receiver", "on_paid", "app/signals.py"),
            symbol("signal_receiver", "on_shipped", "app/signals.py"),
            symbol("management_command", "reindex", "app/commands.py"),
            symbol("management_command", "backfill", "app/commands.py"),
        ]
        graph = Graph(symbols=symbols,
                      edges=[Edge(from_id="url:api/orders/", to_id="view:orders",
                                  status=Status.CONNECTED)])
        pages = {"orders-main": {s.id for s in symbols}}
        document = render_document(build_map(graph, pages, git_sha="0" * 12),
                                   console=_console_for(graph))
        path = pathlib.Path(tempfile.mkdtemp()) / "map.html"
        path.write_text(document.single_file(), encoding="utf-8")
        return path.as_uri()

    def _on_map(self, script: str, before=None):
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            try:
                browser = playwright.chromium.launch()
            except Exception as error:  # pragma: no cover - no browser downloaded
                raise unittest.SkipTest(f"no chromium: {error}") from None
            # Wide and short on purpose. The layout picks the shape that fits the
            # canvas best, so a square viewport can legitimately prefer a tall column;
            # what must never happen is four narrow kinds costing four rows on a screen
            # with room for them side by side.
            page = browser.new_page(has_touch=True,
                                    viewport={"width": 1600, "height": 420})
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(self._url(), wait_until="load")
            _open_lens(page, "map")
            page.wait_for_selector("#cv .nd", state="attached")
            page.wait_for_timeout(200)
            if before:
                before(page)
            result = page.evaluate(script)
            browser.close()
        self.assertEqual(errors, [], "the canvas must not throw")
        return result

    def test_two_kinds_share_a_row(self):
        tops = self._on_map("""() => [...document.querySelectorAll('#cv .col')]
            .map(t => Math.round(Number(t.getAttribute('y'))))""")
        self.assertGreaterEqual(len(tops), 4, "the page must draw four kind headings")
        # Not one row necessarily - the layout still picks the shape that fits the
        # viewport best - but four narrow kinds must never cost four rows.
        self.assertLess(len(set(tops)), len(tops),
                        f"every kind still started its own row: {tops}")

    def test_dragging_the_canvas_leaves_nothing_highlighted(self):
        def drag(page):
            card = page.query_selector("#cv .nd")
            box = card.bounding_box()
            x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
            page.mouse.move(x, y)
            page.mouse.down()
            for step in range(1, 9):
                page.mouse.move(x - step * 18, y + step * 9)
            page.mouse.up()

        state = self._on_map("""() => ({
            tracing: document.getElementById('cv').classList.contains('tracing'),
            hot: document.querySelectorAll('#cv .hot').length,
            lit: typeof lit !== 'undefined' && lit ? 1 : 0,
            selected: String(window.getSelection()).trim().length,
            select: getComputedStyle(document.getElementById('cv')).userSelect,
        })""", before=drag)
        self.assertFalse(state["tracing"], "a pan is not a hover")
        self.assertEqual(state["hot"], 0)
        self.assertEqual(state["lit"], 0, "a pan must not select the card it started on")
        self.assertEqual(state["selected"], 0, "the labels are not text to select")
        self.assertEqual(state["select"], "none")


class LanesSideBySideTests(SimpleTestCase):
    """Reported from use: "different languages and databases should be next to each other
    in one container, rather than under each other."

    A band is one container and the things inside it are alternatives - JavaScript or
    Template, Postgres or Redis. Stacked, a reader scrolls past the whole of one to find
    out whether the other is even on this page.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        try:
            from playwright.sync_api import sync_playwright  # noqa: F401
        except ImportError:
            raise unittest.SkipTest("playwright is not installed (the observe extra)") from None

    def _url(self) -> str:
        from seamcheck.graph import Graph, Status, Symbol
        from seamcheck.mapdata import build_map
        from seamcheck.renderers.map_html import render_document

        def symbol(kind, label, file):
            return Symbol(id=f"{kind}:{label}", kind=kind, label=label, sub="", file=file,
                          line=1, status=Status.CONNECTED, snippet=label, chain=[label],
                          note="")

        # One band, two languages, and inside each one a kind whose cards are named so
        # that the alphabet says which belong together.
        symbols = [
            symbol("module", "cart.js", "static/js/cart.js"),
            symbol("module", "orders.js", "static/js/orders.js"),
            symbol("module", "search.js", "static/js/search.js"),
            symbol("dom_selector", "cart-total", "templates/page.html"),
            symbol("dom_selector", "cart-count", "templates/page.html"),
            symbol("dom_selector", "order-total", "templates/page.html"),
        ]
        graph = Graph(symbols=symbols, edges=[])
        pages = {"orders-main": {s.id for s in symbols}}
        document = render_document(build_map(graph, pages, git_sha="0" * 12),
                                   console=_console_for(graph))
        path = pathlib.Path(tempfile.mkdtemp()) / "map.html"
        path.write_text(document.single_file(), encoding="utf-8")
        return path.as_uri()

    def _on_map(self, script: str):
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            try:
                browser = playwright.chromium.launch()
            except Exception as error:  # pragma: no cover - no browser downloaded
                raise unittest.SkipTest(f"no chromium: {error}") from None
            page = browser.new_page(viewport={"width": 1600, "height": 500})
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(self._url(), wait_until="load")
            _open_lens(page, "map")
            page.wait_for_selector("#cv .nd", state="attached")
            page.wait_for_timeout(200)
            result = page.evaluate(script)
            browser.close()
        self.assertEqual(errors, [], "the canvas must not throw")
        return result

    def test_two_containers_stand_beside_each_other(self):
        lanes = self._on_map("""() => [...document.querySelectorAll('#cv .lanename')]
            .map(t => ({name: t.textContent,
                        x: Math.round(Number(t.getAttribute('x'))),
                        y: Math.round(Number(t.getAttribute('y')))}))""")
        self.assertGreaterEqual(len(lanes), 2, f"two languages, two containers: {lanes}")
        self.assertEqual(len({lane["y"] for lane in lanes}), 1,
                         f"the containers must start on one line: {lanes}")
        self.assertEqual(len({lane["x"] for lane in lanes}), len(lanes),
                         f"each container needs its own column: {lanes}")

    def test_a_container_is_only_as_wide_as_what_is_in_it(self):
        boxes = self._on_map("""() => [...document.querySelectorAll('#cv .langbox')]
            .map(b => ({x: Math.round(Number(b.getAttribute('x'))),
                        w: Math.round(Number(b.getAttribute('width')))}))""")
        self.assertGreaterEqual(len(boxes), 2)
        boxes.sort(key=lambda b: b["x"])
        for near, far in zip(boxes, boxes[1:], strict=False):
            self.assertLessEqual(near["x"] + near["w"], far["x"] + 1,
                                 f"the containers overlap: {boxes}")

    def test_neighbours_in_the_alphabet_are_neighbours_on_the_canvas(self):
        """"Organise it so things that belong to each other are under each other."

        Two halves: the cards are sorted, so `cart.js` and `orders.js` are neighbours at
        all, and they are filled column-first, so the row wrap cannot tear a family apart.
        """
        cards = self._on_map("""() => [...document.querySelectorAll('#cv .nd[data-id]')]
            .filter(g => g.dataset.id.startsWith('module:'))
            .map(g => {
              const r = g.querySelector('rect');
              return {id: g.dataset.id,
                      x: Math.round(Number(r.getAttribute('x'))),
                      y: Math.round(Number(r.getAttribute('y')))};
            })""")
        self.assertEqual(len(cards), 3, cards)
        column = [c for c in cards if c["id"] in ("module:cart.js", "module:orders.js")]
        self.assertEqual(len({c["x"] for c in column}), 1,
                         f"cart.js and orders.js belong in one column: {cards}")
        near, far = sorted(column, key=lambda c: c["y"])
        self.assertLess(far["y"] - near["y"], 70,
                        f"and directly under each other: {cards}")


class ReadingOrderTests(SimpleTestCase):
    """Reported from a phone: "base and the main should be on the left side", and "if API
    reached JS first then css or vica versa it needs to be visible on the map - when it is
    not highlighted and selected".

    The containers used to be sorted by the alphabet, which opened the browser band on CSS
    - the last thing reached, four hops after the page - and put the page itself on the far
    right, in the lane for symbols with no file. The order is the order the code runs in.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        try:
            from playwright.sync_api import sync_playwright  # noqa: F401
        except ImportError:
            raise unittest.SkipTest("playwright is not installed (the observe extra)") from None

    def _url(self) -> str:
        from seamcheck.graph import Graph
        from seamcheck.mapdata import ConnectivityMap, MapEdge, MapNode, PageMap
        from seamcheck.renderers.map_html import render_document

        # The alphabet and the flow DISAGREE here, which is the whole point: CSS sorts
        # first and is reached last, three hops behind the JavaScript that reaches it.
        nodes = [
            MapNode("page:home", "home", "page", "connected"),
            MapNode("module:app.js", "app.js", "module", "connected",
                    file="static/js/app.js", line=1, lang="JavaScript"),
            MapNode("dom_selector:cart", "cart", "dom_selector", "connected",
                    file="static/js/app.js", line=2, lang="JavaScript"),
            MapNode("css_selector:cart", "cart", "css_selector", "connected",
                    file="static/css/site.css", line=3, lang="CSS"),
            MapNode("url:api/cart/", "api/cart/", "url", "connected",
                    file="app/urls.py", line=4, lang="Python"),
            MapNode("view:cart", "cart", "view", "connected",
                    file="app/views.py", line=5, lang="Python"),
        ]
        edges = [MapEdge(a, b, "connected") for a, b in (
            ("page:home", "module:app.js"),
            ("module:app.js", "dom_selector:cart"),
            ("dom_selector:cart", "css_selector:cart"),
            ("module:app.js", "url:api/cart/"),
            ("url:api/cart/", "view:cart"),
        )]
        document = render_document(
            ConnectivityMap(git_sha="0" * 12, generated_at="2026-09-06T00:00:00",
                            pages=[PageMap("home", nodes, edges)]),
            console=_console_for(Graph(symbols=[], edges=[])))
        path = pathlib.Path(tempfile.mkdtemp()) / "map.html"
        path.write_text(document.single_file(), encoding="utf-8")
        return path.as_uri()

    def _lanes(self):
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            try:
                browser = playwright.chromium.launch()
            except Exception as error:  # pragma: no cover - no browser downloaded
                raise unittest.SkipTest(f"no chromium: {error}") from None
            page = browser.new_page(viewport={"width": 1600, "height": 700})
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(self._url(), wait_until="load")
            _open_lens(page, "map")
            page.wait_for_selector("#cv .nd", state="attached")
            page.wait_for_timeout(200)
            lanes = page.evaluate("""() => [...document.querySelectorAll('#cv .lanename')]
                .map(t => ({name: t.textContent,
                            x: Math.round(Number(t.getAttribute('x')))}))""")
            browser.close()
        self.assertEqual(errors, [], "the canvas must not throw")
        return lanes

    def test_the_page_is_the_leftmost_container(self):
        lanes = self._lanes()

        self.assertTrue(lanes, "the browser band has containers")
        first = min(lanes, key=lambda lane: lane["x"])
        self.assertEqual(first["name"], "The page",
                         f"the page is where a reader starts: {lanes}")

    def test_containers_stand_in_the_order_the_code_runs(self):
        lanes = {lane["name"]: lane["x"] for lane in self._lanes()}

        for name in ("The page", "JavaScript", "CSS"):
            self.assertIn(name, lanes, lanes)
        self.assertLess(lanes["The page"], lanes["JavaScript"], lanes)
        self.assertLess(lanes["JavaScript"], lanes["CSS"],
                        f"CSS is reached through the JavaScript, not before it: {lanes}")

    def test_a_band_one_language_owns_is_still_a_container(self):
        """The server band here is Python and nothing else. It used to be the only band
        drawn without an inner border, so it read as unsorted remainder."""
        lanes = self._lanes()

        self.assertIn("Python", [lane["name"] for lane in lanes],
                      f"the server band names its one language: {lanes}")


class OneFunctionsWorldTests(SimpleTestCase):
    """Reported from a phone, filtering on `submitPushes()`: "it needs to drop everything,
    not just 1 node - I want to see how things are working for submit_push and all the
    connections it has."

    The page built for a function walks OUTWARD from what the function owns, one hop, and
    then follows a fixed shape upstream - a store row is reached by a handler, a handler by
    its route, a route by the fetch that resolves to it. That shape only ever ran towards
    the browser. A function ON the browser side therefore stopped at the request it makes:
    on the reference project `submitPushes` drew five nodes, all of them JavaScript, and
    the route it calls, the handler behind it and the keys that handler writes were absent
    from the one view built to answer "what happens when this runs".
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        try:
            from playwright.sync_api import sync_playwright  # noqa: F401
        except ImportError:
            raise unittest.SkipTest("playwright is not installed (the observe extra)") from None

    def _url(self) -> str:
        from seamcheck.graph import Graph
        from seamcheck.mapdata import ConnectivityMap, MapEdge, MapNode, PageMap
        from seamcheck.renderers.map_html import render_document

        # One push, followed the whole way: a call in a module, the request it makes, the
        # route that answers, the handler behind it, and the key that handler writes.
        nodes = [
            MapNode("page:cart", "cart", "page", "connected"),
            MapNode("module:cart.js", "cart.js", "module", "connected",
                    file="static/js/cart.js", line=1, lang="JavaScript"),
            MapNode("js_call:/api/cart/", "/api/cart/", "js_call", "connected",
                    file="static/js/cart.js", line=2, lang="JavaScript", owner="addToCart"),
            MapNode("fetch_target:/api/cart/", "/api/cart/", "fetch_target", "connected",
                    file="static/js/cart.js", line=2, lang="JavaScript", owner="addToCart"),
            MapNode("url:api/cart/", "api/cart/", "url", "connected",
                    file="app/urls.py", line=3, lang="Python"),
            MapNode("view:cart_add", "cart_add", "view", "connected",
                    file="app/views.py", line=4, lang="Python", owner="cart_add"),
            MapNode("redis_key_use:cart:*:items", "cart:*:items", "redis_key_use",
                    "connected", file="app/views.py", line=5, lang="Python",
                    owner="cart_add"),
        ]
        edges = [MapEdge(a, b, "connected") for a, b in (
            ("page:cart", "module:cart.js"),
            ("module:cart.js", "js_call:/api/cart/"),
            ("js_call:/api/cart/", "fetch_target:/api/cart/"),
            ("fetch_target:/api/cart/", "url:api/cart/"),
            ("url:api/cart/", "view:cart_add"),
            ("view:cart_add", "redis_key_use:cart:*:items"),
        )]
        document = render_document(
            ConnectivityMap(git_sha="0" * 12, generated_at="2026-09-06T00:00:00",
                            pages=[PageMap("cart", nodes, edges)]),
            console=_console_for(Graph(symbols=[], edges=[])))
        path = pathlib.Path(tempfile.mkdtemp()) / "map.html"
        path.write_text(document.single_file(), encoding="utf-8")
        return path.as_uri()

    def _world_of(self, name: str):
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            try:
                browser = playwright.chromium.launch()
            except Exception as error:  # pragma: no cover - no browser downloaded
                raise unittest.SkipTest(f"no chromium: {error}") from None
            page = browser.new_page(viewport={"width": 1400, "height": 900})
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(self._url(), wait_until="load")
            _open_lens(page, "map")
            page.wait_for_selector("#cv .nd", state="attached")
            page.wait_for_timeout(200)
            ids = page.evaluate("""async (name) => {
                await buildFunctionPage(name, null, 1);
                return (PAGES[FN_PAGE].nodes || []).map(n => n.id);
            }""", name)
            browser.close()
        self.assertEqual(errors, [], "the canvas must not throw")
        return set(ids)

    def test_a_browser_function_is_followed_across_the_seam(self):
        world = self._world_of("addToCart")

        self.assertIn("fetch_target:/api/cart/", world, world)
        self.assertIn("url:api/cart/", world,
                      f"the route the request resolves to is part of its world: {world}")
        self.assertIn("view:cart_add", world,
                      f"and the handler behind that route: {world}")
        self.assertIn("redis_key_use:cart:*:items", world,
                      f"and what the handler touches - that IS how it works: {world}")

    def test_a_server_function_still_reaches_back_to_the_browser(self):
        # The direction that already worked has to keep working: a handler's world
        # includes the route and the request that arrive at it.
        world = self._world_of("cart_add")

        self.assertIn("view:cart_add", world, world)
        self.assertIn("url:api/cart/", world, world)
        self.assertIn("fetch_target:/api/cart/", world,
                      f"the request that arrives is still part of it: {world}")


class FunctionListEscapesTheSheetTests(SimpleTestCase):
    """Reported from a phone: "the function filter dropdown search needs to overflow the
    filter container, it's not that UX friendly."

    On a narrow screen the three pickers live inside the Filter sheet, and that sheet
    scrolls its own content (`overflow-y:auto`). An absolutely positioned list inside a
    scrolling box is trapped in it: the suggestions open below the fold of a box the
    reader then has to scroll, inside a page that also scrolls, to choose a function.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        try:
            from playwright.sync_api import sync_playwright  # noqa: F401
        except ImportError:
            raise unittest.SkipTest("playwright is not installed (the observe extra)") from None

    def _open_the_list_on_a_phone(self):
        from playwright.sync_api import sync_playwright

        from seamcheck.graph import Graph
        from seamcheck.mapdata import ConnectivityMap, MapEdge, MapNode, PageMap
        from seamcheck.renderers.map_html import render_document

        # Enough functions that the list is worth opening, and one shared prefix so a
        # search returns several rows.
        nodes = [MapNode("page:cart", "cart", "page", "connected"),
                 MapNode("module:cart.js", "cart.js", "module", "connected",
                         file="static/js/cart.js", line=1, lang="JavaScript")]
        edges = [MapEdge("page:cart", "module:cart.js", "connected")]
        defined, calls = {}, {}
        for i in range(12):
            name = f"submitPushes{i}"
            node = MapNode(f"js_call:/api/push/{i}/", f"/api/push/{i}/", "js_call",
                           "connected", file="static/js/cart.js", line=2 + i,
                           lang="JavaScript", owner=name)
            nodes.append(node)
            edges.append(MapEdge("module:cart.js", node.id, "connected"))
            defined[name] = "static/js/cart.js"
            calls[name] = []
        document = render_document(
            ConnectivityMap(git_sha="0" * 12, generated_at="2026-09-06T00:00:00",
                            pages=[PageMap("cart", nodes, edges)],
                            defined=defined, calls=calls),
            console=_console_for(Graph(symbols=[], edges=[])))
        path = pathlib.Path(tempfile.mkdtemp()) / "map.html"
        path.write_text(document.single_file(), encoding="utf-8")

        with sync_playwright() as playwright:
            try:
                browser = playwright.chromium.launch()
            except Exception as error:  # pragma: no cover - no browser downloaded
                raise unittest.SkipTest(f"no chromium: {error}") from None
            # A phone, which is where this was reported and the only width where the
            # pickers are inside the sheet at all.
            page = browser.new_page(viewport={"width": 390, "height": 844})
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(path.as_uri(), wait_until="load")
            _open_lens(page, "map")
            page.wait_for_selector("#cv .nd", state="attached")
            page.click("#filterbtn")
            # The sheet slides in, and while it does it carries a transform - which makes
            # it the containing block for anything fixed inside it. Measuring through that
            # reads a scaled rectangle and says nothing about where the list lands.
            page.wait_for_function(
                "() => getComputedStyle(document.getElementById('filtersheet')).transform"
                " === 'none'", timeout=5000)
            page.fill("#q", "submitPushes")
            page.wait_for_selector("#fnlist .fnrow", state="attached", timeout=5000)
            page.wait_for_timeout(150)
            measured = page.evaluate("""() => {
                const list = document.getElementById('fnlist');
                const box = list.getBoundingClientRect();
                const row = list.querySelector('.fnrow').getBoundingClientRect();
                const at = document.elementFromPoint(row.left + row.width / 2,
                                                     row.top + row.height / 2);
                return {
                  top: Math.round(box.top), bottom: Math.round(box.bottom),
                  left: Math.round(box.left), right: Math.round(box.right),
                  height: Math.round(box.height),
                  viewport: window.innerHeight, wide: window.innerWidth,
                  hit: !!(at && at.closest('#fnlist')),
                };
            }""")
            browser.close()
        self.assertEqual(errors, [], "the canvas must not throw")
        return measured

    def test_the_suggestions_are_on_screen_and_clickable(self):
        measured = self._open_the_list_on_a_phone()

        self.assertTrue(measured["hit"],
                        f"a tap on the first suggestion has to reach it: {measured}")
        self.assertGreater(measured["height"], 60,
                           f"the list is drawn at a usable height: {measured}")
        self.assertLessEqual(measured["bottom"], measured["viewport"] + 1,
                             f"and no part of it hangs below the screen: {measured}")
        self.assertGreaterEqual(measured["top"], 0, measured)
        self.assertLessEqual(measured["right"], measured["wide"] + 1,
                             f"nor off the right edge: {measured}")
        self.assertGreaterEqual(measured["left"], 0, measured)


class NoPullToRefreshTests(SimpleTestCase):
    """Reported from a phone: "when I move down on the map with one finger it's possible,
    however when I want to move upward it wants to update the full page."

    That is the browser's pull-to-refresh taking the drag. It only fires in one direction,
    which is why panning one way worked and the other reloaded the report - and a reload
    of this page is not cheap: the reader loses the page they were on, the filter they set
    and the card they had open.

    The page did say `overscroll-behavior:none` - on BODY. Per the CSS Overscroll Behavior
    spec that value is NOT propagated to the viewport: only the ROOT element's is. `overflow`
    propagates from body, `overscroll-behavior` does not, and the two rules read alike. So
    the declaration did nothing on the one platform that has the gesture.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        try:
            from playwright.sync_api import sync_playwright  # noqa: F401
        except ImportError:
            raise unittest.SkipTest("playwright is not installed (the observe extra)") from None

    def _computed(self):
        from playwright.sync_api import sync_playwright

        from seamcheck.graph import Graph
        from seamcheck.mapdata import ConnectivityMap, MapEdge, MapNode, PageMap
        from seamcheck.renderers.map_html import render_document

        nodes = [MapNode("page:home", "home", "page", "connected"),
                 MapNode("url:api/x/", "api/x/", "url", "connected", file="urls.py", line=1)]
        document = render_document(
            ConnectivityMap(git_sha="0" * 12, generated_at="2026-09-06T00:00:00",
                            pages=[PageMap("home", nodes,
                                           [MapEdge("page:home", "url:api/x/", "connected")])]),
            console=_console_for(Graph(symbols=[], edges=[])))
        path = pathlib.Path(tempfile.mkdtemp()) / "map.html"
        path.write_text(document.single_file(), encoding="utf-8")

        with sync_playwright() as playwright:
            try:
                browser = playwright.chromium.launch()
            except Exception as error:  # pragma: no cover - no browser downloaded
                raise unittest.SkipTest(f"no chromium: {error}") from None
            page = browser.new_page(viewport={"width": 390, "height": 844})
            page.goto(path.as_uri(), wait_until="load")
            _open_lens(page, "map")
            page.wait_for_selector("#cv .nd", state="attached")
            measured = page.evaluate("""() => {
                const root = getComputedStyle(document.documentElement);
                const clip = document.querySelector('.cvclip');
                const sheet = document.getElementById('filtersheet');
                return {
                  rootOverscrollY: root.overscrollBehaviorY,
                  clipTouch: getComputedStyle(clip).touchAction,
                  layerTouch: getComputedStyle(document.getElementById('cvlayer')).touchAction,
                  sheetTouch: sheet ? getComputedStyle(sheet).touchAction : null,
                };
            }""")
            browser.close()
        return measured

    def test_the_root_element_refuses_the_gesture(self):
        # On the root, not on body: body's value is never propagated to the viewport.
        measured = self._computed()

        self.assertEqual(measured["rootOverscrollY"], "none",
                         f"a drag past the edge must not reach the browser: {measured}")

    def test_the_whole_map_area_owns_its_gestures_not_only_the_svg(self):
        # `touch-action:none` was on the svg and its children. The clip and the layer
        # around it - which is three viewports wide and slides under the finger during a
        # pan - said nothing, so a drag that began on one of them was the browser's.
        measured = self._computed()

        self.assertEqual(measured["clipTouch"], "none", measured)
        self.assertEqual(measured["layerTouch"], "none", measured)

    def test_the_sheets_can_still_be_scrolled_with_a_finger(self):
        # The reason this is not simply set on body: the filter sheet is a scrolling box,
        # and touch-action:none on an ancestor would make it unscrollable on a phone.
        measured = self._computed()

        self.assertNotEqual(measured["sheetTouch"], "none",
                            f"the sheet has to keep its own scrolling: {measured}")


class AFunctionAcrossItsPagesTests(SimpleTestCase):
    """Reported from a phone, having filtered on `submit_push`: "I filter on submit_push
    but I can filter on different html and then it says nothing to do with that. When I
    choose the html part it should show all html separated, like the different containers
    we did today - and the dropdown should only show those htmls which belong to that
    function."

    Two halves of one mistake. The Page picker offered every page in the project while a
    function was picked, so almost any choice narrowed the function to a page it was never
    on and drew an empty canvas. And the function's own view unioned its pages into one
    heap, so the only way to ask "where does this happen" was to narrow, one page at a
    time, losing the rest.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        try:
            from playwright.sync_api import sync_playwright  # noqa: F401
        except ImportError:
            raise unittest.SkipTest("playwright is not installed (the observe extra)") from None

    def _url(self) -> str:
        from seamcheck.graph import Graph
        from seamcheck.mapdata import ConnectivityMap, MapEdge, MapNode, PageMap
        from seamcheck.renderers.map_html import render_document

        # `push()` lives in a shared module that TWO pages load, and a third page has
        # nothing to do with it. That third page is the one that must not be offered.
        def page_of(name, file):
            nodes = [
                MapNode(f"page:{name}", name, "page", "connected"),
                MapNode(f"module:{file}", file, "module", "connected",
                        file=f"static/js/{file}", line=1, lang="JavaScript"),
            ]
            edges = [MapEdge(f"page:{name}", f"module:{file}", "connected")]
            return nodes, edges

        nodes, edges = [], []
        for name, file, owner in (("cart", "cart.js", "push"),
                                  ("orders", "orders.js", "push"),
                                  ("about", "about.js", "spin")):
            page_nodes, page_edges = page_of(name, file)
            call = MapNode(f"js_call:{name}", f"/api/{name}/", "js_call", "connected",
                           file=f"static/js/{file}", line=2, lang="JavaScript", owner=owner)
            page_nodes.append(call)
            page_edges.append(MapEdge(f"module:{file}", call.id, "connected"))
            nodes.append((name, page_nodes, page_edges))
            edges.append(page_edges)
        document = render_document(
            ConnectivityMap(
                git_sha="0" * 12, generated_at="2026-09-06T00:00:00",
                pages=[PageMap(name, ns, es) for name, ns, es in nodes],
                defined={"push": "static/js/cart.js", "spin": "static/js/about.js"},
                calls={"push": [], "spin": []}),
            console=_console_for(Graph(symbols=[], edges=[])))
        path = pathlib.Path(tempfile.mkdtemp()) / "map.html"
        path.write_text(document.single_file(), encoding="utf-8")
        return path.as_uri()

    def _after_picking(self, name: str):
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            try:
                browser = playwright.chromium.launch()
            except Exception as error:  # pragma: no cover - no browser downloaded
                raise unittest.SkipTest(f"no chromium: {error}") from None
            page = browser.new_page(viewport={"width": 1400, "height": 900})
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(self._url(), wait_until="load")
            _open_lens(page, "map")
            page.wait_for_selector("#cv .nd", state="attached")
            page.evaluate("(name) => pickFunction(name)", name)
            page.wait_for_function("() => current === FN_PAGE", timeout=5000)
            page.wait_for_timeout(250)
            state = page.evaluate("""() => ({
                options: [...document.getElementById('pg').options].map(o => o.textContent),
                lanes: [...document.querySelectorAll('#cv .lanename')].map(t => t.textContent),
            })""")
            browser.close()
        self.assertEqual(errors, [], "the canvas must not throw")
        return state

    def test_the_picker_offers_only_the_pages_the_function_is_on(self):
        state = self._after_picking("push")
        offered = " | ".join(state["options"])

        self.assertIn("push()", offered, offered)
        self.assertIn("cart", offered, offered)
        self.assertIn("orders", offered, offered)
        self.assertNotIn("about", offered,
                         f"push() was never on that page, so narrowing to it can only "
                         f"draw an empty canvas: {offered}")

    def test_each_page_the_function_touches_is_its_own_container(self):
        state = self._after_picking("push")

        self.assertIn("cart", state["lanes"], state["lanes"])
        self.assertIn("orders", state["lanes"],
                      f"both pages at once, separated - not one at a time: {state['lanes']}")
