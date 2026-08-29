"""CSS facts: which selectors are defined, which tokens are declared, which are used."""

from __future__ import annotations

import json
import os
import re
import subprocess

from signal_map.graph import Status, Symbol

_PARSE_SCRIPT = os.path.join(os.path.dirname(__file__), os.pardir, "css_tools", "parse_css.mjs")
# Tailwind escapes variant separators in the compiled CSS (`.md\:flex`, `.w-1\/2`)
# while templates write them bare. Capturing only [\w-] stops at the backslash and
# yields "md", which matches no template class.
_SELECTOR_TOKEN_RE = re.compile(r"([#.])((?:\\.|[\w-])+)")
_CSS_ESCAPE_RE = re.compile(r"\\(.)")


def parse_css_files(css_files: list[str]) -> list[dict]:
    existing = [path for path in css_files if os.path.isfile(path)]
    if not existing:
        return []
    result = subprocess.run(
        ["node", _PARSE_SCRIPT], input="\n".join(existing),
        capture_output=True, text=True, check=True,
    )
    return [json.loads(line) for line in result.stdout.splitlines()]


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
            symbol = _symbol(
                "css_token_use", use["name"], "token", path, use["line"],
                f"var({use['name']})",
            )
            if symbol.id not in seen:
                seen.add(symbol.id)
                symbols.append(symbol)
    return symbols


def css_imports(css_files: list[str]) -> dict[str, list[str]]:
    """Raw @import targets per file, for Task 10's import-graph walk."""
    return {
        record["path"]: [rule["params"].strip("'\" ") for rule in record.get("imports", [])]
        for record in parse_css_files(css_files)
    }
