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

import io
import logging
import os
import subprocess
import sys
import threading
from collections.abc import Iterator

logger = logging.getLogger(__name__)

# Reported once per parser. These run once per batch of files, and the fact being
# reported is about the parser, not about any one batch.
_reported: set[str] = set()


def report(what: str, message: str, *args) -> None:
    """Tell the user an extractor lost its evidence, once per subject, on stderr.

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
    # Only write our own line when logging cannot: the CLI runs inside quiet(), which
    # disables WARNING, and printing unconditionally would say everything twice under -v.
    if logging.root.manager.disable < logging.WARNING:
        return
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


def run_parser(script: str, paths: list[str], what: str) -> Iterator[str]:
    """Feed newline-separated paths to a parser, yield its NDJSON lines as they arrive.

    Streamed, not collected. The parser answers with one record per file and a record is
    the file's whole syntax tree; holding the run's output as one string before reading
    a line of it cost 2.6 GB on a 21,000-file monorepo, on top of the trees built from
    it. Read this way, a line is parsed and dropped before the next one lands. A parser
    that dies part-way still yields every record it finished: the files it did read are
    not lost to the ones it did not.
    """
    try:
        process = subprocess.Popen(
            ["node", script], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except FileNotFoundError:
        _report(what, "seamcheck: no %s symbols - Node.js is not on PATH. Every other "
                      "extractor still ran.", what)
        return

    # The parser reads all of stdin before it writes, so the path list is handed over on
    # its own thread: past the pipe's buffer, a single-threaded write would wait on a
    # reader that is itself waiting on us.
    def feed() -> None:
        try:
            process.stdin.write("\n".join(paths).encode("utf-8"))
            process.stdin.close()
        except (BrokenPipeError, OSError):
            pass  # the parser is gone; its exit code says so below

    stderr_text: list[bytes] = []
    feeder = threading.Thread(target=feed, daemon=True)
    drainer = threading.Thread(target=lambda: stderr_text.append(process.stderr.read()), daemon=True)
    feeder.start()
    drainer.start()
    # split on "\n" only, never splitlines(): Python also breaks on U+2028 and U+2029,
    # which are legal unescaped inside a JSON string and appear verbatim in real
    # JavaScript source. One of them in one file of a 6,378-file repository cut a record
    # in half and made the whole run unparseable.
    records = 0
    with io.TextIOWrapper(process.stdout, encoding="utf-8", errors="replace", newline="\n") as out:
        for line in out:
            line = line.rstrip("\n")
            if line:
                records += 1
                yield line
    feeder.join()
    drainer.join()
    if process.wait() != 0:
        stderr = b"".join(stderr_text).decode("utf-8", errors="replace").strip()
        _report(what, "seamcheck: %s %s symbols - %s exited %s%s. Every other extractor "
                      "still ran. %s", "incomplete" if records else "no", what,
                os.path.basename(script), process.returncode,
                f" after {records} file(s)" if records else "", stderr.splitlines()[-1:] or "")


def node_line(node) -> int | None:
    """The line an AST node starts on, whichever shape `loc` arrived in.

    parse_js emits `loc` as `[start_line, end_line]` - the only two numbers anything
    here reads, in place of the three dicts and four ints ESTree carries per node, which
    were most of the 1.2 GB the reference project's ASTs took. A hand-built tree in a
    test may still use the ESTree dict, so both are read.
    """
    loc = node.get("loc") if isinstance(node, dict) else None
    if isinstance(loc, list):
        return loc[0] if loc else None
    if isinstance(loc, dict):
        return (loc.get("start") or {}).get("line")
    return None


def node_end_line(node) -> int | None:
    loc = node.get("loc") if isinstance(node, dict) else None
    if isinstance(loc, list):
        return loc[1] if len(loc) > 1 else None
    if isinstance(loc, dict):
        return (loc.get("end") or {}).get("line")
    return None
