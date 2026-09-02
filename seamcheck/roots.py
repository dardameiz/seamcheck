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
# Three ways a template loads a script, not one. Only the project's own `static_js` tag
# was read, so every file pulled in with a plain `{% static %}` - which is what Django's
# own admin templates and anything not using the custom tag do - was never scanned at all.
# 30 CSS rules on the project measured were reported dead while admin JS applied them.
_STATIC_JS_RE = re.compile(
    r"""\{%\s*static_js\s+['"]([^'"]+\.js)['"]"""
    r"""|\{%\s*static\s+['"]([^'"]+\.js)['"]"""
    r"""|<script[^>]*\bsrc\s*=\s*['"]/?static/([^'"]+\.js)['"]"""
)


# The stylesheet analogue, which did not exist. JavaScript was discovered from what the
# templates load; CSS was discovered only by walking a configured directory - and on the
# reference project that directory was auto-detected as `src` while the stylesheets live
# in `pointless/static`. One admin stylesheet, linked from `templates/admin/base.html`
# and never opened, accounted for 87% of the project's 3,862 claims: every class it
# defines was reported as an element nothing styles. A template that says
# `<link rel="stylesheet" href="{% static 'x.css' %}">` has declared where its CSS is,
# and that declaration outranks any guess about directory layout.
_STATIC_CSS_RE = re.compile(
    r"""\{%\s*static\s+['"]([^'"]+\.css)['"]"""
    r"""|<link[^>]*\bhref\s*=\s*['"]/?static/([^'"?]+\.css)"""
)


def static_css_references(templates_root: str) -> set[str]:
    """Every stylesheet the templates link by `{% static %}` or a /static/ href."""
    root = pathlib.Path(templates_root)
    if not root.is_dir():
        return set()
    found: set[str] = set()
    for template in root.rglob("*.html"):
        try:
            source = template.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for groups in _STATIC_CSS_RE.findall(source):
            reference = next((g for g in groups if g), None)
            if reference:
                found.add(reference)
    return found


def framework_stylesheets(templates_root: str | None) -> list[str]:
    """Stylesheets a framework ships and a project's templates rely on, from site-packages.

    A Django project's `templates/admin/*.html` extend Django's own admin templates, which
    load `admin/css/base.css`, `forms.css`, `widgets.css` from the django package. Every
    class those files define - form-row, submit-row, aligned, module - then appears in the
    project's templates with no stylesheet in the repository defining it. On the reference
    project that was 1,430 elements reported unstyled. The oracle is one import away.
    """
    if not templates_root or not (pathlib.Path(templates_root) / "admin").is_dir():
        return []
    try:
        import django
    except ImportError:
        return []
    base = pathlib.Path(django.__file__).parent / "contrib" / "admin" / "static" / "admin" / "css"
    return sorted(str(p) for p in base.glob("*.css")) if base.is_dir() else []


def vite_entry_map(vite_config: str) -> dict[str, str]:
    """Entry name -> file. The name is what a template asks for by `{% vite_asset %}`,
    so it is the only handle that connects a bundle back to the page that loads it."""
    # A project with no Vite config is the normal case outside Django+Vite, and it is an
    # empty answer rather than a crash - the same reasoning the templates_root default
    # already used one caller up.
    try:
        source = pathlib.Path(vite_config).read_text(encoding="utf-8")
    except OSError:
        return {}
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
        for groups in _STATIC_JS_RE.findall(source):
            # One alternation, three capture groups; exactly one is non-empty per match.
            reference = next((g for g in groups if g), None)
            if reference:
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


# Stylesheets a TOOL wrote into the repo. A pytest-html run drops `assets/style.css` next
# to its report; scanning it reported 36 of that template's own classes as this project's
# dead code. Same family as dist/ and vendor bundles, one directory the exclusion missed.
_REPORT_ARTIFACT_DIRS = frozenset({
    "assets", "htmlcov", "playwright-report", "test-results", "allure-report",
    "lighthouse", "coverage-report", "reports",
})
_REPORT_ARTIFACT_NAMES = frozenset({"style.css", "report.css", "coverage.css"})


def _is_report_artifact(path: pathlib.Path) -> bool:
    parts = set(path.parts)
    return bool(parts & _REPORT_ARTIFACT_DIRS) and path.name in _REPORT_ARTIFACT_NAMES


def _is_project_stylesheet(path: pathlib.Path, tailwind_output: str | None) -> bool:
    """Whether this stylesheet is code the project wrote, and can therefore act on.

    Three kinds get excluded, and every one of them was measured producing findings nobody
    could use:

    * **Built output.** `dist/` holds a COPY of the source, so scanning it doubles every
      symbol and then reports the copies as unreferenced - 145 findings on the project
      measured, and the exclusion list never applied because it only ran during config
      detection, not when a config was written by hand.
    * **Vendor bundles.** `font-awesome.min.css` alone contributed 2,388 "dead" rules, 55%
      of the total. Font Awesome ships thousands of icons and a project uses a dozen; the
      other 2,376 are true, useless, and undeletable.
    * **Generated utility CSS.** The Tailwind build output is already read separately, to
      learn which utility classes exist. Reading it as source as well reported 150 utilities
      nobody happened to use as dead code.
    """
    from seamcheck.autoconfig import EXCLUDED_DIRS

    if any(part in EXCLUDED_DIRS for part in path.parts):
        return False
    if path.name.endswith(".min.css"):
        return False
    if _is_report_artifact(path):
        return False
    return not (tailwind_output and path.resolve() == pathlib.Path(tailwind_output).resolve())


def discover_css_files(
    css_source_root: str, tailwind_output: str | None = None,
    templates_root: str | None = None, static_roots: list[str] | None = None,
    extra_roots: list[str] | None = None,
) -> list[str]:
    """Every stylesheet the PROJECT wrote under the CSS root, plus whatever they @import,
    plus every stylesheet a template links - wherever that happens to live.

    The @import walk mirrors the Python and JS reachability walks: a file list, not an
    allow-list, so a stylesheet pulled in only by an @import chain is still scanned.
    """
    from seamcheck.extractors.css_extractor import css_imports

    found = [
        str(path) for path in pathlib.Path(css_source_root).rglob("*.css")
        if _is_project_stylesheet(path, tailwind_output)
    ] if css_source_root and pathlib.Path(css_source_root).is_dir() else []
    # Every other static directory the project serves from. _is_project_stylesheet still
    # applies, so vendor bundles, built output and generated utility CSS stay out.
    for extra in extra_roots or []:
        base = pathlib.Path(extra)
        if base.is_dir():
            found += [
                str(path) for path in base.rglob("*.css")
                if _is_project_stylesheet(path, tailwind_output)
            ]
    # Linked from a template: the project's own statement of which CSS applies, and the
    # only discovery that survives a wrong guess about the directory layout.
    if templates_root and static_roots:
        for reference in sorted(static_css_references(templates_root)):
            for base in static_roots:
                candidate = pathlib.Path(base) / reference
                if candidate.is_file() and _is_project_stylesheet(candidate, tailwind_output):
                    found.append(str(candidate))
                    break
    found = list(dict.fromkeys(found))
    seen = set(found)
    queue = list(found)
    while queue:
        imports = css_imports(queue)
        queue = []
        for source, targets in imports.items():
            for target in targets:
                resolved = (pathlib.Path(source).parent / target).resolve()
                if (resolved.is_file() and str(resolved) not in seen
                        and _is_project_stylesheet(resolved, tailwind_output)):
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
