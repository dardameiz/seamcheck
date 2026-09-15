"""CSS facts: which selectors are defined, which tokens are declared, which are used."""

from __future__ import annotations

import json
import os
import pathlib
import re
from dataclasses import replace

from seamcheck.graph import Status, Symbol
from seamcheck.nodetools import parser_path, report, run_parser

_FALLBACK_NOTE = (
    "Resolves to the fallback written into the var() call; no definition is required. "
    "Only a bare var(--x) with no definition renders nothing."
)

_CSS_TOOLS = os.path.join(os.path.dirname(__file__), os.pardir, "css_tools")
# Tailwind escapes variant separators in the compiled CSS (`.md\:flex`, `.w-1\/2`)
# while templates write them bare. Capturing only [\w-] stops at the backslash and
# yields "md", which matches no template class.
_SELECTOR_TOKEN_RE = re.compile(r"([#.])((?:\\.|[\w-])+)")
_CSS_ESCAPE_RE = re.compile(r"\\(.)")
# `[data-state="open"] { ... }` styles an element BY that attribute, which is a use of it.
# The selector reader only ever matched # and . tokens, so 27 attribute selectors on the
# project measured were invisible and the attributes they style looked unread.
_ATTRIBUTE_SELECTOR_RE = re.compile(r"\[\s*data-([\w-]+)")
# `content: attr(data-x)` RENDERS the attribute's value through generated content - a read,
# same class of evidence as an attribute selector, and one this reader never looked for at
# all: B1 (docs/seamcheck-findings-from-leanos.md). Scoped to a `content:` declaration
# specifically (stopping at `;`/`{`/`}`) so a `content: "data-tip"` STRING - the literal
# text, not the function call - is never mistaken for a read of the attribute.
_CONTENT_ATTR_RE = re.compile(r"content\s*:[^;{}]*?\battr\(\s*data-([\w-]+)\s*\)", re.I)


def parse_css_files(css_files: list[str], *, report_failures: bool = True) -> list[dict]:
    """Parse each stylesheet. `report_failures=False` when the caller has better names."""
    existing = [path for path in css_files if os.path.isfile(path)]
    if not existing:
        return []
    records = [
        json.loads(line)
        for line in run_parser(parser_path(_CSS_TOOLS, "parse_css"), existing, "CSS")
    ]
    unreadable = [r.get("path", "?") for r in records if "error" in r]
    if unreadable and report_failures:
        shown = ", ".join(os.path.basename(path) for path in unreadable[:3])
        report(
            "css-parse-failures",
            "%s CSS file(s) could not be parsed and contributed no rules (%s%s). Selectors "
            "defined in them will look undefined, and classes they style will look unused.",
            len(unreadable), shown, ", ..." if len(unreadable) > 3 else "",
        )
    return records


def _symbol(kind: str, label: str, sub: str, path: str, line, snippet: str) -> Symbol:
    return Symbol(
        id=f"{kind}:{sub}:{label}" if kind != "css_selector" else f"css_selector:{sub}:{label}",
        kind=kind, label=label, sub=sub, file=path, line=line,
        status=Status.UNCERTAIN, snippet=snippet, chain=[os.path.basename(path), label], note="",
    )


