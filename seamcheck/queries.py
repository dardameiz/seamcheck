"""The questions, answered small.

An agent's questions are "what is this called", "what is wrong in this file", "who calls
this" and "what changed since main". Until now the only way to ask any of them was
`seamcheck json`, which is 72.6 MB on the reference project, so the agent paid about 18
million tokens for four answers it could have had for a few hundred each.

Everything here reads the cached scan, returns an envelope, and is bounded by default. The
CLI and the MCP server both call these functions and neither adds logic of its own, because
two implementations of one question is how two surfaces come to disagree.
"""
from __future__ import annotations

import contextlib
import difflib
import os
import subprocess

from seamcheck import envelope, scancache
from seamcheck.triage import judged_ids, load_triage


def _row(symbol) -> dict:
    return {"id": symbol.id, "kind": symbol.kind, "label": symbol.label,
            "status": symbol.status.value, "file": symbol.file, "line": symbol.line,
            "owner": symbol.owner or "", "note": symbol.note or ""}


def _scan(repo_root: str, refresh: bool = False):
    graph, how = scancache.cached_scan(repo_root, refresh=refresh)
    return graph, {"scan_seconds": how.get("seconds", 0.0), "cached": how.get("cached", False)}


def symbols(repo_root: str = ".", search: str = "", kind: str = "", limit: int = 25,
            cursor: str = "", refresh: bool = False) -> dict:
    """Find a symbol by substring. The cheap way to turn a name into an id.

    `refresh` skips the scan cache in both directions - the escape for a tree the cache
    cannot judge on its own (a fresh checkout, a restored backup, a clock that just got
    corrected); see `scancache`'s module docstring.
    """
    graph, cost = _scan(repo_root, refresh=refresh)
    needle = search.lower()
    rows = [_row(s) for s in graph.symbols
            if (not needle or needle in s.id.lower() or needle in (s.label or "").lower())
            and (not kind or s.kind == kind)]
    rows.sort(key=lambda row: (row["kind"], row["id"]))
    shown, cut = envelope.page(rows, limit, cursor)
    return envelope.answer("symbols", {"symbols": shown}, repo=repo_root,
                           truncated=cut, cost=cost)


def near(repo_root: str = ".", symbol_id: str = "", limit: int = 5, graph=None) -> list[str]:
    """Ids close to one that does not exist, for the hint on a miss.

    `graph` lets a caller that already paid for a scan (`explain`, on a miss) pass it
    straight through rather than paying for a second cache lookup right behind the first -
    resolved via `_scan(repo_root)` only when nothing was handed in.
    """
    if graph is None:
        graph, _ = _scan(repo_root)
    return difflib.get_close_matches(symbol_id, [s.id for s in graph.symbols],
                                     n=limit, cutoff=0.5)


# What counts as a finding by default. `uncertain` is deliberately not here: it is the tool
# saying it could not tell, and reporting it as a finding is how a guess gets laundered into
# a fact. `status=` can still ask for it explicitly - see findings()'s docstring.
FINDING_STATUSES = ("unresolved", "unused")
ALL_STATUSES = ("unresolved", "unused", "uncertain", "connected")


def _normalize_file(file: str, repo_root: str) -> str:
    """The path exactly as the scan stored it, so a filter matches without knowing the graph's
    own convention for spelling one.

    Every symbol's `file` was already made relative to `repo_root` (and stripped of a leading
    "./") at scan time by `graph.relativise` - a filter should not have to know that to match.
    An absolute path under `repo_root` is rewritten relative to it; a "./"-prefixed one is
    stripped the same way `relativise` strips it. Anything else (already relative, or outside
    the repo root) passes through unchanged and simply will not match, which is what the
    no-match warning in `findings()` is for.
    """
    if not file:
        return file
    if os.path.isabs(file):
        # A different drive on Windows raises ValueError; leave `file` as-is and let it
        # simply fail to match rather than raising out of a read-only query.
        with contextlib.suppress(ValueError):
            file = os.path.relpath(os.path.abspath(file), os.path.abspath(repo_root))
    if file.startswith("./"):
        file = file[2:]
    return file


