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

logger = logging.getLogger(__name__)

# Parsers run once per batch of files, so a missing Node printed the same line ninety
# times in one scan. The fact is about the parser, not about the batch.
_reported: set[str] = set()


def _report(what: str, message: str, *args) -> None:
    if what in _reported:
        return
    _reported.add(what)
    logger.warning(message, *args)


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
