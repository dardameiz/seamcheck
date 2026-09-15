import pathlib
import tempfile
import unittest

from seamcheck.services import detect_services


class WorkspaceGlobTests(unittest.TestCase):
    def test_a_workspace_that_lists_the_project_root_itself_is_one_service(self):
        # saleor-dashboard's pnpm-workspace.yaml is `packages: ["."]`. Path.glob(".") raises
        # IndexError on Python 3.12, so `seamcheck share` and the seamcheck_services MCP
        # tool crashed on it, and the map lost its service labels.
        root = pathlib.Path(tempfile.mkdtemp())
        (root / "pnpm-workspace.yaml").write_text('packages:\n  - "."\n', encoding="utf-8")
        (root / "package.json").write_text('{"name": "dashboard"}', encoding="utf-8")
        self.assertEqual(len(detect_services(str(root))), 1)