def _record_symbols(
    record: dict, path: str, seen: set[str], *, line_offset: int = 0
) -> list[Symbol]:
    """Selectors, token definitions and token uses out of one parsed stylesheet record.

    Shared between a standalone .css file and a template's inline <style> block: a
    `:root { --accent: ... }` or a `var(--accent)` means exactly the same thing in
    either place, and reading only `selectors` out of a template's block - as
    `extract_template_css` used to - left every token DEFINED or READ there invisible.
    B3 (docs/seamcheck-findings-from-leanos.md): a token set only from JavaScript
    (`element.style.setProperty(...)`) then looked unused, because its CSS-side reads,
    sitting in a template's <style> block, were never symbols to link it to at all.
    """
    symbols: list[Symbol] = []

    def _line(raw) -> int | None:
        return (raw + line_offset) if raw else raw

    for rule in record.get("selectors", []):
        for marker, raw_name in _SELECTOR_TOKEN_RE.findall(rule["selector"]):
            name = _CSS_ESCAPE_RE.sub(r"\1", raw_name)
            sub = "id" if marker == "#" else "class"
            symbol = _symbol("css_selector", name, sub, path, _line(rule["line"]), rule["selector"])
            if symbol.id not in seen:
                seen.add(symbol.id)
                symbols.append(symbol)
    for definition in record.get("tokenDefs", []):
        symbol = _symbol(
            "css_token_def", definition["name"], "token", path, _line(definition["line"]),
            f"{definition['name']}: ...",
        )
        if symbol.id not in seen:
            seen.add(symbol.id)
            symbols.append(symbol)
    for use in record.get("tokenUses", []):
        # A use that carries its own fallback is a different symbol from one that
        # demands a definition, and must not share an id with it: 53 of this
        # project's 63 "undefined token" findings were the fallback form.
        fallback = bool(use.get("fallback"))
        symbol = _symbol(
            "css_token_use", use["name"],
            "token-fallback" if fallback else "token", path, _line(use["line"]),
            f"var({use['name']}, ...)" if fallback else f"var({use['name']})",
        )
        if fallback:
            symbol = replace(symbol, note=_FALLBACK_NOTE)
        if symbol.id not in seen:
            seen.add(symbol.id)
            symbols.append(symbol)
    return symbols


def extract_css(css_files: list[str]) -> list[Symbol]:
    symbols: list[Symbol] = []
    seen: set[str] = set()

    for record in parse_css_files(css_files):
        symbols += _record_symbols(record, record["path"], seen)
    return symbols


_STYLE_BLOCK_RE = re.compile(r"<style[^>]*>(.*?)</style>", re.S | re.I)

# A template's <style> block is not necessarily CSS. On the reference project one block
# carried a {% comment %} explaining why some furniture is hidden, and postcss refused the
# whole block over it - so every selector in it looked undefined and every class it styled
# looked unused. Same failure the inline <script> path had, same fix: blank the template
# syntax, keep the line count, so a rule's line still points at the template.
# The prose BETWEEN {% comment %} and {% endcomment %} has to go with the tags. Blanking
# only the tags leaves an English paragraph sitting in the middle of a stylesheet.
_DJANGO_COMMENT_RE = re.compile(r"\{%\s*comment\s*%\}.*?\{%\s*endcomment\s*%\}", re.S | re.I)
_DJANGO_TAG_RE = re.compile(r"\{%.*?%\}", re.S)
_DJANGO_VAR_RE = re.compile(r"\{\{.*?\}\}", re.S)


def _blank(match: re.Match) -> str:
    return "".join(character if character == "\n" else " " for character in match.group(0))


def _neutralise_css(block: str) -> str:
    """Template tags blanked; interpolated values become a literal postcss accepts."""
    blanked = _DJANGO_TAG_RE.sub(_blank, _DJANGO_COMMENT_RE.sub(_blank, block))
    return _DJANGO_VAR_RE.sub(lambda m: "0" + " " * (len(m.group(0)) - 1), blanked)


