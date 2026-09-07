"""MCP surface. Thin wrappers over seamcheck.api - the same code the CLI runs.

THE LOOP THESE TOOLS EXIST FOR, in order, because the individual tools do not imply it:

  1. seamcheck_unverified   the claims nobody has judged - a queue, worst first
  2. read the actual code   open the file at the line. The note is what the tool THINKS;
                            it is not evidence. Reasoning about the note is not verifying.
  3. seamcheck_triage       record the verdict WITH a `why` from seamcheck_why_wrong.
                            `genuinely-dead` is a verdict too - a finding confirmed right
                            is as useful as one confirmed wrong.
  4. seamcheck_share        the report, containing counts and fixed words and none of the
                            code. It returns the markdown AND a pre-filled issue link.
  5. SHOW IT AND ASK        paste the report into the conversation, say what it contains,
                            and ask whether to send it. Then hand over the link.

Step 5 is not optional and no tool here performs it. Nothing in this package makes a
network call: the report is prepared, and a person decides. An agent that opens the link
itself, or that submits on the user's behalf without being asked, has taken a decision
that was never its own - and the repository may belong to an employer who never agreed.

Four more tools answer questions OUTSIDE that loop, cheaply, because the only way to ask
them used to be `seamcheck json` - 72 MB, about 18 million tokens on the reference project:

  seamcheck_symbols    turn a name into an id, before spending a call on explain or triage.
  seamcheck_findings   what is wrong, filtered by file/kind/status/owner - the other place
                       to START, when the question is already scoped to one file.
  seamcheck_diff       what a commit changed: appeared, vanished, or flipped status.
  seamcheck_snapshot   write the baseline `check` and `diff` compare against - before this
                       existed, only a human running the CLI could ever create one.

Kept deliberately thin: an agent asking `check` must get the answer the terminal would give,
because the moment the two disagree neither can be trusted. Every tool here is one call into
`api` or `queries` - never a second implementation of what either already answers.
"""

from __future__ import annotations

from typing import Literal, TypedDict

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
from seamcheck.queries import ALL_STATUSES
from seamcheck.triage import TriageStatus, WhyWrong

# ---------------------------------------------------------------------------------------
# Closed vocabularies, derived from the ONE place each already lives - never retyped by
# hand, so a member added to the real enum cannot silently go missing from the schema an
# agent reads. Two vocabularies share the word "status" but are not the same thing: a
# TRIAGE status is a human's verdict on a finding (approved/confirmed/deferred/untriaged);
# a FINDING status is what the scanner itself measured (unresolved/unused/uncertain/
# connected). Keeping them as two names (`TriageDisposition` vs `FindingStatus`) is the
# whole point - conflating them is exactly the kind of drift this task exists to remove.
# ---------------------------------------------------------------------------------------

TriageDisposition = Literal[tuple(status.value for status in TriageStatus)]
# `why` is optional (the empty string means "no reason given" - see api.triage), so the
# sentinel joins the enum's own members rather than being invented separately.
WhyReason = Literal[("", *(reason.value for reason in WhyWrong))]
FindingStatus = Literal[tuple(ALL_STATUSES)]
# The `status` FILTER argument additionally accepts "" for "use the default finding set" -
# see queries.findings' docstring. Not the same alias as FindingStatus: that one types a
# value the scanner freshly produced (always one of the four), this one types a caller's
# input (which may deliberately ask for nothing in particular).
FindingStatusFilter = Literal[("", *ALL_STATUSES)]
# NOT "json" or "map": 72 MB and 8.6 MB on the reference project, and neither was ever
# documented as something an agent should ask for. seamcheck_report refuses both itself,
# before ever calling into api.report - see its own docstring.
Fmt = Literal["terminal", "markdown", "html"]


class ErrorInfo(TypedDict):
    code: str
    message: str
    hint: str


