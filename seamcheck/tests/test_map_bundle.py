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
