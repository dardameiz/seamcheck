"""`seamcheck config` says which entry sources run - the design doc's "shows which sources
ran and why". Both printers: the plain CLI one and the Django management command's."""

import io
import pathlib
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

from django.core.management import call_command

from seamcheck import cli


def _repo(files: dict[str, str]) -> str:
    root = pathlib.Path(tempfile.mkdtemp())
    for name, body in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    return str(root)


NEXT_APP = {
    "next.config.js": "module.exports = {};",
    "app/page.tsx": "export default function P() { return null; }",
}


class PlainConfigTests(unittest.TestCase):
    def test_config_says_which_entry_sources_run(self):
        out = io.StringIO()
        with redirect_stdout(out):
            cli._show_config_plain(_repo(NEXT_APP))
        self.assertIn("what the map starts from:", out.getvalue())
        self.assertRegex(out.getvalue(), r"nextjs\s+0\.95\s+runs")

    def test_an_unknown_forced_source_is_said_not_raised(self):
        # `config` is where a person goes to find out what is wrong; a traceback there
        # would hide the one sentence that says it.
        declared = ({"entry_sources": ["nope"]}, {"entry_sources": "SEAMCHECK_CONFIG"})
        out = io.StringIO()
        with mock.patch("seamcheck.autoconfig.effective", return_value=declared), redirect_stdout(out):
            code = cli._show_config_plain(_repo(NEXT_APP))
        self.assertEqual(code, 0)
        self.assertIn("Unknown entry source 'nope'", out.getvalue())


class ManagementCommandConfigTests(unittest.TestCase):
    def test_the_management_command_says_which_entry_sources_run_too(self):
        out = io.StringIO()
        call_command("seamcheck", "--show-config", "--repo-root", _repo(NEXT_APP), stdout=out)
        self.assertIn("what the map starts from:", out.getvalue())
        self.assertRegex(out.getvalue(), r"nextjs\s+0\.95\s+runs")