class Envelope(TypedDict):
    """The exact shape `envelope.answer()`/`envelope.failure()` return - one schema every
    query tool shares, because the envelope is the one thing none of them may reinvent.

    `data` is left loose here on purpose: a tool that returns one narrows it with a
    one-line subclass (`FindingsEnvelope` etc., below) rather than a `Generic[T]` - tried
    first, and rejected because FastMCP wraps a *generic* TypedDict's structured content
    in an extra `{"result": ...}` layer that a concrete TypedDict does not get, which would
    have made every OTHER tool's structured content flat and these four alone nested one
    level deeper for no reason a client could guess.
    """

    schema: int
    ok: bool
    command: str
    repo: str
    sha: str
    data: dict | None
    truncated: dict | None
    warnings: list[str]
    cost: dict
    error: ErrorInfo | None


class Finding(TypedDict):
    """One symbol, as `queries._row()` renders it - the shape shared by symbols, findings,
    unverified and (for `appeared`/`vanished`) diff."""

    id: str
    kind: str
    label: str
    status: FindingStatus
    file: str
    line: int | None
    owner: str
    note: str


class ChangedFinding(Finding):
    # `queries.diff()`'s "changed" rows are a Finding plus the status it used to have -
    # a plain `Finding` here would let pydantic silently DROP `was` from structured
    # output (verified empirically: an extra TypedDict key is stripped, not rejected).
    was: FindingStatus


class FindingsData(TypedDict):
    findings: list[Finding]
    by_kind: dict[str, int]
    by_status: dict[str, int]
    statuses: list[str]


class SymbolsData(TypedDict):
    symbols: list[Finding]


class DiffData(TypedDict):
    baseline: str
    since: str
    counts: dict[str, int]
    appeared: list[Finding]
    vanished: list[Finding]
    changed: list[ChangedFinding]


class SnapshotData(TypedDict):
    sha: str
    path: str
    symbols: int


class FindingsEnvelope(Envelope):
    data: FindingsData | None


class SymbolsEnvelope(Envelope):
    data: SymbolsData | None


class DiffEnvelope(Envelope):
    data: DiffData | None


class SnapshotEnvelope(Envelope):
    data: SnapshotData | None


class ChangeRow(TypedDict):
    # api.check()'s new_unresolved/new_unused rows.
    id: str
    label: str
    kind: str
    note: str


class InvalidatedRow(TypedDict):
    # api.check()'s triage_invalidated rows - see diff.diff_graphs.
    symbol_id: str
    stored_fingerprint: str
    current_fingerprint: str
    note: str


class MarkRow(TypedDict):
    """One triage mark, as `report.mark_dict()` renders it.

    `status` is freshly read off the current scan's Symbol (always one of the four), so
    the closed FindingStatus type is safe. `marked` is freshly read off a TriageEntry that
    `load_triage()` already validated against TriageStatus at load time (or it would have
    raised there), so TriageDisposition is safe too. `why` is NOT: it is free text on a
    checked-in file a human can hand-edit, including entries written before WhyWrong
    existed - typing it as the closed enum would make an old, valid triage.json crash a
    tool call the moment its structured output was validated.
    """

    symbol_id: str
    label: str
    kind: str
    status: FindingStatus
    file: str
    line: int | None
    marked: TriageDisposition
    why: str
    when: str
    who: str
    reason: str
    expired: str
    returned: bool


class CheckResult(TypedDict, total=False):
    """api.check()'s return shape. `total=False` because `bad_ref` is the one field that
    is genuinely sometimes absent (only the `since`-could-not-resolve branch sets it) -
    the other seven keys are present on every branch, but TypedDict has no way to mark a
    single field optional without either `NotRequired` (typing, 3.11+; this project
    supports 3.10) or a second, near-duplicate TypedDict for the bad-ref branch alone."""

    passed: bool
    message: str
    bad_ref: str
    new_unresolved: list[ChangeRow]
    new_unused: list[ChangeRow]
    triage_invalidated: list[InvalidatedRow]
    returned: list[MarkRow]
    counts: dict[str, int]


class UnverifiedResult(TypedDict):
    total_unjudged: int
    by_kind: dict[str, int]
    findings: list[Finding]


class WhyWrongResult(TypedDict):
    reasons: dict[str, str]


class TriageResult(TypedDict):
    ok: bool
    message: str


