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
}


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


def failure(command: str, code: str, message: str, *, hint: str = "") -> dict:
    """A failure. `code` must be a key of ERRORS."""
    if code not in ERRORS:
        raise ValueError(f"undocumented error code {code!r}; add it to envelope.ERRORS")
    return {
        "schema": SCHEMA,
        "ok": False,
        "command": command,
        "repo": "",
        "sha": "",
        "data": None,
        "truncated": None,
        "warnings": [],
        "cost": {},
        "error": {"code": code, "message": message, "hint": hint},
    }


def page(rows: list, limit: int, cursor: str = "") -> tuple[list, dict]:
    """One page of rows, and an honest account of what was left out.

    The cursor is the offset as a string rather than an opaque token: the row order is
    stable within a scan, an agent can read it, and there is nothing to keep server-side.
    """
    start = int(cursor) if cursor.isdigit() else 0
    shown = rows[start:start + limit]
    following = start + len(shown)
    return shown, {
        "returned": len(shown),
        "total": len(rows),
        "offset": start,
        "cursor": str(following) if following < len(rows) else "",
    }
