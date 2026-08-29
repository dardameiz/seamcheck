"""MCP surface. Thin wrappers over signal_map.api - the same code the CLI runs."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from signal_map import api

mcp = FastMCP("signal-map")


@mcp.tool()
def signal_map_check(repo_root: str = ".") -> dict:
    """Scan the project and report findings new since the last snapshot."""
    return api.check(repo_root)


@mcp.tool()
def signal_map_explain(symbol_id: str, repo_root: str = ".") -> str:
    """Explain one symbol: where it is, how it was reached, and why it is classified so."""
    return api.explain(api.scan(repo_root), symbol_id)


@mcp.tool()
def signal_map_triage(symbol_id: str, status: str, repo_root: str = ".", reason: str = "") -> dict:
    """Record a human disposition (approved/confirmed/deferred) against a finding."""
    return api.triage(symbol_id, status, repo_root, reason)


if __name__ == "__main__":
    mcp.run()
