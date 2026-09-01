"""Serve one rendered report to the local network, so a phone can open it.

This is not hosting and not uploading: the bytes never leave the network you are on,
nothing is stored anywhere, and the server dies with the command. It exists because the
report is a self-contained file and the only hard part is getting that file onto a device
that cannot reach localhost.

Opt-in, because binding beyond loopback is a real choice. The URL carries a random token
so another device on the network cannot stumble into your codebase's structure.
"""

from __future__ import annotations

import json
import pathlib
import re
import secrets
import shutil
import socket
import subprocess
import time
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


# Cap on a single source file. A minified bundle or a generated migration can be
# megabytes, and nobody reads those in a viewer; the snippet still stands for them.
_MAX_SOURCE_BYTES = 2_000_000

# The inventory walks the whole tree and reads every text file in it, which on a large
# repository is several seconds and a payload of megabytes. Neither belongs in every scan
# or in every page load, so it is built on first request and kept - the answer only
# changes when files do, and a reader who opens Files twice should wait once.
_INVENTORY_CACHE: dict[str, dict] = {}


class _OneFileHandler(BaseHTTPRequestHandler):
    def __init__(self, *args, body: bytes, path: str, sources=None, repo_root: str = "",
                 **kwargs):
        self._body = body
        self._path = path
        # An ALLOWLIST of repo-relative paths, not a document root. The report already
        # names every file it found, so the set of files worth reading is known exactly -
        # and serving from a known set means no amount of `../` in a request can reach a
        # file the scan never saw. This server binds beyond loopback by default.
        self._sources = frozenset(sources or ())
        self._repo_root = repo_root
        super().__init__(*args, **kwargs)

    def _json(self, payload: str) -> None:
        encoded = payload.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Robots-Tag", "noindex, nofollow")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(encoded)

    def _serve_inventory(self) -> None:
        """Every file on disk, which is a different question from every file scanned."""
        if not self._repo_root:
            self.send_error(404)
            return
        cached = _INVENTORY_CACHE.get(self._repo_root)
        if cached is None:
            from seamcheck.inventory import build_inventory

            try:
                cached = build_inventory(self._repo_root, set(self._sources))
            except OSError as error:
                cached = {"error": str(error), "folders": [], "totals": {}, "files": 0}
            _INVENTORY_CACHE[self._repo_root] = cached
        self._json(json.dumps(cached))

    def _serve_source(self, query: str) -> None:
        from urllib.parse import parse_qs

        wanted = (parse_qs(query).get("path") or [""])[0]
        if wanted not in self._sources or not self._repo_root:
            self.send_error(404)
            return
        target = pathlib.Path(self._repo_root, wanted)
        try:
            # resolve() collapses any traversal; the containment check is belt and braces
            # over the allowlist, because a symlink inside the repo could still point out.
            resolved = target.resolve()
            if not resolved.is_relative_to(pathlib.Path(self._repo_root).resolve()):
                self.send_error(404)
                return
            if resolved.stat().st_size > _MAX_SOURCE_BYTES:
                payload = json.dumps({"path": wanted, "error": "too large to display"})
            else:
                payload = json.dumps({
                    "path": wanted,
                    "text": resolved.read_text(encoding="utf-8", errors="replace"),
                })
        except OSError:
            self.send_error(404)
            return
        self._json(payload)

    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler's required name
        route, _, query = self.path.partition("?")
        if route.rstrip("/") == f"{self._path.rstrip('/')}/source":
            self._serve_source(query)
            return
        if route.rstrip("/") == f"{self._path.rstrip('/')}/inventory":
            self._serve_inventory()
            return
        if route.rstrip("/") != self._path.rstrip("/"):
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


def serve_once(html: str, host: str = "0.0.0.0", port: int = 0, sources=None,
               repo_root: str = "") -> tuple[ThreadingHTTPServer, str]:
    """Start a server for one document. Returns the server and the URL to open."""
    server, path = _bind(html, host, port, sources=sources, repo_root=repo_root)
    shown = local_ip() if host in ("0.0.0.0", "") else host
    return server, f"http://{shown}:{server.server_port}{path}"


def _bind(html: str, host: str, port: int, sources=None,
          repo_root: str = "") -> tuple[ThreadingHTTPServer, str]:
    path = f"/{secrets.token_urlsafe(9)}"
    handler = partial(_OneFileHandler, body=html.encode("utf-8"), path=path,
                      sources=sources, repo_root=repo_root)
    return ThreadingHTTPServer((host, port), handler), path


def serve_addresses(html: str, host: str = "0.0.0.0", port: int = 0, sources=None,
                    repo_root: str = "") -> tuple[ThreadingHTTPServer, dict[str, str]]:
    """Start the server and name every address it can be reached at.

    `serve_once` answers with one URL, which was the right shape when serving was an
    opt-in for the phone. It is now what `map` does by default, and the two addresses are
    different answers to different questions: loopback is the one to click here (and the
    one a VS Code terminal hands to a real browser, unlike a file:// link, which VS Code
    opens inside itself), the LAN one is the one to type on a phone. Same port, same
    token, so they are the same document.
    """
    server, path = _bind(html, host, port, sources=sources, repo_root=repo_root)
    port_number = server.server_port
    addresses = {"local": f"http://127.0.0.1:{port_number}{path}"}
    if host not in ("127.0.0.1", "localhost"):
        addresses["lan"] = f"http://{local_ip()}:{port_number}{path}"
    return server, addresses


_TUNNEL_URL_RE = re.compile(rb"https://[a-z0-9-]+\.trycloudflare\.com")


def public_tunnel(port: int, timeout: float = 30.0) -> tuple[subprocess.Popen, str]:
    """A temporary public HTTPS address for a local port, via a locally-run cloudflared.

    For the phone that is not on this wifi. cloudflared is open source (Apache-2.0) and a
    quick tunnel needs no account: it opens an outbound connection and Cloudflare hands
    back a random hostname that lives as long as the process.

    This is the one thing in Seamcheck that leaves the machine. Anyone holding the full
    link can read the report, so the address keeps the same unguessable path the LAN
    server uses, and every other path on the tunnel answers 404.
    """
    if shutil.which("cloudflared") is None:
        raise RuntimeError(
            "cloudflared is not installed. It is the tunnel client (Apache-2.0):\n"
            "    brew install cloudflared\n"
            "    https://github.com/cloudflare/cloudflared"
        )
    process = subprocess.Popen(  # noqa: S603 - fixed argv, no shell, no user input
        ["cloudflared", "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{port}"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        line = process.stderr.readline()
        if not line:
            break
        match = _TUNNEL_URL_RE.search(line)
        if match:
            return process, match.group().decode()
    process.terminate()
    raise RuntimeError("cloudflared did not report a public address; no tunnel was opened.")
