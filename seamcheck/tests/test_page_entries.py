import pathlib
import tempfile
import unittest
from unittest import mock

from seamcheck import api
from seamcheck.entries.base import Entry
from seamcheck.graph import Edge, Graph, Status, Symbol


def _repo(files: dict[str, str]) -> str:
    root = pathlib.Path(tempfile.mkdtemp())
    for name, body in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    return str(root)


def _symbol(id_, kind, label, file):
    return Symbol(id=id_, kind=kind, label=label, sub="", file=file, line=1,
                  status=Status.CONNECTED, snippet="", chain=[], note="")


EMPTY = Graph(symbols=[], edges=[])
NEXT_APP = {
    "next.config.js": "module.exports = {};",
    "tsconfig.json": '{"compilerOptions": {"baseUrl": ".", "paths": {"@/*": ["./*"]}}}',
    "app/layout.tsx": "import './globals.css'; export default function L({ children }) { return children; }",
    "app/globals.css": ".x { color: red }",
    "app/page.tsx": "import { greet } from '@/lib/greet'; export default function P() { return greet(); }",
    "lib/greet.ts": "export function greet() { return 'hi'; }",
}


class PageFilesForTests(unittest.TestCase):
    def test_a_nextjs_page_reaches_its_layout_its_alias_imports_and_its_stylesheet(self):
        root = _repo(NEXT_APP)
        pages = api._page_files_for(root, EMPTY)
        self.assertEqual(pages["next:/"],
                         {"app/page.tsx", "app/layout.tsx", "lib/greet.ts", "app/globals.css"})

    def test_a_python_handler_reaches_its_own_file(self):
        root = _repo({"app/views.py": "def x(request): pass"})
        graph = Graph(symbols=[_symbol("url:x/", "url", "x/", "app/urls.py"),
                               _symbol("view:app.views.x", "view", "x", "app/views.py")],
                      edges=[Edge("url:x/", "view:app.views.x", Status.CONNECTED)])
        self.assertEqual(api._page_files_for(root, graph)["server:app/views.py"], {"app/views.py"})


class PageEntriesTests(unittest.TestCase):
    def test_entries_from_an_already_scanned_graph_need_no_second_scan(self):
        root = _repo(NEXT_APP)
        with mock.patch("seamcheck.scancache.cached_scan") as cached:
            found = api.page_entries(root, EMPTY)
        cached.assert_not_called()
        self.assertEqual([entry.key for entry in found], ["next:/"])


class PageFilesTests(unittest.TestCase):
    def test_page_files_keeps_its_one_argument_shape(self):
        root = _repo(NEXT_APP)
        with mock.patch("seamcheck.scancache.cached_scan", return_value=(EMPTY, "memory")):
            pages = api.page_files(root)
        self.assertEqual(set(pages), {"next:/"})
        self.assertTrue(all(isinstance(files, set) for files in pages.values()))


class NamesFromEntriesTests(unittest.TestCase):
    def test_an_entrys_label_reaches_the_name_the_map_draws(self):
        entry = Entry(key="next:/", kind="page", roots=("app/page.tsx",), title="Home",
                      where="/ - app/page.tsx", label="/")
        self.assertEqual(api._names_from_entries([entry])["next:/"].label, "/")
