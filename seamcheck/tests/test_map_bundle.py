"""The map as a folder: a small index.html plus data/<chunk>.js loaded on demand.

One file holds every page's rows inline, and a browser has to parse all of it before it
can draw anything - a 22 MB map of a 511k-line repository froze a laptop. The bundle
keeps the page small and fetches each chunk as a classic `<script src>` when a reader
first looks at it, which is the one on-demand loader that works from `file://`.

These tests pin the shape both writers and the server rely on: the same chunks, either
inlined or as `SC.chunk(...)` assets; the auto-bundle note; a stale `data/` swept before
a rewrite but a foreign one refused; and the assets served beside the map.
"""

from __future__ import annotations

import base64
import gzip
import pathlib
import tempfile
import threading
import urllib.error
import urllib.request
from unittest import mock

from django.test import SimpleTestCase

from seamcheck import api
from seamcheck.mapdata import ConnectivityMap, MapEdge, MapNode, PageMap
from seamcheck.renderers import map_html
from seamcheck.serve import serve_addresses


def _map(pages=1):
    built = []
    for i in range(pages):
        node = MapNode(f"url:x{i}", f"api/thing{i}/", "url", "connected", file="v.py", line=3)
        built.append(PageMap(f"page{i}", [MapNode(f"page:p{i}", f"page{i}", "page", "connected"), node],
                             [MapEdge(f"page:p{i}", f"url:x{i}", "connected")]))
    return ConnectivityMap(git_sha="abc123def456", generated_at="2026-08-30T00:00:00", pages=built)


class DocumentShape(SimpleTestCase):
    def test_the_single_file_is_exactly_what_render_returns(self):
        self.assertEqual(map_html.render_document(_map()).single_file(), map_html.render(_map()))

    def test_the_single_file_carries_every_chunk_inline_and_no_data_folder(self):
        document = map_html.render_document(_map(3))
        html = document.single_file()

        self.assertIn("const BUNDLE=null;", html)
        for name, enc, _ in document.chunks:
            self.assertIn(f'data-chunk="{name}" data-enc="{enc}"', html)

    def test_the_bundle_moves_every_chunk_out_of_the_page(self):
        document = map_html.render_document(_map(3))
        index, assets = document.bundle()

        self.assertIn('const BUNDLE="data/";', index)
        self.assertNotIn('type="text/plain" data-chunk=', index)
        self.assertEqual(set(assets), {f"data/{name}.js" for name, _, _ in document.chunks})
        for (name, enc, _), body in zip(document.chunks, assets.values(), strict=True):
            # A classic script: one call the page's loader answers, nothing else executes.
            self.assertTrue(body.startswith(f'SC.chunk("{name}","{enc}",'.encode()), body[:60])
            self.assertTrue(body.rstrip().endswith(b");"))

    def test_a_page_chunk_and_the_search_index_are_separate_assets(self):
        _, assets = map_html.render_document(_map(2)).bundle()

        self.assertIn("data/p0.js", assets)
        self.assertIn("data/p1.js", assets)
        self.assertIn("data/search.js", assets)

    def test_a_small_map_does_not_prefer_the_bundle_but_a_huge_one_does(self):
        document = map_html.render_document(_map())

        self.assertFalse(document.prefers_bundle())
        with mock.patch.object(map_html, "SINGLE_FILE_LIMIT", 10):
            self.assertTrue(document.prefers_bundle())

    def test_the_index_is_a_fraction_of_the_single_file(self):
        document = map_html.render_document(_map(40))
        index, _ = document.bundle()

        self.assertLess(len(index), len(document.single_file()))


