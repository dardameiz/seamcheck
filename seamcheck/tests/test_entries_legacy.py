import os
import pathlib
import tempfile
import unittest
from unittest import mock

from seamcheck.entries.legacy import LegacySource
from seamcheck.roots import discover_js_roots


def _repo(files: dict[str, str]) -> str:
    root = pathlib.Path(tempfile.mkdtemp())
    for name, body in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    return str(root)


TEMPLATE_SITE = {
    "templates/home.html": '<script src="/static/js/app.js"></script>',
    "static/js/app.js": "console.log(1);",
}
NEXT_SITE = {
    "next.config.js": "module.exports = {};",
    "app/page.tsx": "export default function P() { return null; }",
    "scripts/loose.js": "console.log(1);",
}


def _not_declared():
    # conftest.py configures SEAMCHECK_CONFIG with `js_entry_files: []`, so inside the
    # suite every js_entry_files looks written by hand unless this says otherwise.
    return mock.patch("seamcheck.autoconfig.declared_config", return_value={})


class TodaysPagesTests(unittest.TestCase):
    def test_a_template_loaded_script_is_a_page_keyed_by_its_filename(self):
        root = _repo(TEMPLATE_SITE)
        found = LegacySource().entries(root, {"templates_root": "templates"}, graph=None)
        self.assertEqual([(e.key, e.kind, e.roots) for e in found],
                         [("app", "page", ("static/js/app.js",))])

    def test_roots_are_exactly_discover_js_roots_in_the_same_order(self):
        # The legacy guard: this source returns today's roots, in today's order.
        root = _repo({**TEMPLATE_SITE,
                      "templates/other.html": '<script src="/static/js/other.js"></script>',
                      "static/js/other.js": ""})
        expected = discover_js_roots(
            vite_config=os.path.join(root, "vite.config.js"),
            templates_root=os.path.join(root, "templates"),
            static_root=os.path.join(root, "static"),
        )
        found = LegacySource().entries(root, {"templates_root": "templates"}, graph=None)
        self.assertEqual([e.roots[0] for e in found],
                         [os.path.relpath(path, root).replace(os.sep, "/") for path in expected])

    def test_a_root_nothing_names_keeps_todays_title_and_empty_address(self):
        root = _repo({"src/main.js": ""})
        found = LegacySource().entries(root, {"js_entry_files": ["src/main.js"]}, graph=None)
        # No label: a legacy key is already the name a reader knows the page by.
        self.assertEqual([(e.key, e.title, e.where, e.label) for e in found], [("main", "main", "", "")])


class DetectTests(unittest.TestCase):
    def test_detects_when_a_template_loads_a_script(self):
        root = _repo(TEMPLATE_SITE)
        self.assertEqual(LegacySource().detect(root, {"templates_root": "templates"}), 0.5)

    def test_does_not_detect_when_nothing_is_declared_or_discovered(self):
        self.assertEqual(LegacySource().detect(_repo({"README.md": ""}), {}), 0.0)


class SweepTests(unittest.TestCase):
    """autoconfig turns every loose .js file into its own entry when there is no bundler to
    ask. Beside Next.js those files are scripts, not pages - but autoconfig is untouched, so
    the scan still reads them."""

    def test_a_detected_sweep_is_not_pages_when_nextjs_has_pages(self):
        root = _repo(NEXT_SITE)
        with _not_declared():
            found = LegacySource().entries(root, {"js_entry_files": ["scripts/loose.js"]}, graph=None)
        self.assertEqual(found, [])

    def test_a_js_entry_files_written_by_hand_is_never_dropped(self):
        root = _repo(NEXT_SITE)
        declared = {"js_entry_files": ["scripts/loose.js"]}
        with mock.patch("seamcheck.autoconfig.declared_config", return_value=declared):
            found = LegacySource().entries(root, dict(declared), graph=None)
        self.assertEqual([e.key for e in found], ["loose"])

    def test_a_detected_sweep_stays_when_nothing_else_has_pages(self):
        root = _repo({"scripts/loose.js": "console.log(1);"})
        with _not_declared():
            found = LegacySource().entries(root, {"js_entry_files": ["scripts/loose.js"]}, graph=None)
        self.assertEqual([e.key for e in found], ["loose"])

    def test_template_loaded_scripts_stay_even_beside_nextjs(self):
        root = _repo({**NEXT_SITE, **TEMPLATE_SITE})
        with _not_declared():
            found = LegacySource().entries(root, {"templates_root": "templates"}, graph=None)
        self.assertEqual([e.key for e in found], ["app"])
