"""Per-commit history: which commit each finding arrived in.

A scan describes one commit. Two scans describe a change. This module turns the
snapshots on disk into a series - each commit paired with what it changed against the
snapshot before it - so a reader can ask "what did THIS commit do" instead of reading a
30,000-symbol graph and guessing.

The series only ever contains commits that were actually scanned. A commit with no
snapshot is absent, never interpolated: inventing a diff for an unscanned commit would
attribute changes to whichever commit happened to be next in the log.
"""

from __future__ import annotations

import os
import pathlib
import re
import shutil
import subprocess
from dataclasses import dataclass, field

from signal_map.graph import Graph
from signal_map.snapshot import _SCANS_DIR, load_snapshot

# Copied into each temporary worktree before scanning it. A checkout of an old commit has
# the source but not the git-ignored things the scan needs to run - the interpreter's
# packages, the JS parser, the settings the app reads at import.
DEFAULT_LINKS = ("venv", "node_modules", ".env")


@dataclass(frozen=True)
class CommitEntry:
    sha: str
    subject: str
    date: str
    symbols: int
    # node id -> "added" | "removed" | "status", against the previous scanned commit.
    changed: dict[str, str] = field(default_factory=dict)
    # Every change this commit made, carried in full. The canvas draws today's code, so
    # it can only show a change that survived to today: a symbol this commit deleted, or
    # one it added that a later commit took away again, exists on no page and would
    # otherwise render as an empty screen under a note claiming two things changed.
    changes: list[dict] = field(default_factory=list)
    baseline_sha: str | None = None


def _tool_root() -> pathlib.Path:
    """The directory holding the signal_map package that is running right now."""
    return pathlib.Path(__file__).resolve().parent.parent


def _git(args: list[str], repo_root: str) -> str:
    return subprocess.run(
        ["git", "-C", repo_root, *args], capture_output=True, text=True, check=True
    ).stdout.strip()


def snapshot_shas(repo_root: str = ".") -> list[str]:
    directory = pathlib.Path(repo_root) / _SCANS_DIR
    if not directory.is_dir():
        return []
    return sorted(path.stem for path in directory.glob("*.json"))


def _topo_rank(repo_root: str) -> dict[str, int]:
    """Each commit's position in git's own ancestry order, oldest first."""
    try:
        shas = _git(["rev-list", "--topo-order", "--all"], repo_root).splitlines()
    except subprocess.CalledProcessError:
        return {}
    return {sha: rank for rank, sha in enumerate(reversed(shas))}


def _commit_order(shas: list[str], repo_root: str) -> list[tuple[int, str, str, str]]:
    """(sort key, sha, date, subject) for every sha git still knows, oldest first.

    Ordered by commit time, because "the last commit" means the most recent one and a
    reader opening this asks for it by that name. Pure ancestry order would answer with
    whichever branch git chose to walk first - with two branches scanned it put a tool
    commit ahead of work committed two hours later.

    Ancestry breaks the ties. Two commits made in the same second share a timestamp -
    routine during a rebase, a script or CI - and sorting only by time falls back to
    comparing the sha, which puts a child before its parent and inverts the diff.

    Ordering is display only: a commit's baseline is its nearest scanned ancestor, found
    from the parent graph, so no diff depends on how this list happens to sort.
    """
    rank = _topo_rank(repo_root)
    known = []
    for sha in shas:
        try:
            # %cI, not %cs: two commits an hour apart on the same day are two different
            # answers to "which one am I looking at".
            line = _git(["show", "-s", "--format=%ct\x1f%cI\x1f%s", sha], repo_root)
        except subprocess.CalledProcessError:
            continue  # rebased away, or from another clone - not this history
        stamp, date, subject = line.split("\x1f", 2)
        known.append(((int(stamp), rank.get(sha, 0)), sha, date, subject))
    return sorted(known)


_LINE_SUFFIX = re.compile(r":\d+$")


