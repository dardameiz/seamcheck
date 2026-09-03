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


# HTML reads an id without any JavaScript, and until now none of these counted as a read.
# Measured on Sentry: `<a href="#create" data-toggle="tab">` sits four lines from the
# element it targets, in the same file, and `id="create"` was still reported unused.
#
# Two shapes. One carries a `#`, because it is a URL fragment; the other is a bare id,
# because the attribute's whole purpose is to name one. Getting that backwards silently
# matches nothing.
_FRAGMENT_ATTRS = frozenset({
    "href",                                    # <a href="#panel">
    "data-target", "data-bs-target",           # Bootstrap 4 / 5 toggles
    "data-bs-parent", "data-parent",           # accordions
    "xlink:href",                              # <use xlink:href="#icon">
})
_BARE_ID_ATTRS = frozenset({
    "for",                                     # <label for="field">
    "form",                                    # <input form="the-form">
    "list",                                    # <input list="suggestions">
    "aria-controls", "aria-labelledby", "aria-describedby", "aria-owns",
    "aria-flowto", "aria-details", "aria-errormessage",
    "popovertarget", "commandfor",             # modern HTML, no JS involved
    "headers",                                 # <td headers="col1 col2">
})


# A separate pass. _ATTRIBUTE_RE is deliberately narrow - it defines what a DEFINITION
# looks like - and widening it to catch reads would change what counts as a dom_attr.
_ID_REF_RE = re.compile(
    r"""\b(""" + "|".join(
        re.escape(name) for name in sorted(_FRAGMENT_ATTRS | _BARE_ID_ATTRS)
    ) + r""")\s*=\s*(?:"([^"]*)"|'([^']*)')"""
)


def _id_references(attribute: str, value: str) -> list[str]:
    """Ids this attribute READS. Several of them are space-separated lists."""
    attribute = attribute.lower()
    cleaned = _INTERPOLATION_RE.sub(_EXPRESSION_MARK, _replace_block_tags(value or ""))
    out: list[str] = []
    if attribute in _FRAGMENT_ATTRS:
        # Only a same-document fragment. `href="/page#x"` targets another document's id,
        # and `href="#"` targets nothing at all.
        if not cleaned.startswith("#") or len(cleaned) < 2:
            return []
        out = [cleaned[1:]]
    elif attribute in _BARE_ID_ATTRS:
        out = cleaned.split()
    return [name for name in out if name and _EXPRESSION_MARK not in name]


def _line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


# A `<script>` block is not markup. Its string literals routinely NAME attributes -
# `['daily_hours_active', 'data-modal-daily-hours']` is a mapping table, not an element -
# and reading them here invented an attribute at that line and then reported it unused.
# Measured on the reference project: a phantom `dom_attr` for every such string, each one
# an `unused` finding about an element that does not exist, sitting next to the real
# attribute in the same file. The script's own contents are read by the JavaScript
# extractors, which know a string from an element; blanked here rather than skipped, so
# every line number after a script block still lands where it did.
_SCRIPT_OR_STYLE_RE = re.compile(
    r"(<(script|style)\b[^>]*>)(.*?)(</\2\s*>)", re.IGNORECASE | re.DOTALL
)


def _without_code_blocks(text: str) -> str:
    def blank(match: re.Match) -> str:
        body = match.group(3)
        # Newlines kept, everything else spaced out: the regex below is line-accurate and
        # a shorter replacement would move every attribute after the first script.
        return match.group(1) + re.sub(r"[^\n]", " ", body) + match.group(4)

    return _SCRIPT_OR_STYLE_RE.sub(blank, text)


def scan_templates(template_files: list[str]) -> list[Symbol]:
    symbols: list[Symbol] = []
    for file_path in template_files:
        raw = pathlib.Path(file_path).read_text(encoding="utf-8", errors="replace")
        text = _without_code_blocks(raw)
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

        # Ids the markup READS. Evidence, never a claim: a fragment can legitimately point
        # at an element some script creates later, so "nothing defines it" is not a
        # conclusion this scan can reach.
        for match in _ID_REF_RE.finditer(text):
            attribute = match.group(1)
            value = match.group(2) if match.group(2) is not None else match.group(3)
            line = _line_of(text, match.start())
            for target in _id_references(attribute, value):
                symbols.append(
                    Symbol(
                        id=f"dom_selector:id:{target}:{file_path}:{line}",
                        kind="dom_selector", label=target, sub="id:evidence",
                        file=file_path, line=line, status=Status.UNCERTAIN,
                        snippet=f'{attribute}="{value}"',
                        chain=[pathlib.Path(file_path).name, target],
                        note="Referenced from the markup itself - an anchor, a toggle, a "
                             "label or an ARIA relationship. Evidence that the id is live.",
                    )
                )
    return symbols
