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

from seamcheck.adapters.discovery import SKIP_DIRS

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

    dirs: list[pathlib.Path] = []
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
        raw = entry[1] if isinstance(entry, (tuple, list)) and len(entry) == 2 else entry
        resolved = _inside(raw, repo_root)
        if resolved:
            dirs.append(resolved)
    for config in _first_party_apps(repo_root):
        candidate = pathlib.Path(config.path) / "static"
        if candidate.is_dir():
            dirs.append(candidate)
    return list(dict.fromkeys(dirs))


def _find_file(repo_root: pathlib.Path, names: tuple[str, ...]) -> pathlib.Path | None:
    """Shallowest match, so a stray copy deep in a subproject does not win.

    Prunes excluded directories as it goes rather than listing everything and filtering:
    `rglob` walked node_modules and the virtualenv in full - 3.5 s per name on the
    reference project, and detection asks for several.
    """
    wanted = set(names)
    matches: list[pathlib.Path] = []
    for directory, dirnames, filenames in os.walk(repo_root):
        dirnames[:] = sorted(
            d for d in dirnames if d not in EXCLUDED_DIRS and d.lower() not in _COLLECTED_HINTS
        )
        for filename in filenames:
            if filename in wanted:
                matches.append(pathlib.Path(directory) / filename)
    matches.sort(key=lambda path: (len(path.relative_to(repo_root).parts), str(path)))
    return matches[0] if matches else None


def _settings():
    """Django's settings, or None when this is not a Django project.

    None is a real answer. Five of the six backend adapters have nothing to do with
    Django, and asking them to produce a settings module to be scanned was the reason
    `seamcheck` answered "no Django project here" on a perfectly good Express repository.
    """
    try:
        from django.conf import settings

        settings.INSTALLED_APPS  # noqa: B018 - forces the lazy object, raises if unset
        return settings
    except Exception:
        return None


def detect(repo_root: str = ".") -> tuple[dict, dict[str, str]]:
    """(config, provenance). Every value paired with how it was arrived at."""
    settings = _settings()

    root = pathlib.Path(repo_root).resolve()
    config: dict = {}
    why: dict[str, str] = {}

    def put(key, value, source):
        if value not in (None, "", [], {}):
            config[key] = value
            why[key] = source

    if settings is None:
        # No Django. Everything below reads Django's settings for paths Django already
        # knows; without it the filesystem is the only source, and it is a good one - the
        # JS, CSS, template and Vite discovery underneath was always filesystem-based.
        return _detect_without_django(root, put, config, why)

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


# Where a project that is not Django tends to keep the things a scan needs. Ordered, and
# the first that exists wins - these are conventions, not guesses: every one of them is
# the documented layout of a framework the adapters already read.
_TEMPLATE_DIRS = ("templates", "views", "public", "src/views", "app/views", "src/pages",
                  "pages", "app", "src")
_STATIC_DIRS = ("public", "static", "assets", "src/assets", "src/static", "www", "dist",
                "src", "client", "frontend")


def _detect_without_django(root, put, config, why):
    """The same config, read off the filesystem instead of out of Django's settings."""
    templates = _densest([root / d for d in _TEMPLATE_DIRS if (root / d).is_dir()],
                         "*.html", root)
    if templates:
        put("templates_root", _rel(templates[0][1], root),
            f"found in the repo ({templates[0][0]} html files)")

    statics = [root / d for d in _STATIC_DIRS if (root / d).is_dir()]
    if statics:
        best = _densest(statics, "*", root)
        if best:
            put("static_root", _rel(best[0][1], root), "found in the repo")
        css = _densest(statics, "*.css", root)
        if css:
            put("css_source_root", _rel(css[0][1], root),
                f"found in the repo ({css[0][0]} stylesheets)")

    vite = _find_file(root, _VITE_NAMES)
    if vite:
        put("vite_config", _rel(vite, root), "found in the repo")

    tailwind = _find_file(root, _TAILWIND_HINTS)
    if tailwind:
        put("tailwind_build_output", _rel(tailwind, root), "found in the repo")

    # The JavaScript. Django projects find their entries through `{% static %}` script
    # tags and a Vite config, and neither exists here - so without this the frontend half
    # of the graph is empty, every route comes back `uncertain`, and the scan has read one
    # side of a seam it exists to compare.
    entries = _js_entries(root, config)
    if entries:
        put("js_entry_files", entries, f"javascript found in the repo ({len(entries)} files)")

    # No URLconf, and that is correct - the Express, Fastify, NestJS, Next.js, Flask and
    # FastAPI adapters read routes from source, not from a Python module Django imports.
    why.setdefault("urlconf_module", "not a Django project")
    return config, why


