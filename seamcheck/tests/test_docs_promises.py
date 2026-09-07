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

    def test_no_adapter_is_never_actually_raised(self):
        # docs/commands.md and README used to list `no_adapter` among the envelope
        # error codes findings/symbols/diff return with exit 0 - it is a real, defined
        # code (envelope.ERRORS), but nothing in the package ever raises it: the
        # matching scenario ("nothing here this knows how to read") is a PRE-FLIGHT
        # check that bypasses the envelope entirely and exits 4 with a plain stderr
        # message before any of the three commands even starts. Greps every .py file
        # under seamcheck/ (tests excluded) for the string, so this fails the moment a
        # future change actually starts emitting it - at which point the doc caveat
        # below should be removed, not this test.
        import re

        # envelope.py: the code's own definition (envelope.ERRORS). exitcodes.py: the
        # exit-code MAPPING table (envelope_exit_code) - a passive lookup entry that
        # exists for completeness (every documented code must map to something), not a
        # place that ever constructs or raises one. Neither is "raising" no_adapter.
        excused = {"seamcheck/envelope.py", "seamcheck/exitcodes.py"}
        hits = []
        for path in (ROOT / "seamcheck").rglob("*.py"):
            if "tests" in path.parts:
                continue
            relative = str(path.relative_to(ROOT))
            if relative in excused:
                continue
            text = path.read_text()
            if re.search(r'["\']no_adapter["\']', text):
                hits.append(relative)

        self.assertEqual(hits, [], f"no_adapter is now actually raised from: {hits}")

    def test_commands_md_no_longer_claims_no_adapter_is_returned(self):
        text = (ROOT / "docs/commands.md").read_text()

        self.assertIn("no scenario that reaches it", text)

    def test_llms_txt_states_the_same_django_minimum_pyproject_declares(self):
        # llms.txt said "Requires Django 5+"; pyproject.toml declares django>=4.2, with
        # its own comment explaining why that floor is deliberate (a hard django>=5.0
        # could try to UPGRADE a project running Django 4.2 just because someone
        # installed a linter). Parsed from pyproject.toml rather than hand-typed here,
        # so a future bump of the real minimum cannot silently leave this stale too.
        import re

        pyproject = (ROOT / "pyproject.toml").read_text()
        match = re.search(r'django\s*=\s*\["django>=([\d.]+)"\]', pyproject)
        self.assertIsNotNone(match, "could not find pyproject.toml's django>=X.Y spec")
        minimum = match.group(1)

        llms = (ROOT / "llms.txt").read_text()
        self.assertIn(f"Django {minimum}+", llms)
