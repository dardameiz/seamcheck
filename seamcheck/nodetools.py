"""Running the bundled Node parsers.

`pip install seamcheck` puts the package in site-packages, where the parsers' `import
acorn` and `import postcss` resolve against nothing: the only reason they ever worked is
that the project they were developed in happened to have a node_modules beside them. So
each parser ships pre-bundled with its dependency inlined, and the plain source file is
kept for development against the repo's own node_modules.

Failure is reported, never raised. A parser that cannot run costs the scan its JavaScript
or its CSS - about half the graph - and a scan that dies takes the other half with it,
which is strictly worse than one that says what it lost.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys

logger = logging.getLogger(__name__)

# Reported once per parser. These run once per batch of files, and the fact being
# reported is about the parser, not about any one batch.
_reported: set[str] = set()


def report(what: str, message: str, *args) -> None:
    """Tell the user a parser failed, once per subject, on stderr.

    Not only via `logging`: the CLI runs the entire scan inside `quiet()`, which disables
    WARNING-level logging so the host project's start-up noise stays out of the output.
    That is right for someone else's warnings and catastrophic for our own - a scan that
    silently produced zero JavaScript symbols looks exactly like a clean one, and a tool
    whose whole claim is that it never asserts more than its evidence cannot ship a
    failure mode where missing evidence reads as a pass.

    stdout carries the data, so diagnostics go to stderr and stay pipeable either way.
    """
    if what in _reported:
        return
    _reported.add(what)
    logger.warning(message, *args)
    try:
        print("seamcheck: " + (message % args if args else message), file=sys.stderr)
    except (TypeError, ValueError):  # a malformed format string must not kill the scan
        print("seamcheck: " + message, file=sys.stderr)


# Kept for callers inside this module; `report` is the name other modules use.
_report = report


def parser_path(directory: str, name: str) -> str:
    """The bundled parser if it shipped, else the source next to it."""
    bundled = os.path.join(directory, f"{name}.bundle.mjs")
    return bundled if os.path.isfile(bundled) else os.path.join(directory, f"{name}.mjs")


def run_parser(script: str, paths: list[str], what: str) -> list[str]:
    """Feed newline-separated paths to a parser, return its NDJSON lines."""
    try:
        result = subprocess.run(
            ["node", script], input="\n".join(paths),
            capture_output=True, text=True, check=False,
        )
    except FileNotFoundError:
        _report(what, "seamcheck: no %s symbols - Node.js is not on PATH. Every other "
                      "extractor still ran.", what)
        return []
    if result.returncode != 0:
        _report(what, "seamcheck: no %s symbols - %s exited %s. Every other extractor "
                      "still ran. %s", what, os.path.basename(script), result.returncode,
                (result.stderr or "").strip().splitlines()[-1:] or "")
        return []
    return result.stdout.splitlines()