def findings(repo_root: str = ".", file: str = "", kind: str = "", status: str = "",
             owner: str = "", limit: int = 25, cursor: str = "", refresh: bool = False,
             include_triaged: bool = False, only_blocking: bool = False, graph=None) -> dict:
    """What is wrong, narrowed by file, kind, status or owning function.

    By default this returns only what the tool is willing to call broken - `unresolved` and
    `unused` - because folding `uncertain` (a guess) or `connected` (not a problem) into
    "findings" would launder a guess into a fact. Passing an explicit `status` is still
    honoured exactly as asked, uncertain and connected included: a caller triaging one file
    may genuinely want to see them. Either way `data["statuses"]` names the statuses actually
    searched, so the answer can never be misread as the default set when it is not.

    A finding carrying ANY triage mark is also left out by default - "what is wrong" has to
    mean one thing whether it is read here or asked about interactively, and `unverified()`
    uses the same predicate (`triage.judged_ids`), reused rather than re-derived, so the two
    cannot silently drift into disagreeing about what "judged" means. Pass
    `include_triaged=True` for a caller that genuinely wants everything, marks included.

    `only_blocking=True` is the THIRD, narrower answer - not "everything", not "nothing
    judged" - used by SARIF and `check --format github` (`api._findings_report`): a
    CONFIRMED mark does not silence a finding (see `triage.blocking_ids`, `check`'s own
    gate), so with this set a CONFIRMED finding is INCLUDED (it still blocks the build)
    while an APPROVED/DEFERRED one stays excluded. Without it, `judged_ids` excludes
    every marked finding regardless of status - the plain, default answer above, which
    `check --format sarif` used to be built from even though the CI gate itself judges
    CONFIRMED findings differently: the SARIF file could say "clean" for the exact
    finding that had just failed the build. Mutually exclusive with `include_triaged`
    in practice - a caller asking for "only what's blocking" is not also asking for
    "everything, judged or not" - `only_blocking` wins if both are somehow passed.

    `refresh` skips the scan cache in both directions - see `symbols`' docstring. `graph`
    lets a caller that already scanned (`api.report`'s SARIF/GitHub path) pass it straight
    through instead of paying for a second scan right behind the first.
    """
    if status and status not in ALL_STATUSES:
        return envelope.failure(
            "findings", "bad_argument", f"Unknown status {status!r}.",
            hint="One of: " + ", ".join(ALL_STATUSES))
    if graph is None:
        graph, cost = _scan(repo_root, refresh=refresh)
    else:
        cost = {"scan_seconds": 0.0, "cached": True}
    wanted = (status,) if status else FINDING_STATUSES
    file = _normalize_file(file, repo_root)
    warnings = []
    if file and not any(s.file == file for s in graph.symbols):
        warnings.append(f"No file matched {file!r} in the scan.")
    entries = load_triage(repo_root)
    if only_blocking:
        from seamcheck.triage import blocking_ids

        # Exclude only what has ACTUALLY been silenced (a valid mark whose status is
        # not CONFIRMED) - never a CONFIRMED or a stale mark, both of which
        # triage.blocking_ids() (has_blocking_findings' own predicate) still counts as
        # blocking. judged_ids() alone is a superset of "silenced"; subtracting
        # blocking_ids() from it is what narrows it to exactly that.
        judged = judged_ids(entries) - blocking_ids(graph, entries)
    else:
        judged = set() if include_triaged else judged_ids(entries)
    rows = [_row(s) for s in graph.symbols
            if s.status.value in wanted
            and s.id not in judged
            and (not file or s.file == file)
            and (not kind or s.kind == kind)
            and (not owner or (s.owner or "") == owner)]
    rows.sort(key=lambda row: (row["status"], row["kind"], row["file"], row["line"] or 0))
    by_kind: dict[str, int] = {}
    by_status: dict[str, int] = {}
    for row in rows:
        by_kind[row["kind"]] = by_kind.get(row["kind"], 0) + 1
        by_status[row["status"]] = by_status.get(row["status"], 0) + 1
    shown, cut = envelope.page(rows, limit, cursor)
    return envelope.answer(
        "findings",
        {"findings": shown, "by_kind": by_kind, "by_status": by_status,
         "statuses": list(wanted)},
        repo=repo_root, truncated=cut, cost=cost, warnings=warnings)