class ServiceRow(TypedDict):
    name: str
    root: str
    language: str
    deployable: bool
    evidence: list[str]


class ServicesResult(TypedDict):
    services: list[ServiceRow]


# Every reading tool gets the same two annotations: a client can act on `readOnlyHint`
# without asking a human first, and `destructiveHint: False` says a repeat call is never
# worse than the first. `seamcheck_triage` and `seamcheck_snapshot` are the only writers
# and are annotated where they are declared, not here.
_READS = {"readOnlyHint": True, "destructiveHint": False}

mcp = _Server("seamcheck", instructions=__doc__)


@mcp.tool(annotations=_READS)
def seamcheck_check(repo_root: str = ".") -> CheckResult:
    """Scan the project and report findings new since the last snapshot."""
    return api.check(repo_root)


@mcp.tool(annotations=_READS)
def seamcheck_explain(symbol_id: str, repo_root: str = ".") -> str:
    """Explain one symbol: where it is, how it was reached, and why it is classified so.

    On a miss, also names the ids closest to the one given - the same hint the CLI's
    `explain` prints, so the two surfaces cannot disagree about what a typo meant.
    """
    from seamcheck.scancache import cached_scan

    graph, _how = cached_scan(repo_root)
    return api.explain_with_hint(graph, symbol_id, repo_root)


@mcp.tool(annotations={"readOnlyHint": False})
def seamcheck_triage(symbol_id: str, status: TriageDisposition, repo_root: str = ".",
                     reason: str = "", why: WhyReason = "", undo: bool = False) -> TriageResult:
    """Record a human disposition (approved/confirmed/deferred) against a finding.

    `reason` is prose and stays local. `why` is one of a fixed set - see
    seamcheck_why_wrong - and is the only part `seamcheck share` can pass on.
    `undo` takes an earlier mark off instead; the finding is raised again.
    """
    return api.triage(symbol_id, status, repo_root, reason, why, undo=undo)


@mcp.tool(annotations=_READS)
def seamcheck_unverified(repo_root: str = ".", limit: int = 25, kind: str = "") -> UnverifiedResult:
    """Findings nobody has judged yet — the queue to work through.

    Each row carries the file, the line and the note, so you can open the code and decide
    whether the claim is true without another call. Judge one, record it with
    seamcheck_triage(..., why=...), and take the next.
    """
    return api.unverified(repo_root, limit, kind)


@mcp.tool(annotations=_READS)
def seamcheck_why_wrong() -> WhyWrongResult:
    """The fixed reasons a finding can be wrong, for the `why` argument of triage."""
    from seamcheck.triage import WHY_HELP

    return {"reasons": WHY_HELP}


@mcp.tool(annotations=_READS)
def seamcheck_report(fmt: Fmt = "markdown", repo_root: str = ".") -> str | Envelope:
    """Render the findings digest: terminal, markdown or html.

    `json` and `map` are not offered - they are 72 MB and 8.6 MB on the reference project
    - and asking for either returns the coded `too_large` failure immediately, before
    paying for the scan `api.report` would otherwise run: the schema already refuses to
    advertise them, but a client that ignores the schema (or calls this as plain Python,
    the way seamcheck's own tests do) must still be refused rather than handed the payload.
    `full` is deliberately not a parameter here either: an agent that hits this ceiling
    wants the narrower answer, not a way to force the big one.
    """
    from seamcheck import envelope

    if fmt in ("json", "map"):
        return envelope.failure(
            "report", "too_large",
            f"seamcheck_report does not offer fmt={fmt!r} - json is ~72 MB and map ~8.6 MB "
            "on a large project, both far over any usable reply size.",
            hint="Use seamcheck_findings for what is wrong, or seamcheck_explain for one "
                 "symbol, instead of the whole report.")
    try:
        return api.report(repo_root, fmt)
    except envelope.TooLarge as error:
        # Reachable only if a future change makes a non-json/map format raise TooLarge
        # too (today only json's own size gate in api.py does) - kept as the same
        # documented contract every other `api.report` caller honours, not dead code for
        # a shape this tool happens not to hit yet.
        return envelope.failure(
            "report", "too_large",
            f"The rendered report is {error.size_bytes / 1e6:.1f} MB "
            f"(~{error.tokens:,} tokens) - too large to return here.",
            hint="Use seamcheck_findings for what is wrong, or seamcheck_explain for one "
                 "symbol, instead of the whole report.")


