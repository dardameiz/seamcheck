import io
import pathlib
from unittest import mock

from django.test import SimpleTestCase

import seamcheck
from seamcheck import nodetools
from seamcheck.nodetools import parser_path, run_parser

# Located from the package, not from the working directory: an installed copy lives
# wherever pip put it, and a relative "seamcheck/" only resolves when the tests happen
# to be run from the repo root.
PACKAGE = pathlib.Path(seamcheck.__file__).parent


class ParserLocationTests(SimpleTestCase):
    def test_the_bundled_parser_is_preferred_over_the_source(self):
        # `pip install` puts the package where `import acorn` and `import postcss`
        # resolve against nothing; the only reason the source ever ran is that the
        # project it was written in had a node_modules beside it.
        for directory, name in (("js_tools", "parse_js"), ("css_tools", "parse_css")):
            path = parser_path(str(PACKAGE / directory), name)
            self.assertTrue(path.endswith(f"{name}.bundle.mjs"), path)

    def test_both_bundles_actually_shipped(self):
        for directory, name in (("js_tools", "parse_js"), ("css_tools", "parse_css")):
            bundle = PACKAGE / directory / f"{name}.bundle.mjs"
            self.assertTrue(bundle.is_file(), f"{bundle} is missing")
            # Bundled means the dependency is inlined, not imported by name.
            source = bundle.read_text(encoding="utf-8", errors="replace")
            self.assertNotIn("from 'acorn'", source)
            self.assertNotIn("from 'postcss'", source)

    def test_the_source_parser_is_used_when_no_bundle_is_present(self):
        path = parser_path("/nowhere", "parse_js")

        self.assertTrue(path.endswith("parse_js.mjs"))


def _process(returncode, stdout="", stderr=""):
    """What Popen hands back: three pipes and an exit code."""
    return mock.Mock(
        stdin=io.BytesIO(), stdout=io.BytesIO(stdout.encode()),
        stderr=io.BytesIO(stderr.encode()), returncode=returncode,
        wait=mock.Mock(return_value=returncode),
    )


class DegradationTests(SimpleTestCase):
    def setUp(self):
        nodetools._reported.clear()

    def test_a_missing_node_costs_its_symbols_and_nothing_else(self):
        # check=True raised, which took the other half of the graph down with it.
        with mock.patch("subprocess.Popen", side_effect=FileNotFoundError):
            self.assertEqual(list(run_parser("p.mjs", ["a.js"], "JavaScript")), [])

    def test_a_parser_that_exits_non_zero_is_reported_not_raised(self):
        with (
            mock.patch("subprocess.Popen", return_value=_process(1, stderr="ERR_MODULE_NOT_FOUND")),
            self.assertLogs("seamcheck.nodetools", "WARNING") as logs,
        ):
            self.assertEqual(list(run_parser("p.mjs", ["a.css"], "CSS")), [])

        self.assertIn("no CSS symbols", logs.output[0])

    def test_a_parser_that_dies_part_way_keeps_what_it_sent(self):
        # 21,000 files of NDJSON once overflowed the pipe and the whole batch was lost.
        # The records that arrived before the crash are real; only the rest is missing.
        with (
            mock.patch("subprocess.Popen", return_value=_process(1, '{"a":1}\n', "ENOBUFS")),
            self.assertLogs("seamcheck.nodetools", "WARNING") as logs,
        ):
            self.assertEqual(list(run_parser("p.mjs", ["a.js", "b.js"], "JavaScript")), ['{"a":1}'])

        self.assertIn("incomplete JavaScript symbols", logs.output[0])
        self.assertIn("after 1 file(s)", logs.output[0])

    def test_it_says_so_once_not_once_per_batch(self):
        # Parsers run once per batch of files, so an unguarded warning repeats per batch.
        with (
            mock.patch("subprocess.Popen", side_effect=FileNotFoundError),
            self.assertLogs("seamcheck.nodetools", "WARNING") as logs,
        ):
            for _ in range(5):
                list(run_parser("p.mjs", ["a.js"], "JavaScript"))

        self.assertEqual(len(logs.output), 1)

    def test_a_working_parser_streams_its_lines(self):
        with mock.patch("subprocess.Popen", return_value=_process(0, '{"a":1}\n{"b":2}\n')):
            self.assertEqual(list(run_parser("p.mjs", ["a.js"], "JavaScript")),
                             ['{"a":1}', '{"b":2}'])
