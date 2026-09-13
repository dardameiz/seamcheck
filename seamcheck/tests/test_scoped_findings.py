import pathlib
import subprocess
import tempfile

from django.test import SimpleTestCase, override_settings

from seamcheck.api import scoped_findings
from seamcheck.graph import Graph, Status, Symbol


def _symbol(id_, file, status=Status.UNRESOLVED):
    return Symbol(
        id=id_, kind="dom_selector", label=id_, sub="", file=file, line=1,
        status=status, snippet="", chain=[], note="unresolved note",
    )


def _git(args, cwd):
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True)


def _init_project(root: pathlib.Path):
    """One page ('entry_a'), two files, a real git history under it."""
    (root / "entry_a.js").write_text("import './module_a.js';\n", encoding="utf-8")
    (root / "module_a.js").write_text("export function f() {}\n", encoding="utf-8")
    _git(["init", "-q"], root)
    _git(["config", "user.email", "t@t"], root)
    _git(["config", "user.name", "t"], root)
    _git(["add", "-A"], root)
    _git(["commit", "-q", "-m", "initial"], root)


class ScopedFindingsCommitScopeTests(SimpleTestCase):
    def test_a_staged_file_surfaces_its_pages_unresolved_findings(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            _init_project(root)
            (root / "module_a.js").write_text("export function f() { /* changed */ }\n")
            _git(["add", "module_a.js"], root)

            graph = Graph(
                symbols=[
                    _symbol("s1", "module_a.js", Status.UNRESOLVED),
                    _symbol("s2", "unrelated.js", Status.UNRESOLVED),
                ],
                edges=[],
            )

            with override_settings(SEAMCHECK_CONFIG={"js_entry_files": ["entry_a.js"]}):
                result = scoped_findings(str(root), "commit", graph=graph)

            self.assertEqual(result["scope"], "commit")
            self.assertEqual(result["changed_files"], ["module_a.js"])
            self.assertEqual(set(result["pages"]), {"entry_a"})
            page = result["pages"]["entry_a"]
            self.assertEqual(page["touched_files"], ["module_a.js"])
            finding_ids = {f["id"] for f in page["findings"]}
            # s1 (in the touched page) is reported; s2 (a different, untouched page) is not -
            # scoped to the NEIGHBOURHOOD, not the whole repo.
            self.assertIn("s1", finding_ids)
            self.assertNotIn("s2", finding_ids)

    def test_nothing_staged_returns_no_pages(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            _init_project(root)
            graph = Graph(symbols=[_symbol("s1", "module_a.js")], edges=[])

            with override_settings(SEAMCHECK_CONFIG={"js_entry_files": ["entry_a.js"]}):
                result = scoped_findings(str(root), "commit", graph=graph)

            self.assertEqual(result, {"scope": "commit", "changed_files": [], "pages": {}})

    def test_a_connected_symbol_in_the_touched_page_is_not_reported(self):
        # Only unresolved/unused are findings - a healthy connected symbol in the same
        # page as a genuine change is not something to flag.
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            _init_project(root)
            (root / "module_a.js").write_text("export function f() { /* changed */ }\n")
            _git(["add", "module_a.js"], root)

            graph = Graph(
                symbols=[_symbol("s1", "module_a.js", Status.CONNECTED)],
                edges=[],
            )

            with override_settings(SEAMCHECK_CONFIG={"js_entry_files": ["entry_a.js"]}):
                result = scoped_findings(str(root), "commit", graph=graph)

            self.assertEqual(result["pages"]["entry_a"]["findings"], [])
