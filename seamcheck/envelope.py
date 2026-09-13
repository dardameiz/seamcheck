"""The one shape every machine-readable answer takes.

Before this, each command answered in its own dialect: `check` printed a Python dict repr
(single quotes, not JSON), `explain` printed prose, `triage` printed one English sentence,
and `json` printed the entire graph - 72 MB and about 18 million tokens on the reference
project. An agent had to write a parser per command, and for two of them there was nothing
to parse.

The envelope is deliberately boring: a version, whether it worked, the answer, what was left
out, and what it cost. `error` is a CODE first and prose second, because a program branches
on the code and shows the prose to a person.
"""
from __future__ import annotations

SCHEMA = 1

# Every code a caller can branch on. A code that is not in here must not be emitted: an
# undocumented code is a string, not an interface.
ERRORS = {
    "unknown_symbol": "No symbol with that id in the current scan.",
    "no_baseline": "No snapshot to compare against yet.",
    "no_adapter": "Nothing here this knows how to read.",
    "bad_argument": "An argument was outside its allowed set.",
    "missing_dependency": "The project imports something that is not installed here.",
    "no_git": "This is not a git repository, or the ref could not be resolved.",
    "too_large": "The answer is bigger than the limit; ask for less or pass --full --yes.",
    "stale_snapshot": "The stored snapshot could not be read by this version of seamcheck.",
    "no_upstream": "The current branch has no upstream branch configured to compare against.",
}


class TooLarge(Exception):
    """Raised by a library call (`api.report`) instead of printing a refusal and exiting.

    `api.report` is called by the MCP server as well as both CLI front doors, and a
    server has no process to exit - raising here and letting each CALLER translate it into
    its own idiom (a management command prints to stderr and exits `EXIT_USAGE`; the plain
    CLI path does the same without Django; an MCP tool would map it to the `too_large` code
    above) is what "guard it once" means. Never raise `SystemExit` from inside `api.py`.
    """

    def __init__(self, size_bytes: int, tokens: int):
        super().__init__(f"{size_bytes:,} bytes (~{tokens:,} tokens), over the size gate.")
        self.size_bytes = size_bytes
        self.tokens = tokens


def answer(command: str, data, *, repo: str = "", sha: str = "",
           warnings: list[str] | None = None, truncated: dict | None = None,
           cost: dict | None = None) -> dict:
    """A successful answer, ready for json.dumps."""
    return {
        "schema": SCHEMA,
        "ok": True,
        "command": command,
        "repo": repo,
        "sha": sha,
        "data": data,
        "truncated": truncated,
        "warnings": warnings or [],
        "cost": cost or {},
        "error": None,
    }


def failure(command: str, code: str, message: str, *, hint: str = "", repo: str = "",
            sha: str = "", cost: dict | None = None) -> dict:
    """A failure. `code` must be a key of ERRORS.

    Carries the same `repo`, `sha` and `cost` `answer()` does: the case that motivated this
    envelope was a mistyped symbol id that cost 88.5 seconds, and a failure that could not
    report what it cost could not say that either.
    """
    if code not in ERRORS:
        raise ValueError(f"undocumented error code {code!r}; add it to envelope.ERRORS")
    return {
        "schema": SCHEMA,
        "ok": False,
        "command": command,
        "repo": repo,
        "sha": sha,
        "data": None,
        "truncated": None,
        "warnings": [],
        "cost": cost or {},
        "error": {"code": code, "message": message, "hint": hint},
    }


def page(rows: list, limit: int, cursor: str = "") -> tuple[list, dict]:
    """One page of rows, and an honest account of what was left out.

    The cursor is the offset as a string rather than an opaque token: the row order is
    stable within a scan, an agent can read it, and there is nothing to keep server-side.
    An unparseable or out-of-range cursor starts from the beginning rather than raising,
    so a caller that lost its place gets the first page and not a stack trace. `limit` is
    clamped to at least 1: `limit=0` would return zero rows and a cursor equal to the
    offset it started from, so a caller that paged with it would loop forever, always one
    page away from the end and never reaching it.
    """
    try:
        start = int(cursor)
        if start < 0:
            start = 0
    except ValueError:
        start = 0
    limit = max(1, limit)
    shown = rows[start:start + limit]
    following = start + len(shown)
    return shown, {
        "returned": len(shown),
        "total": len(rows),
        "offset": start,
        "cursor": str(following) if following < len(rows) else "",
    }
