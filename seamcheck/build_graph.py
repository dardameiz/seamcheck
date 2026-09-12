"""Whether the bundler's own output contains what the source graph says is reachable.

A module can be perfectly wired up in source - imported (statically or dynamically) from a
real entry file, so the connectivity graph sees it as CONNECTED - and still never reach a
player. The source graph has no way to see any of these:

  * an obfuscator/minifier plugin that cannot see through a dynamic import's specifier and
    silently leaves it unbundled;
  * a `manualChunks` rule that drops a chunk;
  * an import that resolves for a local dev server but not for the production build
    (a case-sensitive filesystem mismatch, a conditional export the bundler picks
    differently).

Reference case: a project's own `main.js` registered 62 button modules, each loaded by a
literal `() => import('./buttons/<id>.js')`. 41 of the 62 ended up in the Vite build; the
other 21 were shipped as raw, unminified source fetched directly by the browser, for five
days, with zero errors and zero 404s - the only symptom was extra weight nothing measures
per-module. The connectivity graph reported all 62 as CONNECTED, correctly: the `import()`
is real. What was missing lives one layer below the source graph, in the bundler's own
output - which is exactly what this module reads.
"""

from __future__ import annotations

import json
import os

from seamcheck.graph import Status, Symbol

_BUILD_GAP_NOTE = (
    "Reachable from an entry file by import()/import, but absent from the build "
    "manifest. A bundler plugin (an obfuscator scrambling import() specifiers, a "
    "manualChunks rule, an environment-specific resolution) silently dropped it - the "
    "browser still gets it, as raw unbundled source, so nothing errors and nothing 404s."
)


def _normalize(path: str) -> str:
    """POSIX-style, no leading './' - the shape both a manifest key and a relpath can take."""
    posix = path.replace(os.sep, "/")
    return posix[2:] if posix.startswith("./") else posix


def read_build_manifest(manifest_path: str) -> set[str] | None:
    """Every source module path the bundler actually emitted, or None if unreadable.

    Understands Vite's manifest.json shape: keys are source-relative paths, each entry
    optionally repeats the same path in its own `src` field (present on entry/dynamic-entry
    chunks, absent on plain asset entries - where the key itself is the source path).
    A missing or unparsable manifest is not an error here: the caller decides what that
    means (usually: the project has not been built yet, or does not use this bundler).
    """
    try:
        with open(manifest_path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None

    paths: set[str] = set()
    for key, entry in data.items():
        if not isinstance(entry, dict):
            continue
        src = entry.get("src")
        paths.add(_normalize(src if isinstance(src, str) else key))
    return paths


def _relatives(file_paths, root: str) -> dict[str, str]:
    relatives: dict[str, str] = {}
    for file_path in file_paths:
        abs_path = os.path.abspath(file_path)
        try:
            relatives[file_path] = _normalize(os.path.relpath(abs_path, root))
        except ValueError:
            # Different drives on Windows - not this project's shape, but relpath raises
            # rather than returning something wrong, and a crash here would lose every
            # other finding in the scan with it.
            continue
    return relatives


def find_build_gaps(
    check_files: list[str],
    manifest_path: str,
    build_root: str,
    *,
    entry_files: list[str] | None = None,
    ignore: frozenset[str] = frozenset(),
) -> list[Symbol]:
    """Dynamic-import targets the build manifest never mentions.

    `check_files` - every module that is EVER a dynamic-import target somewhere in the
    entry graph (`js_extractor.discover_dynamic_import_targets`, not `discover_js_files`'s
    full reachable set: a bundler's manifest gets its own entry per true entry point and
    per dynamic-import target, but a module reached only by static `import ... from`
    commonly folds into a shared chunk with no manifest key of its own - checking every
    reachable file instead produced ~190 false gaps on this tool's own reference project,
    none of them missing from the build in any real sense).

    `build_root` - the directory the manifest's own paths are written relative to (a Vite
    project's root, typically where its config file lives - NOT necessarily where
    `vite.config.js` itself sits; see `autoconfig._find_vite_manifest`).

    `entry_files` - the TRUE entry points, used only for the root-sanity check below. Every
    real Vite entry always gets its own manifest key, which `check_files` (deliberately
    narrow - only ever dynamic-import targets) cannot promise even when `build_root` is
    exactly right: a project whose obfuscator ate EVERY dynamic-import specifier would have
    zero overlap between `check_files` and the manifest despite a correct root, and that is
    precisely the worst-case incident this check exists to catch - it must not also be the
    case the sanity guard silently swallows. Omit only when the caller has no better set to
    offer; `check_files` is then used for both purposes, which is weaker but not wrong.

    `ignore` - source-relative paths (in the same `a/b/c.js` shape this function produces
    findings with) to exempt: a module deliberately externalised, loaded from a CDN at
    runtime, or excluded from the production build on purpose. Never guessed at - callers
    name what they know is intentional.

    Returns one UNRESOLVED `build_gap` symbol per gap. Empty (not an error, and not a
    finding) when the manifest cannot be read at all - a project with no build step, or one
    not yet built - or when `build_root` does not line up with the manifest's own paths
    closely enough for even one entry (or, lacking those, one checked file) to match it.
    """
    manifest_paths = read_build_manifest(manifest_path)
    if manifest_paths is None:
        return []

    root = os.path.abspath(build_root)
    relatives = _relatives(check_files, root)

    # `build_root` is a guess: if NOTHING in the root-sanity set matches ANYTHING in the
    # manifest, that is the wrong root, not "every dynamic import silently unbundled" -
    # reporting every checked file as a gap would be a false-positive avalanche from one
    # bad guess. Real coverage, however partial, is required before a gap is trusted.
    sanity_relatives = _relatives(entry_files, root) if entry_files else relatives
    if sanity_relatives and manifest_paths.isdisjoint(sanity_relatives.values()):
        return []

    findings: list[Symbol] = []
    seen: set[str] = set()
    for file_path, rel in relatives.items():
        if rel in seen or rel in ignore or rel in manifest_paths:
            continue
        seen.add(rel)
        findings.append(
            Symbol(
                id=f"build_gap:{rel}",
                kind="build_gap",
                label=rel,
                sub="reachable, not bundled",
                file=file_path,
                line=None,
                status=Status.UNRESOLVED,
                snippet="",
                chain=[],
                note=_BUILD_GAP_NOTE,
            )
        )
    return findings