# The SHARED list, not a second one written here. A private copy missed `fixtures`, so a
# project's test fixtures were read as application code and their deliberately-broken fetch
# calls were reported as real findings - which is exactly what running this on its own
# repository turned up.
_JS_SKIP = SKIP_DIRS


def _js_entries(root: pathlib.Path, config: dict) -> list[str]:
    """Every first-party script, as an entry.

    A Django project has one entry per bundle and the imports fan out from there. A plain
    project usually has no bundler to ask, so each script is treated as its own entry -
    which over-counts entries and under-counts nothing, and the alternative was reading no
    JavaScript at all.
    """
    bases = [root / config[key] for key in ("static_root", "templates_root") if key in config]
    bases = [b for b in dict.fromkeys(bases) if b.is_dir()] or [root]
    found: list[str] = []
    for base in bases:
        for path in base.rglob("*.js"):
            # Only the parts INSIDE the repo. Checking the absolute path means where the
            # project happens to live on disk decides what gets scanned - a checkout under
            # /private/tmp matched "tmp", under ~/build matched "build", and every file in
            # the project was skipped. The skip list describes a project's own layout.
            try:
                inside = path.resolve().relative_to(root).parts
            except ValueError:
                inside = path.parts
            if any(part in _JS_SKIP for part in inside):
                continue
            # A minified sibling is the same code compiled; reading both reports every
            # symbol twice.
            if path.name.endswith((".min.js", ".bundle.js")):
                continue
            # RELATIVE to the repo, because that is how the scan records a symbol's file
            # (see relativise). Absolute entries match no symbol, every page ends up with
            # no seeds, and build_map drops all of them - the map then shows nothing but
            # the "not reached from any page" buckets.
            # relative_to, not relpath: `_rel` goes through os.path.relpath, and when the
            # root itself arrives with a "." in it that comes back as "./public/js/x.js"
            # while the scan records "public/js/x.js". One leading dot and the page matches
            # nothing.
            found.append(path.resolve().relative_to(root).as_posix())
            if len(found) >= 400:
                return sorted(dict.fromkeys(found))
    return sorted(dict.fromkeys(found))


def _declared() -> dict:
    """SEAMCHECK_CONFIG out of Django settings, when there are Django settings.

    A project with no Django in it has nowhere to put that setting, and asking for it is
    not an error - it is an Express repository.
    """
    try:
        from django.conf import settings

        return dict(getattr(settings, "SEAMCHECK_CONFIG", {}) or {})
    except Exception:
        # ImportError (no Django) or ImproperlyConfigured (Django, no settings). Both mean
        # the same thing here: nothing was declared, so detection is the whole answer.
        return {}


def effective(repo_root: str = ".") -> tuple[dict, dict[str, str]]:
    """The config a scan will actually use: what was written, over what was detected.

    An explicit `SEAMCHECK_CONFIG` always wins, key by key. Detection only fills the gaps,
    so adding this cannot change the answer for a project that had already configured
    itself - it only gives one to a project that had not.
    """
    detected, why = detect(repo_root)
    declared = _declared()
    merged = {**detected, **declared}
    for key in declared:
        why[key] = "SEAMCHECK_CONFIG"
    for key in merged:
        why.setdefault(key, "default")
    return merged, why
