import json
import os
import pathlib
import subprocess
import tempfile

from django.test import SimpleTestCase

from signal_map.graph import Graph, Status, Symbol, graph_to_dict
from signal_map.history import commit_series, snapshot_shas
from signal_map.snapshot import _SCANS_DIR


def _symbol(id_, status=Status.CONNECTED):
    return Symbol(
        id=id_, kind="url", label=id_, sub="", file="v.py", line=1,
        status=status, snippet="", chain=[id_], note="",
    )


class _Repo:
    """A throwaway git repo with snapshots written for real commits."""

    def __init__(self, tmp):
        self.root = pathlib.Path(tmp)
        self._git("init", "-q")
        self._git("config", "user.email", "t@t")
        self._git("config", "user.name", "t")

    def _git(self, *args):
        return subprocess.run(
            ["git", "-C", str(self.root), *args], capture_output=True, text=True, check=True
        ).stdout.strip()

    def commit(self, subject, graph, when=None):
        (self.root / "f.txt").write_text(subject, encoding="utf-8")
        self._git("add", "-A")
        env = dict(os.environ)
        if when:
            env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = when
        subprocess.run(
            ["git", "-C", str(self.root), "commit", "-q", "-m", subject],
            check=True, capture_output=True, text=True, env=env,
        )
        sha = self._git("rev-parse", "HEAD")
        path = self.root / _SCANS_DIR / f"{sha}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(graph_to_dict(graph)), encoding="utf-8")
        return sha


class CommitSeriesTests(SimpleTestCase):
    def test_each_commit_carries_what_it_changed_against_the_one_scanned_before_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _Repo(tmp)
            first = repo.commit("one", Graph([_symbol("url:a")], []))
            second = repo.commit(
                "two", Graph([_symbol("url:a"), _symbol("url:b")], [])
            )

            series = commit_series(tmp)

            self.assertEqual([entry.sha for entry in series], [second, first])
            self.assertEqual(series[0].changed, {"url:b": "added"})
            self.assertEqual(series[0].baseline_sha, first)

    def test_the_earliest_scanned_commit_claims_no_changes(self):
        # Diffing it against nothing would present the entire graph as that commit's work.
        with tempfile.TemporaryDirectory() as tmp:
            repo = _Repo(tmp)
            repo.commit("one", Graph([_symbol("url:a")], []))

            self.assertEqual(commit_series(tmp)[0].changed, {})
            self.assertIsNone(commit_series(tmp)[0].baseline_sha)

    def test_a_symbol_that_only_changed_status_is_reported_as_such(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _Repo(tmp)
            repo.commit("one", Graph([_symbol("url:a", Status.CONNECTED)], []))
            repo.commit("two", Graph([_symbol("url:a", Status.UNRESOLVED)], []))

            self.assertEqual(commit_series(tmp)[0].changed, {"url:a": "status"})

    def test_a_commit_git_no_longer_knows_is_left_out_not_guessed_at(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _Repo(tmp)
            real = repo.commit("one", Graph([_symbol("url:a")], []))
            orphan = repo.root / _SCANS_DIR / f"{'0' * 40}.json"
            orphan.write_text(json.dumps(graph_to_dict(Graph([], []))), encoding="utf-8")

            self.assertEqual(len(snapshot_shas(tmp)), 2)
            self.assertEqual([entry.sha for entry in commit_series(tmp)], [real])

    def test_a_repository_with_no_snapshots_yields_an_empty_series(self):
        with tempfile.TemporaryDirectory() as tmp:
            _Repo(tmp)

            self.assertEqual(commit_series(tmp), [])


class BackfillDriverTests(SimpleTestCase):
    def test_the_driver_re_imports_the_tool_after_django_has_loaded_the_checkouts(self):
        from signal_map.history import _DRIVER

        driver = _DRIVER.format(tool="/tool", sha="abc", out="/out")
        setup = driver.index("django.setup()")

        # django.setup() imports the checkout's own signal_map. Everything that makes the
        # scan use THIS one has to happen after that line, or it is a no-op that still
        # produces a clean-looking scan of the wrong thing.
        self.assertGreater(driver.index("del sys.modules"), setup)
        self.assertGreater(driver.rindex("sys.path.insert(0, TOOL)"), setup)
        self.assertGreater(driver.index("from signal_map import api"), setup)
        self.assertIn("assert api.__file__.startswith(TOOL)", driver)


class OrderingTests(SimpleTestCase):
    def test_commits_made_in_the_same_second_still_order_parent_before_child(self):
        # Routine during a rebase, a script or CI. Sorting by timestamp falls back to
        # comparing the sha string, which puts a child before its parent and inverts
        # every diff in the series.
        with tempfile.TemporaryDirectory() as tmp:
            repo = _Repo(tmp)
            stamp = "2026-01-01T00:00:00"
            first = repo.commit("one", Graph([_symbol("url:a")], []), when=stamp)
            second = repo.commit("two", Graph([_symbol("url:a"), _symbol("url:b")], []), when=stamp)

            series = commit_series(tmp)

            self.assertEqual([entry.sha for entry in series], [second, first])
            self.assertEqual(series[0].changed, {"url:b": "added"})
