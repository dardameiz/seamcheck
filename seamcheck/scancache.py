"""One scan, remembered, so asking a second question is cheap.

Measured before this existed: every command and every MCP tool called `api.scan` afresh, so
five explanations on the reference project were five 90-second scans - and a mistyped symbol
id cost the same 88.5 seconds as a correct one, to be told the id was wrong.

The graph is a pure function of (the files, the tool version, the config), so the key is a
hash of exactly those three. Two layers: a process memo, for an MCP session answering
question after question, and a file under `.seamcheck/cache/`, for the next command in the
same shell. Both are invalidated by the same key, so neither can serve a stale answer.
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import time

from seamcheck.graph import Graph, graph_from_dict, graph_to_dict

_MEMO: dict[str, Graph] = {}
_CACHE_DIR = ".seamcheck/cache"
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


def stamp(repo_root: str) -> str:
    """The key: every input file's path, size and mtime, plus the version and the config.

    Size and mtime rather than content: hashing a 100M-line repository to decide whether to
    scan it would cost more than the scan. The pair is what every build tool trusts, and a
    tree that changes without either changing is a tree somebody is lying about.
    """
    digest = hashlib.sha256()
    digest.update(_version().encode())
    root = pathlib.Path(repo_root)
    for current, directories, files in os.walk(root):
        directories[:] = sorted(d for d in directories if d not in _SKIP and not d.startswith("."))
        for name in sorted(files):
            path = pathlib.Path(current) / name
            try:
                info = path.stat()
            except OSError:
                continue
            digest.update(str(path.relative_to(root)).encode())
            digest.update(f"{info.st_size}:{info.st_mtime_ns}".encode())
    return digest.hexdigest()[:32]


def _path(repo_root: str, key: str) -> pathlib.Path:
    return pathlib.Path(repo_root) / _CACHE_DIR / f"{key}.json"


def cached_scan(repo_root: str, *, refresh: bool = False) -> tuple[Graph, dict]:
    """The graph, from memory, from disk, or from a real scan - and which of the three."""
    from seamcheck import api

    key = stamp(repo_root)
    memo_key = f"{repo_root}\0{key}"
    if not refresh and memo_key in _MEMO:
        return _MEMO[memo_key], {"cached": True, "key": key, "seconds": 0.0, "from": "memory"}

    path = _path(repo_root, key)
    if not refresh and path.is_file():
        try:
            graph = graph_from_dict(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            graph = None
        if graph is not None:
            _MEMO[memo_key] = graph
            return graph, {"cached": True, "key": key, "seconds": 0.0, "from": "disk"}

    started = time.monotonic()
    graph = api.scan(repo_root)
    took = round(time.monotonic() - started, 2)
    _MEMO[memo_key] = graph
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(graph_to_dict(graph)), encoding="utf-8")
    except OSError:
        pass  # a read-only checkout still gets the process memo
    return graph, {"cached": False, "key": key, "seconds": took, "from": "scan"}


def clear(repo_root: str = "") -> None:
    """Forget everything, or everything for one repository."""
    if not repo_root:
        _MEMO.clear()
        return
    for memo_key in [k for k in _MEMO if k.startswith(f"{repo_root}\0")]:
        del _MEMO[memo_key]
