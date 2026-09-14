"""One served, pre-focused map per page a `--scope` run touched, all at once.

`--scope`'s JSON is the agent/CI-facing answer; this is the "quick snapshot to imagine
what's happening" the visual half was asked for. A "commit" scope is usually one page and
one link; a "push" can span several, and the owner's own request was explicit: a
SEPARATE link per page, not one map with a picker at the top - so this opens one server
per touched page rather than reusing `map --serve`'s single-document flow.

Deliberately terser than `map --serve`'s own address explanation (no per-run essay on what
"anywhere" means) - repeated once per page it would dominate the terminal on a three-page
push. The full explanation still lives on plain `seamcheck map --serve`, one call away.
"""

from __future__ import annotations

import os
import threading


def serve_scoped_maps(
    repo_root: str, mode: str, *, tunnel: bool = False, local_only: bool = False,
    open_it: bool = False, write=print,
) -> None:
    """Render and serve every page `mode` ("commit" or "push") touches, until Ctrl-C.

    `write` takes one string at a time (bare `print` for the plain CLI door,
    `self.stdout.write` for the Django one) so neither door needs its own copy of this.
    """
    from seamcheck import api
    from seamcheck.changescope import NoUpstreamError
    from seamcheck.serve import public_tunnel, serve_addresses
    from seamcheck.usersettings import wants_tunnel

    try:
        result = api.scoped_findings(repo_root, mode)
    except (NoUpstreamError, ValueError) as error:
        write(str(error))
        return
    pages = sorted(result.get("pages") or {})
    if not pages:
        changed = result.get("changed_files") or []
        write(
            "Nothing staged/unpushed to show." if not changed
            else f"{len(changed)} file(s) changed, none map to a known page."
        )
        return

    tunnel, why = wants_tunnel(flag=tunnel, local_only=local_only)
    host = "127.0.0.1" if local_only else "0.0.0.0"
    abs_root = os.path.abspath(repo_root)

    servers = []
    for page in pages:
        html = api.scoped_map_document(repo_root, page).single_file()
        # sources=set(api.LAST_MAP_FILES): scoped_map_document() just populated it for
        # THIS page's render, exactly as _map_document does for the full map - without
        # it, the served map's "view code" panel could not fetch any file's real
        # contents (the source endpoint's own allow-list defaults empty) and silently
        # fell back to a bare snippet, even though the map genuinely was being served.
        server, addresses = serve_addresses(
            html, host=host, repo_root=abs_root, sources=set(api.LAST_MAP_FILES),
        )
        servers.append(server)
        write(f"\n{page}")
        write(f"  this machine  {addresses['local']}")
        if "lan" in addresses:
            write(f"  same wifi     {addresses['lan']}")
        if tunnel:
            try:
                _proxy, public = public_tunnel(server.server_port)
            except RuntimeError as error:
                write(f"  no public link ({error})")
            else:
                path = addresses["local"][addresses["local"].index("/", 8):]
                write(f"  anywhere      {public}{path}  ({why})")
        if open_it:
            import webbrowser

            webbrowser.open(addresses["local"])

    write(f"\n{len(servers)} page(s) served. Ctrl-C to stop all of them.")
    threads = [threading.Thread(target=server.serve_forever, daemon=True) for server in servers]
    for thread in threads:
        thread.start()
    try:
        while any(thread.is_alive() for thread in threads):
            for thread in threads:
                thread.join(timeout=0.1)
    except KeyboardInterrupt:
        write("\nstopped")
    finally:
        for server in servers:
            server.shutdown()
