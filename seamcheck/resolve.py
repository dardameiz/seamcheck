"""One import resolver for every walk that follows a first-party JavaScript import.

Every walk used to resolve only paths starting with ".", and treated everything else as
node_modules. A modern project imports its own code mostly through a tsconfig alias
(`@/components/x`) or a workspace package (`@scope/ui`), so a Next.js page on leanos-app
reached 1 of the 83 files it actually uses. Resolution order:

  1. a relative path;
  2. the nearest tsconfig.json / jsconfig.json: `paths`, then `baseUrl`, `extends` followed;
  3. a workspace package, through its manifest;
  4. anything left is a package somebody else wrote.

A non-script target (a stylesheet, JSON, an image) is an ASSET: recorded as reached, never
walked into. Nothing here imports seamcheck at load time - `js_extractor` imports this
module, and `services` pulls in every adapter.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
from dataclasses import dataclass

# The order js_extractor._resolve_import used, so a walk that moves to this resolver picks
# the same file it always did when a folder holds index.js AND index.ts.
_INDEX_NAMES = ("index.js", "index.mjs", "index.ts", "index.tsx", "index.jsx", "index.cjs")
_ASSET_EXTENSIONS = (
    ".css", ".scss", ".sass", ".less", ".json", ".svg", ".png", ".jpg", ".jpeg", ".gif",
    ".webp", ".avif", ".ico", ".woff", ".woff2", ".ttf", ".otf", ".eot", ".mp4", ".mp3",
)
_CONFIG_NAMES = ("tsconfig.json", "jsconfig.json")
# A JSON string, a line comment or a block comment - strings first, so `//` inside
# "https://json.schemastore.org/tsconfig" is part of a string and survives. Nearly every
# tsconfig.json opens with that $schema line; stripping `//...` blindly broke the JSON and
# silently lost every alias in the file.
_JSONC_COMMENT = re.compile(r'"(?:\\.|[^"\\])*"|//[^\n]*|/\*.*?\*/', re.S)
_JSONC_TRAILING_COMMA = re.compile(r'"(?:\\.|[^"\\])*"|,(?=\s*[}\]])', re.S)


def _js_extensions() -> tuple[str, ...]:
    from seamcheck.extractors.js_extractor import _JS_EXTENSIONS

    return _JS_EXTENSIONS


@dataclass(frozen=True)
class ResolvedImport:
    file: str | None = None      # a first-party script: walk into it
    asset: str | None = None     # a first-party non-script: reached, not walked
    third_party: bool = False    # a package this repository does not hold


def norm_path(path: str, repo_root: str) -> str:
    """Repo-relative with forward slashes - the one spelling `symbol.file` ever has."""
    try:
        relative = os.path.relpath(os.path.abspath(path), os.path.abspath(repo_root))
    except ValueError:  # a different drive on Windows
        relative = os.path.normpath(path)
    return relative.replace(os.sep, "/")


def _classify(path: str) -> ResolvedImport:
    if os.path.splitext(path)[1].lower() in _ASSET_EXTENSIONS:
        return ResolvedImport(asset=path)
    return ResolvedImport(file=path)


def _on_disk(base: str) -> str | None:
    """`base` as written, with a script or asset extension, or as a folder's index file."""
    if os.path.isfile(base):
        return base
    for extension in _js_extensions() + _ASSET_EXTENSIONS:
        if os.path.isfile(base + extension):
            return base + extension
    for name in _INDEX_NAMES:
        candidate = os.path.join(base, name)
        if os.path.isfile(candidate):
            return candidate
    return None


def _load_jsonc(path: str) -> dict:
    try:
        text = pathlib.Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    text = _JSONC_COMMENT.sub(lambda m: m.group(0) if m.group(0).startswith('"') else "", text)
    text = _JSONC_TRAILING_COMMA.sub(lambda m: m.group(0) if m.group(0).startswith('"') else "", text)
    try:
        data = json.loads(text)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _is_within(path: str, root: str) -> bool:
    try:
        return os.path.commonpath([path, root]) == root
    except ValueError:  # different drives on Windows
        return False


