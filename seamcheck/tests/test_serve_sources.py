"""Serving source to the code viewer, which means serving files from a private repo.

The map binds beyond loopback by default so a phone on the same wifi can open it. Adding
an endpoint that returns file contents to that server is the kind of change that turns a
report into a file server, so the endpoint is an ALLOWLIST of the paths the scan actually
named - not a document root - and the containment check is belt and braces over it.
"""

from __future__ import annotations

import json
import pathlib
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

from seamcheck.serve import serve_addresses


class SourceEndpoint(unittest.TestCase):
    def setUp(self):
        self.root = pathlib.Path(tempfile.mkdtemp())
        (self.root / "app").mkdir()
        (self.root / "app" / "views.py").write_text("def index():\n    return 1\n", encoding="utf-8")
        (self.root / "secret.env").write_text("TOKEN=hunter2\n", encoding="utf-8")
        outside = self.root.parent / "outside.txt"
        outside.write_text("not yours\n", encoding="utf-8")
        self.outside = outside

        self.server, self.addresses = serve_addresses(
            "<html>map</html>", host="127.0.0.1",
            sources={"app/views.py"}, repo_root=str(self.root),
        )
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = self.addresses["local"]

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def _get(self, path: str):
        return urllib.request.urlopen(f"{self.base}/source?path={path}", timeout=5)

    def test_an_allowlisted_file_comes_back_whole(self):
        payload = json.loads(self._get("app/views.py").read())
        self.assertEqual(payload["path"], "app/views.py")
        self.assertIn("def index()", payload["text"])

    def test_a_file_in_the_repo_that_is_not_allowlisted_is_404(self):
        """The scan never named it, so the viewer has no business asking for it."""
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self._get("secret.env")
        self.assertEqual(caught.exception.code, 404)

    def test_traversal_out_of_the_repo_is_404(self):
        for attempt in ("../outside.txt", "..%2Foutside.txt", "app/../../outside.txt",
                        "/etc/passwd", "%2Fetc%2Fpasswd"):
            with self.subTest(attempt=attempt), self.assertRaises(urllib.error.HTTPError) as caught:
                self._get(attempt)
            self.assertEqual(caught.exception.code, 404)

    def test_the_map_itself_is_still_served(self):
        self.assertIn(b"map", urllib.request.urlopen(self.base, timeout=5).read())

    def test_an_unknown_path_on_the_server_is_404(self):
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(f"{self.base}/../etc/passwd", timeout=5)
        self.assertEqual(caught.exception.code, 404)

    def test_source_responses_are_not_cached_or_indexed(self):
        headers = self._get("app/views.py").headers
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertIn("noindex", headers["X-Robots-Tag"])


class WithoutSources(unittest.TestCase):
    """A server started without an allowlist serves no source at all."""

    def test_the_endpoint_is_404_when_no_sources_were_given(self):
        root = pathlib.Path(tempfile.mkdtemp())
        (root / "a.py").write_text("x = 1\n", encoding="utf-8")
        server, addresses = serve_addresses("<html>map</html>", host="127.0.0.1")
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(f"{addresses['local']}/source?path=a.py", timeout=5)
            self.assertEqual(caught.exception.code, 404)
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
