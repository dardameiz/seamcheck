import asyncio
import tempfile
from pathlib import Path

from django.test import SimpleTestCase, override_settings

from signal_map.mcp_server import signal_map_check, signal_map_explain, signal_map_triage

FIXTURES_DIR = str(Path(__file__).parent / "fixtures")
_CONFIG = {
    "urlconf_module": "signal_map.tests.fixtures.fixture_urls",
    "js_entry_files": ["fixture_entry.js"],
    "js_project_root": FIXTURES_DIR,
    "entry_point_files": [],
}
GET_THING = "view:signal_map.tests.fixtures.fixture_views.get_thing"


@override_settings(SIGNAL_MAP_CONFIG=_CONFIG)
class McpToolFunctionTests(SimpleTestCase):
    def test_check_returns_a_json_serializable_dict(self):
        result = signal_map_check(repo_root=".")

        self.assertIn("passed", result)
        self.assertIsInstance(result["new_unresolved"], list)
        self.assertIn("counts", result)

    def test_explain_returns_markdown_for_a_real_symbol(self):
        text = signal_map_explain(GET_THING, repo_root=".")

        self.assertIn("get_thing", text)

    def test_explain_is_honest_about_an_unknown_symbol(self):
        self.assertIn("No symbol", signal_map_explain("view:nope", repo_root="."))

    def test_triage_rejects_an_unknown_status(self):
        result = signal_map_triage(GET_THING, "bogus", repo_root=".")

        self.assertFalse(result["ok"])
        self.assertIn("Unknown status", result["message"])

    def test_triage_writes_an_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = signal_map_triage(GET_THING, "approved", repo_root=tmp, reason="fine")

            self.assertTrue(result["ok"], result["message"])
            self.assertTrue((Path(tmp) / "signal_map" / "triage.json").is_file())

    def test_the_four_tools_are_registered_on_the_server(self):
        from signal_map.mcp_server import mcp

        self.assertEqual(mcp.name, "signal-map")

        # mcp.list_tools() is FastMCP's public registry accessor (the same call the
        # MCP protocol's tools/list request serves); it's async because that request is.
        registered = {tool.name for tool in asyncio.run(mcp.list_tools())}
        self.assertEqual(
            registered,
            {"signal_map_check", "signal_map_explain", "signal_map_triage", "signal_map_report"},
        )
