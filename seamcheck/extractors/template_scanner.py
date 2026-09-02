"""Every id / class / data-* attribute a template puts into the DOM.

A regex over raw template text, deliberately: Django's own Lexer tokenises {% %} and
{{ }} tags and treats HTML as opaque literal text, so it has no notion of an attribute
and would need an HTML parser bolted on anyway. Template-inheritance traversal belongs to
the caller, which passes every file in the chain.
"""

from __future__ import annotations

import pathlib
import re

from seamcheck.graph import Status, Symbol

# A data-* attribute needs no value: `<button data-share-link>` is how a boolean flag is
# written, and requiring `="..."` skipped every one of them - so JavaScript selecting
# [data-share-link] was reported as reaching for an element that does not exist.
_ATTRIBUTE_RE = re.compile(
    r"""\b(id|class|data-[\w-]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'))?"""
)
# Two different things, and treating them alike was a bug in both directions.
#
# `{{ value }}` and `${ x }` INTERPOLATE: `flag-icon-{{ code }}` is one name assembled at
# runtime, and the literal `flag-icon-` is a fragment of it, not a class.
#
# `{% if %}` DELIMITS: `class="ev-watch{% if label %} ev-watch-wide{% endif %}"` renders as
# `ev-watch` or `ev-watch ev-watch-wide`, and `ev-watch` is a whole name either way. Marking
# it a fragment lost 41 classes that are plainly in the markup.
_INTERPOLATION_RE = re.compile(r"\{\{.*?\}\}|\$\{[^}]*\}")
_BLOCK_TAG_RE = re.compile(r"\{%.*?%\}")
# What counts as "still inside a name" on either side of a template tag.
_NAME_CHAR = re.compile(r"[\w-]").match
# A class token is written by a human or a utility framework. Anything carrying JS
# punctuation came from a script block, not from markup.
_NOT_A_CLASS_RE = re.compile(r"""[${}()'"^,;]|^\W+$""")
# Substituted for a template expression instead of a space, so a name BUILT around one is
# still recognisable as a fragment afterwards. Replacing with a space made
# `class="flag-icon flag-icon-{{ code }}"` yield the literal token `flag-icon-`, a class
# that exists in no stylesheet because it exists nowhere - the real class is
# `flag-icon-us`. 38 findings on the project measured were prefixes of names like this.
# \x00 cannot occur in markup and cannot be split on, so the fragment carries the mark.
_EXPRESSION_MARK = "\x00"


def _replace_block_tags(value: str) -> str:
    """A `{% %}` tag separates names, EXCEPT where it joins one.

    Both shapes are real and they need opposite treatment:

        class="ev-watch{% if x %} ev-watch-wide{% endif %}"   two whole names
        class="ad-badge-{% if x %}urgent{% else %}low{% endif %}"   ONE assembled name

    Treating every tag as whitespace was right for the first and wrong for the second,
    where it produced three classes that do not exist - the dangling prefix `ad-badge-`
    and the two branch texts `urgent` and `low`, reported as if a person had written them
    - while the name that DOES exist, `ad-badge-urgent`, went unreported. 83 findings.

    The tell is the characters either side of the tag. Flanked by name characters, the tag
    is inside a name and the whole run is assembled at runtime; otherwise it delimits.
    """
    out, cursor = [], 0
    for match in _BLOCK_TAG_RE.finditer(value):
        before = value[match.start() - 1] if match.start() else ""
        after = value[match.end()] if match.end() < len(value) else ""
        joins = bool(before) and bool(after) and _NAME_CHAR(before) and _NAME_CHAR(after)
        out.append(value[cursor:match.start()])
        out.append(_EXPRESSION_MARK if joins else " ")
        cursor = match.end()
    out.append(value[cursor:])
    return "".join(out)


def _tokens(attribute: str, value: str) -> list[str]:
    # Interpolations become the mark (they are part of one name); block tags separate
    # names unless they sit inside one - see _replace_block_tags.
    cleaned = _INTERPOLATION_RE.sub(_EXPRESSION_MARK, _replace_block_tags(value))
    if attribute == "class":
        return [
            token
            for token in cleaned.split()
            # A token touching an expression is half a name assembled at runtime. Dropped
            # rather than reported: the whole name is unknowable, so nothing about it can
            # be checked, and a prefix is not a class anyone wrote.
            if _EXPRESSION_MARK not in token and not _NOT_A_CLASS_RE.search(token)
        ]
    cleaned = cleaned.strip()
    if not cleaned or _EXPRESSION_MARK in cleaned:
        return []
    return [cleaned]


def _line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def scan_templates(template_files: list[str]) -> list[Symbol]:
    symbols: list[Symbol] = []
    for file_path in template_files:
        text = pathlib.Path(file_path).read_text(encoding="utf-8", errors="replace")
        for match in _ATTRIBUTE_RE.finditer(text):
            attribute = match.group(1)
            value = match.group(2) if match.group(2) is not None else match.group(3)
            if value is None:
                # `id` and `class` with no value say nothing; a bare data-* is the flag.
                if not attribute.startswith("data-"):
                    continue
                value = ""
            kind = "data" if attribute.startswith("data-") else attribute
            label_prefix = attribute[len("data-"):] if kind == "data" else ""
            line = _line_of(text, match.start())

            for token in _tokens("class" if kind == "class" else kind, value):
                label = label_prefix if kind == "data" else token
                if not label:
                    continue
                symbols.append(
                    Symbol(
                        id=f"dom_attr:{kind}:{label}:{file_path}:{line}",
                        kind="dom_attr",
                        label=label,
                        sub=kind,
                        file=file_path,
                        line=line,
                        status=Status.UNCERTAIN,
                        snippet=f'{attribute}="{value}"',
                        chain=[pathlib.Path(file_path).name, label],
                        note="",
                    )
                )
            if kind == "data" and not _tokens("data", value):
                symbols.append(
                    Symbol(
                        id=f"dom_attr:data:{label_prefix}:{file_path}:{line}",
                        kind="dom_attr", label=label_prefix, sub="data", file=file_path,
                        line=line, status=Status.UNCERTAIN, snippet=f'{attribute}="{value}"',
                        chain=[pathlib.Path(file_path).name, label_prefix], note="",
                    )
                )
    return symbols
