"""Work out the config from the project, so `pip install seamcheck && seamcheck map` works.

Seamcheck shipped requiring a hand-written `SEAMCHECK_CONFIG` dict. That is a wall in
front of the first run: someone who installed the package had to reverse-engineer eight
paths out of their own settings before the tool would say anything at all, with nothing
telling them what the keys meant. It is also what made the corpus idea impossible - a
cloned repo has no config, so the tool could scan exactly one project on earth.

**Most of these are not guesses.** Django already knows where its URLconf, its templates,
its apps and its static dirs are, and asking the settings and the app registry is exact.
Only three keys need a glob: the bundler config, the utility-CSS build output, and where
JavaScript lives. Every detected value records where it came from, so `seamcheck config`
can show its work rather than presenting a black box.

Detection errs WIDE. The one config bug found while validating this against a real project
was a `css_source_root` set too narrow: it excluded the admin stylesheets while the admin
templates were still scanned, and that asymmetry invented 185 findings out of working CSS.
A root that is too broad costs a slower scan; one that is too narrow reports bugs that are
not there. So the roots default to the repo and the junk is excluded by name.
"""

from __future__ import annotations

import os
import pathlib

# Never scanned. Built output, other people's code, and the virtualenv - all of which
# contain enormous amounts of CSS and JS that is not this project's to answer for.
# `dist` matters most: a bundler's output is a COPY of the source, so scanning it doubles
# every symbol and reports the copies as unreferenced.
EXCLUDED_DIRS = frozenset({
    ".git", ".hg", ".svn", ".tox", ".nox", ".mypy_cache", ".ruff_cache", ".pytest_cache",
    "__pycache__", "node_modules", "bower_components", "vendor",
    "venv", ".venv", "env", ".env", "virtualenv", "site-packages",
    "dist", "build", "out", ".next", ".nuxt", ".parcel-cache", ".vite",
    "migrations", "coverage", "htmlcov", ".idea", ".vscode",
})

# Where a `collectstatic` run puts its output. Same problem as `dist`: a copy.
_COLLECTED_HINTS = ("staticfiles", "static_root", "static_collected", "collected_static")

_VITE_NAMES = ("vite.config.js", "vite.config.mjs", "vite.config.ts", "vite.config.cjs")
_TAILWIND_HINTS = ("tailwind-output.css", "tailwind.css", "output.css", "tailwind.min.css")


def excluded(path: pathlib.Path, repo_root: pathlib.Path) -> bool:
    """True when `path` sits inside a directory nothing should scan."""
    try:
        parts = path.relative_to(repo_root).parts
    except ValueError:
        parts = path.parts
    return any(
        part in EXCLUDED_DIRS or part.lower() in _COLLECTED_HINTS
        for part in parts
    )


def _inside(path, repo_root: pathlib.Path) -> pathlib.Path | None:
    """`path` as an absolute Path, or None when it is outside the repo.

    A template dir in site-packages belongs to a dependency. Scanning it reports third-party
    markup as this project's dead code, which is both wrong and unfixable by the reader.
    """
    if not path:
        return None
    candidate = pathlib.Path(str(path)).resolve()
    try:
        candidate.relative_to(repo_root)
    except ValueError:
        return None
    return None if excluded(candidate, repo_root) else candidate


def _rel(path: pathlib.Path, repo_root: pathlib.Path) -> str:
    return os.path.relpath(path, repo_root)


def _first_party_apps(repo_root: pathlib.Path) -> list:
    """App configs whose code actually lives in this repo.

    `INSTALLED_APPS` mixes this project's apps with Django's own and every dependency's.
    The filesystem answers which is which: a first-party app's `path` is under the repo,
    a dependency's is under site-packages.
    """
    from django.apps import apps

    found = []
    for config in apps.get_app_configs():
        if _inside(getattr(config, "path", None), repo_root):
            found.append(config)
    return found


def _densest(candidates: list[pathlib.Path], pattern: str, repo_root: pathlib.Path):
    """Of several candidate directories, the one holding the most matching files.

    `templates_root` is a single path while Django allows many template dirs. Their common
    ancestor is usually the repo root, which is too broad to be useful, so the dominant one
    wins and `seamcheck config` names the rest.
    """
    scored = []
    for directory in candidates:
        if not directory.is_dir():
            continue
        count = sum(
            1 for path in directory.rglob(pattern) if not excluded(path, repo_root)
        )
        if count:
            scored.append((count, directory))
    scored.sort(key=lambda pair: (-pair[0], len(str(pair[1]))))
    return scored


