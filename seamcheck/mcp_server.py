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


if __name__ == "__main__":
    mcp.run()
