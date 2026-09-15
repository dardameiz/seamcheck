import pathlib
import tempfile
import unittest

from seamcheck.adapters.nextjs_adapter import NextJSAdapter
from seamcheck.entries.nextjs import NextJSSource
from seamcheck.progress import null


def _repo(files: dict[str, str]) -> str:
    root = pathlib.Path(tempfile.mkdtemp())
    for name, body in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    return str(root)


PAGE = "export default function P() { return null; }"
LAYOUT = "export default function L({ children }) { return children; }"
CONFIG = {"next.config.js": "module.exports = {};"}


def _keys(root):
    return [entry.key for entry in NextJSSource().entries(root, {}, graph=None)]


class AppRouterTests(unittest.TestCase):
    def test_the_root_page_is_home(self):
        [entry] = NextJSSource().entries(_repo({**CONFIG, "app/page.tsx": PAGE}), {}, graph=None)
        self.assertEqual((entry.key, entry.kind, entry.title, entry.where, entry.label),
                         ("next:/", "page", "Home", "/ - app/page.tsx", "/"))

    def test_a_page_is_rooted_with_every_layout_above_it_nearest_first(self):
        root = _repo({**CONFIG, "app/layout.tsx": LAYOUT, "app/pricing/layout.tsx": LAYOUT,
                      "app/pricing/template.tsx": LAYOUT, "app/pricing/page.tsx": PAGE})
        pricing = next(e for e in NextJSSource().entries(root, {}, graph=None) if e.key == "next:/pricing")
        self.assertEqual(pricing.roots, ("app/pricing/page.tsx", "app/pricing/template.tsx",
                                         "app/pricing/layout.tsx", "app/layout.tsx"))

    def test_a_route_handler_is_not_a_page(self):
        self.assertEqual(_keys(_repo({**CONFIG, "app/api/checkout/route.ts": "export function POST() {}"})), [])

    def test_a_dynamic_segment_keeps_its_brackets_and_the_title_uses_the_fixed_part(self):
        [entry] = NextJSSource().entries(_repo({**CONFIG, "app/pricing/[locale]/page.tsx": PAGE}), {}, graph=None)
        self.assertEqual((entry.key, entry.title), ("next:/pricing/[locale]", "Pricing"))

    def test_route_groups_vanish_and_slots_and_private_folders_are_not_pages(self):
        root = _repo({**CONFIG, "app/(marketing)/about/page.tsx": PAGE,
                      "app/@modal/login/page.tsx": PAGE, "app/_lib/page.tsx": PAGE})
        self.assertEqual(_keys(root), ["next:/about"])

    def test_keys_name_the_routes_the_adapter_reports(self):
        root = _repo({**CONFIG, "app/page.tsx": PAGE, "app/pricing/[locale]/page.tsx": PAGE,
                      "pages/about.tsx": PAGE})
        urls = {s.id.removeprefix("url:") for s in NextJSAdapter().scan(root, {}, null()).symbols
                if s.kind == "url"}
        self.assertTrue({key.removeprefix("next:") for key in _keys(root)} <= urls)


class PagesRouterTests(unittest.TestCase):
    def test_a_file_under_pages_is_a_page(self):
        self.assertEqual(_keys(_repo({**CONFIG, "pages/about.tsx": PAGE})), ["next:/about"])

    def test_underscore_files_and_the_api_folder_are_not_pages(self):
        root = _repo({**CONFIG, "pages/_app.tsx": PAGE, "pages/_document.tsx": PAGE,
                      "pages/api/hello.ts": "export default function h() {}", "pages/index.tsx": PAGE})
        self.assertEqual(_keys(root), ["next:/"])


class MonorepoTests(unittest.TestCase):
    def test_two_apps_each_with_a_home_page_get_two_keys_qualified_like_the_adapters_urls(self):
        root = _repo({"apps/web/next.config.js": "", "apps/web/app/page.tsx": PAGE,
                      "apps/docs/next.config.js": "", "apps/docs/app/page.tsx": PAGE})
        self.assertEqual(sorted(_keys(root)), ["next:apps/docs:/", "next:apps/web:/"])

    def test_a_qualified_page_is_labelled_with_its_app_and_its_address(self):
        root = _repo({"apps/web/next.config.js": "", "apps/web/app/page.tsx": PAGE,
                      "apps/docs/next.config.js": "", "apps/docs/app/page.tsx": PAGE})
        labels = sorted(entry.label for entry in NextJSSource().entries(root, {}, graph=None))
        self.assertEqual(labels, ["apps/docs:/", "apps/web:/"])


class DetectTests(unittest.TestCase):
    def test_detect_is_the_adapters_answer(self):
        root = _repo({**CONFIG, "app/page.tsx": PAGE})
        self.assertEqual(NextJSSource().detect(root, {}), NextJSAdapter().detect(root, {}))

    def test_an_unrelated_project_is_zero(self):
        self.assertEqual(NextJSSource().detect(_repo({"README.md": ""}), {}), 0.0)
