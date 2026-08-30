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
    """(rank, sha, date, subject) for every sha git still knows, oldest first.

    Ordered by ancestry, not by commit time. Two commits made in the same second share a
    timestamp - routine during a rebase, a script, or CI - and sorting those by timestamp
    falls back to comparing the sha, which puts a child before its parent and inverts the
    diff. A commit git cannot rank (orphaned, from another clone) falls back to its time,
    placed after everything ranked.
    """
    rank = _topo_rank(repo_root)
    known = []
    for sha in shas:
        try:
            line = _git(["show", "-s", "--format=%ct\x1f%cs\x1f%s", sha], repo_root)
        except subprocess.CalledProcessError:
            continue  # rebased away, or from another clone - not this history
        stamp, date, subject = line.split("\x1f", 2)
        known.append((rank.get(sha, len(rank) + int(stamp)), sha, date, subject))
    return sorted(known)


def _changes(before: Graph, after: Graph) -> dict[str, str]:
    was = {symbol.id: symbol.status for symbol in before.symbols}
    now = {symbol.id: symbol.status for symbol in after.symbols}
    changed = dict.fromkeys(now.keys() - was.keys(), "added")
    changed.update(dict.fromkeys(was.keys() - now.keys(), "removed"))
    changed.update(
        {sid: "status" for sid in now.keys() & was.keys() if was[sid] is not now[sid]}
    )
    return changed


def commit_series(repo_root: str = ".") -> list[CommitEntry]:
    """Every scanned commit, newest first, each carrying what it changed."""
    ordered = _commit_order(snapshot_shas(repo_root), repo_root)
    series: list[CommitEntry] = []
    previous: tuple[str, Graph] | None = None
    for _, sha, date, subject in ordered:
        graph = load_snapshot(sha, repo_root)
        if graph is None:
            continue
        entry = CommitEntry(
            sha=sha, subject=subject, date=date, symbols=len(graph.symbols),
            changed=_changes(previous[1], graph) if previous else {},
            baseline_sha=previous[0] if previous else None,
        )
        series.append(entry)
        previous = (sha, graph)
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