def _find_config(start_dir: str, project_root: str) -> str | None:
    # Absolute, never resolve()d: resolving turns macOS's /var into /private/var, so an
    # alias target built from it would not equal the same file reached by a relative
    # import - one file under two spellings, and a page's reach would miss the match.
    root = os.path.abspath(project_root)
    current = os.path.abspath(start_dir)
    while True:
        for name in _CONFIG_NAMES:
            candidate = os.path.join(current, name)
            if os.path.isfile(candidate):
                return candidate
        parent = os.path.dirname(current)
        if current == root or parent == current or not _is_within(parent, root):
            return None
        current = parent


def _extended_config(config_dir: str, extends: str) -> str | None:
    """A relative `extends`. One naming a package (`@tsconfig/next`) lives in node_modules,
    which this does not read, so it contributes nothing rather than a guess."""
    candidate = os.path.join(config_dir, extends)
    for path in (candidate, candidate + ".json"):
        if os.path.isfile(path):
            return os.path.abspath(path)
    return None


def _config_chain(config_path: str, seen: frozenset[str] = frozenset()) -> tuple[str | None, str, dict]:
    """(base_url_dir or None, the dir `paths` resolve against, paths) with `extends` applied.

    The nearer config wins: its own `paths` entries override the ones it extends, and its
    `baseUrl` replaces theirs. `paths` without any `baseUrl` resolves against the config
    that declared it, as TypeScript does.
    """
    config_dir = os.path.dirname(config_path)
    if config_path in seen:
        return None, config_dir, {}
    seen = seen | {config_path}
    data = _load_jsonc(config_path)
    options = data.get("compilerOptions")
    options = options if isinstance(options, dict) else {}

    base_url_dir: str | None = None
    paths_dir = config_dir
    paths: dict[str, list[str]] = {}
    extends = data.get("extends")
    for parent in ([extends] if isinstance(extends, str) else extends if isinstance(extends, list) else []):
        parent_path = _extended_config(config_dir, parent) if isinstance(parent, str) else None
        if parent_path:
            parent_base, parent_paths_dir, parent_paths = _config_chain(parent_path, seen)
            base_url_dir = parent_base or base_url_dir
            if parent_paths:
                paths, paths_dir = dict(parent_paths), parent_paths_dir
    if isinstance(options.get("baseUrl"), str):
        base_url_dir = os.path.normpath(os.path.join(config_dir, options["baseUrl"]))
    own = options.get("paths")
    if isinstance(own, dict) and own:
        paths = {**paths, **{k: v for k, v in own.items() if isinstance(v, list)}}
        paths_dir = config_dir
    if paths and base_url_dir:
        paths_dir = base_url_dir
    return base_url_dir, paths_dir, paths


class _Aliases:
    def __init__(self, base_url_dir: str | None, paths_dir: str, paths: dict[str, list[str]]):
        self.base_url_dir = base_url_dir
        exact = [(alias, "", targets) for alias, targets in paths.items() if "*" not in alias]
        # The most specific pattern first: `@/components/*` before `@/*`, as TypeScript picks.
        wild = sorted(((alias.split("*", 1)[0], alias.split("*", 1)[1], targets)
                       for alias, targets in paths.items() if "*" in alias),
                      key=lambda item: -len(item[0]))
        self.exact = {alias: targets for alias, _, targets in exact}
        self.wild = wild
        self.paths_dir = paths_dir

    def candidates(self, import_path: str) -> list[str]:
        found = [os.path.join(self.paths_dir, target) for target in self.exact.get(import_path, [])]
        for prefix, suffix, targets in self.wild:
            if (import_path.startswith(prefix) and import_path.endswith(suffix)
                    and len(import_path) >= len(prefix) + len(suffix)):
                middle = import_path[len(prefix):len(import_path) - len(suffix)]
                found += [os.path.join(self.paths_dir, target.replace("*", middle, 1))
                          for target in targets if isinstance(target, str)]
        if self.base_url_dir:
            found.append(os.path.join(self.base_url_dir, import_path))
        return [os.path.normpath(path) for path in found]


