"""The parsed-tree cache is a budget, not a promise to hold everything.

A 21,000-file monorepo came to 3.4 GB of RSS when every tree stayed resident for the
scan. That is fine on a workstation and fatal on an 8 GB CI box, so the cache admits
files until their parser output reaches the budget and then stops admitting. What is
not admitted is still parsed and still yielded - the scan is complete either way - it
is just parsed again for the next extractor that asks.
"""

from __future__ import annotations

import os
import pathlib
import tempfile
import unittest
from unittest import mock

from seamcheck.extractors import js_extractor
from seamcheck.extractors.js_extractor import clear_parse_cache, iter_parsed


class AstCacheBudgetTests(unittest.TestCase):
    def setUp(self):
        clear_parse_cache()
        self.addCleanup(clear_parse_cache)
        directory = pathlib.Path(tempfile.mkdtemp())
        self.paths = []
        for index in range(3):
            path = directory / f"{index}.js"
            path.write_text(f"export const v{index} = fetch('/api/{index}/');\n")
            self.paths.append(str(path))

    def _parses(self):
        """How many times the parser was actually spawned for these files."""
        real = js_extractor.run_parser
        spawns = []

        def counting(script, paths, what):
            spawns.append(list(paths))
            return real(script, paths, what)

        return spawns, mock.patch.object(js_extractor, "run_parser", counting)

    def test_within_the_budget_a_file_is_parsed_once_per_scan(self):
        spawns, patch = self._parses()
        with patch:
            first = dict(iter_parsed(self.paths))
            second = dict(iter_parsed(self.paths))
        self.assertEqual(set(first), set(self.paths))
        self.assertEqual(second, first)
        self.assertEqual(len(spawns), 1)

    def test_over_the_budget_every_file_is_still_parsed_but_not_kept(self):
        spawns, patch = self._parses()
        with patch, mock.patch.dict(os.environ, {"SEAMCHECK_AST_CACHE_MB": "0"}):
            first = dict(iter_parsed(self.paths))
            second = dict(iter_parsed(self.paths))
        # Nothing lost: the scan sees all three files both times.
        self.assertEqual(set(first), set(self.paths))
        self.assertEqual(set(second), set(self.paths))
        # Nothing kept: the second pass parsed them again instead of growing the cache.
        self.assertEqual(js_extractor._AST_CACHE, {})
        self.assertEqual(len(spawns), 2)

    def test_the_budget_is_a_quarter_of_memory_unless_set(self):
        with mock.patch.dict(os.environ, {"SEAMCHECK_AST_CACHE_MB": "580"}):
            # 580 MB of trees at 5.8x is 100 MB of parser output.
            self.assertEqual(js_extractor._ast_budget(), 100 * 1024 * 1024)
        with mock.patch.dict(os.environ, {"SEAMCHECK_AST_CACHE_MB": ""}):
            self.assertGreater(js_extractor._ast_budget(), 0)
