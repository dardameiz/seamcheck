from unittest import mock

from django.test import SimpleTestCase

from seamcheck.changescope import NoUpstreamError
from seamcheck.scopedserve import serve_scoped_maps


def _fake_server():
    server = mock.Mock()
    server.server_port = 12345
    # serve_forever must return promptly - it runs in a real thread in the code
    # under test, and a Mock call returns instantly, which is exactly what we want.
    server.serve_forever = mock.Mock(return_value=None)
    return server


class NothingToServeTests(SimpleTestCase):
    def test_no_pages_touched_prints_a_message_and_serves_nothing(self):
        lines = []
        with mock.patch(
            "seamcheck.api.scoped_findings",
            return_value={"scope": "commit", "changed_files": [], "pages": {}},
        ), mock.patch("seamcheck.serve.serve_addresses") as serve_addresses:
            serve_scoped_maps(".", "commit", write=lines.append)

        serve_addresses.assert_not_called()
        self.assertTrue(any("nothing" in line.lower() for line in lines))

    def test_no_upstream_prints_the_error_and_serves_nothing(self):
        lines = []
        with mock.patch(
            "seamcheck.api.scoped_findings", side_effect=NoUpstreamError("no upstream configured"),
        ), mock.patch("seamcheck.serve.serve_addresses") as serve_addresses:
            serve_scoped_maps(".", "push", write=lines.append)

        serve_addresses.assert_not_called()
        self.assertIn("no upstream configured", "\n".join(lines))


class ServingTests(SimpleTestCase):
    def _scoped(self, pages):
        return {"scope": "commit", "changed_files": ["a.js"], "pages": pages}

    def test_one_touched_page_is_served_once(self):
        lines = []
        server = _fake_server()
        with (
            mock.patch("seamcheck.api.scoped_findings",
                      return_value=self._scoped({"push-arena-main": {"findings": []}})),
            mock.patch("seamcheck.api.scoped_map_document",
                      return_value=mock.Mock(single_file=lambda: "<html></html>")) as doc,
            mock.patch("seamcheck.serve.serve_addresses",
                      return_value=(server, {"local": "http://127.0.0.1:12345/tok"})) as serve_addresses,
        ):
            # local_only: this test does not care about tunnelling, and must not depend
            # on whatever this machine's own `seamcheck config --tunnel` is set to.
            serve_scoped_maps(".", "commit", local_only=True, write=lines.append)

        doc.assert_called_once_with(".", "push-arena-main")
        serve_addresses.assert_called_once()
        self.assertTrue(any("push-arena-main" in line for line in lines))
        self.assertTrue(any("http://127.0.0.1:12345/tok" in line for line in lines))
        server.serve_forever.assert_called_once()

    def test_two_touched_pages_get_two_separate_servers(self):
        lines = []
        server_a, server_b = _fake_server(), _fake_server()
        addresses = iter([
            (server_a, {"local": "http://127.0.0.1:1/a"}),
            (server_b, {"local": "http://127.0.0.1:2/b"}),
        ])
        with (
            mock.patch("seamcheck.api.scoped_findings",
                      return_value=self._scoped({
                          "page-a": {"findings": []}, "page-b": {"findings": []},
                      })),
            mock.patch("seamcheck.api.scoped_map_document",
                      return_value=mock.Mock(single_file=lambda: "<html></html>")),
            mock.patch("seamcheck.serve.serve_addresses",
                      side_effect=lambda *a, **k: next(addresses)) as serve_addresses,
        ):
            serve_scoped_maps(".", "push", local_only=True, write=lines.append)

        self.assertEqual(serve_addresses.call_count, 2)
        self.assertTrue(any("http://127.0.0.1:1/a" in line for line in lines))
        self.assertTrue(any("http://127.0.0.1:2/b" in line for line in lines))

    def test_tunnel_requested_and_succeeding_prints_an_anywhere_line(self):
        lines = []
        server = _fake_server()
        with (
            mock.patch("seamcheck.api.scoped_findings",
                      return_value=self._scoped({"page": {"findings": []}})),
            mock.patch("seamcheck.api.scoped_map_document",
                      return_value=mock.Mock(single_file=lambda: "<html></html>")),
            mock.patch("seamcheck.serve.serve_addresses",
                      return_value=(server, {"local": "http://127.0.0.1:1/tok"})),
            mock.patch("seamcheck.serve.public_tunnel",
                      return_value=(mock.Mock(), "https://random.trycloudflare.com")),
        ):
            serve_scoped_maps(".", "commit", tunnel=True, write=lines.append)

        self.assertTrue(any("anywhere" in line and "trycloudflare" in line for line in lines))

    def test_a_failed_tunnel_does_not_stop_the_local_server_from_being_reported(self):
        lines = []
        server = _fake_server()
        with (
            mock.patch("seamcheck.api.scoped_findings",
                      return_value=self._scoped({"page": {"findings": []}})),
            mock.patch("seamcheck.api.scoped_map_document",
                      return_value=mock.Mock(single_file=lambda: "<html></html>")),
            mock.patch("seamcheck.serve.serve_addresses",
                      return_value=(server, {"local": "http://127.0.0.1:1/tok"})),
            mock.patch("seamcheck.serve.public_tunnel",
                      side_effect=RuntimeError("cloudflared is not installed")),
        ):
            serve_scoped_maps(".", "commit", tunnel=True, write=lines.append)

        self.assertTrue(any("http://127.0.0.1:1/tok" in line for line in lines))
        self.assertTrue(any("no public link" in line for line in lines))
