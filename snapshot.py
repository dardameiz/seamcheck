"""Scan results on disk, keyed by the commit they describe."""

from __future__ import annotations

import json
import pathlib
import subprocess

from signal_map.graph import Graph, graph_from_dict, graph_to_dict

_SCANS_DIR = pathlib.Path("OTHER") / "signal-map" / "scans"


def current_git_sha(repo_root: str) -> str:
    result = subprocess.run(
        ["git", "-C", repo_root, "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


def _snapshot_path(sha: str, repo_root: str) -> pathlib.Path:
    return pathlib.Path(repo_root) / _SCANS_DIR / f"{sha}.json"


def save_snapshot(graph: Graph, sha: str, repo_root: str) -> str:
    path = _snapshot_path(sha, repo_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(graph_to_dict(graph), indent=2), encoding="utf-8")
    return str(path)


def load_snapshot(sha: str, repo_root: str) -> Graph | None:
    path = _snapshot_path(sha, repo_root)
    if not path.is_file():
        return None
    return graph_from_dict(json.loads(path.read_text(encoding="utf-8")))