class WritingTheMap(SimpleTestCase):
    def setUp(self):
        self.root = pathlib.Path(tempfile.mkdtemp())
        self.document = map_html.render_document(_map(2))

    def test_an_html_destination_gets_one_file(self):
        written, note = api.write_map_document(self.document, str(self.root / "map.html"))

        self.assertEqual(written, str(self.root / "map.html"))
        self.assertEqual(note, "")
        self.assertIn("const BUNDLE=null;", (self.root / "map.html").read_text())
        self.assertFalse((self.root / "data").exists())

    def test_a_folder_destination_gets_index_and_data(self):
        written, note = api.write_map_document(self.document, str(self.root / "out") + "/")

        self.assertEqual(written, str(self.root / "out" / "index.html"))
        self.assertEqual(note, "")
        self.assertIn('const BUNDLE="data/";', (self.root / "out" / "index.html").read_text())
        self.assertTrue((self.root / "out" / "data" / "p0.js").is_file())
        self.assertTrue((self.root / "out" / "data" / "search.js").is_file())

    def test_a_destination_without_an_html_suffix_is_a_folder(self):
        written, _ = api.write_map_document(self.document, str(self.root / "map"))

        self.assertEqual(written, str(self.root / "map" / "index.html"))

    def test_bundle_true_forces_a_folder_even_for_a_dot_html_name(self):
        written, _ = api.write_map_document(self.document, str(self.root / "map.html"), bundle=True)

        self.assertEqual(written, str(self.root / "map.html" / "index.html"))

    def test_a_map_too_big_for_one_file_becomes_a_folder_with_a_note(self):
        with mock.patch.object(map_html, "SINGLE_FILE_LIMIT", 10):
            written, note = api.write_map_document(self.document, str(self.root / "big.html"))

        self.assertEqual(written, str(self.root / "big" / "index.html"))
        self.assertIn("Written as a folder", note)
        self.assertIn(str(self.root / "big" / "index.html"), note)
        self.assertFalse((self.root / "big.html").exists())

    def test_a_stale_chunk_from_a_previous_run_is_swept(self):
        out = str(self.root / "out") + "/"
        api.write_map_document(self.document, out)
        stale = self.root / "out" / "data" / "p9.js"
        stale.write_text("SC.chunk('p9','json',{});")

        api.write_map_document(self.document, out)

        self.assertFalse(stale.exists(), "a page that no longer exists could be served")
        self.assertTrue((self.root / "out" / "data" / "p0.js").is_file())

    def test_a_data_folder_that_is_not_ours_is_refused(self):
        theirs = self.root / "site"
        (theirs / "data").mkdir(parents=True)
        (theirs / "data" / "users.js").write_text("secrets")
        (theirs / "index.html").write_text("<html>their site</html>")

        with self.assertRaises(ValueError):
            api.write_map_document(self.document, str(theirs) + "/")
        self.assertTrue((theirs / "data" / "users.js").is_file(), "their file must survive")


class ServingTheBundle(SimpleTestCase):
    def setUp(self):
        index, assets = map_html.render_document(_map(2)).bundle()
        self.index = index
        self.server, addresses = serve_addresses(index, host="127.0.0.1", assets=assets)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = addresses["local"]

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def _get(self, url):
        return urllib.request.urlopen(url, timeout=5)

    def test_the_map_url_ends_with_a_slash_so_relative_data_resolves(self):
        self.assertTrue(self.url.endswith("/"), self.url)
        with self._get(self.url) as response:
            self.assertIn('const BUNDLE="data/";', response.read().decode())

    def test_a_chunk_is_served_beside_the_map_as_javascript(self):
        with self._get(self.url + "data/p0.js") as response:
            self.assertEqual(response.headers["Content-Type"], "application/javascript")
            self.assertEqual(response.headers["Cache-Control"], "no-store")
            self.assertTrue(response.read().startswith(b'SC.chunk("p0"'))

    def test_the_bare_token_redirects_to_the_slash_form(self):
        opener = urllib.request.build_opener(_NoRedirect())
        with self.assertRaises(urllib.error.HTTPError) as raised:
            opener.open(self.url.rstrip("/"), timeout=5)
        self.assertEqual(raised.exception.code, 301)
        self.assertEqual(raised.exception.headers["Location"], "/" + self.url.rsplit("/", 2)[-2] + "/")

    def test_a_chunk_that_was_never_rendered_is_404(self):
        with self.assertRaises(urllib.error.HTTPError) as raised:
            self._get(self.url + "data/p9.js")
        self.assertEqual(raised.exception.code, 404)

    def test_nothing_outside_the_token_is_served(self):
        with self.assertRaises(urllib.error.HTTPError) as raised:
            self._get(self.url.rsplit("/", 2)[0] + "/data/p0.js")
        self.assertEqual(raised.exception.code, 404)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, msg, headers, fp)


