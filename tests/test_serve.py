import urllib.request

from django.test import SimpleTestCase

from signal_map.serve import local_ip, serve_once


class ServeOnceTests(SimpleTestCase):
    def _serve(self, html="<!doctype html><p>hi</p>"):
        server, url = serve_once(html, host="127.0.0.1")
        import threading

        threading.Thread(target=server.handle_request, daemon=True).start()
        return server, url

    def test_the_document_is_served_at_its_token_url(self):
        server, url = self._serve()
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                body = response.read().decode()
                self.assertIn("hi", body)
        finally:
            server.server_close()

    def test_the_url_carries_a_random_token(self):
        _, first = serve_once("x", host="127.0.0.1")
        _, second = serve_once("x", host="127.0.0.1")

        # Another device on the network must not be able to guess the path.
        self.assertNotEqual(first.rsplit("/", 1)[-1], second.rsplit("/", 1)[-1])
        self.assertGreaterEqual(len(first.rsplit("/", 1)[-1]), 8)

    def test_a_wrong_path_is_not_served(self):
        server, url = self._serve()
        try:
            wrong = url.rsplit("/", 1)[0] + "/nope"
            with self.assertRaises(urllib.error.HTTPError) as raised:
                urllib.request.urlopen(wrong, timeout=5)
            self.assertEqual(raised.exception.code, 404)
        finally:
            server.server_close()

    def test_the_response_forbids_caching_and_indexing(self):
        # The report names files in a private repository.
        server, url = self._serve()
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                self.assertEqual(response.headers["Cache-Control"], "no-store")
                self.assertIn("noindex", response.headers["X-Robots-Tag"])
        finally:
            server.server_close()

    def test_local_ip_returns_an_address_without_sending_anything(self):
        address = local_ip()

        self.assertRegex(address, r"^\d+\.\d+\.\d+\.\d+$")
