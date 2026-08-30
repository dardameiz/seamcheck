"""MCP surface. Thin wrappers over seamcheck.api - the same code the CLI runs."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from seamcheck import api

mcp = FastMCP("seamcheck")


@mcp.tool()
def seamcheck_check(repo_root: str = ".") -> dict:
    """Scan the project and report findings new since the last snapshot."""
    return api.check(repo_root)


@mcp.tool()
def seamcheck_explain(symbol_id: str, repo_root: str = ".") -> str:
    """Explain one symbol: where it is, how it was reached, and why it is classified so."""
    return api.explain(api.scan(repo_root), symbol_id)


@mcp.tool()
def seamcheck_triage(symbol_id: str, status: str, repo_root: str = ".", reason: str = "") -> dict:
    """Record a human disposition (approved/confirmed/deferred) against a finding."""
    return api.triage(symbol_id, status, repo_root, reason)


@mcp.tool()
def seamcheck_report(fmt: str = "markdown", repo_root: str = ".") -> str:
    """Render the findings digest: terminal, markdown or html."""
    return api.report(repo_root, fmt)


def main() -> None:
    """Entry point for the `seamcheck-mcp` command.

    An agent launches this and talks to it over stdin/stdout - there is no port and no
    daemon. Django has to be set up first, because every tool below reads a real project
    through its URLconf and settings, and the agent's working directory is the project.
    """
    import os
    import pathlib
    import sys

    import django

    from seamcheck.cli import find_project

    found = find_project(pathlib.Path.cwd())
    if found:
        settings_module, root = found
        sys.path.insert(0, str(root))
        os.chdir(root)
        os.environ.setdefault("DJANGO_SETTINGS_MODULE", settings_module)
    elif not os.environ.get("DJANGO_SETTINGS_MODULE"):
        # stderr, never stdout: stdout is the protocol channel and a stray line on it
        # corrupts the session rather than producing a readable error.
        print(
            "seamcheck-mcp: no Django project here. Set the MCP server's working "
            "directory to a project root, or set DJANGO_SETTINGS_MODULE.",
            file=sys.stderr,
        )
        raise SystemExit(2)
    django.setup()
    mcp.run()


if __name__ == "__main__":
    main()