def _console(rows_in_findings: int, rows_in_changes: int = 2):
    from seamcheck.console import Console, Row, Section

    def rows(count, prefix):
        return [Row(id=f"{prefix}{i}", label=f"{prefix}-label-{i}", kind="url",
                    status="unresolved" if i % 2 else "unused", file=f"app/views_{i}.py",
                    line=i, note="nobody reaches this route from any page", snippet="x = 1")
                for i in range(count)]

    return Console(
        git_sha="abc123def456", generated_at="2026-08-30T00:00:00", baseline_sha=None,
        backend={}, frontend={}, counts={}, groups=[],
        sections=[Section("findings", "Findings", "what is wrong", rows(rows_in_findings, "f")),
                  Section("changes", "Changes", "what moved", rows(rows_in_changes, "c"))],
    )


class LazySections(SimpleTestCase):
    """Rows are read when a section is opened, not with the map.

    On the game the section rows were 800 KB of a 2.2 MB index - a third of what the
    browser had to parse before drawing anything, for lists most readers never open.
    """

    def test_a_long_section_leaves_only_its_counts_in_the_page(self):
        document = map_html.render_document(_map(), console=_console(60))
        index, assets = document.bundle()

        self.assertIn('"key": "findings"', index.replace('":"', '": "'))
        self.assertNotIn("f-label-59", index, "the rows are still inline")
        self.assertIn('"total":60', index.replace(" ", ""))
        self.assertIn('"chunk":"cfindings"', index.replace(" ", ""))
        self.assertIn("data/cfindings.js", assets)
        name, enc, text = next(c for c in document.chunks if c[0] == "cfindings")
        self.assertEqual(enc, "gz")
        self.assertIn(b"f-label-59", gzip.decompress(base64.b64decode(text)))

    def test_a_short_section_keeps_its_rows_in_the_page(self):
        index, assets = map_html.render_document(_map(), console=_console(60)).bundle()

        self.assertIn("c-label-1", index)
        self.assertNotIn("data/cchanges.js", assets)

    def test_the_cli_shape_still_carries_the_rows(self):
        # Callers that read the payload without a chunk list - the CLI's own renderers -
        # get the rows in place, as before.
        payload = map_html._console_payload(_console(60))
        self.assertIn("f-label-59", payload)
        self.assertNotIn('"chunk"', payload)

    def test_a_long_file_list_is_a_count_in_the_page_and_a_chunk_beside_it(self):
        files = [{"path": f"app/module_{i}/views.py", "counts": {"connected": i},
                  "declarations": i, "known": i} for i in range(120)]
        document = map_html.render_document(_map(), files=files)
        index, assets = document.bundle()

        self.assertIn('"count":120', index.replace(" ", ""))
        self.assertIn('"chunk":"files"', index.replace(" ", ""))
        self.assertNotIn("module_119", index)
        self.assertIn("data/files.js", assets)

    def test_a_short_file_list_stays_in_the_page(self):
        files = [{"path": "a/b.py", "counts": {"connected": 1}, "declarations": 1, "known": 1}]
        index, assets = map_html.render_document(_map(), files=files).bundle()

        self.assertIn('"count":1', index.replace(" ", ""))
        self.assertIn("a/b.py", index)
        self.assertNotIn("data/files.js", assets)


