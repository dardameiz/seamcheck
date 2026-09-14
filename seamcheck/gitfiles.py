"""What `git` itself says this repository actually contains.

`.gitignore` is not simple text to re-parse: nested `.gitignore` files, negation
(`!keep.js`), globs and directory-only patterns all combine to decide whether one path
counts - and a project's own conventions lean on exactly that. `OTHER/` here holds one-off
scripts by project convention (`.gitignore: OTHER/`), and on the reference project a
one-off Playwright probe living there (`OTHER/seo/cards_check.mjs`) was read as a
first-party writer of a DOM element, and a demo seeder plus an archived management-command
folder together produced nine findings that were true of code not in the repository at
all. None of that is the product; asking `git` is the one way to get every rule right
without reimplementing gitignore.

`git ls-files --cached --others --exclude-standard` is exactly that question, answered by
the tool that already enforces it: `--cached` is every tracked file (committed or staged),
`--others --exclude-standard` adds untracked files that are NOT ignored - a brand-new file
mid-edit is real project input the moment it exists, staged or not.
"""

from __future__ import annotations

import subprocess


def tracked_files(repo_root: str) -> frozenset[str] | None:
    """Every path this repo's git considers "in", relative to `repo_root`, POSIX-separated.

    `None` when this is not a git repository, git is not installed, or the command failed
    for any other reason - every caller must treat `None` as "cannot answer" and fall back
    to its own prior (gitignore-blind) behaviour, never as "nothing is tracked". A scanner
    that reads nothing because git was momentarily unavailable is a worse failure than one
    that occasionally reads a gitignored file, which is the failure mode this replaces.
    """
    try:
        result = subprocess.run(
            ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            cwd=repo_root, capture_output=True, timeout=30, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return frozenset(
        entry for entry in result.stdout.decode("utf-8", errors="replace").split("\0") if entry
    )
