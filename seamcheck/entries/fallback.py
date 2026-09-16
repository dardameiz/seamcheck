"""The last resort, run only when every other source found nothing.

A first-party script no other first-party file imports is where something starts, even if
no framework says so. It is listed under its filename and says plainly that no framework
claims it - the same rule pagenames.py follows for a root no template loads: no invented
names.
"""

from __future__ import annotations

import os

from seamcheck.entries.base import Entry
from seamcheck.resolve import norm_path

# The same ceiling api._js_roots puts on a sweep of the whole tree, for the same reason:
# a repository with thousands of scripts and no entry anywhere is rare, and parsing all of
# them turns a map into a wait.
_MAX_FILES = 4000

# How many of those may be offered as pages. saleor-dashboard produced 1,240, which is a
# file listing rather than a page list: nobody opens a picker with four figures in it, and
# the length is itself a claim that these are the project's pages. The cut is by depth
# first, so what survives is what sits nearest the top of the project - a page's entry is
# far more often `src/index.tsx` than five directories down - and the note says what was
# left out rather than letting the list read as the whole answer.
_MAX_ENTRIES = 200

# A file that only ever runs under a test runner is not somewhere a reader starts. 902 of
# saleor-dashboard's 1,240 were these, and a further 6 were the tool configs below.
_TEST_DIRS = frozenset({"test", "tests", "__tests__", "__mocks__", "e2e", "spec", "specs",
                        "cypress", "testing"})
_TEST_MARKERS = (".test.", ".spec.", ".stories.", ".story.", ".cy.")

# `playwright.config.ts`, `jest.config.js`, `next.config.mjs`: a tool's own settings file,
# which nothing imports because the tool reads it, not the product.
_CONFIG_MARKER = ".config."
_BUNDLERS = frozenset({"vite", "webpack", "rollup", "parcel", "rspack", "esbuild", "snowpack"})
_SCRIPT_SUFFIXES = frozenset({"js", "mjs", "cjs", "ts", "mts", "cts"})


def _is_test_file(relative: str) -> bool:
    parts = relative.split("/")
    name = parts[-1]
    return (any(part in _TEST_DIRS for part in parts[:-1])
            or any(marker in name for marker in _TEST_MARKERS)
            or name.startswith("test_"))


def _is_tool_config(relative: str) -> bool:
    return _CONFIG_MARKER in relative.rsplit("/", 1)[-1]


def _bundler_config(root: str) -> str:
    """The bundler config sitting in this repository, if there is one.

    Its presence means the project HAS declared where it starts and this source could not
    read it. That is a fact about the tool, not about the project, and the house rule says
    to state it rather than leave a reader to infer it from a list of loose scripts.
    """
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return ""
    for name in names:
        stem, sep, suffix = name.partition(_CONFIG_MARKER)
        if sep and stem in _BUNDLERS and suffix in _SCRIPT_SUFFIXES:
            return name
    return ""


def _note(bundler: str, total: int) -> str:
    parts = []
    if bundler:
        parts.append(f"a bundler config ({bundler}) declares where this project starts and it "
                     f"could not be read here; this is every script nothing imports, not the "
                     f"project's own page list")
    if total > _MAX_ENTRIES:
        parts.append(f"{total} scripts nothing imports; showing the {_MAX_ENTRIES} nearest "
                     f"the repository root")
    return " · ".join(parts)


class FallbackSource:
    name = "fallback"

    def detect(self, repo_root: str, config: dict) -> float:
        return 0.0

    def entries(self, repo_root: str, config: dict, graph) -> list[Entry]:
        from seamcheck.extractors.js_extractor import build_module_graph
        from seamcheck.extractors.url_reference_extractor import find_js_files

        root = os.path.abspath(repo_root)
        files = sorted(os.path.abspath(path) for path in find_js_files(root))[:_MAX_FILES]
        if not files:
            return []
        imported = set().union(*build_module_graph(root, files).edges.values())
        candidates = [
            relative for relative in (norm_path(path, root) for path in files
                                      if path not in imported)
            if not _is_test_file(relative) and not _is_tool_config(relative)
        ]
        if not candidates:
            return []
        candidates.sort(key=lambda relative: (relative.count("/"), relative))
        note = _note(_bundler_config(root), len(candidates))
        return [Entry(
            key=f"entry_file:{relative}", kind="entry_file", roots=(relative,),
            title=os.path.basename(relative), where="no framework says this is a page",
            evidence="a first-party script no other first-party file imports",
            note=note, label=relative,
        ) for relative in candidates[:_MAX_ENTRIES]]
