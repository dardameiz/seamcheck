import os
import pathlib
import tempfile
import unittest

from seamcheck.entries import fallback
from seamcheck.entries.fallback import FallbackSource


def _repo(files: dict[str, str]) -> str:
    root = pathlib.Path(tempfile.mkdtemp())
    for name, body in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    return str(root)


class FallbackSourceTests(unittest.TestCase):
    def test_a_script_nothing_imports_is_an_entry_file_named_by_its_filename(self):
        found = FallbackSource().entries(_repo({"src/main.js": "console.log(1);"}), {}, graph=None)
        self.assertEqual([(e.key, e.kind, e.roots, e.title, e.where, e.label) for e in found],
                         [("entry_file:src/main.js", "entry_file", ("src/main.js",), "main.js",
                           "no framework says this is a page", "src/main.js")])

    def test_a_script_another_script_imports_is_not_an_entry(self):
        found = FallbackSource().entries(_repo({"main.js": "import './lib'", "lib.js": ""}), {}, graph=None)
        self.assertEqual([e.key for e in found], ["entry_file:main.js"])

    def test_a_relative_repo_root_gives_the_same_answer(self):
        root = _repo({"main.js": ""})
        here = os.getcwd()
        try:
            os.chdir(root)
            found = FallbackSource().entries(".", {}, graph=None)
        finally:
            os.chdir(here)
        self.assertEqual([e.key for e in found], ["entry_file:main.js"])

    def test_no_scripts_means_no_entries(self):
        self.assertEqual(FallbackSource().entries(_repo({"README.md": ""}), {}, graph=None), [])

    def test_detect_is_zero_it_runs_only_when_asked(self):
        self.assertEqual(FallbackSource().detect(_repo({"main.js": ""}), {}), 0.0)


class WhatIsNotAPageTests(unittest.TestCase):
    """The fallback is the last resort, and on saleor-dashboard it became the primary
    answer: 1,240 entries - 902 of them tests and stories, 6 of them tool configs. A file
    that only ever runs under a test runner is not somewhere a reader starts, and neither
    is a tool's own settings file."""

    def test_a_test_or_a_story_is_not_an_entry(self):
        found = FallbackSource().entries(_repo({
            "src/main.js": "",
            "src/thing.test.ts": "",
            "src/thing.spec.js": "",
            "src/thing.stories.tsx": "",
            "src/__tests__/helper.js": "",
            "e2e/login.ts": "",
        }), {}, graph=None)

        self.assertEqual([e.key for e in found], ["entry_file:src/main.js"])

    def test_a_tool_config_is_not_an_entry(self):
        found = FallbackSource().entries(_repo({
            "src/main.js": "",
            "playwright.config.ts": "",
            "jest.config.js": "",
            "next.config.mjs": "",
        }), {}, graph=None)

        self.assertEqual([e.key for e in found], ["entry_file:src/main.js"])


class TooManyToListTests(unittest.TestCase):
    """A list of every script nothing imports is not a page list once it is thousands
    long. Capped, and the cap says what it left out rather than implying the list is
    the whole answer."""

    def _many(self, extra: dict) -> list:
        files = {f"src/a/b/script{i}.js": "" for i in range(fallback._MAX_ENTRIES + 5)}
        return FallbackSource().entries(_repo({**files, **extra}), {}, graph=None)

    def test_more_scripts_than_the_cap_are_cut_to_it(self):
        self.assertEqual(len(self._many({})), fallback._MAX_ENTRIES)

    def test_a_cut_list_says_how_many_scripts_there_were(self):
        found = self._many({})

        self.assertIn(str(fallback._MAX_ENTRIES + 5), found[0].note)

    def test_the_scripts_nearest_the_repository_root_are_the_ones_kept(self):
        # Truncation has to be by something. A page's entry sits near the top of a
        # project far more often than five directories down, so depth is the order.
        found = self._many({"index.js": ""})

        self.assertEqual(found[0].key, "entry_file:index.js")


class BundlerConfigTests(unittest.TestCase):
    """A project with a bundler config HAS declared its entries; this source failing to
    read them is the tool's limit, not the project's shape. The house rule says so on the
    entries rather than presenting a guess as the page list."""

    def test_a_bundler_config_whose_entries_were_not_read_is_said_on_every_entry(self):
        root = _repo({"vite.config.js": "export default defineConfig(userConfig)",
                      "src/main.js": ""})

        found = FallbackSource().entries(root, {}, graph=None)

        self.assertEqual([e.key for e in found], ["entry_file:src/main.js"])
        self.assertIn("bundler config", found[0].note)

    def test_with_no_bundler_config_there_is_nothing_to_say(self):
        found = FallbackSource().entries(_repo({"src/main.js": ""}), {}, graph=None)

        self.assertEqual(found[0].note, "")
