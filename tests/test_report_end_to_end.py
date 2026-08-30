from pathlib import Path

from django.test import SimpleTestCase, override_settings

from signal_map import api
from signal_map.mcp_server import signal_map_report

FIXTURES_DIR = str(Path(__file__).parent / "fixtures")
_CONFIG = {
    "urlconf_module": "signal_map.tests.fixtures.fixture_urls",
    "js_entry_files": ["fixture_entry.js"],
    "js_project_root": FIXTURES_DIR,
    "entry_point_files": [],
}


@override_settings(SIGNAL_MAP_CONFIG=_CONFIG)
class EndToEndReportTests(SimpleTestCase):
    def test_every_format_renders_from_a_real_scan(self):
        for fmt, marker in (
            ("terminal", "Signal Map"),
            ("markdown", "## Signal Map"),
            ("html", "<!doctype html>"),
        ):
            with self.subTest(fmt=fmt):
                self.assertIn(marker, api.report(".", fmt))

    def test_an_unknown_format_raises_with_the_allowed_list(self):
        with self.assertRaises(ValueError) as raised:
            api.report(".", "yaml")

        self.assertIn("markdown", str(raised.exception))

    def test_the_real_fixture_scan_never_presents_uncertain_as_actionable(self):
        # The pipeline guardrail, carried into every surface.
        out = api.report(".", "markdown")

        self.assertIn("no evidence either way", out.lower())

    def test_the_mcp_tool_returns_a_rendered_report(self):
        self.assertIn("## Signal Map", signal_map_report(repo_root="."))
