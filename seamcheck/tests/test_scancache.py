"""Five explanations were five full scans.

Every MCP tool and every CLI command ran `api.scan` from scratch, so on the reference project
looking at five symbols cost 7.5 minutes, and a mistyped id cost the same 88.5 seconds as a
real one. The graph is a pure function of the files, the tool version and the config, so it
can be remembered.
"""
import os
import pathlib
import tempfile
from unittest import mock

from django.test import SimpleTestCase

from seamcheck import scancache
from seamcheck.graph import Graph, Status, Symbol


def _graph():
    return Graph(symbols=[Symbol(id="url:x", kind="url", label="/x/", sub="", file="urls.py",
                                 line=1, status=Status.CONNECTED, snippet="", chain=[],
                                 note="")],
                 edges=[])


class ScanCacheTests(SimpleTestCase):
    def setUp(self):
        # Isolate every test's disk cache under its own temp directory, so a test run never
        # reads or writes this machine's real ~/.cache/seamcheck - the same reason the cache
        # was moved out of the SCANNED repository in the first place.
        self._cache_home = tempfile.TemporaryDirectory()
        self.addCleanup(self._cache_home.cleanup)
        env_patch = mock.patch.dict(os.environ, {"XDG_CACHE_HOME": self._cache_home.name})
        env_patch.start()
        self.addCleanup(env_patch.stop)
        scancache.clear()  # no cross-test bleed through the in-process memo either

    def test_the_second_call_does_not_scan_again(self):
        with tempfile.TemporaryDirectory() as root:
            (pathlib.Path(root) / "urls.py").write_text("x = 1")
            with mock.patch("seamcheck.api.scan", return_value=_graph()) as scan:
                first, how_first = scancache.cached_scan(root)
                second, how_second = scancache.cached_scan(root)

            self.assertEqual(scan.call_count, 1, "the second call must not rescan")
            self.assertFalse(how_first["cached"])
            self.assertTrue(how_second["cached"])
            self.assertEqual([s.id for s in second.symbols], [s.id for s in first.symbols])

    def test_a_changed_file_invalidates_it(self):
        with tempfile.TemporaryDirectory() as root:
            source = pathlib.Path(root) / "urls.py"
            source.write_text("x = 1")
            with mock.patch("seamcheck.api.scan", return_value=_graph()) as scan:
                scancache.cached_scan(root)
                source.write_text("x = 2")
                scancache.cached_scan(root)

            self.assertEqual(scan.call_count, 2, "a changed file must be rescanned")

    def test_refresh_forces_a_scan(self):
        with tempfile.TemporaryDirectory() as root:
            (pathlib.Path(root) / "urls.py").write_text("x = 1")
            with mock.patch("seamcheck.api.scan", return_value=_graph()) as scan:
                scancache.cached_scan(root)
                scancache.cached_scan(root, refresh=True)

            self.assertEqual(scan.call_count, 2)

    def test_the_version_is_part_of_the_key(self):
        # A cache entry that survives an upgrade is a lie about what this tool would say.
        with tempfile.TemporaryDirectory() as root:
            (pathlib.Path(root) / "urls.py").write_text("x = 1")
            first = scancache._stamp(root)
            with mock.patch("seamcheck.scancache._version", return_value="99.0.0"):
                second = scancache._stamp(root)

            self.assertNotEqual(first, second)

    def test_an_input_exactly_as_new_as_the_entry_forces_a_rescan(self):
        # The freshness check is STRICT (`<`, not `<=`): an input whose mtime reads
        # IDENTICAL to the entry's own stamp is judged not-older, so it must miss. A coarse
        # filesystem clock (a Docker bind mount, overlayfs, NFS, most CI runners) can
        # genuinely produce this - a file written in the same tick the cache was stamped in
        # - and confusing it with `<=` would serve that file's PREVIOUS version back as
        # current.
        #
        # This used to be driven through the real filesystem: forcing the source file's
        # mtime to collide with the cache file's. But `_scan_tree`'s KEY is also a hash of
        # every input's mtime, so that collision changed the key too - the second call
        # missed because it looked for a cache file that had never existed, never because
        # the freshness comparison was exercised at all. `_scan_tree` is mocked here to hold
        # the key FIXED and report only the freshness mtime, so a rescan can only mean the
        # `<` comparison actually ran and actually rejected the tie.
        with tempfile.TemporaryDirectory() as root:
            tree_calls = iter([("same-key", 1_000), ("same-key", 1_000)])
            with mock.patch("seamcheck.scancache._scan_tree",
                            side_effect=lambda _root: next(tree_calls)), \
                 mock.patch("seamcheck.scancache.time.time_ns", return_value=1_000), \
                 mock.patch("seamcheck.api.scan", return_value=_graph()) as scan:
                scancache.cached_scan(root)
                scancache.cached_scan(root)

            self.assertEqual(scan.call_count, 2,
                              "an input as new as the cache entry itself must force a rescan")

    def test_clear_forgets_the_disk_entry_too(self):
        with tempfile.TemporaryDirectory() as root:
            (pathlib.Path(root) / "urls.py").write_text("x = 1")
            with mock.patch("seamcheck.api.scan", return_value=_graph()) as scan:
                scancache.cached_scan(root)
                scancache.clear(root)
                scancache.cached_scan(root)

            self.assertEqual(scan.call_count, 2,
                              "a cleared repository must rescan, even with the tree unchanged")

    def test_old_entries_are_evicted_after_three(self):
        # Nothing evicted anything before this - a long-lived repo's cache directory only
        # ever grew.
        with tempfile.TemporaryDirectory() as root:
            source = pathlib.Path(root) / "urls.py"
            cache_dir = scancache._repo_cache_dir(root)
            with mock.patch("seamcheck.api.scan", return_value=_graph()):
                for content in ("a", "ab", "abc", "abcd"):
                    source.write_text(content)
                    scancache.cached_scan(root)

            self.assertEqual(len(list(cache_dir.glob("*.json"))), 3,
                              "a repository's cache directory must not grow without bound")

    def test_the_cache_never_writes_inside_the_scanned_repository(self):
        # This tool exists to scan other people's repositories - not to leave files in them.
        with tempfile.TemporaryDirectory() as root:
            (pathlib.Path(root) / "urls.py").write_text("x = 1")
            with mock.patch("seamcheck.api.scan", return_value=_graph()):
                scancache.cached_scan(root)

            self.assertFalse((pathlib.Path(root) / ".seamcheck").exists())

    def test_writing_a_snapshot_between_two_calls_does_not_bust_the_cache(self):
        # `seamcheck scan` writes exactly this file as its own side effect - the documented
        # "scan, then ask a question" sequence never hit the cache before this, because the
        # scan's own snapshot write changed the tree's shape out from under the very key
        # that was supposed to describe it.
        from seamcheck.snapshot import save_snapshot

        with tempfile.TemporaryDirectory() as root:
            (pathlib.Path(root) / "urls.py").write_text("x = 1")
            with mock.patch("seamcheck.api.scan", return_value=_graph()) as scan:
                scancache.cached_scan(root)
                save_snapshot(_graph(), "d" * 40, root)
                _, how_second = scancache.cached_scan(root)

            self.assertEqual(scan.call_count, 1,
                              "a snapshot written between calls must not force a rescan")
            self.assertTrue(how_second["cached"])

    def test_writing_the_triage_file_between_two_calls_does_not_bust_the_cache(self):
        # `api._marks` rewrites this file the moment a scan finds an expired mark - a READ
        # that must never look, to the cache, like an edit to the project it just read.
        from seamcheck.triage import TriageEntry, TriageStatus, save_triage

        with tempfile.TemporaryDirectory() as root:
            (pathlib.Path(root) / "urls.py").write_text("x = 1")
            with mock.patch("seamcheck.api.scan", return_value=_graph()) as scan:
                scancache.cached_scan(root)
                save_triage(
                    [TriageEntry(symbol_id="url:x", fingerprint="f", status=TriageStatus.APPROVED,
                                 who="t", when="2026-01-01", reason="")],
                    root,
                )
                _, how_second = scancache.cached_scan(root)

            self.assertEqual(scan.call_count, 1,
                              "writing the triage file between calls must not force a rescan")
            self.assertTrue(how_second["cached"])

    def test_writing_the_connectivity_map_between_two_calls_does_not_bust_the_cache(self):
        # api.write_map() writes docs/maps/connectivity-map.json on every `seamcheck scan`
        # and every seamcheck_snapshot MCP call, right next to the snapshot write the test
        # above already covers - a third tool-state write path the original fix (that one
        # enumerated `_SCANS_DIR` and `_TRIAGE_FILE`) did not enumerate, and the MCP
        # snapshot tool is what made it reachable from an agent loop.
        import subprocess

        from seamcheck import api

        with tempfile.TemporaryDirectory() as root:
            (pathlib.Path(root) / "urls.py").write_text("x = 1")
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.email", "a@example.com"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.name", "a"], cwd=root, check=True)
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            subprocess.run(["git", "commit", "-q", "-m", "x"], cwd=root, check=True)

            with mock.patch("seamcheck.api.scan", return_value=_graph()) as scan:
                scancache.cached_scan(root)
                api.write_map(_graph(), root)
                _, how_second = scancache.cached_scan(root)

            self.assertEqual(
                scan.call_count, 1,
                "writing the connectivity map between calls must not force a rescan")
            self.assertTrue(how_second["cached"])

    def test_a_dot_directory_is_not_blanket_skipped(self):
        # The walk used to skip EVERY dot-directory by convention. A project's own
        # `js_entry_files` or `templates_root` can point INTO one on purpose (`.storybook/`,
        # `.output/`) - and when it does, both the key and the freshness check went blind to
        # edits there, so a stale graph looked fresh indefinitely.
        with tempfile.TemporaryDirectory() as root:
            hidden = pathlib.Path(root) / ".storybook" / "preview.js"
            hidden.parent.mkdir(parents=True)
            hidden.write_text("x = 1")
            with mock.patch("seamcheck.api.scan", return_value=_graph()) as scan:
                scancache.cached_scan(root)
                hidden.write_text("x = 2")
                scancache.cached_scan(root)

            self.assertEqual(scan.call_count, 2,
                              "an edit inside a dot-directory the scanner can be configured "
                              "to read must still bust the cache")

    def test_a_directory_the_scanner_never_reads_is_still_skipped(self):
        # dist/build/node_modules etc. stay excluded - not by a blanket rule, but because
        # EXCLUDED_DIRS and SKIP_DIRS (the sets the scan itself honours) both name them.
        with tempfile.TemporaryDirectory() as root:
            vendored = pathlib.Path(root) / "node_modules" / "pkg.js"
            vendored.parent.mkdir(parents=True)
            vendored.write_text("x = 1")
            with mock.patch("seamcheck.api.scan", return_value=_graph()) as scan:
                scancache.cached_scan(root)
                vendored.write_text("x = 2")
                scancache.cached_scan(root)

            self.assertEqual(scan.call_count, 1,
                              "an edit inside a vendored directory the scanner never reads "
                              "must not force a rescan")

    def test_the_entry_is_stamped_from_a_clock_read_before_the_scan_runs(self):
        # A real scan takes on the order of a minute. If the entry's timestamp were read
        # AFTER api.scan() returns rather than before it starts, a file saved anywhere in
        # that window would be baked into the graph and then judged OLDER than the (too-late)
        # entry that already baked it in - served as fresh forever, until something else
        # changed.
        #
        # This used to be driven through the real filesystem: editing a file mid-scan and
        # counting rescans. But `_scan_tree`'s KEY is also a hash of every input's mtime, so
        # editing the file changed the key too - the second call missed because it looked
        # for a cache file that had never existed under that (new) key, which happens
        # whether the entry is stamped before or after the scan. It proved nothing about the
        # stamp order it was named for.
        #
        # Pinning the ORDER directly is the honest way: `_scan_tree` is mocked to hold the
        # key fixed and report a freshness mtime that lands strictly BETWEEN the "before"
        # and "after" clock readings a real mid-scan edit would produce, and the wall clock
        # is mocked to answer differently depending on whether `api.scan` has run yet. Only
        # a pre-scan stamp reads the smaller ("before") value and correctly misses; a
        # post-scan stamp would read the larger ("after") value and wrongly hit.
        with tempfile.TemporaryDirectory() as root:
            scanned = {"already": False}

            def fake_time_ns():
                return 2_000 if scanned["already"] else 1_000

            def fake_scan(_repo_root):
                scanned["already"] = True
                return _graph()

            # Second call's freshness mtime (1_500) sits strictly between the "before" (1_000)
            # and "after" (2_000) clock readings: fresh only against a stamp taken after.
            tree_calls = iter([("same-key", 500), ("same-key", 1_500)])

            with mock.patch("seamcheck.scancache._scan_tree",
                            side_effect=lambda _root: next(tree_calls)), \
                 mock.patch("seamcheck.scancache.time.time_ns", side_effect=fake_time_ns), \
                 mock.patch("seamcheck.api.scan", side_effect=fake_scan) as scan:
                scancache.cached_scan(root)
                scancache.cached_scan(root)

            self.assertEqual(scan.call_count, 2,
                              "an entry stamped from a clock read BEFORE the scan runs must "
                              "miss on a file that was touched at or after that reading - "
                              "stamped from a clock read after the scan instead, the second "
                              "call's freshness mtime (1_500) would look older than the "
                              "entry's own (2_000) and wrongly serve the stale graph as fresh")

    def test_clear_works_through_a_different_spelling_of_the_same_path(self):
        # The disk directory was always keyed by the RESOLVED path (_repo_cache_dir), but
        # the memo used to be keyed by whatever string the caller passed in - so clearing
        # via "." left the memo entry alive under its own, differently spelled key, and the
        # next call, even for the very same repository, served it right back.
        with tempfile.TemporaryDirectory() as root:
            (pathlib.Path(root) / "urls.py").write_text("x = 1")
            with mock.patch("seamcheck.api.scan", return_value=_graph()) as scan:
                scancache.cached_scan(root)
                cwd = os.getcwd()
                os.chdir(root)
                try:
                    scancache.clear(".")
                finally:
                    os.chdir(cwd)
                scancache.cached_scan(root)

            self.assertEqual(scan.call_count, 2,
                              "clearing via a different spelling of the same path must still "
                              "invalidate the memo, not just the disk directory")

    def test_the_memo_keeps_only_the_two_most_recently_used_graphs(self):
        # Each entry holds a whole graph - tens of thousands of symbols on a real project -
        # for the life of the process, and the intended consumer is a long-lived MCP server
        # answering questions about many repositories in one session. Nothing bounded this
        # before, so a long session's memo only ever grew.
        with mock.patch("seamcheck.api.scan", return_value=_graph()):
            roots = []
            for _ in range(3):
                tmp = tempfile.TemporaryDirectory()
                self.addCleanup(tmp.cleanup)
                (pathlib.Path(tmp.name) / "urls.py").write_text("x = 1")
                roots.append(str(pathlib.Path(tmp.name).resolve()))
                scancache.cached_scan(tmp.name)

            memo_repo_roots = {key.split("\0", 1)[0] for key in scancache._MEMO}

            self.assertEqual(len(scancache._MEMO), 2,
                              "the memo must never hold more than 2 graphs at once")
            self.assertEqual(memo_repo_roots, set(roots[1:]),
                              "the least recently used repository must be the one evicted")
