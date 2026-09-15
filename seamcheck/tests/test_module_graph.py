import os
import pathlib
import tempfile
import unittest

from seamcheck.extractors.js_extractor import build_module_graph, discover_js_files


def _repo(files: dict[str, str]) -> str:
    root = pathlib.Path(tempfile.mkdtemp())
    for name, body in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    return str(root)


def _abs(root: str, *names: str) -> set[str]:
    return {os.path.join(root, name) for name in names}


ALIAS = '{"compilerOptions": {"baseUrl": ".", "paths": {"@/*": ["./*"]}}}'


class ModuleGraphTests(unittest.TestCase):
    def test_walking_out_from_one_root_reaches_everything_it_imports(self):
        root = _repo({"a.ts": "import './b'", "b.ts": "import './c'", "c.ts": "export const x = 1;"})
        graph = build_module_graph(root, [os.path.join(root, "a.ts")])
        self.assertEqual(graph.reachable_files([os.path.join(root, "a.ts")]),
                         _abs(root, "a.ts", "b.ts", "c.ts"))

    def test_one_graph_answers_each_roots_reach_separately(self):
        root = _repo({"a.ts": "import './shared'", "b.ts": "export const y = 2;", "shared.ts": ""})
        graph = build_module_graph(root, [os.path.join(root, "a.ts"), os.path.join(root, "b.ts")])
        self.assertEqual(graph.reachable_files([os.path.join(root, "a.ts")]), _abs(root, "a.ts", "shared.ts"))
        self.assertEqual(graph.reachable_files([os.path.join(root, "b.ts")]), _abs(root, "b.ts"))

    def test_an_import_cycle_is_walked_once(self):
        root = _repo({"a.ts": "import './b'", "b.ts": "import './a'"})
        graph = build_module_graph(root, [os.path.join(root, "a.ts")])
        self.assertEqual(graph.reachable_files([os.path.join(root, "b.ts")]), _abs(root, "a.ts", "b.ts"))

    def test_a_stylesheet_import_is_reached_as_an_asset_and_not_walked(self):
        root = _repo({"a.ts": "import './styles.css'", "styles.css": ".x { color: red }"})
        graph = build_module_graph(root, [os.path.join(root, "a.ts")])
        self.assertEqual(graph.reachable_files([os.path.join(root, "a.ts")]), _abs(root, "a.ts"))
        self.assertEqual(graph.reachable_assets([os.path.join(root, "a.ts")]), _abs(root, "styles.css"))

    def test_an_alias_import_is_followed(self):
        root = _repo({
            "tsconfig.json": ALIAS,
            "app/page.tsx": "import { X } from '@/components/X'",
            "components/X.tsx": "export const X = 1;",
        })
        graph = build_module_graph(root, [os.path.join(root, "app/page.tsx")])
        self.assertIn(os.path.join(root, "components/X.tsx"),
                      graph.reachable_files([os.path.join(root, "app/page.tsx")]))

    def test_a_package_import_is_not_an_edge(self):
        root = _repo({"a.ts": "import React from 'react'"})
        graph = build_module_graph(root, [os.path.join(root, "a.ts")])
        self.assertEqual(graph.reachable_files([os.path.join(root, "a.ts")]), _abs(root, "a.ts"))

    def test_a_root_that_is_not_javascript_is_its_own_whole_reach(self):
        # A Python route handler is an entry too; its reach is its own file, and the
        # call graph - not this walk - carries it further.
        root = _repo({"views.py": "def x(request): pass"})
        graph = build_module_graph(root, [os.path.join(root, "views.py")])
        self.assertEqual(graph.reachable_files([os.path.join(root, "views.py")]), _abs(root, "views.py"))


class ExistingWalksFollowAliasesTests(unittest.TestCase):
    def test_discover_js_files_now_follows_a_tsconfig_alias(self):
        root = _repo({
            "tsconfig.json": ALIAS,
            "app/page.tsx": "import { X } from '@/components/X'",
            "components/X.tsx": "export const X = 1;",
        })
        self.assertIn(os.path.join(root, "components/X.tsx"), discover_js_files(["app/page.tsx"], root))
