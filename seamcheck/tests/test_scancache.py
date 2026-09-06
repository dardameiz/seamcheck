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
            first = scancache.stamp(root)
            with mock.patch("seamcheck.scancache._version", return_value="99.0.0"):
                second = scancache.stamp(root)

            self.assertNotEqual(first, second)

    def test_a_same_tick_collision_forces_a_rescan(self):
        # A key built only from (path, size, mtime) would trust this: same path, same size,
        # and - forced here rather than hoped for, since APFS's nanosecond clock makes a real
        # same-tick collision unreproducible - the same mtime as the cache entry itself. A
        # coarse filesystem (a Docker bind mount, overlayfs, NFS, most CI runners) produces
        # exactly this by accident. The freshness guard has to catch it even though identity
        # says "unchanged".
        with tempfile.TemporaryDirectory() as root:
            source = pathlib.Path(root) / "urls.py"
            source.write_text("x = 1")
            with mock.patch("seamcheck.api.scan", return_value=_graph()) as scan:
                scancache.cached_scan(root)
                cache_dir = scancache._repo_cache_dir(root)
                cache_file = next(cache_dir.glob("*.json"))
                collide_ns = cache_file.stat().st_mtime_ns
                os.utime(source, ns=(collide_ns, collide_ns))

                scancache.cached_scan(root)

            self.assertEqual(scan.call_count, 2,
                              "an input as new as the cache entry must force a rescan")

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
