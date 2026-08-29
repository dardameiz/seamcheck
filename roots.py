"""Where a JS scan has to start.

Bundled code is reachable from the bundler's entry points, but a template can also load a
script directly. Those files are imported by nothing, so a bundler-only root set never
reaches them and every endpoint they call looks unused.
"""

from __future__ import annotations

import pathlib
import re

_VITE_ENTRY_RE = re.compile(r"['\"][\w-]+['\"]\s*:\s*\w+\(\s*__dirname\s*,\s*['\"]([^'\"]+)['\"]")
_STATIC_JS_RE = re.compile(r"\{%\s*static_js\s+['\"]([^'\"]+\.js)['\"]")


def vite_entries(vite_config: str) -> list[str]:
    source = pathlib.Path(vite_config).read_text(encoding="utf-8")
    return [path for path in _VITE_ENTRY_RE.findall(source) if path.endswith(".js")]


def static_js_references(templates_root: str) -> set[str]:
    references: set[str] = set()
    for template in pathlib.Path(templates_root).rglob("*.html"):
        references |= set(_STATIC_JS_RE.findall(template.read_text(encoding="utf-8", errors="replace")))
    return references


def discover_js_roots(vite_config: str, templates_root: str, static_root: str) -> list[str]:
    roots = list(vite_entries(vite_config))
    static_base = pathlib.Path(static_root)
    for reference in sorted(static_js_references(templates_root)):
        candidate = static_base / reference
        if candidate.is_file():
            roots.append(str(candidate))
    return [root for root in dict.fromkeys(roots) if pathlib.Path(root).is_file()]
