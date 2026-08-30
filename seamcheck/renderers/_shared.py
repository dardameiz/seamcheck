"""Formatting shared by every renderer - one canonical implementation per concern.

terminal.py and html.py had byte-for-byte identical `_where()` copies, and markdown.py
had a third that only differed by wrapping the result in backticks. Put it here once;
a renderer that wants markup around it (markdown's backticks) wraps the call site
instead of forking the function.
"""

from __future__ import annotations

import os


def where(symbol) -> str:
    """"file:line", "file" when there is no line, or "" when there is no file at all.

    `symbol.file` arrives in whichever form its extractor happened to build it in -
    repo-relative ("pointless/static/x.js"), relative with a leading "./" (os.path.join
    with "."), or absolute (the entry-point extractor's os.path.abspath()). Three
    spellings of the same location is already a readability bug; an absolute path is
    worse, since it leaks the machine's directory layout into a document the README
    tells people to publish. os.path.relpath() collapses all three to one repo-relative
    form when rendered from the repo root - the normal way both the CLI and the MCP
    server are run.
    """
    if not symbol.file:
        return ""
    path = os.path.relpath(symbol.file)
    return f"{path}:{symbol.line}" if symbol.line else path
