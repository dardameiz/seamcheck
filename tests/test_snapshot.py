import tempfile

from django.test import SimpleTestCase

from signal_map.graph import Graph, Status, Symbol
from signal_map.snapshot import current_git_sha, load_snapshot, save_snapshot


def _graph():
    symbol = Symbol(
        id="a", kind="view", label="a", sub="", file="f.py", line=1,
        status=Status.CONNECTED, snippet="def a(): ...", chain=["a"], note="",
    )
    return Graph(symbols=[symbol], edges=[])


class SnapshotTests(SimpleTestCase):
    def test_save_then_load_round_trips(self):
        with tempfile.TemporaryDirectory() as tmp:
            save_snapshot(_graph(), "abc123", tmp)

            loaded = load_snapshot("abc123", tmp)

            self.assertIsNotNone(loaded)
            self.assertEqual(loaded.symbols[0].id, "a")
            self.assertEqual(loaded.symbols[0].status, Status.CONNECTED)

    def test_loading_an_unknown_sha_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(load_snapshot("nope", tmp))

    def test_snapshot_is_written_under_the_gitignored_working_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = save_snapshot(_graph(), "abc123", tmp)

            self.assertIn("OTHER/signal-map/scans", path)

    def test_current_git_sha_reads_the_real_repo(self):
        sha = current_git_sha(".")

        self.assertEqual(len(sha), 40)