class PagesAndSections(SimpleTestCase):
    """One template loading many bundles is ONE page with sections, not many pages.

    The reference project's arena was 77 rows under one name in the picker, told apart by
    a build artefact's filename. The writer now unions the entries that share a title and
    address into a `group:N` page - one chunk, so it loads like any other - and marks
    every entry with its group, so the browser can offer a Page and a Section.
    """

    @staticmethod
    def _entries():
        def entry(name, node):
            return PageMap(name, [MapNode(f"page:{name}", name, "page", "connected"),
                                  MapNode(node, node, "url", "connected", file="v.py", line=1),
                                  MapNode("url:both", "both", "url", "connected", file="v.py", line=2)],
                           [MapEdge(f"page:{name}", node, "connected"),
                            MapEdge(f"page:{name}", "url:both", "connected")],
                           title="Arena", where="/arena/")
        alone = PageMap("home-main", [MapNode("page:home-main", "home-main", "page", "connected"),
                                      MapNode("url:home", "home", "url", "connected")],
                        [MapEdge("page:home-main", "url:home", "connected")],
                        title="Home", where="/")
        return [entry("arena-main", "url:a"), entry("arena-side", "url:b"), alone]

    def _meta(self):
        import json
        out = map_html.render(ConnectivityMap("0" * 12, "", self._entries()))
        start = out.index("{", out.index("MAPDATA"))
        return json.JSONDecoder().raw_decode(out, start)[0], out

    def test_entries_sharing_a_name_get_one_union_page_and_a_group(self):
        meta, _ = self._meta()
        by_name = {p["page"]: p for p in meta["pages"]}
        self.assertIn("group:0", by_name)
        union = by_name["group:0"]
        self.assertTrue(union.get("union"))
        self.assertEqual(union["title"], "Arena")
        # Two page roots, three distinct urls: the shared one is counted once.
        self.assertEqual(union["n"], 5)
        self.assertEqual({by_name[n]["g"] for n in ("group:0", "arena-main", "arena-side")}, {0})
        self.assertEqual(by_name["home-main"]["g"], 1)
        # A page with one entry gets no union: there is nothing to union.
        self.assertNotIn("group:1", by_name)
        self.assertEqual([p["page"] for p in meta["pages"]],
                         ["group:0", "arena-main", "arena-side", "home-main"])

    @staticmethod
    def _chunk(out, name):
        import json
        tag = f'data-chunk="{name}" data-enc="json">'
        start = out.index(tag) + len(tag)
        return json.JSONDecoder().raw_decode(out, start)[0]

    def test_the_union_is_one_chunk_that_holds_every_entrys_rows_once(self):
        meta, out = self._meta()
        index = [p["page"] for p in meta["pages"]].index("group:0")
        body = self._chunk(out, f"p{index}")
        ids = [row[0] for row in body["nodes"]]
        self.assertEqual(len(ids), len(set(ids)), "a node the entries share is sent once")
        self.assertEqual(sorted(ids), ["page:arena-main", "page:arena-side",
                                       "url:a", "url:b", "url:both"])
        self.assertEqual(sum(1 for e in body["edges"] if e[1] == "url:both"), 2,
                         "each entry's own edge to the shared node survives")

    def test_the_union_is_not_in_the_search_index(self):
        """A hit lands on the entry that has it, never on the union."""
        meta, out = self._meta()
        index = [p["page"] for p in meta["pages"]].index("group:0")
        body = self._chunk(out, "search")
        self.assertNotIn(index, body["page"])
        self.assertEqual(body["n"], 4, "url:both once, on the first entry that holds it")


