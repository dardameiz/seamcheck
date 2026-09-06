"""One scan, remembered, so asking a second question is cheap.

Measured before this existed: every command and every MCP tool called `api.scan` afresh, so
five explanations on the reference project were five 90-second scans - and a mistyped symbol
id cost the same 88.5 seconds as a correct one, to be told the id was wrong.

The key is a hash of the file tree's SHAPE - every input file's relative path and size - plus
the tool version and the Django settings module name. Two things are deliberately left out:
content, because hashing a 100M-line repository to decide whether to scan it would cost more
than the scan; and the effective config, because computing it costs about seven seconds on
the reference project (see `api._config`) and would eat most of the win. The settings module
name is nearly free and still tells apart two runs that would otherwise build different
graphs from the same files.

mtime is left out of the key on purpose too, and handled separately as a freshness check
rather than an identity check - the way build tools handle it. A key built from
(path, size, mtime) looks precise, but two writes landing in the same mtime tick - ordinary
on a Docker bind mount, overlayfs, NFS, or most CI runners, and not reproducible on APFS's
nanosecond clock without forcing it - would hash to the very same key and let a stale graph
be served as a fresh one, because identity alone can't tell the two writes apart. So a cache
entry is trusted only when every input file's mtime is STRICTLY OLDER than the entry's own
mtime; if any input is as new as or newer than the entry, it is treated as stale and rescanned
even though its key still matches. The worst case is an unnecessary rescan inside one tick -
never a stale graph handed out as a fresh one.

Two layers: a process memo, for an MCP session answering question after question, and a file
under this machine's own cache directory (`_cache_root` - never inside the scanned
repository, which this tool exists to scan, not to write into), for the next command in the
same shell. Both layers are judged by the same freshness rule, so neither can serve a stale
answer.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import pathlib
import time

from seamcheck.graph import Graph, graph_from_dict, graph_to_dict

# memo_key -> (graph, cached_at_ns). cached_at_ns is judged against a fresh walk's latest
# input mtime exactly the way a disk entry's own mtime is - see `cached_scan`.
_MEMO: dict[str, tuple[Graph, int]] = {}

# How many cache files one repository may keep before the oldest (by mtime) is evicted.
# Nothing evicted anything before this existed, so a long-lived repo's cache directory only
# ever grew.
_KEEP_PER_REPO = 3

# Directories whose contents never change what a scan says.
_SKIP = {".git", "node_modules", "__pycache__", ".venv", "venv", ".seamcheck", "dist",
         "build", ".mypy_cache", ".ruff_cache", ".pytest_cache"}


def _version() -> str:
    """The installed version, or "0" outside an install (see `cli.version_line`)."""
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("seamcheck")
    except PackageNotFoundError:  # running straight from a source tree
        return "0"


def _cache_root(env: dict | None = None) -> pathlib.Path:
    """Where this machine keeps every repository's cache - never inside a scanned tree.

    XDG first, `~/.cache` after it - the same ladder `usersettings.settings_path` climbs for
    `~/.config`, so the one convention a person already knows for settings applies unchanged
    to cache.
    """
    env = os.environ if env is None else env
    base = env.get("XDG_CACHE_HOME") or os.path.join(
        env.get("HOME") or os.path.expanduser("~"), ".cache")
    return pathlib.Path(base) / "seamcheck"


def _repo_cache_dir(repo_root: str, env: dict | None = None) -> pathlib.Path:
    """One repository's own slot, named from its real path rather than mirroring it.

    A hash rather than a mirrored path so two checkouts of the same project - a worktree, a
    second clone, a CI runner - never collide, and a checkout that moves or is renamed just
    misses its old entries instead of silently reading someone else's.
    """
    real = str(pathlib.Path(repo_root).resolve())
    digest = hashlib.sha256(real.encode()).hexdigest()[:16]
    return _cache_root(env) / digest


def _scan_tree(repo_root: str) -> tuple[str, int]:
    """The key (identity) and the latest mtime seen (freshness), from one walk.

    Identity is (version, settings module, every file's relative path and size) - nothing a
    build could touch without changing what it produces. Freshness is judged separately, by
    the caller, against a cache entry's own mtime; see the module docstring for why.
    """
    digest = hashlib.sha256()
    digest.update(_version().encode())
    digest.update(os.environ.get("DJANGO_SETTINGS_MODULE", "").encode())
    root = pathlib.Path(repo_root)
    latest_mtime_ns = 0
    for current, directories, files in os.walk(root):
        directories[:] = sorted(d for d in directories if d not in _SKIP and not d.startswith("."))
        for name in sorted(files):
            path = pathlib.Path(current) / name
            try:
                info = path.stat()
            except OSError:
                continue
            digest.update(str(path.relative_to(root)).encode())
            digest.update(str(info.st_size).encode())
            latest_mtime_ns = max(latest_mtime_ns, info.st_mtime_ns)
    return digest.hexdigest()[:32], latest_mtime_ns


def stamp(repo_root: str) -> str:
    """The key: the file tree's shape, the tool version, and the settings module.

    Not the effective config - see the module docstring for why - and not mtime, which is a
    freshness question answered separately by `cached_scan`, not an identity one answered
    here.
    """
    key, _ = _scan_tree(repo_root)
    return key


def _path(repo_root: str, key: str, env: dict | None = None) -> pathlib.Path:
    return _repo_cache_dir(repo_root, env) / f"{key}.json"


def _evict_oldest(directory: pathlib.Path, keep: int = _KEEP_PER_REPO) -> None:
    """Keep at most `keep` cache files in this repository's directory, oldest out first."""
    try:
        entries = sorted(directory.glob("*.json"), key=lambda p: p.stat().st_mtime_ns)
    except OSError:
        return
    for stale in entries[:-keep] if len(entries) > keep else []:
        with contextlib.suppress(OSError):
            stale.unlink()


def cached_scan(repo_root: str, *, refresh: bool = False) -> tuple[Graph, dict]:
    """The graph, from memory, from disk, or from a real scan - and which of the three."""
    from seamcheck import api

    key, latest_input_mtime_ns = _scan_tree(repo_root)
    memo_key = f"{repo_root}\0{key}"

    if not refresh:
        memoized = _MEMO.get(memo_key)
        if memoized is not None:
            graph, cached_at_ns = memoized
            if latest_input_mtime_ns < cached_at_ns:
                return graph, {"cached": True, "key": key, "seconds": 0.0, "from": "memory"}

    path = _path(repo_root, key)
    if not refresh and path.is_file():
        try:
            cache_mtime_ns = path.stat().st_mtime_ns
        except OSError:
            cache_mtime_ns = None
        if cache_mtime_ns is not None and latest_input_mtime_ns < cache_mtime_ns:
            try:
                graph = graph_from_dict(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                graph = None
            if graph is not None:
                _MEMO[memo_key] = (graph, cache_mtime_ns)
                return graph, {"cached": True, "key": key, "seconds": 0.0, "from": "disk"}

    started = time.monotonic()
    graph = api.scan(repo_root)
    took = round(time.monotonic() - started, 2)
    cached_at_ns = time.time_ns()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(graph_to_dict(graph)), encoding="utf-8")
        cached_at_ns = path.stat().st_mtime_ns  # the entry's real, comparable timestamp
        _evict_oldest(path.parent)
    except OSError:
        pass  # a read-only or missing cache directory still gets the process memo
    _MEMO[memo_key] = (graph, cached_at_ns)
    return graph, {"cached": False, "key": key, "seconds": took, "from": "scan"}


def clear(repo_root: str = "") -> None:
    """Forget everything in memory, or one repository's memory AND its disk cache.

    No argument sweeps only the process memo - there is no single global disk root to walk
    once every repository keeps its own hashed slot under `_cache_root()`, and a machine-wide
    disk wipe was never asked for. With a `repo_root`, both layers are actually emptied: the
    memo entries for it, and the cache directory itself - a caller that clears and then reads
    again, even in a fresh process, gets a real scan, not whatever was left on disk.
    """
    if not repo_root:
        _MEMO.clear()
        return
    for memo_key in [k for k in _MEMO if k.startswith(f"{repo_root}\0")]:
        del _MEMO[memo_key]
    directory = _repo_cache_dir(repo_root)
    if not directory.is_dir():
        return
    for entry in directory.glob("*.json"):
        with contextlib.suppress(OSError):
            entry.unlink()
    with contextlib.suppress(OSError):
        directory.rmdir()  # not empty (a concurrent write raced us), or already gone either way