def _identity(symbol) -> str:
    """What a symbol IS, with where it sits removed.

    Most ids end in the line the symbol was found on. Inserting two lines above a block
    therefore renames every symbol below it: one commit that added a CPS duration moved
    115 untouched selectors from :143 to :145 and reported all 115 as removed AND added.
    A diff that loud about a line shift hides the one real change inside it.
    """
    return _LINE_SUFFIX.sub("", symbol.id) if symbol.line else symbol.id


def _detail(symbol, change: str) -> dict:
    return {"id": symbol.id, "label": symbol.label, "kind": symbol.kind,
            "file": symbol.file or "", "line": symbol.line, "change": change}


def _change_detail(before: Graph, after: Graph) -> list[dict]:
    """Every changed symbol, named, whether or not it still exists to be drawn."""
    was = {_identity(symbol): symbol for symbol in before.symbols}
    now = {_identity(symbol): symbol for symbol in after.symbols}
    changes = [_detail(was[key], "removed") for key in sorted(was.keys() - now.keys())]
    changes += [_detail(now[key], "added") for key in sorted(now.keys() - was.keys())]
    changes += [
        _detail(now[key], "status")
        for key in sorted(now.keys() & was.keys())
        if was[key].status is not now[key].status
    ]
    return changes


def _changes(before: Graph, after: Graph) -> dict[str, str]:
    was = {_identity(symbol): symbol.status for symbol in before.symbols}
    now = {_identity(symbol): (symbol.status, symbol.id) for symbol in after.symbols}
    changed: dict[str, str] = {}
    for key, (status, symbol_id) in now.items():
        if key not in was:
            changed[symbol_id] = "added"
        elif was[key] is not status:
            changed[symbol_id] = "status"
    # A removed symbol has no id in the current graph and is never drawn; its identity is
    # all there is left to count it by.
    changed.update(dict.fromkeys(was.keys() - now.keys(), "removed"))
    return changed


def _parents(repo_root: str) -> dict[str, list[str]]:
    """The whole commit graph's parent links, in one call."""
    try:
        lines = _git(["rev-list", "--parents", "--all"], repo_root).splitlines()
    except subprocess.CalledProcessError:
        return {}
    return {parts[0]: parts[1:] for parts in (line.split() for line in lines) if parts}


def _nearest_scanned_ancestor(sha: str, scanned: set[str], parents: dict[str, list[str]]) -> str | None:
    """The closest ancestor of `sha` that was itself scanned.

    A commit's baseline has to be something it descends from. Taking "the previous entry
    in the list" compares a commit against whatever sorted before it, which on a repo with
    more than one branch means comparing across a fork and calling the other branch's
    absent work a change this commit made.
    """
    seen, queue = {sha}, list(parents.get(sha, []))
    while queue:
        candidate = queue.pop(0)
        if candidate in scanned:
            return candidate
        if candidate not in seen:
            seen.add(candidate)
            queue.extend(parents.get(candidate, []))
    return None


def commit_series(repo_root: str = ".") -> list[CommitEntry]:
    """Every scanned commit, newest first, each carrying what it changed."""
    ordered = _commit_order(snapshot_shas(repo_root), repo_root)
    scanned = {sha for _, sha, _, _ in ordered}
    parents = _parents(repo_root)
    graphs: dict[str, Graph] = {}
    series: list[CommitEntry] = []
    for _, sha, date, subject in ordered:
        graph = load_snapshot(sha, repo_root)
        if graph is None:
            continue
        graphs[sha] = graph
        baseline = _nearest_scanned_ancestor(sha, scanned, parents)
        series.append(CommitEntry(
            sha=sha, subject=subject, date=date, symbols=len(graph.symbols),
            changed=_changes(graphs[baseline], graph) if baseline in graphs else {},
            changes=_change_detail(graphs[baseline], graph) if baseline in graphs else [],
            baseline_sha=baseline if baseline in graphs else None,
        ))
    return list(reversed(series))


