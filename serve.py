"""Serve one rendered report to the local network, so a phone can open it.

This is not hosting and not uploading: the bytes never leave the network you are on,
nothing is stored anywhere, and the server dies with the command. It exists because the
report is a self-contained file and the only hard part is getting that file onto a device
that cannot reach localhost.

Opt-in, because binding beyond loopback is a real choice. The URL carries a random token
so another device on the network cannot stumble into your codebase's structure.
"""

from __future__ import annotations

import secrets
import socket
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def local_ip() -> str:
    """The address this machine has on the LAN, discovered without sending anything.

    Connecting a UDP socket only selects a route; no packet is transmitted.
    """
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("192.0.2.1", 1))  # TEST-NET-1, reserved and unroutable
        return probe.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        probe.close()


class _OneFileHandler(BaseHTTPRequestHandler):
    def __init__(self, *args, body: bytes, path: str, **kwargs):
        self._body = body
        self._path = path
        super().__init__(*args, **kwargs)

    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler's required name
        if self.path.rstrip("/") != self._path.rstrip("/"):
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(self._body)))
        # A report names files in a private repo; no cache, no index, no referrer.
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Robots-Tag", "noindex, nofollow")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(self._body)

    def log_message(self, *args):
        return


def serve_once(html: str, host: str = "0.0.0.0", port: int = 0) -> tuple[ThreadingHTTPServer, str]:
    """Start a server for one document. Returns the server and the URL to open."""
    path = f"/{secrets.token_urlsafe(9)}"
    handler = partial(_OneFileHandler, body=html.encode("utf-8"), path=path)
    server = ThreadingHTTPServer((host, port), handler)
    shown = local_ip() if host in ("0.0.0.0", "") else host
    return server, f"http://{shown}:{server.server_port}{path}"
