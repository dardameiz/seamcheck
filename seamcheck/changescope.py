"""Which page(s) and feature(s) a git scope of changed files belongs to.

Built for the moment right before a commit or a push: "what am I touching, and does
seamcheck see anything wrong with it" - without reading the whole repo's findings, and
without the reader having to know or type a page name.

Two scopes:
  - "commit" - files staged right now, about to become one commit. Usually one page.
  - "push"   - every file across commits not yet pushed to the upstream branch. Can span
    several pages - a working session commonly touches more than one area before a push.

Page membership is read from `api.page_files()` - the SAME file-to-page mapping a
generated map is built from - so "seamcheck thinks this touched Push Arena" can never
disagree with what the map actually shows for the same repo state. Feature membership is
read off `symbol.sub`'s trailing `[Feature Name]` suffix, which every symbol already
carries after a normal scan (see pipeline._with_feature_labels) - nothing here recomputes
that walk, it only reads what a scan already wrote down.
"""

from __future__ import annotations

import re
import subprocess

from seamcheck.graph import Graph

_FEATURE_SUFFIX_RE = re.compile(r"\[([^\[\]]+)\]\s*$")


class NoUpstreamError(Exception):
    """"push" scope needs an upstream branch to diff against, and none is configured."""


def _git(args: list[str], repo_root: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=repo_root, capture_output=True, text=True, check=True,
    )
    return result.stdout


def changed_files(repo_root: str, scope: str) -> list[str]:
    """Repo-relative paths changed in `scope` ("commit" or "push"), newest-git-status-first.

    "commit": `git diff --cached --name-only` - exactly what `git commit` is about to
    record, so this answers the question at the moment it matters, before the commit
    exists to diff against.

    "push": every file touched in commits not yet on the upstream branch. Raises
    NoUpstreamError rather than guessing at a base branch name - a wrong guess ("origin/
    main" on a repo whose trunk is "development", say) would silently answer the wrong
    question, which is worse than refusing to answer at all.
    """
    if scope == "commit":
        output = _git(["diff", "--cached", "--name-only"], repo_root)
    elif scope == "push":
        try:
            _git(
                ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"],
                repo_root,
            )
        except subprocess.CalledProcessError as exc:
            raise NoUpstreamError(
                "no upstream branch configured for the current branch - 'push' scope has "
                "nothing to compare against. Set one with `git push -u <remote> <branch>` "
                "or pass an explicit base with --since."
            ) from exc
        output = _git(["diff", "@{upstream}...HEAD", "--name-only"], repo_root)
    else:
        raise ValueError(f"unknown scope {scope!r}; expected 'commit' or 'push'")
    return [line for line in output.splitlines() if line]


def pages_touched(changed: list[str], pages: dict[str, set[str]]) -> dict[str, set[str]]:
    """Page key -> the subset of `changed` that lands in it, for every page with a hit.

    A file can belong to more than one page (a shared module several entries import), so
    this is a many-to-many answer, not a single owner per file - the same file showing up
    under two pages is real, not a bug to resolve here.
    """
    changed_set = set(changed)
    hits = {
        page: files & changed_set
        for page, files in pages.items()
        if files & changed_set
    }
    return dict(sorted(hits.items()))


def features_touched(graph: Graph, changed: set[str]) -> set[str]:
    """Distinct feature labels carried by symbols whose file is in `changed`.

    Reads the `[Feature Name]` suffix `pipeline._with_feature_labels` already appended to
    `symbol.sub` for every symbol reachable from an interactive root - it does not walk
    the graph again. A symbol with no such suffix (most of them: only DOM-reachable code
    gets one) contributes nothing, which is correct - it has no feature to report.
    """
    features: set[str] = set()
    for symbol in graph.symbols:
        if symbol.file not in changed:
            continue
        match = _FEATURE_SUFFIX_RE.search(symbol.sub or "")
        if match:
            features.add(match.group(1))
    return features