def _manifest_entries(package_dir: str, data: dict) -> list[str]:
    """Where a workspace package's code starts, best first. A package whose `main` points at
    unbuilt `dist/` still has its source; the first candidate that exists wins."""
    found: list[str] = []
    exports = data.get("exports")
    if isinstance(exports, dict):
        exports = exports.get(".", exports)
    if isinstance(exports, str):
        found.append(exports)
    elif isinstance(exports, dict):
        found += [exports[key] for key in ("source", "import", "default", "require")
                  if isinstance(exports.get(key), str)]
    found += [data[key] for key in ("source", "module", "main") if isinstance(data.get(key), str)]
    found += ["src/index", "index"]
    return [os.path.normpath(os.path.join(package_dir, path)) for path in found]


class Resolver:
    """Built once per walk for one project root; caches configs and the workspace index."""

    def __init__(self, project_root: str):
        self.project_root = os.path.abspath(project_root)
        self._config_by_dir: dict[str, str | None] = {}
        self._aliases_by_config: dict[str, _Aliases | None] = {}
        self._packages: dict[str, tuple[str, str | None]] | None = None

    def resolve(self, current_file: str, import_path: str) -> ResolvedImport:
        if not import_path:
            return ResolvedImport()
        if import_path.startswith("."):
            base = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(current_file)), import_path))
            target = _on_disk(base)
            return _classify(target) if target else ResolvedImport()

        aliases = self._aliases_for(os.path.dirname(os.path.abspath(current_file)))
        if aliases is not None:
            for candidate in aliases.candidates(import_path):
                target = _on_disk(candidate)
                if target:
                    return _classify(target)

        target = self._workspace_target(import_path)
        if target:
            return _classify(target)
        return ResolvedImport(third_party=True)

    def _aliases_for(self, directory: str) -> _Aliases | None:
        if directory not in self._config_by_dir:
            self._config_by_dir[directory] = _find_config(directory, self.project_root)
        config = self._config_by_dir[directory]
        if config is None:
            return None
        if config not in self._aliases_by_config:
            base_url_dir, paths_dir, paths = _config_chain(config)
            self._aliases_by_config[config] = (
                _Aliases(base_url_dir, paths_dir, paths) if paths or base_url_dir else None
            )
        return self._aliases_by_config[config]

    def _workspace_target(self, import_path: str) -> str | None:
        packages = self._workspace_packages()
        for name, (package_dir, entry) in packages.items():
            if import_path == name:
                return entry
            if import_path.startswith(name + "/"):
                rest = import_path[len(name) + 1:]
                beside = [os.path.dirname(entry)] if entry else []
                for folder in (*beside, package_dir, os.path.join(package_dir, "src")):
                    target = _on_disk(os.path.join(folder, rest))
                    if target:
                        return target
        return None

    def _workspace_packages(self) -> dict[str, tuple[str, str | None]]:
        """Package name -> (its folder, the file its code starts at, or None)."""
        if self._packages is not None:
            return self._packages
        self._packages = {}
        from seamcheck.services import _globs_from_workspaces, _workspace_folders

        root = pathlib.Path(self.project_root)
        try:
            patterns = _globs_from_workspaces(root)
        except OSError:
            return self._packages
        for pattern in patterns:
            for folder in _workspace_folders(root, pattern):
                manifest = folder / "package.json"
                if not manifest.is_file():
                    continue
                try:
                    data = json.loads(manifest.read_text(encoding="utf-8", errors="replace"))
                except (OSError, ValueError):
                    continue
                name = data.get("name") if isinstance(data, dict) else None
                if not isinstance(name, str) or not name or name in self._packages:
                    continue
                entry = next((found for found in map(_on_disk, _manifest_entries(str(folder), data)) if found), None)
                self._packages[name] = (str(folder), entry)
        return self._packages