def extract_template_css(template_files: list[str]) -> list[Symbol]:
    """CSS written inside a template's own <style> block: selectors, and now tokens too.

    Reading only .css files made every element styled that way look like one nothing
    reaches. This project keeps 1,016 class and id selectors in 29 templates' <style>
    blocks - the same order as the whole "nothing reaches it" backlog.

    Token definitions and uses go through the same record now (B3,
    docs/seamcheck-findings-from-leanos.md): this used to read only `selectors` out of
    each parsed block, so a `:root { --token: ... }` or a `var(--token)` living in a
    template's own styles was invisible - the one place a token is set at runtime
    (`element.style.setProperty(...)`) then looked unused, because none of its CSS-side
    reads existed as symbols to link it to.

    The blocks go through the same postcss parse as a stylesheet, so a selector mentioned
    in a comment is still not a selector, and line numbers are mapped back to the
    template rather than to the block.
    """
    import pathlib
    import tempfile

    symbols: list[Symbol] = []
    seen: set[str] = set()
    with tempfile.TemporaryDirectory() as scratch:
        origins: dict[str, tuple[str, int]] = {}
        for index, template in enumerate(sorted(template_files)):
            try:
                source = pathlib.Path(template).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for match in _STYLE_BLOCK_RE.finditer(source):
                block = match.group(1)
                if not block.strip():
                    continue
                # Where the block starts in the template, so a rule's line points at the
                # template a reader would open, not at an offset inside a fragment.
                offset = source.count("\n", 0, match.start(1))
                path = os.path.join(scratch, f"{index}-{offset}.css")
                pathlib.Path(path).write_text(_neutralise_css(block), encoding="utf-8")
                origins[path] = (template, offset)

        # Scratch files are named by index, so the generic message would name "148-70.css".
        # The template is what a reader can open.
        records = parse_css_files(list(origins), report_failures=False)
        unreadable = [origins[r["path"]][0] for r in records if "error" in r]
        if unreadable:
            shown = ", ".join(os.path.basename(name) for name in unreadable[:3])
            report(
                "inline-style-parse-failures",
                "%s inline <style> block(s) could not be parsed (%s%s). Selectors defined "
                "only there will look undefined, and classes they style will look unused.",
                len(unreadable), shown, ", ..." if len(unreadable) > 3 else "",
            )
        for record in records:
            template, offset = origins[record["path"]]
            symbols += _record_symbols(record, template, seen, line_offset=offset)
    # One symbol per name: none of the three kinds' ids carry a line, so duplicates -
    # the same selector or token appearing in more than one block - would collide.
    # _record_symbols already keeps only the first; nothing further to dedupe here.
    return symbols


def css_imports(css_files: list[str]) -> dict[str, list[str]]:
    """Raw @import targets per file, for Task 10's import-graph walk."""
    return {
        record["path"]: [rule["params"].strip("'\" ") for rule in record.get("imports", [])]
        for record in parse_css_files(css_files)
    }


def extract_css_attribute_selectors(css_files: list[str]) -> list[Symbol]:
    """`[data-x]` selectors and `content: attr(data-x)`, as reaches-for-an-element rather
    than as style rules.

    Emitted as dom_selectors so they match template data attributes through the same
    matcher a `querySelector('[data-x]')` goes through - a stylesheet and a script asking
    for the same attribute are the same claim, and deserve the same answer. `attr()`
    inside `content:` is the same claim again, just rendered rather than matched on: two
    components set a data attribute purely so a shared stylesheet could show it this way,
    with nothing reading it back through `getAttribute`/`dataset`/a selector, and both
    looked unread (B1, docs/seamcheck-findings-from-leanos.md).
    """
    symbols: list[Symbol] = []
    seen: set[str] = set()
    for path in css_files:
        try:
            text = pathlib.Path(path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        basename = os.path.basename(path)

        def _add(name: str, line: int, snippet: str, note: str, *, path=path, basename=basename) -> None:
            symbol_id = f"dom_selector:data:{name}:{path}:{line}"
            if symbol_id in seen:
                return
            seen.add(symbol_id)
            symbols.append(
                Symbol(
                    id=symbol_id, kind="dom_selector", label=name, sub="data:css",
                    file=path, line=line, status=Status.UNCERTAIN,
                    snippet=snippet, chain=[basename], note=note,
                )
            )

        for match in _ATTRIBUTE_SELECTOR_RE.finditer(text):
            name = match.group(1)
            _add(name, text.count("\n", 0, match.start()) + 1, f"[data-{name}]",
                 "A stylesheet selects on this attribute. Evidence that it is used; the "
                 "verdict belongs to the attribute, not to this rule.")
        for match in _CONTENT_ATTR_RE.finditer(text):
            name = match.group(1)
            _add(name, text.count("\n", 0, match.start()) + 1, f"content: attr(data-{name})",
                 "A stylesheet renders this attribute's value through generated content. "
                 "Evidence that it is used; the verdict belongs to the attribute, not to "
                 "this rule.")
    return symbols
