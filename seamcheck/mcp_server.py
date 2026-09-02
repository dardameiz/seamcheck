"""MCP surface. Thin wrappers over seamcheck.api - the same code the CLI runs.

Kept deliberately thin: an agent asking `check` must get the answer the terminal would give,
because the moment the two disagree neither can be trusted. Every tool here is one call into
`api`.
"""

from __future__ import annotations

# The class was renamed between major versions of the MCP SDK - `FastMCP` in 1.x,
# `MCPServer` in 2.x - and `mcp>=1.0` in the packaging resolved to 2.x, so a fresh
# `pip install seamcheck[mcp]` produced a server that crashed on IMPORT. Nobody would see
# that until an agent tried to use it, and then the failure is a dead stdio pipe rather
# than a message. Both names are tried.
try:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as _Server
except ModuleNotFoundError:  # pragma: no cover - depends which SDK is installed
    try:  # mcp 2.x
        from mcp.server.mcpserver import MCPServer as _Server
    except ModuleNotFoundError as error:  # pragma: no cover
        raise ModuleNotFoundError(
            "seamcheck-mcp needs the MCP SDK: pip install 'seamcheck[mcp]'"
        ) from error

from seamcheck import api

mcp = _Server("seamcheck")


@mcp.tool()
def seamcheck_check(repo_root: str = ".") -> dict:
    """Scan the project and report findings new since the last snapshot."""
    return api.check(repo_root)


@mcp.tool()
def seamcheck_explain(symbol_id: str, repo_root: str = ".") -> str:
    """Explain one symbol: where it is, how it was reached, and why it is classified so."""
    return api.explain(api.scan(repo_root), symbol_id)


@mcp.tool()
def seamcheck_triage(symbol_id: str, status: str, repo_root: str = ".", reason: str = "",
                     why: str = "") -> dict:
    """Record a human disposition (approved/confirmed/deferred) against a finding.

    `reason` is prose and stays local. `why` is one of a fixed set - see
    seamcheck_why_wrong - and is the only part `seamcheck share` can pass on.
    """
    return api.triage(symbol_id, status, repo_root, reason, why)


@mcp.tool()
def seamcheck_why_wrong() -> dict:
    """The fixed reasons a finding can be wrong, for the `why` argument of triage."""
    from seamcheck.triage import WHY_HELP

    return {"reasons": WHY_HELP}


@mcp.tool()
def seamcheck_report(fmt: str = "markdown", repo_root: str = ".") -> str:
    """Render the findings digest: terminal, markdown or html."""
    return api.report(repo_root, fmt)


@mcp.tool()
def seamcheck_share(repo_root: str = ".", with_deps: bool = False) -> str:
    """Build a report about the scan that contains none of the scanned code.

    Counts and fixed words only - no paths, names, routes, snippets or repository
    identity. Returns markdown for a person to read and decide whether to send. This
    makes no network call; nothing is transmitted by generating it.
    """
    from seamcheck import share

    markdown, payload = share.report(repo_root, with_deps=with_deps)
    return markdown + "\n\nPre-filled issue link (submits nothing until pressed):\n" + share.issue_url(payload)


@mcp.tool()
def seamcheck_services(repo_root: str = ".") -> dict:
    """List the services this repository declares, and which of them are deployable.

    A monorepo is not one application. Returns each service's name, root directory,
    language, and the evidence that made it a service.
    """
    from seamcheck.services import detect_services

    return {
        "services": [
            {"name": s.name, "root": s.root, "language": s.language,
             "deployable": s.deployable, "evidence": s.evidence}
            for s in detect_services(repo_root)
        ]
    }


def _setup_django_if_present() -> None:
    """Bootstrap Django only when this actually is a Django project.

    It used to REFUSE anything else, which was the same gate the CLI had: an agent pointed
    at an Express, Supabase or Firebase repository got "no Django project here" and exit 2,
    for a scan that needs no Django at all. Six of the seven backends are read from source.
    """
    import os
    import pathlib
    import sys

    from seamcheck.cli import find_project

    found = find_project(pathlib.Path.cwd())
    settings_module = None
    if found:
        settings_module, root = found
        sys.path.insert(0, str(root))
        os.chdir(root)
        os.environ.setdefault("DJANGO_SETTINGS_MODULE", settings_module)
    settings_module = settings_module or os.environ.get("DJANGO_SETTINGS_MODULE")
    if not settings_module:
        return  # not a Django project, and that is fine

    try:
        import django
    except ModuleNotFoundError:
        # stderr, never stdout: stdout is the protocol channel and a stray line on it
        # corrupts the session rather than producing a readable error.
        print(
            "seamcheck-mcp: this looks like a Django project but Django is not installed "
            "here. Run the server from the project's own virtualenv.",
            file=sys.stderr,
        )
        raise SystemExit(2) from None
    try:
        django.setup()
    except ModuleNotFoundError as error:
        print(
            f"seamcheck-mcp: this project imports {error.name!r}, which is not installed "
            "here. Seamcheck reads a Django project by importing it, so run the server "
            "from the project's own virtualenv rather than from a global install.",
            file=sys.stderr,
        )
        raise SystemExit(2) from None


def main() -> None:
    """Entry point for the `seamcheck-mcp` command.

    An agent launches this and talks to it over stdin/stdout - there is no port and no
    daemon. The agent's working directory is the project.
    """
    _setup_django_if_present()
    mcp.run()


if __name__ == "__main__":
    main()