class StoreLayers(SimpleTestCase):
    """Redis and the database are global layers, and each node on them says which pages.

    A key is touched from the arena, the leaderboard and a worker; "show me Redis" wants
    all of it on one canvas, with the key nothing touches - which sits in a not-reached
    bucket - beside them. The layer chunk carries, per node, the ordinary pages that
    hold it, so the browser can narrow the layer to one page and a card can say where
    it is touched from.
    """

    @staticmethod
    def _entries():
        from seamcheck.mapdata import UNREACHED_PAGE

        def page(name, title, where, keys, extra=()):
            nodes = [MapNode(f"page:{name}", name, "page", "connected")]
            edges = []
            for key in keys:
                nodes.append(MapNode(f"redis:{key}", key, "redis_key", "connected", file="r.py", line=1))
                edges.append(MapEdge(f"page:{name}", f"redis:{key}", "connected"))
            nodes.extend(extra)
            return PageMap(name, nodes, edges, title=title, where=where)

        model = MapNode("db_table:score", "score", "db_table", "connected", file="m.py", line=3)
        arena_a = page("arena-main", "Arena", "/arena/", ["user:1:stats", "leaderboard:hourly"], [model])
        arena_b = page("arena-side", "Arena", "/arena/", ["user:1:stats"])
        home = page("home-main", "Home", "/", ["leaderboard:hourly"])
        orphan = PageMap(f"{UNREACHED_PAGE}:backend",
                         [MapNode("redis:orphan:key", "orphan:key", "redis_key", "unused",
                                  file="r.py", line=9)],
                         [], title="Not reached from any page", where="Reached by nothing — 1")
        return [arena_a, arena_b, home, orphan]

    def _render(self):
        import json
        out = map_html.render(ConnectivityMap("0" * 12, "", self._entries()))
        start = out.index("{", out.index("MAPDATA"))
        meta = json.JSONDecoder().raw_decode(out, start)[0]
        return meta, out

    @staticmethod
    def _chunk(out, name):
        import json
        tag = f'data-chunk="{name}" data-enc="json">'
        start = out.index(tag) + len(tag)
        return json.JSONDecoder().raw_decode(out, start)[0]

    def test_each_store_is_one_layer_page_across_the_whole_map(self):
        meta, out = self._render()
        names = [p["page"] for p in meta["pages"]]
        self.assertIn("layer:redis", names)
        self.assertIn("layer:database", names)
        redis = self._chunk(out, f"p{names.index('layer:redis')}")
        ids = sorted(row[0] for row in redis["nodes"])
        # Every key once, including the one no page reaches.
        self.assertEqual(ids, ["redis:leaderboard:hourly", "redis:orphan:key", "redis:user:1:stats"])
        database = self._chunk(out, f"p{names.index('layer:database')}")
        self.assertEqual([row[0] for row in database["nodes"]], ["db_table:score"],
                         "a Django table is the database")

    def test_a_layer_node_lists_the_ordinary_pages_that_hold_it(self):
        meta, out = self._render()
        names = [p["page"] for p in meta["pages"]]
        redis = self._chunk(out, f"p{names.index('layer:redis')}")
        on = {row[0]: pg for row, pg in zip(redis["nodes"], redis["pg"], strict=True)}
        by_name = {name: i for i, name in enumerate(names)}
        self.assertEqual(on["redis:user:1:stats"],
                         [by_name["arena-main"], by_name["arena-side"]])
        self.assertEqual(on["redis:leaderboard:hourly"],
                         [by_name["arena-main"], by_name["home-main"]])
        # Not the union the entries were folded into, and not the bucket: on no page.
        self.assertEqual(on["redis:orphan:key"], [])
        for pg in redis["pg"]:
            self.assertNotIn(by_name["group:0"], pg)
        # Ordinary pages do not carry the column.
        self.assertNotIn("pg", self._chunk(out, f"p{by_name['arena-main']}"))
        self.assertNotIn("pg", self._chunk(out, f"p{by_name['layer:stripe']}")
                         if "layer:stripe" in by_name else {})

    def test_shared_is_what_two_pages_reach_and_each_page_says_which_rows(self):
        meta, out = self._render()
        names = [p["page"] for p in meta["pages"]]
        by_name = {name: i for i, name in enumerate(names)}
        self.assertIn("layer:shared", names)
        shared = self._chunk(out, f"p{by_name['layer:shared']}")
        # Two sections of the arena both hold user:1:stats; that is one page, not two.
        # The leaderboard key is on the arena and on home - that is shared.
        self.assertEqual([row[0] for row in shared["nodes"]], ["redis:leaderboard:hourly"])
        self.assertEqual(shared["pg"], [[by_name["arena-main"], by_name["home-main"]]])
        # An ordinary page carries the list sparsely - only the rows two pages reach.
        arena = self._chunk(out, f"p{by_name['arena-main']}")
        row = [row[0] for row in arena["nodes"]].index("redis:leaderboard:hourly")
        self.assertEqual(arena["shared"], [[row, [by_name["arena-main"], by_name["home-main"]]]])
        self.assertNotIn("shared", self._chunk(out, f"p{by_name['arena-side']}"))
        self.assertNotIn("shared", self._chunk(out, f"p{by_name['layer:redis']}"))

    def test_no_shared_layer_when_nothing_is_reached_from_two_pages(self):
        import json
        out = map_html.render(ConnectivityMap("0" * 12, "", self._entries()[2:]))
        start = out.index("{", out.index("MAPDATA"))
        meta = json.JSONDecoder().raw_decode(out, start)[0]
        self.assertNotIn("layer:shared", [p["page"] for p in meta["pages"]])
