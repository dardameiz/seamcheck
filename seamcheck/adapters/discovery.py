"""Finding the thing that identifies a framework, when it is not at the repository root.

Every adapter's `detect` began by reading `<repo>/package.json` or importing settings, and
every one of them returned zero on a real large project - because a real large project is a
monorepo. Measured on four:

    immich       @nestjs/core lives in server/package.json      -> detected fastapi, 5 routes
    n8n          express lives in packages/cli/package.json     -> detected nothing, 0 routes
    sentry       ROOT_URLCONF is a line in conf/server.py       -> 0 routes from 1.7M lines
    saleor       same                                            -> 0 routes from 847k lines

Two of those are among the largest open web applications there are, and the tool read
nothing at all from either. Nothing was broken in the readers; they were never told where
to look.
"""

from __future__ import annotations

import fnmatch
import os
import pathlib
import re

# Deep enough for apps/web, packages/@scope/name and server/src; shallow enough not to walk
# a vendored tree. Every layout that failed above sits at three or less.
_MAX_DEPTH = 4

SKIP_DIRS = {
    "node_modules", ".git", "dist", "build", "coverage", ".next", "out", "vendor",
    "site-packages", "__pycache__", ".venv", "venv", ".tox", ".cache", "bower_components",
    "target", "tmp", "temp", "fixtures", "__fixtures__",
    # collectstatic output. A COPY of the source tree, so every file in it is a second
    # spelling of a file already read - which made `staticfiles/…/stats_manager.js` count
    # as a second writer of everything `pointless/…/stats_manager.js` writes, and turned
    # a build step into findings. Same disease as `./x.js` vs `x.js`, one directory up.
    "staticfiles", "static_collected", "collected_static", "collectstatic",
    # This project's own directory of cloned third-party repositories, and its recall
    # fixtures - tiny projects with bugs planted ON PURPOSE. Scanning itself found all
    # four of them and reported them as its own defects, which is true and useless.
    "corpus", "recall_fixtures",
}

# `ROOT_URLCONF = "sentry.conf.urls"` - a plain assignment in a settings module, which is
# how every Django project on earth declares it, and which needs no import to read.
_ROOT_URLCONF_RE = re.compile(r"""^\s*ROOT_URLCONF\s*=\s*["']([\w.]+)["']""", re.M)


def _walk(repo_root: str, name: str) -> list[pathlib.Path]:
    """Every file called `name` within a few levels, skipping vendored trees.

    os.walk with pruning, not glob. A depth-limited glob still DESCENDS through every
    skipped directory to test the pattern, so pointing it at a checkout that happens to
    contain vendored clones walked millions of lines per call - this ran on every detect()
    and turned a 75-second test suite into a ten-minute one.
    """
    base = pathlib.Path(repo_root)
    found: list[pathlib.Path] = []
    root_depth = len(base.parts)
    for directory, subdirectories, names in os.walk(base):
        here = pathlib.Path(directory)
        if len(here.parts) - root_depth >= _MAX_DEPTH:
            subdirectories[:] = []
        else:
            subdirectories[:] = [
                d for d in subdirectories if d not in SKIP_DIRS and not d.startswith(".")
            ]
        for filename in names:
            if fnmatch.fnmatch(filename, name):
                found.append(here / filename)
    return found


def manifests(repo_root: str) -> list[pathlib.Path]:
    """Every package.json in the repository, root or nested."""
    return _walk(repo_root, "package.json")


def declares(repo_root: str, *names: str) -> bool:
    """Whether any manifest in the repository declares one of these dependencies."""
    for manifest in manifests(repo_root):
        try:
            text = manifest.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if any(f'"{name}"' in text for name in names):
            return True
    return False


def root_urlconf(repo_root: str) -> str | None:
    """The Django URLconf module, read from settings as TEXT rather than imported.

    A cloned repository cannot be imported - that is the entire reason static mode exists -
    so the module that names the URLconf has to be found the same way. Several settings
    files may assign it (base.py, production.py); they nearly always agree, and where they
    do not, the first by path order is used rather than guessing at inheritance.
    """
    seen: list[str] = []
    for path in _walk(repo_root, "*.py"):
        name = path.name
        if not (name.startswith("settings") or name in ("server.py", "base.py", "common.py")
                or "settings" in path.parts):
            continue
        try:
            match = _ROOT_URLCONF_RE.search(path.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
        if match and match.group(1) not in seen:
            seen.append(match.group(1))
    return seen[0] if seen else None


def module_file(repo_root: str, module: str) -> str | None:
    """The file a dotted module name refers to, allowing for a src/ layout."""
    relative = module.replace(".", "/")
    base = pathlib.Path(repo_root)
    for prefix in ("", "src", "lib", "app", "backend"):
        root = base / prefix if prefix else base
        for candidate in (root / f"{relative}.py", root / relative / "__init__.py"):
            if candidate.is_file():
                return str(candidate)
    return None
