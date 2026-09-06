"""Five explanations were five full scans.

Every MCP tool and every CLI command ran `api.scan` from scratch, so on the reference project
looking at five symbols cost 7.5 minutes, and a mistyped id cost the same 88.5 seconds as a
real one. The graph is a pure function of the files, the tool version and the config, so it
can be remembered.
"""
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
