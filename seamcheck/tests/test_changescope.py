import pathlib
import subprocess
import tempfile

from django.test import SimpleTestCase

from seamcheck.changescope import (
    NoUpstreamError,
    changed_files,
    features_touched,
    pages_touched,
)
from seamcheck.graph import Graph, Status, Symbol


def _symbol(id_, file, sub=""):
    return Symbol(
        id=id_, kind="dom_selector", label=id_, sub=sub, file=file, line=1,
        status=Status.CONNECTED, snippet="", chain=[], note="",
    )


class _Repo:
    """A throwaway git repo, real commits and real staging - no mocking of git itself."""

    def __init__(self, root):
        self.root = pathlib.Path(root)
        self._git("init", "-q")
        self._git("config", "user.email", "t@t")
        self._git("config", "user.name", "t")

    def _git(self, *args):
        return subprocess.run(
            ["git", "-C", str(self.root), *args],
            capture_output=True, text=True, check=True,
        ).stdout.strip()

    def write(self, name, content="x"):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def stage(self, *names):
        self._git("add", *names)

    def commit(self, message="c"):
        self._git("add", "-A")
        self._git("commit", "-q", "-m", message)
        return self._git("rev-parse", "HEAD")


class ChangedFilesCommitScopeTests(SimpleTestCase):
    def test_a_staged_file_is_listed(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _Repo(tmp)
            repo.write("a.js")
            repo.commit("initial")
            repo.write("b.js")
            repo.stage("b.js")

            self.assertEqual(changed_files(tmp, "commit"), ["b.js"])

    def test_an_unstaged_change_is_not_listed(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _Repo(tmp)
            repo.write("a.js")
            repo.commit("initial")
            repo.write("a.js", "changed but not staged")

            self.assertEqual(changed_files(tmp, "commit"), [])

    def test_nothing_staged_is_an_empty_list_not_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _Repo(tmp)
            repo.write("a.js")
            repo.commit("initial")

            self.assertEqual(changed_files(tmp, "commit"), [])


class ChangedFilesPushScopeTests(SimpleTestCase):
    def _repo_with_upstream(self, tmp):
        remote_dir = pathlib.Path(tmp) / "remote.git"
        remote_dir.mkdir()
        subprocess.run(
            ["git", "init", "-q", "--bare", str(remote_dir)], check=True, capture_output=True,
        )
        work_dir = pathlib.Path(tmp) / "work"
        work_dir.mkdir()
        repo = _Repo(work_dir)
        repo.write("a.js")
        repo.commit("initial")
        repo._git("branch", "-M", "main")
        repo._git("remote", "add", "origin", str(remote_dir))
        repo._git("push", "-u", "origin", "main")
        return repo

    def test_commits_not_yet_pushed_are_listed(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._repo_with_upstream(tmp)
            repo.write("b.js")
            repo.stage("b.js")
            repo.commit("feature work")
            repo.write("c.js")
            repo.stage("c.js")
            repo.commit("more feature work")

            self.assertEqual(sorted(changed_files(str(repo.root), "push")), ["b.js", "c.js"])

    def test_everything_already_pushed_is_an_empty_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._repo_with_upstream(tmp)

            self.assertEqual(changed_files(str(repo.root), "push"), [])

    def test_no_upstream_configured_raises_rather_than_guessing_a_base_branch(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _Repo(tmp)
            repo.write("a.js")
            repo.commit("initial")

            with self.assertRaises(NoUpstreamError):
                changed_files(tmp, "push")


class UnknownScopeTests(SimpleTestCase):
    def test_an_unrecognised_scope_raises_rather_than_silently_matching_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _Repo(tmp)
            repo.write("a.js")
            repo.commit("initial")

            with self.assertRaises(ValueError):
                changed_files(tmp, "banana")


class PagesTouchedTests(SimpleTestCase):
    def test_a_page_with_a_touched_file_is_reported_with_just_that_file(self):
        pages = {
            "push-arena-main": {"push_arena/a.js", "push_arena/b.js"},
            "store-main": {"store/a.js"},
        }

        hits = pages_touched(["push_arena/a.js"], pages)

        self.assertEqual(hits, {"push-arena-main": {"push_arena/a.js"}})

    def test_a_page_with_no_touched_file_is_absent_not_present_with_an_empty_set(self):
        pages = {"store-main": {"store/a.js"}}

        self.assertEqual(pages_touched(["push_arena/a.js"], pages), {})

    def test_a_shared_file_touching_two_pages_reports_both(self):
        pages = {
            "push-arena-main": {"shared/util.js"},
            "store-main": {"shared/util.js"},
        }

        hits = pages_touched(["shared/util.js"], pages)

        self.assertEqual(set(hits), {"push-arena-main", "store-main"})


class FeaturesTouchedTests(SimpleTestCase):
    def test_a_symbol_with_a_feature_suffix_contributes_its_feature(self):
        graph = Graph(
            symbols=[_symbol("s1", "push_arena/achievements.js", sub="id [Achievements]")],
            edges=[],
        )

        self.assertEqual(features_touched(graph, {"push_arena/achievements.js"}), {"Achievements"})

    def test_a_symbol_with_no_feature_suffix_contributes_nothing(self):
        graph = Graph(
            symbols=[_symbol("s1", "push_arena/misc.js", sub="id")],
            edges=[],
        )

        self.assertEqual(features_touched(graph, {"push_arena/misc.js"}), set())

    def test_a_symbol_outside_the_changed_set_is_ignored(self):
        graph = Graph(
            symbols=[_symbol("s1", "push_arena/other.js", sub="id [Achievements]")],
            edges=[],
        )

        self.assertEqual(features_touched(graph, {"push_arena/achievements.js"}), set())

    def test_two_distinct_features_both_surface(self):
        graph = Graph(
            symbols=[
                _symbol("s1", "push_arena/achievements.js", sub="id [Achievements]"),
                _symbol("s2", "push_arena/store.js", sub="id [Store]"),
            ],
            edges=[],
        )

        changed = {"push_arena/achievements.js", "push_arena/store.js"}
        self.assertEqual(features_touched(graph, changed), {"Achievements", "Store"})
