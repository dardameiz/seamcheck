"""Where a JS scan has to start.

Bundled code is reachable from the bundler's entry points, but a template can also load a
script directly. Those files are imported by nothing, so a bundler-only root set never
reaches them and every endpoint they call looks unused.
"""

from __future__ import annotations

import pathlib
import re

# The key may be quoted or bare - `main: resolve(...)` is valid JS, and requiring
# quotes silently skipped this project's largest entry point.
_VITE_ENTRY_RE = re.compile(
    r"(?:['\"]([\w-]+)['\"]|([A-Za-z_$][\w$]*))\s*:\s*\w+\(\s*__dirname\s*,\s*['\"]([^'\"]+)['\"]"
)
_STATIC_JS_RE = re.compile(r"\{%\s*static_js\s+['\"]([^'\"]+\.js)['\"]")


def vite_entry_map(vite_config: str) -> dict[str, str]:
    """Entry name -> file. The name is what a template asks for by `{% vite_asset %}`,
    so it is the only handle that connects a bundle back to the page that loads it."""
    source = pathlib.Path(vite_config).read_text(encoding="utf-8")
    return {
        quoted or bare: path
        for quoted, bare, path in _VITE_ENTRY_RE.findall(source)
        if path.endswith(".js")
    }


def vite_entries(vite_config: str) -> list[str]:
    return list(vite_entry_map(vite_config).values())


def static_js_by_template(templates_root: str) -> dict[str, set[str]]:
    """Script reference -> the templates that load it, relative to the templates root."""
    root = pathlib.Path(templates_root)
    references: dict[str, set[str]] = {}
    for template in root.rglob("*.html"):
        source = template.read_text(encoding="utf-8", errors="replace")
        for reference in _STATIC_JS_RE.findall(source):
            references.setdefault(reference, set()).add(str(template.relative_to(root)))
    return references


def static_js_references(templates_root: str) -> set[str]:
    return set(static_js_by_template(templates_root))


def discover_js_roots(vite_config: str, templates_root: str, static_root: str) -> list[str]:
    roots = list(vite_entries(vite_config))
    static_base = pathlib.Path(static_root)
    for reference in sorted(static_js_references(templates_root)):
        candidate = static_base / reference
        if candidate.is_file():
            roots.append(str(candidate))
    return [root for root in dict.fromkeys(roots) if pathlib.Path(root).is_file()]


def discover_css_files(css_source_root: str) -> list[str]:
    """Every stylesheet under the CSS root, plus whatever they @import.

    The @import walk mirrors the Python and JS reachability walks: a file list, not an
    allow-list, so a stylesheet pulled in only by an @import chain is still scanned.
    """
    from seamcheck.extractors.css_extractor import css_imports

    found = [str(path) for path in pathlib.Path(css_source_root).rglob("*.css")]
    seen = set(found)
    queue = list(found)
    while queue:
        imports = css_imports(queue)
        queue = []
        for source, targets in imports.items():
            for target in targets:
                resolved = (pathlib.Path(source).parent / target).resolve()
                if resolved.is_file() and str(resolved) not in seen:
                    seen.add(str(resolved))
                    queue.append(str(resolved))
    return sorted(seen)


def tailwind_classes(build_output: str) -> set[str]:
    """Class names a utility-CSS build generated, so they are not read as dead references."""
    path = pathlib.Path(build_output)
    if not path.is_file():
        return set()
    from seamcheck.extractors.css_extractor import extract_css

    return {symbol.label for symbol in extract_css([str(path)]) if symbol.sub == "class"}