@mcp.tool(annotations=_READS)
def seamcheck_share(repo_root: str = ".", with_deps: bool = False) -> str:
    """Build a report about the scan that contains none of the scanned code.

    Counts and fixed words only - no paths, names, routes, snippets or repository
    identity. Returns markdown for a person to read and decide whether to send. This
    makes no network call; nothing is transmitted by generating it.
    """
    from seamcheck import share

    markdown, payload = share.report(repo_root, with_deps=with_deps)
    return markdown + "\n\nPre-filled issue link (submits nothing until pressed):\n" + share.issue_url(payload)


@mcp.tool(annotations=_READS)
def seamcheck_services(repo_root: str = ".") -> ServicesResult:
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


@mcp.tool(annotations=_READS)
def seamcheck_findings(repo_root: str = ".", file: str = "", kind: str = "",
                       status: FindingStatusFilter = "", owner: str = "",
                       limit: int = 25, cursor: str = "",
                       include_triaged: bool = False, refresh: bool = False) -> FindingsEnvelope:
    """What is wrong, filtered and bounded. START HERE.

    `file` answers "what is wrong in the file I am editing". The reply carries by_kind and
    by_status, so the vocabulary for the next call comes from this one. Costs one cached
    scan; asking again is free until the files change (`refresh=True` bypasses that cache
    for a tree it cannot judge on its own - a fresh checkout, a restored backup).
    """
    from seamcheck import queries

    return queries.findings(repo_root, file, kind, status, owner, limit, cursor,
                            refresh=refresh, include_triaged=include_triaged)


@mcp.tool(annotations=_READS)
def seamcheck_symbols(repo_root: str = ".", search: str = "", kind: str = "",
                      limit: int = 25, cursor: str = "", refresh: bool = False) -> SymbolsEnvelope:
    """Find a symbol id by name, before spending a call on explain or triage."""
    from seamcheck import queries

    return queries.symbols(repo_root, search, kind, limit, cursor, refresh=refresh)


@mcp.tool(annotations=_READS)
def seamcheck_diff(repo_root: str = ".", since: str = "HEAD~1", limit: int = 25,
                   cursor: str = "", refresh: bool = False) -> DiffEnvelope:
    """What appeared, vanished or changed status since a ref. The "what did I break" call."""
    from seamcheck import queries

    return queries.diff(repo_root, since, limit, cursor, refresh=refresh)


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
def seamcheck_snapshot(repo_root: str = ".", refresh: bool = False) -> SnapshotEnvelope:
    """Record the current graph as the baseline that check and diff compare against.

    Without this an agent could never make a baseline: only the CLI wrote one (as a
    side effect of `seamcheck scan`), so seamcheck_check answered "no baseline" forever
    until a human ran a terminal command. Writes through `api.write_map` - the same
    function the CLI's own scan command calls - rather than saving a snapshot a second
    way; `path` in the reply is the connectivity map written alongside it.
    """
    import subprocess

    from seamcheck import envelope
    from seamcheck.scancache import cached_scan
    from seamcheck.snapshot import current_git_sha

    try:
        sha = current_git_sha(repo_root)
    except (OSError, subprocess.CalledProcessError) as error:
        return envelope.failure(
            "snapshot", "no_git", f"Could not resolve HEAD ({error}).",
            hint="Run this inside a git repository - a snapshot is keyed by commit.",
            repo=repo_root)
    graph, how = cached_scan(repo_root, refresh=refresh)
    path = api.write_map(graph, repo_root)
    return envelope.answer(
        "snapshot", {"sha": sha, "path": path, "symbols": len(graph.symbols)},
        repo=repo_root, sha=sha, cost={"cached": how["cached"]})


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
