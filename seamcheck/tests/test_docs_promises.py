"""The documents promise things the code has to keep.

`docs/agents.md` listed seven tools and omitted seamcheck_unverified - the one designed to
be an agent's first call - and three files promised an exit code the code did not produce.
"""
import asyncio
import pathlib

from django.test import SimpleTestCase

from seamcheck import cli, mcp_server

ROOT = pathlib.Path(__file__).resolve().parents[2]


class DocsTests(SimpleTestCase):
    def test_every_mcp_tool_is_documented(self):
        names = {tool.name for tool in asyncio.run(mcp_server.mcp.list_tools())}
        text = (ROOT / "docs/agents.md").read_text()

        missing = sorted(name for name in names if name not in text)
        self.assertEqual(missing, [], f"undocumented tools: {missing}")

    def test_every_command_is_documented(self):
        text = (ROOT / "docs/commands.md").read_text()

        missing = sorted(name for name in cli.COMMANDS if name not in text)
        self.assertEqual(missing, [], f"undocumented commands: {missing}")

    def test_the_readme_puts_agents_before_the_footer(self):
        text = (ROOT / "README.md").read_text()

        self.assertIn("## For agents", text)
        self.assertLess(text.index("## For agents"), len(text) // 2,
                        "an agent reading the top of the README must find this")
