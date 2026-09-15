import os
import pathlib
import tempfile
import unittest

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
