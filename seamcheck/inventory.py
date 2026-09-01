"""Every file in the tree, not only the ones the scan had a reader for.

The Files view answered "what did seamcheck read", which is a question about seamcheck.
The question a person actually has in front of a repository they did not write is "what is
all this, and does it still belong here" - and a view that silently omits 34,000 of a
project's 36,000 files cannot be asked it.

Two axes, both evidenced, neither inferred:

**Does git track it.** `git ls-files` is the project's own answer to what belongs, so it
is used rather than guessed at. A file present on disk and untracked is generated, local,
or forgotten - and this repository has 6,047 `.gz` and 2,899 `.br` beside their sources,
which is what that looks like at scale.

**Does anything name it.** For a file no extractor reads - an image, a font, an audio clip
- the honest evidence is whether its name appears anywhere in the text of the project. A
name that appears nowhere is a real signal. A name assembled at runtime (`f"img/{slug}.png"`)
defeats it, so a file whose *folder* is referenced is reported as uncertain rather than
unused: the scan can see the folder being used and cannot see which files that reaches.

Nothing here says "delete this". `unused` keeps the meaning it has everywhere else in
seamcheck: both ends are observable and nothing connects them.
"""

from __future__ import annotations

import os
import pathlib
import re
import subprocess
from collections import defaultdict

from seamcheck.adapters.discovery import SKIP_DIRS

# Extensions worth reading as text when building the index of what names what. Reading a
# 400MB video looking for filenames would cost minutes and find nothing.
TEXT_SUFFIXES = frozenset({
    ".py", ".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx", ".vue", ".svelte",
    ".html", ".htm", ".css", ".scss", ".sass", ".less", ".json", ".yml", ".yaml",
    ".toml", ".ini", ".cfg", ".conf", ".md", ".txt", ".sh", ".env", ".po", ".xml",
    ".svg", ".graphql", ".gql", ".sql", ".rb", ".php", ".go", ".java", ".rs",
})

# Files that exist to be referenced by name rather than parsed.
ASSET_SUFFIXES = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".avif", ".ico", ".bmp",
    ".mp4", ".webm", ".mov", ".mp3", ".m4a", ".wav", ".ogg", ".flac",
    ".woff", ".woff2", ".ttf", ".otf", ".eot", ".pdf", ".zip",
})

# Derived from something else in the tree. Not a finding - a fact about the file.
DERIVED_SUFFIXES = frozenset({".gz", ".br", ".map", ".pyc", ".log", ".mo", ".lock"})

_MAX_TEXT_BYTES = 2_000_000
# Anything that could be a path or a filename inside a string, an import, or a url().
_TOKEN_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.\-/]*")


def _walk(root: pathlib.Path) -> list[pathlib.Path]:
    found: list[pathlib.Path] = []
    for here, subdirectories, names in os.walk(root):
        subdirectories[:] = [
            d for d in subdirectories if d not in SKIP_DIRS and not d.startswith(".")
        ]
        found.extend(pathlib.Path(here) / name for name in names)
    return found


def _tracked(root: pathlib.Path) -> set[str] | None:
    """What git says belongs. None when this is not a git repository - in which case the
    view says so, rather than reporting every file as not belonging."""
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z"],
            capture_output=True, timeout=60, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return {name.decode("utf-8", "replace") for name in out.stdout.split(b"\0") if name}


def _mention_index(paths: list[pathlib.Path], root: pathlib.Path) -> tuple[set[str], set[str]]:
    """Every filename-shaped token the project's text contains, and every folder path.

    One pass. `names` holds both `logo.png` and `logo`, because a stem alone is weaker
    evidence than a full filename and the caller grades them differently.
    """
    names: set[str] = set()
    folders: set[str] = set()
    for path in paths:
        if path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        try:
            if path.stat().st_size > _MAX_TEXT_BYTES:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for token in _TOKEN_RE.findall(text):
            base = token.rsplit("/", 1)[-1]
            if base:
                names.add(base)
                names.add(base.rsplit(".", 1)[0])
            if "/" in token:
                folder = token.rsplit("/", 1)[0].strip("/")
                # Every suffix of the folder path, so "static/img/buttons" also counts as
                # a reference to "img/buttons" - templates and CSS name it both ways.
                parts = folder.split("/")
                for i in range(len(parts)):
                    folders.add("/".join(parts[i:]))
    return names, folders


def _folder_referenced(relative: str, folders: set[str]) -> bool:
    parts = pathlib.PurePosixPath(relative).parent.parts
    return any("/".join(parts[i:]) in folders for i in range(len(parts)))


def build_inventory(root: str, read_paths: set[str]) -> dict:
    """One entry per file on disk, grouped by folder to keep the payload small.

    `read_paths` are the files the scan actually produced symbols from, in whatever form
    the graph recorded them; they are matched by their path relative to the root.
    """
    base = pathlib.Path(root).resolve()
    paths = _walk(base)
    tracked = _tracked(base)
    names, folders = _mention_index(paths, base)

    read_relative = set()
    for path in read_paths:
        try:
            read_relative.add(str(pathlib.Path(path).resolve().relative_to(base)))
        except (OSError, ValueError):
            read_relative.add(path)

    grouped: dict[str, list] = defaultdict(list)
    totals: dict[str, int] = defaultdict(int)
    for path in paths:
        try:
            relative = str(path.relative_to(base))
        except ValueError:
            continue
        suffix = path.suffix.lower()
        posix = relative.replace(os.sep, "/")

        if posix in read_relative or relative in read_relative:
            state = "read"
        elif suffix in DERIVED_SUFFIXES:
            state = "derived"
        elif suffix in ASSET_SUFFIXES or suffix not in TEXT_SUFFIXES:
            if path.name in names:
                state = "named"
            elif _folder_referenced(posix, folders) or path.stem in names:
                state = "maybe"
            else:
                state = "orphan"
        else:
            state = "silent"

        in_git = None if tracked is None else (posix in tracked)
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        folder = posix.rsplit("/", 1)[0] if "/" in posix else ""
        grouped[folder].append([path.name, state, size, 1 if in_git else 0])
        totals[state] += 1
        if in_git is False:
            totals["untracked"] += 1

    return {
        "git": tracked is not None,
        "totals": dict(totals),
        "files": sum(len(v) for v in grouped.values()),
        "folders": [
            {"dir": folder, "files": sorted(entries)}
            for folder, entries in sorted(grouped.items())
        ],
    }
