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


def parse_css_files(css_files: list[str]) -> list[dict]:
    existing = [path for path in css_files if os.path.isfile(path)]
    if not existing:
        return []
    records = [
        json.loads(line)
        for line in run_parser(parser_path(_CSS_TOOLS, "parse_css"), existing, "CSS")
    ]
    unreadable = [r.get("path", "?") for r in records if "error" in r]
    if unreadable:
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


def extract_css(css_files: list[str]) -> list[Symbol]:
    symbols: list[Symbol] = []
    seen: set[str] = set()

    for record in parse_css_files(css_files):
        path = record["path"]
        for rule in record.get("selectors", []):
            for marker, raw_name in _SELECTOR_TOKEN_RE.findall(rule["selector"]):
                name = _CSS_ESCAPE_RE.sub(r"\1", raw_name)
                sub = "id" if marker == "#" else "class"
                symbol = _symbol("css_selector", name, sub, path, rule["line"], rule["selector"])
                if symbol.id not in seen:
                    seen.add(symbol.id)
                    symbols.append(symbol)
        for definition in record.get("tokenDefs", []):
            symbol = _symbol(
                "css_token_def", definition["name"], "token", path, definition["line"],
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
                "token-fallback" if fallback else "token", path, use["line"],
                f"var({use['name']}, ...)" if fallback else f"var({use['name']})",
            )
            if fallback:
                symbol = replace(symbol, note=_FALLBACK_NOTE)
            if symbol.id not in seen:
                seen.add(symbol.id)
                symbols.append(symbol)
    return symbols


_STYLE_BLOCK_RE = re.compile(r"<style[^>]*>(.*?)</style>", re.S | re.I)


def extract_template_css(template_files: list[str]) -> list[Symbol]:
    """CSS written inside a template's own <style> block.

    Reading only .css files made every element styled that way look like one nothing
    reaches. This project keeps 1,016 class and id selectors in 29 templates' <style>
    blocks - the same order as the whole "nothing reaches it" backlog.

    The blocks go through the same postcss parse as a stylesheet, so a selector mentioned
    in a comment is still not a selector, and line numbers are mapped back to the
    template rather than to the block.
    """
    import pathlib
    import tempfile

    symbols: list[Symbol] = []
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
                pathlib.Path(path).write_text(block, encoding="utf-8")
                origins[path] = (template, offset)

        for record in parse_css_files(list(origins)):
            template, offset = origins[record["path"]]
            for rule in record.get("selectors", []):
                line = (rule["line"] + offset) if rule["line"] else None
                for marker, raw_name in _SELECTOR_TOKEN_RE.findall(rule["selector"]):
                    name = _CSS_ESCAPE_RE.sub(r"\1", raw_name)
                    symbols.append(_symbol(
                        "css_selector", name, "id" if marker == "#" else "class",
                        template, line, rule["selector"],
                    ))
    # One symbol per selector name: the id carries no line, so duplicates would collide.
    return list({symbol.id: symbol for symbol in symbols}.values())


def css_imports(css_files: list[str]) -> dict[str, list[str]]:
    """Raw @import targets per file, for Task 10's import-graph walk."""
    return {
        record["path"]: [rule["params"].strip("'\" ") for rule in record.get("imports", [])]
        for record in parse_css_files(css_files)
    }


def extract_css_attribute_selectors(css_files: list[str]) -> list[Symbol]:
    """`[data-x]` selectors, as reaches-for-an-element rather than as style rules.

    Emitted as dom_selectors so they match template data attributes through the same
    matcher a `querySelector('[data-x]')` goes through - a stylesheet and a script asking
    for the same attribute are the same claim, and deserve the same answer.
    """
    symbols: list[Symbol] = []
    seen: set[str] = set()
    for path in css_files:
        try:
            text = pathlib.Path(path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for match in _ATTRIBUTE_SELECTOR_RE.finditer(text):
            name = match.group(1)
            line = text.count("\n", 0, match.start()) + 1
            symbol_id = f"dom_selector:data:{name}:{path}:{line}"
            if symbol_id in seen:
                continue
            seen.add(symbol_id)
            symbols.append(
                Symbol(
                    id=symbol_id, kind="dom_selector", label=name, sub="data:css",
                    file=path, line=line, status=Status.UNCERTAIN,
                    snippet=f"[data-{name}]", chain=[os.path.basename(path)],
                    note="A stylesheet selects on this attribute. Evidence that it is used; "
                         "the verdict belongs to the attribute, not to this rule.",
                )
            )
    return symbols
