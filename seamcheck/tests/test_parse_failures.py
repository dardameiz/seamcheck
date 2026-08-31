"""A file the parser cannot read must never vanish quietly.

The scan runs entirely inside `quiet()`, which disables WARNING-level logging so the host
project's start-up noise stays out of the output. Before these tests, a parse failure was
dropped by `if "ast" in record` with no else branch, so a codebase whose files the parser
could not read produced almost no JavaScript
symbols and still reported success. Missing evidence read as a pass, which is the one thing
this tool must never do.

TypeScript and JSX were the original trigger and no longer fail - they reach acorn through
sucrase now - so these fixtures use genuinely invalid syntax instead.
"""

from __future__ import annotations

import contextlib
import io
import pathlib
import tempfile
import unittest

import seamcheck.nodetools as nodetools
from seamcheck.extractors.css_extractor import parse_css_files
from seamcheck.extractors.js_extractor import _parse_files
from seamcheck.quiet import quiet


class ParseFailuresAreReported(unittest.TestCase):
    def setUp(self):
        nodetools._reported.clear()
        self.directory = pathlib.Path(tempfile.mkdtemp())

    def _run(self, call):
        """Run inside quiet(), exactly as the CLI runs a scan, capturing stderr."""
        captured = io.StringIO()
        with quiet(), contextlib.redirect_stderr(captured):
            result = call()
        return result, captured.getvalue()

    def test_unparseable_js_is_reported_through_quiet(self):
        good = self.directory / "ok.js"
        good.write_text("fetch('/api/x/');")
        bad = self.directory / "app.js"
        bad.write_text("function ( { : : :\n")   # genuinely not JavaScript

        parsed, stderr = self._run(lambda: _parse_files([str(good), str(bad)]))

        self.assertIn(str(good), parsed, "valid JavaScript must still parse")
        self.assertNotIn(str(bad), parsed)
        self.assertIn("app.js", stderr)
        self.assertIn("could not be parsed", stderr)

    def test_valid_js_alone_says_nothing(self):
        good = self.directory / "ok.js"
        good.write_text("fetch('/api/x/');")

        parsed, stderr = self._run(lambda: _parse_files([str(good)]))

        self.assertEqual(len(parsed), 1)
        self.assertEqual(stderr, "", "a clean run must stay silent")

    def test_failure_is_reported_once_not_per_file(self):
        paths = []
        for index in range(5):
            bad = self.directory / f"bad{index}.js"
            bad.write_text("function ( { : : :\n")
            paths.append(str(bad))

        _, stderr = self._run(lambda: _parse_files(paths))

        self.assertEqual(stderr.count("seamcheck:"), 1, "one summary line, not five")
        self.assertIn("5 JavaScript file(s)", stderr)

    def test_unparseable_css_is_reported(self):
        bad = self.directory / "broken.css"
        bad.write_text("@media (min-width: {{ oops }} { .a { color: red")

        records, stderr = self._run(lambda: parse_css_files([str(bad)]))

        if any("error" in record for record in records):
            self.assertIn("broken.css", stderr)
            self.assertIn("could not be parsed", stderr)


if __name__ == "__main__":
    unittest.main()