# Run inside a checkout of an old commit, with THIS signal_map ahead of that commit's on
# the path. The project comes from the checkout; the instrument does not.
_DRIVER = """
import os, sys
TOOL = {tool!r}
sys.path.insert(0, TOOL)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "myproject.settings")
import django; django.setup()
# The instrument's settings travel with the instrument. A commit from before the tool
# existed has no SIGNAL_MAP_CONFIG at all, and one whose config merely differed would be
# measured differently - the same "did the project change or did the tool" ambiguity the
# import juggling below exists to remove.
from django.conf import settings as _s
_s.SIGNAL_MAP_CONFIG = {config!r}
# django.setup() puts the checkout ahead of us on sys.path and imports the signal_map
# that commit shipped with. Evict it and re-import from TOOL, or every commit is
# measured by its own scanner and the diff means nothing. The assert is here because
# that failure is silent: the scan succeeds, it is just measuring the wrong thing.
for name in [n for n in list(sys.modules) if n == "signal_map" or n.startswith("signal_map.")]:
    del sys.modules[name]
sys.path.insert(0, TOOL)
from signal_map import api
from signal_map.snapshot import save_snapshot
assert api.__file__.startswith(TOOL), "scanned with the checkout's tool: " + api.__file__
save_snapshot(api.scan("."), {sha!r}, {out!r})
"""


def backfill(repo_root: str = ".", count: int = 10, links: tuple[str, ...] = DEFAULT_LINKS,
             scratch: str | None = None, ref: str = "HEAD") -> list[str]:
    """Scan the last `count` commits into snapshots, so the series is not empty.

    Two things have to be true for the resulting diffs to mean anything.

    The project must come from the commit: each is checked out into its own detached
    worktree and scanned with that directory as the working directory, so an old commit's
    templates are never read against today's URLconf.

    The instrument must not: this module's own signal_map goes on the path ahead of the
    checkout's, so every commit in the series is measured the same way. Running each
    commit's own copy of the tool made a diff mean "the project changed, or the scanner
    did" - and an early run of exactly that reported 85 symbols added and removed on a
    commit that only added a markdown file.
    """
    root = pathlib.Path(repo_root).resolve()
    have = set(snapshot_shas(repo_root))
    # `ref` because the branch you are standing on is not always the one whose history is
    # worth measuring: a tool branch's commits change nothing the scan reads.
    wanted = _git(["log", f"-{count}", "--format=%H", ref], repo_root).splitlines()
    todo = [sha for sha in wanted if sha and sha not in have]
    if not todo:
        return []

    from django.conf import settings

    config = dict(getattr(settings, "SIGNAL_MAP_CONFIG", {}))
    base = pathlib.Path(scratch or os.environ.get("TMPDIR", "/tmp")) / "signal-map-backfill"
    scanned: list[str] = []
    failed: list[tuple[str, list[str]]] = []
    for sha in todo:
        checkout = base / sha[:12]
        shutil.rmtree(checkout, ignore_errors=True)
        checkout.parent.mkdir(parents=True, exist_ok=True)
        _git(["worktree", "add", "--detach", str(checkout), sha], repo_root)
        try:
            for name in links:
                source = root / name
                if source.exists() and not (checkout / name).exists():
                    (checkout / name).symlink_to(source)
            python = checkout / "venv" / "bin" / "python"
            (root / _SCANS_DIR).mkdir(parents=True, exist_ok=True)
            result = subprocess.run(
                [str(python) if python.exists() else "python", "-c",
                 _DRIVER.format(tool=str(_tool_root()), sha=sha, out=str(root),
                                config=config)],
                cwd=checkout, capture_output=True, text=True, check=False,
            )
            if result.returncode == 0 and (root / _SCANS_DIR / f"{sha}.json").is_file():
                scanned.append(sha)
            else:
                # A swallowed failure here reads as "nothing to do" and is indistinguishable
                # from success; the reason is the only useful thing to return.
                failed.append((sha, (result.stderr or result.stdout).strip().splitlines()[-1:]))
        finally:
            _git(["worktree", "remove", "--force", str(checkout)], repo_root)
    if failed and not scanned:
        detail = "; ".join(f"{sha[:8]}: {' '.join(why)}" for sha, why in failed[:3])
        raise RuntimeError(f"every commit failed to scan - {detail}")
    return scanned
