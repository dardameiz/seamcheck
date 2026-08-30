"""Formatting shared by every renderer - one canonical implementation per concern.

terminal.py and html.py had byte-for-byte identical `_where()` copies, and markdown.py
had a third that only differed by wrapping the result in backticks. Put it here once;
a renderer that wants markup around it (markdown's backticks) wraps the call site
instead of forking the function.
"""

from __future__ import annotations


def where(symbol) -> str:
    """"file:line", "file" when there is no line, or "" when there is no file at all."""
    if not symbol.file:
        return ""
    return f"{symbol.file}:{symbol.line}" if symbol.line else symbol.file