def _template_dirs(repo_root: pathlib.Path) -> list[pathlib.Path]:
    from django.conf import settings

    dirs = []
    for engine in getattr(settings, "TEMPLATES", []) or []:
        for entry in engine.get("DIRS", []) or []:
            resolved = _inside(entry, repo_root)
            if resolved:
                dirs.append(resolved)
        if engine.get("APP_DIRS"):
            for config in _first_party_apps(repo_root):
                candidate = pathlib.Path(config.path) / "templates"
                if candidate.is_dir():
                    dirs.append(candidate)
    return list(dict.fromkeys(dirs))


def _static_dirs(repo_root: pathlib.Path) -> list[pathlib.Path]:
    from django.conf import settings

    dirs = []
    for entry in getattr(settings, "STATICFILES_DIRS", []) or []:
        # An entry may be a (prefix, path) tuple.
        raw = entry[1] if isinstance(entry, tuple | list) and len(entry) == 2 else entry
        resolved = _inside(raw, repo_root)
        if resolved:
            dirs.append(resolved)
    for config in _first_party_apps(repo_root):
        candidate = pathlib.Path(config.path) / "static"
        if candidate.is_dir():
            dirs.append(candidate)
    return list(dict.fromkeys(dirs))


def _find_file(repo_root: pathlib.Path, names: tuple[str, ...]) -> pathlib.Path | None:
    """Shallowest match, so a stray copy deep in a subproject does not win."""
    matches = [
        path
        for name in names
        for path in repo_root.rglob(name)
        if not excluded(path, repo_root)
    ]
    matches.sort(key=lambda path: (len(path.relative_to(repo_root).parts), str(path)))
    return matches[0] if matches else None


def detect(repo_root: str = ".") -> tuple[dict, dict[str, str]]:
    """(config, provenance). Every value paired with how it was arrived at."""
    from django.conf import settings

    root = pathlib.Path(repo_root).resolve()
    config: dict = {}
    why: dict[str, str] = {}

    def put(key, value, source):
        if value not in (None, "", [], {}):
            config[key] = value
            why[key] = source

    put("urlconf_module", getattr(settings, "ROOT_URLCONF", None), "settings.ROOT_URLCONF")

    asgi = getattr(settings, "ASGI_APPLICATION", None)
    if asgi and "." in asgi:
        put("asgi_module", asgi.rsplit(".", 1)[0], "settings.ASGI_APPLICATION")

    apps_found = _first_party_apps(root)
    if apps_found:
        put("app_configs", [f"{c.name}.apps.{type(c).__name__}" for c in apps_found],
            "app registry, filtered to apps inside the repo")
        # The top-level package of each first-party app, plus the settings package, which
        # is where the URLconf and the ASGI entry point live.
        prefixes = {c.name.split(".")[0] for c in apps_found}
        settings_pkg = getattr(settings, "SETTINGS_MODULE", "") or ""
        if settings_pkg:
            prefixes.add(settings_pkg.split(".")[0])
        put("first_party_prefixes", sorted(p for p in prefixes if (root / p).is_dir()),
            "first-party app packages + the settings package")

    templates = _densest(_template_dirs(root), "*.html", root)
    if templates:
        put("templates_root", _rel(templates[0][1], root),
            f"settings.TEMPLATES ({templates[0][0]} templates"
            + (f"; {len(templates) - 1} other dir(s) not scanned)" if len(templates) > 1 else ")"))

    statics = _static_dirs(root)
    if statics:
        best = _densest(statics, "*", root)
        if best:
            put("static_root", _rel(best[0][1], root), "settings.STATICFILES_DIRS / app static dirs")
        # Deliberately the widest of the static dirs' common parent rather than one of
        # them: the config bug this whole module exists to prevent was a CSS root set
        # narrow enough to exclude a stylesheet whose templates were still being read.
        css = _densest(statics, "*.css", root)
        if css:
            put("css_source_root", _rel(css[0][1], root),
                f"static dirs ({css[0][0]} stylesheets)")

    vite = _find_file(root, _VITE_NAMES)
    if vite:
        put("vite_config", _rel(vite, root), "found in the repo")

    tailwind = _find_file(root, _TAILWIND_HINTS)
    if tailwind:
        put("tailwind_build_output", _rel(tailwind, root), "found in the repo")

    return config, why


def effective(repo_root: str = ".") -> tuple[dict, dict[str, str]]:
    """The config a scan will actually use: what was written, over what was detected.

    An explicit `SEAMCHECK_CONFIG` always wins, key by key. Detection only fills the gaps,
    so adding this cannot change the answer for a project that had already configured
    itself - it only gives one to a project that had not.
    """
    from django.conf import settings

    detected, why = detect(repo_root)
    declared = dict(getattr(settings, "SEAMCHECK_CONFIG", {}) or {})
    merged = {**detected, **declared}
    for key in declared:
        why[key] = "SEAMCHECK_CONFIG"
    for key in merged:
        why.setdefault(key, "default")
    return merged, why