def _resolve(repo_root: str, ref: str) -> str:
    """The sha a ref points at, or "" when git cannot say."""
    try:
        done = subprocess.run(["git", "-C", repo_root, "rev-parse", ref],
                              capture_output=True, text=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        return ""
    return done.stdout.strip()


def diff(repo_root: str = ".", since: str = "HEAD~1", limit: int = 25,
         cursor: str = "", refresh: bool = False) -> dict:
    """What appeared, what vanished and what changed status since a ref.

    "What did this commit break" is a question CI and an agent both ask, and until now the
    only way to ask it was `check --since`, which folds the answer into a pass/fail gate -
    there was no way to just SEE the list. A separate comparison from the one `check`
    uses, not a shared implementation: this is unfiltered by triage, so a caller diffing
    two arbitrary points sees the raw graph difference rather than a CI-gate's opinion.

    `refresh` skips the scan cache in both directions - see `symbols`' docstring.
    """
    from seamcheck import snapshot

    sha = _resolve(repo_root, since)
    if not sha:
        return envelope.failure("diff", "no_git", f"Could not resolve {since!r}.",
                                hint="Pass a ref this repository has, e.g. --since origin/main.")
    try:
        before = snapshot.load_snapshot(sha, repo_root)
    except (TypeError, KeyError, ValueError) as error:
        # A snapshot written by an older or foreign version of the tool can fail to parse
        # back into a Graph - a schema field renamed, a status value that no longer exists.
        # graph_from_dict raising straight out of a read-only query is exactly the crash
        # the envelope exists to prevent, so it is caught here and turned into a code.
        return envelope.failure(
            "diff", "stale_snapshot",
            f"The stored snapshot for {sha[:12]} could not be read by this version of "
            f"seamcheck ({error}).",
            hint=f"Run `seamcheck scan` at {since} to write a fresh one.")
    if before is None:
        return envelope.failure(
            "diff", "no_baseline", f"No snapshot for {sha[:12]}.",
            hint=f"Run `seamcheck scan` at {since} once, or `seamcheck backfill 20`.")
    graph, cost = _scan(repo_root, refresh=refresh)
    was = {s.id: s for s in before.symbols}
    now = {s.id: s for s in graph.symbols}
    appeared = [_row(now[i]) for i in now.keys() - was.keys()]
    vanished = [_row(was[i]) for i in was.keys() - now.keys()]
    changed = [dict(_row(now[i]), was=was[i].status.value)
               for i in now.keys() & was.keys()
               if now[i].status.value != was[i].status.value]
    for rows in (appeared, vanished, changed):
        rows.sort(key=lambda row: (row["kind"], row["id"]))
    # Computed over the whole (unpaged) lists: the three categories are paged together as
    # one concatenated list below, so a page can show "vanished": [] while more vanished
    # rows sit on a later page - counts are the only way to tell "nothing vanished" from
    # "not on this page yet".
    counts = {"appeared": len(appeared), "vanished": len(vanished), "changed": len(changed)}
    shown, cut = envelope.page(appeared + vanished + changed, limit, cursor)
    ids = {row["id"] for row in shown}
    return envelope.answer(
        "diff",
        {"baseline": sha, "since": since, "counts": counts,
         "appeared": [r for r in appeared if r["id"] in ids],
         "vanished": [r for r in vanished if r["id"] in ids],
         "changed": [r for r in changed if r["id"] in ids]},
        repo=repo_root, truncated=cut, cost=cost)
