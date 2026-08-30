import pathlib
from unittest import mock

from django.test import SimpleTestCase

from seamcheck import nodetools
from seamcheck.nodetools import parser_path, run_parser


class ParserLocationTests(SimpleTestCase):
    def test_the_bundled_parser_is_preferred_over_the_source(self):
        # `pip install` puts the package where `import acorn` and `import postcss`
        # resolve against nothing; the only reason the source ever ran is that the
        # project it was written in had a node_modules beside it.
        for directory, name in (("js_tools", "parse_js"), ("css_tools", "parse_css")):
            path = parser_path(str(pathlib.Path("seamcheck") / directory), name)
            self.assertTrue(path.endswith(f"{name}.bundle.mjs"), path)

    def test_both_bundles_actually_shipped(self):
        for directory, name in (("js_tools", "parse_js"), ("css_tools", "parse_css")):
            bundle = pathlib.Path("seamcheck") / directory / f"{name}.bundle.mjs"
            self.assertTrue(bundle.is_file(), f"{bundle} is missing")
            # Bundled means the dependency is inlined, not imported by name.
            source = bundle.read_text(encoding="utf-8", errors="replace")
            self.assertNotIn("from 'acorn'", source)
            self.assertNotIn("from 'postcss'", source)

    def test_the_source_parser_is_used_when_no_bundle_is_present(self):
        path = parser_path("/nowhere", "parse_js")

        self.assertTrue(path.endswith("parse_js.mjs"))


class DegradationTests(SimpleTestCase):
    def setUp(self):
        nodetools._reported.clear()

    def test_a_missing_node_costs_its_symbols_and_nothing_else(self):
        # check=True raised, which took the other half of the graph down with it.
        with mock.patch("subprocess.run", side_effect=FileNotFoundError):
            self.assertEqual(run_parser("p.mjs", ["a.js"], "JavaScript"), [])

    def test_a_parser_that_exits_non_zero_is_reported_not_raised(self):
        failed = mock.Mock(returncode=1, stdout="", stderr="ERR_MODULE_NOT_FOUND")
        with (
            mock.patch("subprocess.run", return_value=failed),
            self.assertLogs("seamcheck.nodetools", "WARNING") as logs,
        ):
            self.assertEqual(run_parser("p.mjs", ["a.css"], "CSS"), [])

        self.assertIn("no CSS symbols", logs.output[0])

    def test_it_says_so_once_not_once_per_batch(self):
        # Parsers run per batch of files; the same line appeared ninety times in one scan.
        with (
            mock.patch("subprocess.run", side_effect=FileNotFoundError),
            self.assertLogs("seamcheck.nodetools", "WARNING") as logs,
        ):
            for _ in range(5):
                run_parser("p.mjs", ["a.js"], "JavaScript")

        self.assertEqual(len(logs.output), 1)

    def test_a_working_parser_returns_its_lines(self):
        ok = mock.Mock(returncode=0, stdout='{"a":1}\n{"b":2}\n', stderr="")
        with mock.patch("subprocess.run", return_value=ok):
            self.assertEqual(run_parser("p.mjs", ["a.js"], "JavaScript"),
                             ['{"a":1}', '{"b":2}'])
