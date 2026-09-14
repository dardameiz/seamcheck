import pathlib
import subprocess
import tempfile

from django.test import SimpleTestCase, override_settings

from seamcheck.api import scoped_map_document
from seamcheck.graph import Graph, Status, Symbol


def _symbol(id_, file):
    return Symbol(
        id=id_, kind="dom_selector", label=id_, sub="", file=file, line=1,
        status=Status.UNRESOLVED, snippet="", chain=[], note="",
    )


def _git(args, cwd):
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True)


class ScopedMapDocumentTests(SimpleTestCase):
    def _project(self, root: pathlib.Path):
        (root / "entry_a.js").write_text("import './module_a.js';\n", encoding="utf-8")
        (root / "module_a.js").write_text("export function f() {}\n", encoding="utf-8")
        _git(["init", "-q"], root)
        _git(["config", "user.email", "t@t"], root)
        _git(["config", "user.name", "t"], root)
        _git(["add", "-A"], root)
        _git(["commit", "-q", "-m", "initial"], root)

    def test_renders_a_complete_document_focused_on_the_requested_page(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            self._project(root)
            graph = Graph(symbols=[_symbol("s1", "module_a.js")], edges=[])

            with override_settings(SEAMCHECK_CONFIG={"js_entry_files": ["entry_a.js"]}):
                document = scoped_map_document(str(root), "entry_a", graph=graph)
                out = document.single_file()

        self.assertTrue(out.lstrip().startswith("<!doctype html>"))
        self.assertIn('var target="entry_a";', out)
        self.assertIn("switchTo('map')", out)

    def test_a_page_with_no_findings_still_renders(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            self._project(root)
            graph = Graph(symbols=[], edges=[])

            with override_settings(SEAMCHECK_CONFIG={"js_entry_files": ["entry_a.js"]}):
                document = scoped_map_document(str(root), "entry_a", graph=graph)

            self.assertTrue(document.single_file())

    def test_it_populates_last_map_files_so_a_served_map_can_serve_source(self):
        # Without this, the served map's "view code" panel has nothing in its
        # allow-list to fetch (serve.py's /source endpoint refuses any path not in
        # `sources`) and silently falls back to a bare snippet - even though the map
        # genuinely is being served. See scopedserve.py's own sources=set(...) use.
        from seamcheck import api

        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            self._project(root)
            graph = Graph(symbols=[_symbol("s1", "module_a.js")], edges=[])

            with override_settings(SEAMCHECK_CONFIG={"js_entry_files": ["entry_a.js"]}):
                scoped_map_document(str(root), "entry_a", graph=graph)

            self.assertIn("module_a.js", api.LAST_MAP_FILES)
