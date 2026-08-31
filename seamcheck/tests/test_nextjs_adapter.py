"""Next.js: the routes are the filesystem, so the conventions ARE the parser.

There is nothing to parse to find a route here - `app/api/orders/route.ts` serves
`/api/orders` - which makes this the only route reader that cannot be defeated by an
unusual mounting idiom. What it can be defeated by is a directory that does not mean what
it looks like, and a project that uses those uses them everywhere:

    app/(marketing)/about/page.tsx   ->  /about          parentheses vanish
    app/@modal/login/page.tsx        ->  not a route     parallel route slot
    app/_lib/helpers.ts              ->  not a route     private folder
    pages/index.tsx                  ->  /

And the shape that mattered most on real repositories: a Next.js product is nearly always
a monorepo, so `next.config.js` is at `apps/web/`, not at the root. Looking only at the
root found zero routes in two real products with 731 between them.
"""

from __future__ import annotations

import pathlib
import tempfile
import unittest

from seamcheck.adapters.nextjs_adapter import NextJSAdapter, _app_dirs
from seamcheck.progress import null


def _repo(files: dict[str, str]) -> str:
    root = pathlib.Path(tempfile.mkdtemp())
    for name, body in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    return str(root)


def _paths(root: str) -> dict[str, str]:
    scan = NextJSAdapter().scan(root, {}, null())
    return {s.label: s.sub for s in scan.symbols if s.kind == "url"}


PAGE = "export default function P() { return null; }"
CONFIG = {"next.config.js": "module.exports = {};"}


class AppRouter(unittest.TestCase):
    def test_the_root_page_is_slash(self):
        self.assertIn("/", _paths(_repo({**CONFIG, "app/page.tsx": PAGE})))

    def test_a_nested_page_is_its_directory(self):
        self.assertIn("/dashboard", _paths(_repo({**CONFIG, "app/dashboard/page.tsx": PAGE})))

    def test_a_route_handler_reports_its_exported_verbs(self):
        paths = _paths(_repo({**CONFIG, "app/api/orders/route.ts":
                              "export async function GET() {}\nexport async function POST() {}"}))
        self.assertEqual(paths["/api/orders"], "GET/POST")

    def test_a_route_group_does_not_become_a_segment(self):
        self.assertIn("/about", _paths(_repo({**CONFIG, "app/(marketing)/about/page.tsx": PAGE})))

    def test_a_dynamic_segment_is_kept_verbatim(self):
        self.assertIn("/blog/[slug]", _paths(_repo({**CONFIG, "app/blog/[slug]/page.tsx": PAGE})))

    def test_a_catch_all_segment_is_kept(self):
        self.assertIn("/docs/[...path]",
                      _paths(_repo({**CONFIG, "app/docs/[...path]/page.tsx": PAGE})))

    def test_a_parallel_route_slot_is_not_a_url(self):
        self.assertEqual(_paths(_repo({**CONFIG, "app/@modal/login/page.tsx": PAGE})), {})

    def test_a_private_folder_is_not_a_url(self):
        self.assertEqual(_paths(_repo({**CONFIG, "app/_lib/page.tsx": PAGE})), {})

    def test_a_non_route_file_in_app_is_ignored(self):
        self.assertEqual(_paths(_repo({**CONFIG, "app/dashboard/helpers.ts": "export const x=1;"})), {})


class PagesRouter(unittest.TestCase):
    def test_index_is_the_directory_itself(self):
        self.assertIn("/", _paths(_repo({**CONFIG, "pages/index.tsx": PAGE})))

    def test_a_file_name_is_the_last_segment(self):
        self.assertIn("/api/orders", _paths(_repo({**CONFIG, "pages/api/orders.ts": PAGE})))

    def test_framework_plumbing_is_not_a_route(self):
        paths = _paths(_repo({**CONFIG, "pages/_app.tsx": PAGE, "pages/_document.tsx": PAGE}))
        self.assertEqual(paths, {})


class Monorepos(unittest.TestCase):
    def test_an_app_three_levels_down_is_found(self):
        root = _repo({
            "package.json": '{"workspaces": ["apps/*"]}',
            "apps/web/next.config.js": "module.exports = {};",
            "apps/web/app/dashboard/page.tsx": PAGE,
        })
        self.assertIn("/dashboard", _paths(root))

    def test_a_src_layout_is_found(self):
        root = _repo({**CONFIG, "src/app/dashboard/page.tsx": PAGE})
        self.assertIn("/dashboard", _paths(root))

    def test_two_apps_can_each_serve_slash(self):
        """Different apps are different sites; collapsing them loses a real route."""
        root = _repo({
            "apps/web/next.config.js": "module.exports = {};",
            "apps/web/app/page.tsx": PAGE,
            "apps/docs/next.config.js": "module.exports = {};",
            "apps/docs/app/page.tsx": PAGE,
        })
        scan = NextJSAdapter().scan(root, {}, null())
        roots = [s for s in scan.symbols if s.kind == "url" and s.label == "/"]
        self.assertEqual(len(roots), 2)
        self.assertEqual(len({s.id for s in roots}), 2, "ids must be distinct")


class Detection(unittest.TestCase):
    def test_a_config_at_the_root_is_decisive(self):
        self.assertGreater(NextJSAdapter().detect(_repo({**CONFIG, "app/page.tsx": PAGE}), {}), 0.9)

    def test_a_package_json_dependency_is_enough(self):
        root = _repo({"package.json": '{"dependencies": {"next": "14.0.0"}}', "app/page.tsx": PAGE})
        self.assertGreater(NextJSAdapter().detect(root, {}), 0.9)

    def test_an_unrelated_repo_scores_zero(self):
        self.assertEqual(NextJSAdapter().detect(_repo({"a.py": "print(1)"}), {}), 0.0)

    def test_apps_are_found_in_a_monorepo(self):
        root = _repo({
            "apps/web/next.config.js": "module.exports = {};",
            "apps/docs/next.config.js": "module.exports = {};",
        })
        self.assertEqual(len(_app_dirs(root)), 2)


if __name__ == "__main__":
    unittest.main()
