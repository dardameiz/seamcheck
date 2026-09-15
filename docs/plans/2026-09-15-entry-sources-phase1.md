# Entry Sources, Phase 1 — Entries and Next.js Pages — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace seamcheck's Vite/Django-hardwired idea of "a page" with a pluggable `EntrySource` layer, so Next.js (and, generically, any backend's route handlers) get real page/server entries instead of landing in "Not reached from any page" — without changing today's Django/Vite behavior or breaking `api.page_files`'s existing callers.

**Architecture:** A new `seamcheck/entries/` package mirrors `seamcheck/adapters/`: each `EntrySource` detects with a confidence and returns `Entry` objects (key, kind, roots, title, where, group, evidence, note). A new `seamcheck/resolve.py` is the one place that resolves a JS/TS import (relative, tsconfig/jsconfig alias, workspace package) and classifies it as a first-party file, an asset, or third-party — replacing `js_extractor._resolve_import` everywhere. `seamcheck/api.py`'s `page_files()` becomes a thin wrapper over the new `page_entries()` + a shared, once-built `ModuleGraph`. `mapdata.py` widens what counts as "on a page" (file-reach, not just drawn nodes) and stops dropping pages with nothing drawable.

**Tech Stack:** Python 3.10+, stdlib only for the new modules (no new dependencies). Existing test style: `unittest.TestCase`, tempdir-built fixture repos via a per-file `_repo(files: dict[str, str]) -> str` helper (the codebase's real convention — see Task 5's note on why this plan does NOT use a committed `seamcheck/tests/fixtures/entries/` directory as an earlier draft of the design doc assumed).

**Spec:** `docs/plans/2026-09-15-entry-sources-design.md` — this plan implements section 1 ("The entry layer"), section 2 ("What an entry reaches"), the Next.js row of section 3, the "count shown once" part of section 5, and Phase 1's row in section 9. Sections 2d (TS/JS store linker), the Data bucket, Prisma/SQLAlchemy/Mongoose/Knex readers, frontend framework sources (Vite/CRA/React Router/TanStack), screens, the wrapper-folder descent, and Server Actions/`proxy.ts`/`next/font` are explicitly OUT of this plan (phases 2-4).

## Global Constraints

- **No source or reader raises.** Every `EntrySource.entries()` and every helper in `resolve.py` catches its own I/O/parse errors and returns `[]`/`None`/a default — never lets an exception escape to the scan.
- **A computed value is a note, never a guess.** Not directly exercised by Phase 1 (computed routes/bundler entries are phase 3), but nothing in this plan should synthesize a title/URL it isn't sure of.
- **Django stays optional.** No module-level `import django` anywhere under `seamcheck/entries/` or in `seamcheck/resolve.py`.
- **Python floor 3.10.** No `X | Y` in `isinstance` calls, no `datetime.UTC`, no backslash inside an f-string expression. Use `Optional`/`|` union *type hints* freely (3.10 supports `X | Y` in annotations, just not as a runtime `isinstance` argument) — this plan's code follows that rule throughout.
- **`api.page_files(repo_root) -> dict[str, set[str]]` keeps its exact signature.** Every existing caller (`scoped_findings`, `scoped_map_document`, `_map_document`, `seamcheck/changescope.py`) must keep working unmodified.
- **Legacy guard:** `LegacySource` must reproduce today's roots, in today's order, with today's filename-stem keys and `pagenames` titles. `seamcheck/tests/test_roots.py` and `seamcheck/tests/test_pagenames.py` stay green, **unmodified**.
- **Verification gates:** `ruff check seamcheck/` clean after every task. The final task also runs `python -m seamcheck.cli check` (no crash, no new `unresolved` against this repo) and `python tools/corpus.py scan` (no `CRASH`, no repo newly `NO ROUTES`). **That corpus gate runs the backend adapters and the GraphQL/Celery/Stripe readers only** — never the JS extractor, `mapdata` or `page_files` — so it catches a crash but cannot see anything this phase changes. Phase 1's effect is measured by the new `python tools/corpus.py entries` (Task 13) and on leanos-app (Task 15).
- **Workspace:** git worktree `.claude/worktrees/entry-sources-phase1`, branch `worktree-entry-sources-phase1`. `seamcheck` is an editable install pointing at the main checkout: a command run from the worktree root imports the worktree's code, but a command run anywhere else (leanos-app, the corpus) must set `PYTHONPATH=<worktree root>` or it measures `main`. `tools/corpus.py`, `tools/precision.py` and `tools/verify_output.py` look for the corpus in the folder beside the repository root, which from the worktree is `.claude/worktrees/seamcheck-corpus` and does not exist: every corpus command runs with `SEAMCHECK_CORPUS=/Users/balazssimon/dev/seamcheck-corpus`. Without it `corpus.py scan` reports every repository "not cloned" and then crashes writing its results.
- **Commits (agreed 2026-09-15):** batched at five checkpoints, each preceded by `python -m seamcheck.cli check` and `python tools/corpus.py scan`, per the `CLAUDE.md` owner rule: (1) Tasks 1-3, (2) Tasks 5-8 then 4, (3) Tasks 9-11, (4) Tasks 13 and 14, (5) Task 15 and any fix it needs. Task 11 moved into checkpoint 3 while it ran: its renderer edits were already in the working tree when checkpoint 3's gates would run, and a commit must hold exactly what the gates tested. A task's "commit" step means stage its files; its tests and lint still run when the task is done.
- **Code blocks are the starting point**, checked against the real code as each task runs. Where the implementation departs from one, the checkpoint's commit message says where and why.
- **`seamcheck/renderers/map_html.py` rule (from `CLAUDE.md`):** after any edit to this file, run `node --check` on every emitted `<script>` block — a JS syntax error there is a blank page and Python lint will not catch it. `tools/verify_output.py` already does this per block; Task 11 uses it.

---

## Revisions after reading the code (2026-09-15, before any code was written)

The first draft was written from research summaries. Reading the real files before starting changed the following; the task sections below carry the corrected text.

1. **Task 12 is removed; its intent moved into Task 5.** Taking `js_entry_files` out of autoconfig for a Next.js project would move those files from the JS extractor's entry set (read in full, URL literals recorded) to its extra set (calls only) — a change to the scan, not to the page list. The sweep's *pages* are dropped inside `LegacySource` instead, and only when `js_entry_files` was detected rather than written in `SEAMCHECK_CONFIG`.
2. **Server entries are titled by the URLs their views serve** (Task 7). The Next.js adapter labels a view with its file stem, so titling by the view label named every route handler "route", and the picker's grouping merged them into one row.
3. **The module graph walks outward from the roots it is handed** (Task 3), once for every page's roots together. It does not parse every JS/TS file in the repository.
4. **`_js_roots` is not relocated** (Task 5). `api.py` gains `_entry_roots()`, the declared-entries half of `_js_roots`, and `LegacySource` imports it inside its methods — the function-level import `api.py` already uses throughout — which avoids the import cycle without moving code. A root with no name keeps `where=""`, which is what `build_map` shows today.
5. **Corpus measurement is a new `tools/corpus.py entries` subcommand** (Task 13), not two columns on `scan`: the columns need a full scan per repository, which `scan` deliberately never does.
7. **Symbols a page's files hold but nothing draws get their own bucket** (decided with the owner, 2026-09-15, during Task 9). The design doc's membership-by-file rule takes them out of "Not reached from any page"; drawing still starts only from seeds, so most of them (class applications, rules in an imported stylesheet) are never drawn. The map's search is built from drawn nodes, so without a place of their own they would be findable nowhere but the Files view. Task 10 adds one bucket, `undrawn:onpage`, titled "On a page, nothing to draw", placed after the entries and before the not-reached buckets. `mapdata` exports the bucket prefixes as `BUCKET_PREFIXES`; `map_html._payload` (line 6322) keeps every bucket out of `on_pages` and the shared layer through it; `tools/corpus.py entries` and the tests' `_entries` helper use it too. Every symbol is then exactly one of: drawn on an entry, in the undrawn bucket, in a not-reached bucket.
8. **The map opens on the first page that draws something** (found by checkpoint 3's full suite: 26 browser tests failed). The map's script opened page 0 unconditionally (`let current = 0`), which was only safe while entries with nothing to draw were dropped. Task 10 keeps them, so page 0 could be an empty canvas on first sight. `current` now starts at the first page whose meta has more than its own node, falling back to 0; `OpensOnAPageThatDraws` pins it, including that the picker and the canvas agree. The page-list test's "2 entries + 4 not-reached buckets" became "2 + 1": its fixture puts every file on a page, so nothing is unreached and the one bucket is "On a page, nothing to draw" — the design doc's accepted change in bucket counts, not a regression.
9. Smaller: `_find_config` compares absolute paths rather than `resolve()`d ones (macOS `/var` vs `/private/var` made an alias-resolved path differ from a relative-resolved one); the count-once test looks for the count, not for an em dash (today's backend blurb contains one); a page's evidence and note ride on the page's own node as its `note`, which the map already ships in each page's detail chunk, so the sheet shows them with no JavaScript change (Task 10).

---

## Before you start: one deliberate deviation from the design doc

`docs/plans/2026-09-15-entry-sources-design.md` section 8 proposes "a tiny fixture repository under `seamcheck/tests/fixtures/entries/`" per source. Research into the actual codebase found this is **not** how this repo tests framework-shaped code today: `seamcheck/tests/fixtures/` is almost entirely flat individual files, and the real, established pattern for adapter-shaped tests (`test_nextjs_adapter.py` and siblings) is a **tempdir-built repo via a per-test-file `_repo(files: dict[str, str]) -> str` helper**:

```python
def _repo(files: dict[str, str]) -> str:
    root = pathlib.Path(tempfile.mkdtemp())
    for name, body in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    return str(root)
```

Every task below follows the codebase's real convention (tempdir `_repo()`), not the design doc's proposed committed-fixture-directory. This is a genuine gap between the design doc and the codebase, not a shortcut — flagged here so it isn't silently "corrected" back to the doc's wording by someone skimming section 8 later.

---

## Task 1: Entry and EntrySource base types

**Files:**
- Create: `seamcheck/entries/__init__.py` (empty package marker for this task — Task 4 fills it in)
- Create: `seamcheck/entries/base.py`
- Test: `seamcheck/tests/test_entries_base.py`

**Interfaces:**
- Produces: `Entry` (frozen dataclass: `key: str`, `kind: str`, `roots: tuple[str, ...]`, `title: str`, `where: str`, `group: str = ""`, `evidence: str = ""`, `note: str = ""`), `EntrySource` (`runtime_checkable` `Protocol`: `name: str`; `detect(self, repo_root: str, config: dict) -> float`; `entries(self, repo_root: str, config: dict, graph) -> list[Entry]`).

- [ ] **Step 1: Write the failing test**

```python
# seamcheck/tests/test_entries_base.py
import unittest

from seamcheck.entries.base import Entry, EntrySource


class EntryTests(unittest.TestCase):
    def test_a_plain_entry_carries_its_fields(self):
        entry = Entry(
            key="next:/pricing/[locale]", kind="page",
            roots=("app/pricing/[locale]/page.tsx",),
            title="Pricing", where="/pricing/[locale] - app/pricing/[locale]/page.tsx",
        )
        self.assertEqual(entry.key, "next:/pricing/[locale]")
        self.assertEqual(entry.kind, "page")
        self.assertEqual(entry.roots, ("app/pricing/[locale]/page.tsx",))
        self.assertEqual(entry.group, "")
        self.assertEqual(entry.evidence, "")
        self.assertEqual(entry.note, "")

    def test_entry_is_frozen(self):
        entry = Entry(key="k", kind="page", roots=(), title="T", where="W")
        with self.assertRaises(Exception):
            entry.title = "changed"  # type: ignore[misc]


class EntrySourceProtocolTests(unittest.TestCase):
    def test_a_conforming_object_satisfies_the_protocol(self):
        class Stub:
            name = "stub"

            def detect(self, repo_root, config):
                return 0.0

            def entries(self, repo_root, config, graph):
                return []

        self.assertIsInstance(Stub(), EntrySource)

    def test_a_non_conforming_object_does_not(self):
        class NotASource:
            pass

        self.assertNotIsInstance(NotASource(), EntrySource)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest seamcheck/tests/test_entries_base.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'seamcheck.entries'`

- [ ] **Step 3: Write the implementation**

```python
# seamcheck/entries/__init__.py
```
(empty — package marker only; Task 4 adds the registry)

```python
# seamcheck/entries/base.py
"""The entry layer: mirrors `seamcheck/adapters/` for the frontend side. Each
`EntrySource` detects with a confidence and, given the already-scanned graph,
returns the entries it recognises - a page, a server route handler, a static
page, or (in later phases) a screen or a framework-invoked script. See
docs/plans/2026-09-15-entry-sources-design.md section 1.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class Entry:
    key: str                  # unique, stable: "next:/pricing/[locale]", "server:app/api/checkout/route.ts"
    kind: str                 # "page" | "server" | "static_page" | "screen" | "script" | "entry_file"
    roots: tuple[str, ...]    # repo-relative files the import walk starts from
    title: str                # "Pricing"
    where: str                # "/pricing/[locale] - app/pricing/[locale]/page.tsx"
    group: str = ""           # entries sharing a group are sections of one page
    evidence: str = ""        # "filesystem route", "server route handler"
    note: str = ""            # shown in the sheet: "by convention, not declared"


@runtime_checkable
class EntrySource(Protocol):
    name: str

    def detect(self, repo_root: str, config: dict) -> float:
        """0.0-1.0: how confident this source is that it applies here. Filesystem-only -
        no graph is available yet when this runs."""
        ...

    def entries(self, repo_root: str, config: dict, graph) -> list[Entry]:
        """Every entry this source recognises. Never raises; a source that finds
        nothing returns []. `graph` is the already-scanned `seamcheck.graph.Graph`."""
        ...
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest seamcheck/tests/test_entries_base.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Lint and commit**

```bash
ruff check seamcheck/entries/ seamcheck/tests/test_entries_base.py
git add seamcheck/entries/__init__.py seamcheck/entries/base.py seamcheck/tests/test_entries_base.py
git commit -m "feat(entries): add Entry and EntrySource base types"
```

---

## Task 2: The import resolver (`seamcheck/resolve.py`)

**Files:**
- Create: `seamcheck/resolve.py`
- Test: `seamcheck/tests/test_resolve.py`

**Interfaces:**
- Consumes: `seamcheck.services._globs_from_workspaces(root: pathlib.Path) -> list[str]` (existing, `seamcheck/services.py:72`). `_JS_EXTENSIONS` from `seamcheck.extractors.js_extractor` — imported **lazily** (inside a function, not at module top) to avoid a circular import, since Task 3 makes `js_extractor.py` import `Resolver` from this module.
- Produces: `ResolvedImport` (frozen dataclass: `file: str | None`, `asset: str | None`, `third_party: bool`), `Resolver` (class: `__init__(self, project_root: str)`; `resolve(self, current_file: str, import_path: str) -> ResolvedImport`), `norm_path(path: str, repo_root: str) -> str` (a repo-relative, forward-slash path — the same normalisation `api._norm` already does, duplicated here as a small dependency-free utility so `seamcheck/entries/*.py` doesn't have to import `api.py`, which will import `entries` in Task 9 and would otherwise cycle).

- [ ] **Step 1: Write the failing tests**

```python
# seamcheck/tests/test_resolve.py
import pathlib
import tempfile
import unittest

from seamcheck.resolve import Resolver, norm_path


def _repo(files: dict[str, str]) -> str:
    root = pathlib.Path(tempfile.mkdtemp())
    for name, body in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    return str(root)


class RelativeImportTests(unittest.TestCase):
    def test_a_relative_import_resolves_with_an_inferred_extension(self):
        root = _repo({"src/a.ts": "", "src/b.ts": "import './a'"})
        resolved = Resolver(root).resolve(str(pathlib.Path(root, "src/b.ts")), "./a")
        self.assertEqual(resolved.file, str(pathlib.Path(root, "src/a.ts")))
        self.assertIsNone(resolved.asset)
        self.assertFalse(resolved.third_party)

    def test_a_relative_import_of_a_directory_resolves_to_its_index(self):
        root = _repo({"src/lib/index.ts": "", "src/b.ts": ""})
        resolved = Resolver(root).resolve(str(pathlib.Path(root, "src/b.ts")), "./lib")
        self.assertEqual(resolved.file, str(pathlib.Path(root, "src/lib/index.ts")))

    def test_a_relative_import_of_a_stylesheet_is_an_asset_not_a_file(self):
        root = _repo({"src/globals.css": "", "src/b.ts": ""})
        resolved = Resolver(root).resolve(str(pathlib.Path(root, "src/b.ts")), "./globals.css")
        self.assertIsNone(resolved.file)
        self.assertEqual(resolved.asset, str(pathlib.Path(root, "src/globals.css")))

    def test_an_unresolvable_relative_import_resolves_to_nothing(self):
        root = _repo({"src/b.ts": ""})
        resolved = Resolver(root).resolve(str(pathlib.Path(root, "src/b.ts")), "./missing")
        self.assertIsNone(resolved.file)
        self.assertIsNone(resolved.asset)
        self.assertFalse(resolved.third_party)


class TsconfigAliasTests(unittest.TestCase):
    def test_a_wildcard_alias_resolves_against_baseurl(self):
        root = _repo({
            "tsconfig.json": (
                '{"compilerOptions": {"baseUrl": ".", "paths": {"@/*": ["./src/*"]}}}'
            ),
            "src/components/Foo.tsx": "",
            "app/page.tsx": "",
        })
        resolved = Resolver(root).resolve(str(pathlib.Path(root, "app/page.tsx")), "@/components/Foo")
        self.assertEqual(resolved.file, str(pathlib.Path(root, "src/components/Foo.tsx")))

    def test_extends_merges_paths_and_inherits_baseurl(self):
        root = _repo({
            "tsconfig.base.json": (
                '{"compilerOptions": {"baseUrl": ".", "paths": {"@/*": ["./src/*"]}}}'
            ),
            "tsconfig.json": '{"extends": "./tsconfig.base.json", "compilerOptions": {}}',
            "src/lib/util.ts": "",
            "app/page.tsx": "",
        })
        resolved = Resolver(root).resolve(str(pathlib.Path(root, "app/page.tsx")), "@/lib/util")
        self.assertEqual(resolved.file, str(pathlib.Path(root, "src/lib/util.ts")))

    def test_a_child_paths_entry_overrides_the_parent_on_collision(self):
        root = _repo({
            "tsconfig.base.json": (
                '{"compilerOptions": {"baseUrl": ".", "paths": {"@/*": ["./old/*"]}}}'
            ),
            "tsconfig.json": (
                '{"extends": "./tsconfig.base.json", '
                '"compilerOptions": {"paths": {"@/*": ["./src/*"]}}}'
            ),
            "src/x.ts": "",
            "old/x.ts": "",
            "app/page.tsx": "",
        })
        resolved = Resolver(root).resolve(str(pathlib.Path(root, "app/page.tsx")), "@/x")
        self.assertEqual(resolved.file, str(pathlib.Path(root, "src/x.ts")))

    def test_jsonc_comments_and_trailing_commas_do_not_break_parsing(self):
        root = _repo({
            "tsconfig.json": (
                "{\n"
                "  // a comment\n"
                '  "compilerOptions": {"baseUrl": ".", "paths": {"@/*": ["./src/*"],},},\n'
                "}\n"
            ),
            "src/x.ts": "",
            "app/page.tsx": "",
        })
        resolved = Resolver(root).resolve(str(pathlib.Path(root, "app/page.tsx")), "@/x")
        self.assertEqual(resolved.file, str(pathlib.Path(root, "src/x.ts")))


class WorkspacePackageTests(unittest.TestCase):
    def test_a_workspace_package_resolves_through_its_manifest_main(self):
        root = _repo({
            "package.json": '{"workspaces": ["packages/*"]}',
            "packages/ui/package.json": '{"name": "@scope/ui", "main": "src/index.ts"}',
            "packages/ui/src/index.ts": "",
            "app/page.tsx": "",
        })
        resolved = Resolver(root).resolve(str(pathlib.Path(root, "app/page.tsx")), "@scope/ui")
        self.assertEqual(resolved.file, str(pathlib.Path(root, "packages/ui/src/index.ts")))

    def test_a_deep_import_into_a_workspace_package_resolves_relative_to_its_source(self):
        root = _repo({
            "package.json": '{"workspaces": ["packages/*"]}',
            "packages/ui/package.json": '{"name": "@scope/ui", "main": "src/index.ts"}',
            "packages/ui/src/Button.ts": "",
            "app/page.tsx": "",
        })
        resolved = Resolver(root).resolve(str(pathlib.Path(root, "app/page.tsx")), "@scope/ui/Button")
        self.assertEqual(resolved.file, str(pathlib.Path(root, "packages/ui/src/Button.ts")))


class ThirdPartyTests(unittest.TestCase):
    def test_a_bare_specifier_with_no_first_party_match_is_third_party(self):
        root = _repo({"app/page.tsx": ""})
        resolved = Resolver(root).resolve(str(pathlib.Path(root, "app/page.tsx")), "react")
        self.assertTrue(resolved.third_party)
        self.assertIsNone(resolved.file)
        self.assertIsNone(resolved.asset)


class NormPathTests(unittest.TestCase):
    def test_norm_path_is_repo_relative_and_forward_slashed(self):
        root = _repo({"src/a.ts": ""})
        self.assertEqual(norm_path(str(pathlib.Path(root, "src/a.ts")), root), "src/a.ts")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest seamcheck/tests/test_resolve.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'seamcheck.resolve'`

- [ ] **Step 3: Write the implementation**

```python
# seamcheck/resolve.py
"""One import resolver, shared by every walk that follows a first-party JS/TS
import: the JS extractor's file discovery, the module graph in
`extractors/js_extractor.py`, and every `EntrySource`. Centralised so a
`@/x` alias or a workspace package resolves the same way everywhere, instead
of each walker growing its own partial version.

Resolution order (docs/plans/2026-09-15-entry-sources-design.md section 2a):
1. Relative paths.
2. tsconfig.json/jsconfig.json `paths` + `baseUrl`, nearest config, following `extends`.
3. Workspace packages (`@scope/ui` -> that package's `exports`/`source`/`main`/`src/index.*`).
4. A non-script import (CSS, JSON, an image, ...) is an asset: recorded, not walked.
5. Anything left is third-party.
"""
from __future__ import annotations

import json
import os
import pathlib
import re
from dataclasses import dataclass

from seamcheck.services import _globs_from_workspaces

# The order js_extractor._resolve_import used, so every walk that switches to this
# resolver picks the same file it always did when a folder holds index.js AND index.ts.
_INDEX_NAMES = ("index.js", "index.mjs", "index.ts", "index.tsx", "index.jsx", "index.cjs")
_ASSET_EXTENSIONS = (
    ".css", ".scss", ".sass", ".less", ".json", ".svg", ".png", ".jpg", ".jpeg",
    ".gif", ".webp", ".ico", ".woff", ".woff2", ".ttf", ".eot", ".mp4", ".mp3",
)
_CONFIG_NAMES = ("tsconfig.json", "jsconfig.json")


def _js_extensions() -> tuple[str, ...]:
    # Deferred import: js_extractor.py imports Resolver from this module (Task 3), so
    # importing js_extractor at module load time here would be circular.
    from seamcheck.extractors.js_extractor import _JS_EXTENSIONS
    return _JS_EXTENSIONS


@dataclass(frozen=True)
class ResolvedImport:
    file: str | None = None
    asset: str | None = None
    third_party: bool = False


def norm_path(path: str, repo_root: str) -> str:
    """A repo-relative, forward-slashed path - the one shape `symbol.file` is always in."""
    try:
        relative = os.path.relpath(os.path.abspath(path), os.path.abspath(repo_root))
    except ValueError:
        relative = os.path.normpath(path)
    return relative.replace(os.sep, "/")


def _classify(path: str | None) -> ResolvedImport:
    if path is None:
        return ResolvedImport(third_party=True)
    if os.path.splitext(path)[1].lower() in _ASSET_EXTENSIONS:
        return ResolvedImport(asset=path)
    return ResolvedImport(file=path)


def _resolve_bare(path_without_extension: str) -> str | None:
    if os.path.isfile(path_without_extension):
        return path_without_extension
    for extension in _js_extensions() + _ASSET_EXTENSIONS:
        if os.path.isfile(path_without_extension + extension):
            return path_without_extension + extension
    if os.path.isdir(path_without_extension):
        for index_name in _INDEX_NAMES:
            candidate = os.path.join(path_without_extension, index_name)
            if os.path.isfile(candidate):
                return candidate
    return None


def _resolve_relative(current_file: str, import_path: str) -> str | None:
    base = os.path.normpath(os.path.join(os.path.dirname(current_file), import_path))
    return _resolve_bare(base)


def _load_jsonc(path: str) -> dict:
    try:
        text = pathlib.Path(path).read_text(encoding="utf-8")
    except OSError:
        return {}
    text = re.sub(r"//.*", "", text)
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r",(\s*[}\]])", r"\1", text)  # trailing commas
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


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


def _is_within(path: str, root: str) -> bool:
    try:
        return os.path.commonpath([path, root]) == root
    except ValueError:  # different drives on Windows
        return False


def _resolve_extends_path(config_dir: str, extends: str) -> str | None:
    candidate = pathlib.Path(config_dir, extends)
    if candidate.is_file():
        return str(candidate)
    with_suffix = candidate if candidate.suffix else candidate.with_name(candidate.name + ".json")
    if with_suffix.is_file():
        return str(with_suffix)
    return None


def _load_alias_chain(config_path: str, seen: frozenset[str]) -> tuple[str, dict[str, list[str]]] | None:
    """(base_dir, paths) for one tsconfig/jsconfig, `extends` followed recursively.
    `base_dir` is `baseUrl` resolved against whichever config in the chain defines it
    (nearest/child wins); `paths` merges the whole chain, child overriding on collision.
    """
    if config_path in seen:
        return None
    seen = seen | {config_path}
    data = _load_jsonc(config_path)
    config_dir = str(pathlib.Path(config_path).parent)
    options = data.get("compilerOptions") or {}
    paths = dict(options.get("paths") or {})
    base_dir = str(pathlib.Path(config_dir, options["baseUrl"])) if "baseUrl" in options else None

    extends = data.get("extends")
    if isinstance(extends, str):
        extended = _resolve_extends_path(config_dir, extends)
        if extended:
            parent = _load_alias_chain(extended, seen)
            if parent is not None:
                parent_base, parent_paths = parent
                merged = dict(parent_paths)
                merged.update(paths)
                paths = merged
                if base_dir is None:
                    base_dir = parent_base

    return (base_dir or config_dir, paths)


class AliasIndex:
    def __init__(self, base_dir: str, paths: dict[str, list[str]]):
        self.base_dir = base_dir
        self.paths = paths

    def candidates(self, import_path: str) -> list[str]:
        found = []
        for alias, targets in self.paths.items():
            if alias.endswith("*"):
                prefix = alias[:-1]
                if not import_path.startswith(prefix):
                    continue
                remainder = import_path[len(prefix):]
                for target in targets:
                    stem = target[:-1] if target.endswith("*") else target
                    found.append(str(pathlib.Path(self.base_dir, stem + remainder)))
            elif alias == import_path:
                for target in targets:
                    found.append(str(pathlib.Path(self.base_dir, target)))
        return found


def _package_entry(package_dir: pathlib.Path, data: dict) -> str | None:
    exports = data.get("exports")
    if isinstance(exports, str):
        return str(package_dir / exports)
    if isinstance(exports, dict):
        default = exports.get(".") or exports.get("default")
        if isinstance(default, str):
            return str(package_dir / default)
        if isinstance(default, dict):
            for key in ("import", "default", "require"):
                if isinstance(default.get(key), str):
                    return str(package_dir / default[key])
    for key in ("source", "main"):
        if isinstance(data.get(key), str):
            return str(package_dir / data[key])
    for candidate in ("src/index.ts", "src/index.tsx", "src/index.js", "index.ts", "index.tsx", "index.js"):
        if (package_dir / candidate).is_file():
            return str(package_dir / candidate)
    return None


def _build_workspace_index(project_root: str) -> dict[str, str]:
    root = pathlib.Path(project_root)
    index: dict[str, str] = {}
    try:
        patterns = _globs_from_workspaces(root)
    except OSError:
        return index
    for pattern in patterns:
        for package_dir in sorted(root.glob(pattern)):
            if not package_dir.is_dir():
                continue
            manifest = package_dir / "package.json"
            if not manifest.is_file():
                continue
            try:
                data = json.loads(manifest.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            name = data.get("name")
            if not name:
                continue
            source = _package_entry(package_dir, data)
            if source:
                index[name] = source
    return index


class Resolver:
    """Built once per scan for one project root; reused by every import walk in it."""

    def __init__(self, project_root: str):
        self.project_root = project_root
        self._alias_cache: dict[str, AliasIndex | None] = {}
        self._workspace_index: dict[str, str] | None = None

    def resolve(self, current_file: str, import_path: str) -> ResolvedImport:
        if import_path.startswith("."):
            return _classify(_resolve_relative(current_file, import_path))

        alias_index = self._alias_index_for(os.path.dirname(current_file))
        if alias_index is not None:
            for candidate in alias_index.candidates(import_path):
                resolved = _resolve_bare(candidate)
                if resolved is not None:
                    return _classify(resolved)

        for name, source in self._workspaces().items():
            if import_path == name:
                resolved = _resolve_bare(source)
                if resolved is not None:
                    return _classify(resolved)
            elif import_path.startswith(name + "/"):
                remainder = import_path[len(name) + 1:]
                base = str(pathlib.Path(source).parent / remainder)
                resolved = _resolve_bare(base)
                if resolved is not None:
                    return _classify(resolved)

        return ResolvedImport(third_party=True)

    def _alias_index_for(self, start_dir: str) -> AliasIndex | None:
        config_path = _find_config(start_dir, self.project_root)
        if config_path is None:
            return None
        if config_path not in self._alias_cache:
            chain = _load_alias_chain(config_path, frozenset())
            self._alias_cache[config_path] = (
                AliasIndex(chain[0], chain[1]) if chain and chain[1] else None
            )
        return self._alias_cache[config_path]

    def _workspaces(self) -> dict[str, str]:
        if self._workspace_index is None:
            self._workspace_index = _build_workspace_index(self.project_root)
        return self._workspace_index
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest seamcheck/tests/test_resolve.py -v`
Expected: PASS (11 tests). If `test_extends_merges_paths_and_inherits_baseurl` or the override test fails, print `chain` inside `_load_alias_chain` to debug the merge order before changing the merge logic blindly.

- [ ] **Step 5: Lint and commit**

```bash
ruff check seamcheck/resolve.py seamcheck/tests/test_resolve.py
git add seamcheck/resolve.py seamcheck/tests/test_resolve.py
git commit -m "feat(resolve): add the shared import resolver (relative, tsconfig aliases, workspaces)"
```

---

## Task 3: One resolver for every JS walk, and a module graph walked out from the roots

**Files:**
- Modify: `seamcheck/extractors/js_extractor.py` — delete `_resolve_import` (lines 316-330); route its three call sites through `seamcheck.resolve.Resolver` (`discover_js_files` line 414, `discover_dynamic_import_targets` line 447, `extract_js` line 639 — the local names are `path` and `import_path` at all three); add `ModuleGraph` and `build_module_graph`.
- Test: `seamcheck/tests/test_module_graph.py` (new)

**Interfaces:**
- Consumes: `seamcheck.resolve.Resolver` (Task 2); existing `iter_parsed(paths, *, report_failures=True)` (line 177), `_imported_paths(ast) -> list[str]` (line 312), `_JS_EXTENSIONS` (line 21).
- Produces: `ModuleGraph` (dataclass: `edges: dict[str, frozenset[str]]`, `assets: dict[str, frozenset[str]]`; `reachable_files(roots) -> set[str]`; `reachable_assets(roots) -> set[str]`) and `build_module_graph(project_root: str, roots: list[str]) -> ModuleGraph`. It **walks outward from `roots`**; every path in and out is absolute.

**This task changes what `scan()` reads.** `extract_js` and `discover_js_files` follow a tsconfig alias or a workspace package after it, where before they stopped at anything not starting with `.`. On leanos-app the walk from `app/page.tsx` goes from 1 file to 83 (design doc, measured). The corpus `scan` gate cannot see this (it never runs the JS extractor); Task 15 measures it.

- [ ] **Step 1: Write the failing tests**

```python
# seamcheck/tests/test_module_graph.py
import os
import pathlib
import tempfile
import unittest

from seamcheck.extractors.js_extractor import build_module_graph, discover_js_files


def _repo(files: dict[str, str]) -> str:
    root = pathlib.Path(tempfile.mkdtemp())
    for name, body in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    return str(root)


def _abs(root: str, *names: str) -> set[str]:
    return {os.path.join(root, name) for name in names}


ALIAS = '{"compilerOptions": {"baseUrl": ".", "paths": {"@/*": ["./*"]}}}'


class ModuleGraphTests(unittest.TestCase):
    def test_walking_out_from_one_root_reaches_everything_it_imports(self):
        root = _repo({"a.ts": "import './b'", "b.ts": "import './c'", "c.ts": "export const x = 1;"})
        graph = build_module_graph(root, [os.path.join(root, "a.ts")])
        self.assertEqual(graph.reachable_files([os.path.join(root, "a.ts")]),
                         _abs(root, "a.ts", "b.ts", "c.ts"))

    def test_one_graph_answers_each_roots_reach_separately(self):
        root = _repo({"a.ts": "import './shared'", "b.ts": "export const y = 2;", "shared.ts": ""})
        graph = build_module_graph(root, [os.path.join(root, "a.ts"), os.path.join(root, "b.ts")])
        self.assertEqual(graph.reachable_files([os.path.join(root, "a.ts")]), _abs(root, "a.ts", "shared.ts"))
        self.assertEqual(graph.reachable_files([os.path.join(root, "b.ts")]), _abs(root, "b.ts"))

    def test_a_stylesheet_import_is_reached_as_an_asset_and_not_walked(self):
        root = _repo({"a.ts": "import './styles.css'", "styles.css": ".x { color: red }"})
        graph = build_module_graph(root, [os.path.join(root, "a.ts")])
        self.assertEqual(graph.reachable_files([os.path.join(root, "a.ts")]), _abs(root, "a.ts"))
        self.assertEqual(graph.reachable_assets([os.path.join(root, "a.ts")]), _abs(root, "styles.css"))

    def test_an_alias_import_is_followed(self):
        root = _repo({
            "tsconfig.json": ALIAS,
            "app/page.tsx": "import { X } from '@/components/X'",
            "components/X.tsx": "export const X = 1;",
        })
        graph = build_module_graph(root, [os.path.join(root, "app/page.tsx")])
        self.assertIn(os.path.join(root, "components/X.tsx"),
                      graph.reachable_files([os.path.join(root, "app/page.tsx")]))

    def test_a_package_import_is_not_an_edge(self):
        root = _repo({"a.ts": "import React from 'react'"})
        graph = build_module_graph(root, [os.path.join(root, "a.ts")])
        self.assertEqual(graph.reachable_files([os.path.join(root, "a.ts")]), _abs(root, "a.ts"))

    def test_a_root_that_is_not_javascript_is_its_own_whole_reach(self):
        # A Python route handler is an entry too; its reach is its own file, and the
        # call graph - not this walk - carries it further.
        root = _repo({"views.py": "def x(request): pass"})
        graph = build_module_graph(root, [os.path.join(root, "views.py")])
        self.assertEqual(graph.reachable_files([os.path.join(root, "views.py")]), _abs(root, "views.py"))


class ExistingWalksFollowAliasesTests(unittest.TestCase):
    def test_discover_js_files_now_follows_a_tsconfig_alias(self):
        root = _repo({
            "tsconfig.json": ALIAS,
            "app/page.tsx": "import { X } from '@/components/X'",
            "components/X.tsx": "export const X = 1;",
        })
        self.assertIn(os.path.join(root, "components/X.tsx"), discover_js_files(["app/page.tsx"], root))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest seamcheck/tests/test_module_graph.py -v`
Expected: FAIL — `ImportError: cannot import name 'build_module_graph'`

- [ ] **Step 3: Implement**

In `seamcheck/extractors/js_extractor.py`:

1. Add to the imports: `from dataclasses import dataclass` and `from seamcheck.resolve import Resolver`. (`resolve.py` imports `js_extractor` only inside a function, so this is not a cycle.)

2. Replace `_resolve_import` (lines 316-330) with:

```python
def _resolved_file(resolver: Resolver, current_file: str, import_path: str) -> str | None:
    """The script file an import names, or None: a package, an asset, or nothing on disk.

    Through `Resolver`, so `@/components/x` is followed like `./x`. Anything that did not
    start with "." used to be treated as node_modules, and a Next.js page importing through
    its tsconfig alias reached 1 file of the 83 it uses (leanos-app).
    """
    return resolver.resolve(current_file, import_path).file
```

3. In `discover_js_files`, `discover_dynamic_import_targets` and `extract_js`, add `resolver = Resolver(project_root)` as the first line of the body, and change `_resolve_import(path, import_path)` to `_resolved_file(resolver, path, import_path)`.

4. After `discover_dynamic_import_targets`, add:

```python
@dataclass
class ModuleGraph:
    """The first-party import graph reachable from a set of roots, parsed once.

    `edges` holds each file's first-party script imports; `assets` its non-script ones
    (CSS, JSON, images), recorded so a page's reach includes the stylesheet it imports
    without walking into it.
    """

    edges: dict[str, frozenset[str]]
    assets: dict[str, frozenset[str]]

    def reachable_files(self, roots) -> set[str]:
        seen: set[str] = set()
        stack = [root for root in roots if root]
        while stack:
            current = stack.pop()
            if current in seen:
                continue
            seen.add(current)
            stack.extend(target for target in self.edges.get(current, ()) if target not in seen)
        return seen

    def reachable_assets(self, roots) -> set[str]:
        found: set[str] = set()
        for path in self.reachable_files(roots):
            found |= self.assets.get(path, frozenset())
        return found


def build_module_graph(project_root: str, roots: list[str]) -> ModuleGraph:
    """Walk outward from every root at once, parsing each reachable file one time.

    Handed every page's roots together, one graph answers every page's reach: pages share
    most of their imports, and walking them once per root is what `page_files` used to
    spend ~13 s on for the reference project.
    """
    resolver = Resolver(project_root)
    edges: dict[str, frozenset[str]] = {}
    assets: dict[str, frozenset[str]] = {}
    to_visit = [os.path.abspath(root) for root in roots]
    while to_visit:
        batch = [
            path for path in dict.fromkeys(to_visit)
            if path not in edges and path.endswith(_JS_EXTENSIONS) and os.path.isfile(path)
        ]
        to_visit = []
        if not batch:
            break
        for path in batch:
            edges[path] = frozenset()   # claimed before parsing, so a cycle stops here
        for path, ast in iter_parsed(batch):
            scripts: set[str] = set()
            others: set[str] = set()
            for import_path in _imported_paths(ast):
                resolved = resolver.resolve(path, import_path)
                if resolved.file:
                    scripts.add(resolved.file)
                    if resolved.file not in edges:
                        to_visit.append(resolved.file)
                elif resolved.asset:
                    others.add(resolved.asset)
            edges[path] = frozenset(scripts)
            assets[path] = frozenset(others)
    return ModuleGraph(edges=edges, assets=assets)
```

- [ ] **Step 4: Run the new tests, the JS extractor tests, then the whole suite**

Run: `python -m pytest seamcheck/tests/test_module_graph.py seamcheck/tests/test_js_extractor.py seamcheck/tests/test_dom_js_extractor.py -v`
Expected: PASS. The fixture walks in `test_js_extractor.py` use only relative imports, which resolve exactly as before.

Run: `python -m pytest seamcheck/tests -q -p no:cacheprovider`
Expected: 1,718 + the new tests passed, 0 failed (baseline in this worktree: 1,718 passed). This task changes the scan, so the whole suite runs here rather than at the end.

- [ ] **Step 5: Lint and commit**

```bash
ruff check seamcheck/
git add seamcheck/extractors/js_extractor.py seamcheck/tests/test_module_graph.py
git commit -m "feat(js_extractor): follow tsconfig aliases and workspace packages in every import walk

Every walk resolved only imports starting with '.', so a Next.js page importing through
its '@/' alias reached 1 of the 83 files it uses (leanos-app). The three walks now go
through seamcheck/resolve.py, and a module graph built once from every root at once
answers each page's reach.

This changes what scan() reads on alias-using repositories. tools/corpus.py scan does
not run the JS extractor and cannot show it; measured in the phase-1 verification."
```

---

## Task 4: The entry-source registry — `select_all` and `all_entries`

**Do this task after Task 8.** `seamcheck/entries/__init__.py` imports all four sources at module level, so it can only be written once Tasks 5-8 have created them. The numbering is kept so references elsewhere stay valid.

**Files:**
- Modify: `seamcheck/entries/__init__.py` (empty since Task 1)
- Test: `seamcheck/tests/test_entries_registry.py`

**Interfaces:**
- Consumes: `Entry`, `EntrySource` (Task 1); `LegacySource` (Task 5), `FallbackSource` (Task 6), `ServerEntrySource` (Task 7), `NextJSSource` (Task 8).
- Produces: `available() -> list[str]`, `select_all(repo_root: str, config: dict) -> list[tuple[EntrySource, float]]`, `all_entries(repo_root: str, config: dict, graph) -> list[Entry]`.

- [ ] **Step 1: Write the failing tests**

```python
# seamcheck/tests/test_entries_registry.py
import unittest
from unittest import mock

import seamcheck.entries as entries
from seamcheck.entries.base import Entry


class _Stub:
    def __init__(self, name, confidence, found=()):
        self.name = name
        self._confidence = confidence
        self._found = list(found)

    def detect(self, repo_root, config):
        return self._confidence

    def entries(self, repo_root, config, graph):
        return list(self._found)


def _entry(key, kind="page", roots=()):
    return Entry(key=key, kind=kind, roots=tuple(roots), title=key, where="")


class SelectAllTests(unittest.TestCase):
    def test_every_confident_source_runs(self):
        with mock.patch.object(entries, "_SOURCES", (_Stub("low", 0.2), _Stub("a", 0.9), _Stub("b", 0.6))):
            chosen = entries.select_all("/repo", {})
        self.assertEqual([source.name for source, _ in chosen], ["a", "b"])

    def test_when_nothing_is_confident_the_best_guess_still_runs(self):
        with mock.patch.object(entries, "_SOURCES", (_Stub("low", 0.1), _Stub("lower", 0.05))):
            chosen = entries.select_all("/repo", {})
        self.assertEqual([source.name for source, _ in chosen], ["low"])

    def test_entry_sources_forces_the_choice(self):
        with mock.patch.object(entries, "_SOURCES", (_Stub("a", 0.9), _Stub("b", 0.1))):
            chosen = entries.select_all("/repo", {"entry_sources": ["b"]})
        self.assertEqual([source.name for source, _ in chosen], ["b"])

    def test_entry_sources_as_one_string_means_that_one_source(self):
        with mock.patch.object(entries, "_SOURCES", (_Stub("a", 0.9), _Stub("b", 0.1))):
            chosen = entries.select_all("/repo", {"entry_sources": "b"})
        self.assertEqual([source.name for source, _ in chosen], ["b"])

    def test_an_unknown_forced_source_names_the_ones_that_exist(self):
        with mock.patch.object(entries, "_SOURCES", (_Stub("a", 0.9),)):
            with self.assertRaises(ValueError) as raised:
                entries.select_all("/repo", {"entry_sources": ["nope"]})
        self.assertIn("'nope'", str(raised.exception))
        self.assertIn("Available: a", str(raised.exception))


class AllEntriesTests(unittest.TestCase):
    def test_entries_from_every_selected_source_are_combined(self):
        sources = (_Stub("a", 0.9, [_entry("a1")]), _Stub("b", 0.9, [_entry("b1")]))
        with mock.patch.object(entries, "_SOURCES", sources):
            found = entries.all_entries("/repo", {}, graph=None)
        self.assertEqual({entry.key for entry in found}, {"a1", "b1"})

    def test_a_page_file_is_a_page_not_also_a_server_entry(self):
        page = _entry("next:/", "page", ["app/page.tsx", "app/layout.tsx"])
        same_file = _entry("server:app/page.tsx", "server", ["app/page.tsx"])
        handler = _entry("server:app/api/x/route.ts", "server", ["app/api/x/route.ts"])
        sources = (_Stub("next", 0.9, [page]), _Stub("server", 0.6, [same_file, handler]))
        with mock.patch.object(entries, "_SOURCES", sources):
            found = entries.all_entries("/repo", {}, graph=None)
        self.assertEqual([entry.key for entry in found], ["next:/", "server:app/api/x/route.ts"])

    def test_the_fallback_runs_only_when_everything_else_found_nothing(self):
        fallback = _entry("entry_file:main.js", "entry_file")
        with mock.patch.object(entries, "_SOURCES", (_Stub("empty", 0.9),)), \
             mock.patch.object(entries, "FallbackSource") as fallback_source:
            fallback_source.return_value.entries.return_value = [fallback]
            found = entries.all_entries("/repo", {}, graph=None)
        self.assertEqual(found, [fallback])

    def test_the_fallback_does_not_run_when_something_was_found(self):
        with mock.patch.object(entries, "_SOURCES", (_Stub("real", 0.9, [_entry("r1")]),)), \
             mock.patch.object(entries, "FallbackSource") as fallback_source:
            entries.all_entries("/repo", {}, graph=None)
        fallback_source.return_value.entries.assert_not_called()


class RegistryContentsTests(unittest.TestCase):
    def test_the_registry_holds_the_phase_one_sources(self):
        self.assertEqual(entries.available(), ["nextjs", "server", "legacy"])
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest seamcheck/tests/test_entries_registry.py -v`
Expected: FAIL — `AttributeError: module 'seamcheck.entries' has no attribute '_SOURCES'`

- [ ] **Step 3: Implement**

```python
# seamcheck/entries/__init__.py
"""Where a page comes from, for every stack - the second adapter layer.

Mirrors `seamcheck/adapters/__init__.py`: every source at or above 0.5 confidence runs,
so a monorepo gets all of them (leanos-app is Next.js pages AND route handlers), and the
`entry_sources` config key forces a choice the way `server_adapter` does.
"""

from __future__ import annotations

from seamcheck.entries.base import Entry, EntrySource
from seamcheck.entries.fallback import FallbackSource
from seamcheck.entries.legacy import LegacySource
from seamcheck.entries.nextjs import NextJSSource
from seamcheck.entries.server import ServerEntrySource

_SOURCES: tuple[EntrySource, ...] = (NextJSSource(), ServerEntrySource(), LegacySource())
_CONFIDENT = 0.5

__all__ = ["Entry", "EntrySource", "all_entries", "available", "select_all"]


def available() -> list[str]:
    return [source.name for source in _SOURCES]


def _ranked(repo_root: str, config: dict) -> list[tuple[EntrySource, float]]:
    return sorted(((source, source.detect(repo_root, config)) for source in _SOURCES),
                  key=lambda pair: -pair[1])


def select_all(repo_root: str, config: dict) -> list[tuple[EntrySource, float]]:
    config = config or {}
    forced = config.get("entry_sources")
    if forced:
        names = [forced] if isinstance(forced, str) else list(forced)
        by_name = {source.name: source for source in _SOURCES}
        for name in names:
            if name not in by_name:
                raise ValueError(
                    f"Unknown entry source {name!r}. Available: {', '.join(available())}"
                )
        return [(by_name[name], 1.0) for name in names]
    ranked = _ranked(repo_root, config)
    confident = [pair for pair in ranked if pair[1] >= _CONFIDENT]
    return confident or ranked[:1]


def all_entries(repo_root: str, config: dict, graph) -> list[Entry]:
    """Every entry from every selected source; the fallback only when all found nothing."""
    config = config or {}
    found: list[Entry] = []
    for source, _confidence in select_all(repo_root, config):
        found.extend(source.entries(repo_root, config, graph))
    # A Next.js page file also carries a `view` symbol, so it arrives twice - as the page
    # it is and as a server entry. The page is the name a reader knows it by.
    on_pages = {root for entry in found if entry.kind == "page" for root in entry.roots}
    found = [entry for entry in found
             if not (entry.kind == "server" and set(entry.roots) <= on_pages)]
    if not found:
        found = FallbackSource().entries(repo_root, config, graph)
    return found
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest seamcheck/tests/test_entries_registry.py seamcheck/tests/test_entries_base.py -v`
Expected: PASS

- [ ] **Step 5: Lint and commit**

```bash
ruff check seamcheck/entries/ seamcheck/tests/test_entries_registry.py
git add seamcheck/entries/__init__.py seamcheck/tests/test_entries_registry.py
git commit -m "feat(entries): the source registry - select_all, all_entries, and page-over-server dedup"
```

---

## Task 5: `LegacySource` — today's pages, and the sweep dropped when Next.js has pages

**Files:**
- Modify: `seamcheck/api.py` — extract `_entry_roots()` from `_js_roots()` (lines 154-190)
- Create: `seamcheck/entries/legacy.py`
- Test: `seamcheck/tests/test_entries_legacy.py`

**Interfaces:**
- Consumes: `seamcheck.api._discover_roots(config, repo_root)` (existing, line 133); `seamcheck.pagenames.page_names(repo_root, config, graph)`; `seamcheck.autoconfig.declared_config() -> dict` (existing, line 442 — `SEAMCHECK_CONFIG` as written, no detection); `seamcheck.adapters.nextjs_adapter.NextJSAdapter().detect`; `seamcheck.nodetools.report(what, message, *args)`; `seamcheck.resolve.norm_path`.
- Produces: `api._entry_roots(config: dict, repo_root: str) -> list[str]`; `LegacySource` (`name = "legacy"`).

**Why the sweep is dropped here and not in autoconfig.** For a project with no bundler, autoconfig writes every loose `.js` file into `js_entry_files`. Those files matter twice: `scan()` reads them as the JS extractor's *entries* (in full, URL literals recorded), and `page_files()` turns each into a *page*. Removing them from autoconfig would change the first. The design doc only asks to change the second. So autoconfig is untouched, and `LegacySource` returns no pages from a detected sweep when a page-producing source matched. A `js_entry_files` written in `SEAMCHECK_CONFIG` is never dropped.

**Test note:** `conftest.py` configures Django with `SEAMCHECK_CONFIG = {"js_entry_files": [], ...}`, so inside the test suite `declared_config()` reports `js_entry_files` as written by hand. Every sweep test patches `seamcheck.autoconfig.declared_config`.

- [ ] **Step 1: Extract `_entry_roots` in `api.py`**

Add above `_js_roots`:

```python
def _entry_roots(config: dict, repo_root: str) -> list[str]:
    """The declared entry points alone: `js_entry_files` when the config has one, otherwise
    a Vite config's entries and the scripts templates load. `_js_roots` adds the rest of
    the first-party tree for the JS extractor; a page list wants only these."""
    if "js_entry_files" in config:
        return list(config["js_entry_files"])
    return _discover_roots(config, repo_root)
```

In `_js_roots`, replace

```python
    if "js_entry_files" in config:
        entries = list(config["js_entry_files"])
        project_root = config.get("js_project_root", repo_root)
    else:
        entries, project_root = _discover_roots(config, repo_root), repo_root
```

with

```python
    entries = _entry_roots(config, repo_root)
    project_root = (config.get("js_project_root", repo_root)
                    if "js_entry_files" in config else repo_root)
```

Run: `python -m pytest seamcheck/tests/test_roots.py seamcheck/tests/test_pagenames.py seamcheck/tests/test_scoped_findings.py seamcheck/tests/test_scoped_map_document.py -q -p no:cacheprovider`
Expected: PASS, unchanged (a pure extraction).

- [ ] **Step 2: Write the failing tests**

```python
# seamcheck/tests/test_entries_legacy.py
import os
import pathlib
import tempfile
import unittest
from unittest import mock

from seamcheck.entries.legacy import LegacySource
from seamcheck.roots import discover_js_roots


def _repo(files: dict[str, str]) -> str:
    root = pathlib.Path(tempfile.mkdtemp())
    for name, body in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    return str(root)


TEMPLATE_SITE = {
    "templates/home.html": '<script src="/static/js/app.js"></script>',
    "static/js/app.js": "console.log(1);",
}
NEXT_SITE = {
    "next.config.js": "module.exports = {};",
    "app/page.tsx": "export default function P() { return null; }",
    "scripts/loose.js": "console.log(1);",
}
NOT_DECLARED = mock.patch("seamcheck.autoconfig.declared_config", return_value={})


class TodaysPagesTests(unittest.TestCase):
    def test_a_template_loaded_script_is_a_page_keyed_by_its_filename(self):
        root = _repo(TEMPLATE_SITE)
        found = LegacySource().entries(root, {"templates_root": "templates"}, graph=None)
        self.assertEqual([(e.key, e.kind, e.roots) for e in found],
                         [("app", "page", ("static/js/app.js",))])

    def test_roots_are_exactly_discover_js_roots_in_the_same_order(self):
        # The legacy guard: this source returns today's roots, today's order.
        root = _repo({**TEMPLATE_SITE,
                      "templates/other.html": '<script src="/static/js/other.js"></script>',
                      "static/js/other.js": ""})
        expected = discover_js_roots(
            vite_config=os.path.join(root, "vite.config.js"),
            templates_root=os.path.join(root, "templates"),
            static_root=os.path.join(root, "static"),
        )
        found = LegacySource().entries(root, {"templates_root": "templates"}, graph=None)
        self.assertEqual([e.roots[0] for e in found],
                         [os.path.relpath(path, root).replace(os.sep, "/") for path in expected])

    def test_a_root_nothing_names_keeps_todays_title_and_empty_address(self):
        root = _repo({"src/main.js": ""})
        found = LegacySource().entries(root, {"js_entry_files": ["src/main.js"]}, graph=None)
        self.assertEqual([(e.key, e.title, e.where) for e in found], [("main", "main", "")])


class DetectTests(unittest.TestCase):
    def test_detects_when_a_template_loads_a_script(self):
        self.assertEqual(LegacySource().detect(_repo(TEMPLATE_SITE), {"templates_root": "templates"}), 0.5)

    def test_does_not_detect_when_nothing_is_declared_or_discovered(self):
        self.assertEqual(LegacySource().detect(_repo({"README.md": ""}), {}), 0.0)


class SweepTests(unittest.TestCase):
    def test_a_detected_sweep_is_not_pages_when_nextjs_has_pages(self):
        root = _repo(NEXT_SITE)
        with NOT_DECLARED:
            found = LegacySource().entries(root, {"js_entry_files": ["scripts/loose.js"]}, graph=None)
        self.assertEqual(found, [])

    def test_a_js_entry_files_written_by_hand_is_never_dropped(self):
        root = _repo(NEXT_SITE)
        declared = {"js_entry_files": ["scripts/loose.js"]}
        with mock.patch("seamcheck.autoconfig.declared_config", return_value=declared):
            found = LegacySource().entries(root, dict(declared), graph=None)
        self.assertEqual([e.key for e in found], ["loose"])

    def test_a_detected_sweep_stays_when_nothing_else_has_pages(self):
        root = _repo({"scripts/loose.js": "console.log(1);"})
        with NOT_DECLARED:
            found = LegacySource().entries(root, {"js_entry_files": ["scripts/loose.js"]}, graph=None)
        self.assertEqual([e.key for e in found], ["loose"])

    def test_template_loaded_scripts_stay_even_beside_nextjs(self):
        root = _repo({**NEXT_SITE, **TEMPLATE_SITE})
        with NOT_DECLARED:
            found = LegacySource().entries(root, {"templates_root": "templates"}, graph=None)
        self.assertEqual([e.key for e in found], ["app"])
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `python -m pytest seamcheck/tests/test_entries_legacy.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'seamcheck.entries.legacy'`

- [ ] **Step 4: Implement**

```python
# seamcheck/entries/legacy.py
"""Today's pages, as a source: Vite entries, the scripts templates load, `js_entry_files`,
and autoconfig's sweep of loose `.js` files - with today's filename-stem keys and
`pagenames` titles, so a Django or Vite map does not change.

One deliberate exception. The sweep exists for a project with "no bundler to ask"
(autoconfig's own words). When a page-producing source matched, there is something to
ask, and the sweep's loose files are scripts rather than pages. They leave the page list
only: autoconfig still hands them to the JS extractor, so the scan reads them exactly as
before. A `js_entry_files` written in SEAMCHECK_CONFIG is the project saying what its
pages are, and is never dropped.
"""

from __future__ import annotations

import os

from seamcheck.entries.base import Entry
from seamcheck.graph import Graph
from seamcheck.nodetools import report
from seamcheck.resolve import norm_path

_PAGE_PRODUCING_CONFIDENCE = 0.5


def _page_producing_source_matched(repo_root: str, config: dict) -> bool:
    # Next.js is phase 1's only source whose entries are pages. Server entries do not
    # count: an Express API has no page to offer, so its sweep stays.
    from seamcheck.adapters.nextjs_adapter import NextJSAdapter

    return NextJSAdapter().detect(repo_root, config) >= _PAGE_PRODUCING_CONFIDENCE


def _sweep_was_detected(config: dict) -> bool:
    from seamcheck import autoconfig

    return "js_entry_files" in config and "js_entry_files" not in autoconfig.declared_config()


class LegacySource:
    name = "legacy"

    def detect(self, repo_root: str, config: dict) -> float:
        return 0.5 if self._roots(repo_root, config or {}) else 0.0

    def entries(self, repo_root: str, config: dict, graph) -> list[Entry]:
        config = config or {}
        roots = self._roots(repo_root, config)
        if not roots:
            return []
        from seamcheck.pagenames import page_names

        try:
            names = page_names(repo_root, config, graph if graph is not None else Graph(symbols=[], edges=[]))
        except Exception as error:  # noqa: BLE001 - a page under its file name beats no page
            report("entry-names", "Page names could not be read (%s); pages keep their file names.", error)
            names = {}
        found = []
        for root in roots:
            key = os.path.splitext(os.path.basename(root))[0]
            name = names.get(key)
            found.append(Entry(
                key=key, kind="page",
                roots=(norm_path(os.path.join(repo_root, root), repo_root),),
                title=name.title if name else key,
                where=name.where if name else "",
                evidence="a declared JavaScript entry point",
            ))
        return found

    def _roots(self, repo_root: str, config: dict) -> list[str]:
        from seamcheck.api import _entry_roots

        try:
            roots = _entry_roots(config, repo_root)
        except Exception as error:  # noqa: BLE001 - no source raises
            report("entry-legacy", "Declared entry points could not be read (%s).", error)
            return []
        if roots and _sweep_was_detected(config) and _page_producing_source_matched(repo_root, config):
            return []
        return roots
```

- [ ] **Step 5: Run the tests to verify they pass, and the legacy guard files unmodified**

Run: `python -m pytest seamcheck/tests/test_entries_legacy.py seamcheck/tests/test_roots.py seamcheck/tests/test_pagenames.py -v`
Expected: PASS. `git diff --stat seamcheck/tests/test_roots.py seamcheck/tests/test_pagenames.py` prints nothing.

- [ ] **Step 6: Lint and commit**

```bash
ruff check seamcheck/api.py seamcheck/entries/ seamcheck/tests/test_entries_legacy.py
git add seamcheck/api.py seamcheck/entries/legacy.py seamcheck/tests/test_entries_legacy.py
git commit -m "feat(entries): LegacySource - today's pages, and the .js sweep's pages dropped beside Next.js

The sweep leaves the page list only. autoconfig is untouched, so the JS extractor still
reads every swept file as before; a js_entry_files written in SEAMCHECK_CONFIG is never
dropped."
```

---

## Task 6: `FallbackSource` — scripts nothing imports, only when no source found anything

**Files:**
- Create: `seamcheck/entries/fallback.py`
- Test: `seamcheck/tests/test_entries_fallback.py`

**Interfaces:**
- Consumes: `seamcheck.extractors.url_reference_extractor.find_js_files(repo_root) -> list[str]` (existing, line 270; returns absolute paths only when handed an absolute root), `build_module_graph` (Task 3), `norm_path` (Task 2).
- Produces: `FallbackSource` (`name = "fallback"`, `detect` always `0.0`; `all_entries` calls it directly).

- [ ] **Step 1: Write the failing tests**

```python
# seamcheck/tests/test_entries_fallback.py
import pathlib
import tempfile
import unittest

from seamcheck.entries.fallback import FallbackSource


def _repo(files: dict[str, str]) -> str:
    root = pathlib.Path(tempfile.mkdtemp())
    for name, body in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    return str(root)


class FallbackSourceTests(unittest.TestCase):
    def test_a_script_nothing_imports_is_an_entry_file_named_by_its_filename(self):
        found = FallbackSource().entries(_repo({"src/main.js": "console.log(1);"}), {}, graph=None)
        self.assertEqual([(e.key, e.kind, e.roots, e.title, e.where) for e in found],
                         [("entry_file:src/main.js", "entry_file", ("src/main.js",), "main.js",
                           "no framework says this is a page")])

    def test_a_script_another_script_imports_is_not_an_entry(self):
        found = FallbackSource().entries(_repo({"main.js": "import './lib'", "lib.js": ""}), {}, graph=None)
        self.assertEqual([e.key for e in found], ["entry_file:main.js"])

    def test_a_relative_repo_root_gives_the_same_answer(self):
        root = _repo({"main.js": ""})
        here = pathlib.Path.cwd()
        try:
            import os
            os.chdir(root)
            found = FallbackSource().entries(".", {}, graph=None)
        finally:
            os.chdir(here)
        self.assertEqual([e.key for e in found], ["entry_file:main.js"])

    def test_no_scripts_means_no_entries(self):
        self.assertEqual(FallbackSource().entries(_repo({"README.md": ""}), {}, graph=None), [])

    def test_detect_is_zero_it_runs_only_when_asked(self):
        self.assertEqual(FallbackSource().detect(_repo({"main.js": ""}), {}), 0.0)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest seamcheck/tests/test_entries_fallback.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'seamcheck.entries.fallback'`

- [ ] **Step 3: Implement**

```python
# seamcheck/entries/fallback.py
"""The last resort, run only when every other source found nothing.

A first-party script no other first-party file imports is where something starts, even if
no framework says so. It is listed under its filename and says plainly that no framework
claims it - the same rule pagenames.py follows for a root no template loads: no invented
names.
"""

from __future__ import annotations

import os

from seamcheck.entries.base import Entry
from seamcheck.resolve import norm_path

# The same ceiling api._js_roots puts on a sweep of the whole tree, for the same reason:
# a repository with tens of thousands of scripts and no entry anywhere is rare, and
# parsing all of them turns a map into a wait.
_MAX_FILES = 4000


class FallbackSource:
    name = "fallback"

    def detect(self, repo_root: str, config: dict) -> float:
        return 0.0

    def entries(self, repo_root: str, config: dict, graph) -> list[Entry]:
        from seamcheck.extractors.js_extractor import build_module_graph
        from seamcheck.extractors.url_reference_extractor import find_js_files

        root = os.path.abspath(repo_root)
        files = sorted(os.path.abspath(path) for path in find_js_files(root))[:_MAX_FILES]
        if not files:
            return []
        modules = build_module_graph(root, files)
        imported = set().union(*modules.edges.values())
        found = []
        for path in files:
            if path in imported:
                continue
            relative = norm_path(path, root)
            found.append(Entry(
                key=f"entry_file:{relative}", kind="entry_file", roots=(relative,),
                title=os.path.basename(relative), where="no framework says this is a page",
                evidence="a first-party script no other first-party file imports",
            ))
        return found
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest seamcheck/tests/test_entries_fallback.py -v`
Expected: PASS

- [ ] **Step 5: Lint and commit**

```bash
ruff check seamcheck/entries/fallback.py seamcheck/tests/test_entries_fallback.py
git add seamcheck/entries/fallback.py seamcheck/tests/test_entries_fallback.py
git commit -m "feat(entries): FallbackSource - scripts nothing imports, when no source found a page"
```

---

## Task 7: `ServerEntrySource` — one entry per file that handles a request, titled by its URLs

**Files:**
- Create: `seamcheck/entries/server.py`
- Test: `seamcheck/tests/test_entries_server.py`

**Interfaces:**
- Consumes: `graph.symbols` / `graph.edges`. Every adapter emits a `url` symbol, a `view` symbol whose `file` is repo-relative, and an `Edge(from_id=url_id, to_id=view_id)` (Next.js `nextjs_adapter.py:187`, Express `express_adapter.py:477`, FastAPI `fastapi_adapter.py:414`, Flask, NestJS; Django through the URLconf reader).
- Produces: `ServerEntrySource` (`name = "server"`, `detect` a constant `0.6`).

**Why titles come from URLs, not view labels.** The Next.js adapter labels a view with its file stem (`route`, `page`); FastAPI with the function name. Titled by that label, every Next.js route handler was called "route", and `map_html._grouped` — which groups by title and address — merged them all into one picker row.

- [ ] **Step 1: Write the failing tests**

```python
# seamcheck/tests/test_entries_server.py
import unittest

from seamcheck.entries.server import ServerEntrySource
from seamcheck.graph import Edge, Graph, Status, Symbol


def _symbol(id_, kind, label, file):
    return Symbol(id=id_, kind=kind, label=label, sub="", file=file, line=1,
                  status=Status.CONNECTED, snippet="", chain=[], note="")


def _routes(*pairs):
    """(url label, view id, file, view label) -> a graph with url->view edges."""
    symbols, edges = [], []
    for url, view_id, file, view_label in pairs:
        symbols += [_symbol(f"url:{url}", "url", url, file), _symbol(view_id, "view", view_label, file)]
        edges.append(Edge(f"url:{url}", view_id, Status.CONNECTED))
    return Graph(symbols=symbols, edges=edges)


class ServerEntrySourceTests(unittest.TestCase):
    def test_a_route_handler_is_one_entry_titled_by_the_url_it_serves(self):
        graph = _routes(("/api/checkout", "view:app/api/checkout/route.ts", "app/api/checkout/route.ts", "route"))
        [entry] = ServerEntrySource().entries("/repo", {}, graph)
        self.assertEqual(
            (entry.key, entry.kind, entry.roots, entry.title, entry.where),
            ("server:app/api/checkout/route.ts", "server", ("app/api/checkout/route.ts",),
             "/api/checkout", "/api/checkout - app/api/checkout/route.ts"))

    def test_two_route_handlers_with_the_same_view_label_get_different_titles(self):
        graph = _routes(("/api/a", "view:app/api/a/route.ts", "app/api/a/route.ts", "route"),
                        ("/api/b", "view:app/api/b/route.ts", "app/api/b/route.ts", "route"))
        self.assertEqual([e.title for e in ServerEntrySource().entries("/repo", {}, graph)],
                         ["/api/a", "/api/b"])

    def test_several_routes_in_one_file_are_one_entry(self):
        graph = _routes(("/users", "view:routes.users.list", "routes/users.py", "list"),
                        ("/users/{id}", "view:routes.users.get", "routes/users.py", "get"))
        [entry] = ServerEntrySource().entries("/repo", {}, graph)
        self.assertEqual(entry.title, "2 routes")
        self.assertEqual(entry.where, "/users · /users/{id} - routes/users.py")

    def test_a_django_pattern_is_shown_with_its_leading_slash(self):
        graph = _routes(("api/x/", "view:app.views.x", "app/views.py", "x"),
                        ("", "view:app.views.home", "app/home.py", "home"))
        self.assertEqual([e.title for e in ServerEntrySource().entries("/repo", {}, graph)],
                         ["/", "/api/x/"])

    def test_a_view_no_url_reaches_is_titled_by_its_file(self):
        graph = Graph(symbols=[_symbol("view:x", "view", "x", "app/handlers.py")], edges=[])
        [entry] = ServerEntrySource().entries("/repo", {}, graph)
        self.assertEqual((entry.title, entry.where), ("handlers.py", "app/handlers.py"))

    def test_nothing_but_views_becomes_an_entry(self):
        graph = Graph(symbols=[_symbol("css:x", "css_selector", "x", "a.css")], edges=[])
        self.assertEqual(ServerEntrySource().entries("/repo", {}, graph), [])
        self.assertEqual(ServerEntrySource().entries("/repo", {}, None), [])

    def test_detect_is_a_constant_the_graph_decides(self):
        self.assertEqual(ServerEntrySource().detect("/repo", {}), 0.6)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest seamcheck/tests/test_entries_server.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'seamcheck.entries.server'`

- [ ] **Step 3: Implement**

```python
# seamcheck/entries/server.py
"""A file that handles a request is an entry, whichever backend wrote it.

One source, not four: every adapter already records a `view` with its file and an edge
from the URL that serves it. A file holding several handlers is one entry, titled by the
routes it serves. What such an entry reaches is its own file; for a JavaScript handler
the import walk carries it further, for a Python one the call graph does.
"""

from __future__ import annotations

import os

from seamcheck.entries.base import Entry

_SHOWN = 3


def _address(label: str) -> str:
    # Django's resolver reports patterns without their leading slash; a reader reads
    # addresses with one. Same rule as pagenames._best_url.
    return label if label.startswith("/") else f"/{label}"


class ServerEntrySource:
    name = "server"

    def detect(self, repo_root: str, config: dict) -> float:
        # Only the scanned graph can say whether an adapter found a handler, and only
        # entries() sees it. With no view symbols this costs one pass and returns nothing.
        return 0.6

    def entries(self, repo_root: str, config: dict, graph) -> list[Entry]:
        if graph is None:
            return []
        views = {s.id: s for s in graph.symbols if s.kind == "view" and s.file}
        if not views:
            return []
        urls = {s.id: s for s in graph.symbols if s.kind == "url"}
        addresses: dict[str, set[str]] = {view.file: set() for view in views.values()}
        for edge in graph.edges:
            if edge.from_id in urls and edge.to_id in views:
                addresses[views[edge.to_id].file].add(_address(urls[edge.from_id].label))
        found = []
        for file, served in sorted(addresses.items()):
            ordered = sorted(served)
            if len(ordered) == 1:
                title = ordered[0]
            elif ordered:
                title = f"{len(ordered)} routes"
            else:
                title = os.path.basename(file)
            shown = " · ".join(ordered[:_SHOWN]) + (" …" if len(ordered) > _SHOWN else "")
            found.append(Entry(
                key=f"server:{file}", kind="server", roots=(file,), title=title,
                where=f"{shown} - {file}" if shown else file,
                evidence="serves a route" if ordered else "holds a request handler no route reaches",
            ))
        return found
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest seamcheck/tests/test_entries_server.py -v`
Expected: PASS

- [ ] **Step 5: Lint and commit**

```bash
ruff check seamcheck/entries/server.py seamcheck/tests/test_entries_server.py
git add seamcheck/entries/server.py seamcheck/tests/test_entries_server.py
git commit -m "feat(entries): ServerEntrySource - every request-handling file is an entry, titled by its routes"
```

---

## Task 8: `NextJSSource` — App Router and Pages Router pages

**Files:**
- Create: `seamcheck/entries/nextjs.py`
- Test: `seamcheck/tests/test_entries_nextjs.py`
- Then: do Task 4.

**Interfaces:**
- Consumes, from `seamcheck.adapters.nextjs_adapter` (the design doc: URLs "from `nextjs_adapter._url_from`, so route groups, `@slot` and `_private` are handled once"): `NextJSAdapter` (`detect`, and the staticmethod `_app_of(routable, repo_root) -> str`), `_roots(repo_root) -> list[tuple[pathlib.Path, str]]`, `_url_from(path, root, kind) -> str | None`, `_EXTENSIONS`, `_SKIP`.
- Produces: `NextJSSource` (`name = "nextjs"`).

Page keys are qualified exactly as the adapter qualifies its `url` ids (`url:{app}:{url}` only when more than one app), so a page and the route that serves it always name the same app. A test pins that against a real adapter scan.

- [ ] **Step 1: Write the failing tests**

```python
# seamcheck/tests/test_entries_nextjs.py
import pathlib
import tempfile
import unittest

from seamcheck.adapters.nextjs_adapter import NextJSAdapter
from seamcheck.entries.nextjs import NextJSSource
from seamcheck.progress import null


def _repo(files: dict[str, str]) -> str:
    root = pathlib.Path(tempfile.mkdtemp())
    for name, body in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    return str(root)


PAGE = "export default function P() { return null; }"
LAYOUT = "export default function L({ children }) { return children; }"
CONFIG = {"next.config.js": "module.exports = {};"}


def _keys(root):
    return [entry.key for entry in NextJSSource().entries(root, {}, graph=None)]


class AppRouterTests(unittest.TestCase):
    def test_the_root_page_is_home(self):
        [entry] = NextJSSource().entries(_repo({**CONFIG, "app/page.tsx": PAGE}), {}, graph=None)
        self.assertEqual((entry.key, entry.kind, entry.title, entry.where),
                         ("next:/", "page", "Home", "/ - app/page.tsx"))

    def test_a_page_is_rooted_with_every_layout_above_it(self):
        root = _repo({**CONFIG, "app/layout.tsx": LAYOUT, "app/pricing/layout.tsx": LAYOUT,
                      "app/pricing/template.tsx": LAYOUT, "app/pricing/page.tsx": PAGE})
        pricing = next(e for e in NextJSSource().entries(root, {}, graph=None) if e.key == "next:/pricing")
        self.assertEqual(pricing.roots, ("app/pricing/page.tsx", "app/pricing/template.tsx",
                                         "app/pricing/layout.tsx", "app/layout.tsx"))

    def test_a_route_handler_is_not_a_page(self):
        self.assertEqual(_keys(_repo({**CONFIG, "app/api/checkout/route.ts": "export function POST() {}"})), [])

    def test_a_dynamic_segment_keeps_its_brackets_and_the_title_uses_the_static_part(self):
        [entry] = NextJSSource().entries(_repo({**CONFIG, "app/pricing/[locale]/page.tsx": PAGE}), {}, graph=None)
        self.assertEqual((entry.key, entry.title), ("next:/pricing/[locale]", "Pricing"))

    def test_route_groups_vanish_and_slots_and_private_folders_are_not_pages(self):
        root = _repo({**CONFIG, "app/(marketing)/about/page.tsx": PAGE,
                      "app/@modal/login/page.tsx": PAGE, "app/_lib/page.tsx": PAGE})
        self.assertEqual(_keys(root), ["next:/about"])

    def test_keys_name_the_routes_the_adapter_reports(self):
        root = _repo({**CONFIG, "app/page.tsx": PAGE, "app/pricing/[locale]/page.tsx": PAGE,
                      "pages/about.tsx": PAGE})
        urls = {s.id.removeprefix("url:") for s in NextJSAdapter().scan(root, {}, null()).symbols if s.kind == "url"}
        self.assertTrue({key.removeprefix("next:") for key in _keys(root)} <= urls)


class PagesRouterTests(unittest.TestCase):
    def test_a_file_under_pages_is_a_page(self):
        self.assertEqual(_keys(_repo({**CONFIG, "pages/about.tsx": PAGE})), ["next:/about"])

    def test_underscore_files_and_the_api_folder_are_not_pages(self):
        root = _repo({**CONFIG, "pages/_app.tsx": PAGE, "pages/_document.tsx": PAGE,
                      "pages/api/hello.ts": "export default function h() {}", "pages/index.tsx": PAGE})
        self.assertEqual(_keys(root), ["next:/"])


class MonorepoTests(unittest.TestCase):
    def test_two_apps_each_with_a_home_page_get_two_keys(self):
        root = _repo({"apps/web/next.config.js": "", "apps/web/app/page.tsx": PAGE,
                      "apps/docs/next.config.js": "", "apps/docs/app/page.tsx": PAGE})
        self.assertEqual(sorted(_keys(root)), ["next:apps/docs:/", "next:apps/web:/"])


class DetectTests(unittest.TestCase):
    def test_detect_is_the_adapters_answer(self):
        root = _repo({**CONFIG, "app/page.tsx": PAGE})
        self.assertEqual(NextJSSource().detect(root, {}), NextJSAdapter().detect(root, {}))

    def test_an_unrelated_project_is_zero(self):
        self.assertEqual(NextJSSource().detect(_repo({"README.md": ""}), {}), 0.0)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest seamcheck/tests/test_entries_nextjs.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'seamcheck.entries.nextjs'`

- [ ] **Step 3: Implement**

```python
# seamcheck/entries/nextjs.py
"""Next.js pages: every App Router `page.*`, rooted with the layouts and templates around
it, and every Pages Router file that is not `_app`, `_document` or under `api/`.

The URL, the route-group and slot rules and the app qualification all come from the
Next.js adapter, so a page here and the route the adapter reports can never disagree.
Route handlers (`route.*`) are server entries - ServerEntrySource finds them.
"""

from __future__ import annotations

import pathlib
import re

from seamcheck.adapters.nextjs_adapter import _EXTENSIONS, _SKIP, NextJSAdapter, _roots, _url_from
from seamcheck.entries.base import Entry
from seamcheck.resolve import norm_path

# Innermost first: template.* wraps the page inside its layout.
_WRAPPERS = ("template", "layout")


class NextJSSource:
    name = "nextjs"

    def detect(self, repo_root: str, config: dict) -> float:
        return NextJSAdapter().detect(repo_root, config or {})

    def entries(self, repo_root: str, config: dict, graph) -> list[Entry]:
        routable = _roots(repo_root)
        qualify = len({NextJSAdapter._app_of(root, repo_root) for root, _ in routable}) > 1
        found = []
        for router_root, kind in routable:
            app = NextJSAdapter._app_of(router_root, repo_root)
            for path in _page_files(router_root, kind):
                url = _url_from(path, router_root, kind)
                if url is None:
                    continue
                page = norm_path(str(path), repo_root)
                wrappers = [norm_path(str(p), repo_root) for p in _wrappers(path, router_root, kind)]
                found.append(Entry(
                    key=f"next:{app}:{url}" if qualify and app else f"next:{url}",
                    kind="page", roots=(page, *wrappers), title=_title(url),
                    where=f"{url} - {page}", evidence="a page file, routed by the filesystem",
                ))
        return found


def _page_files(router_root: pathlib.Path, kind: str):
    for path in sorted(router_root.rglob("*")):
        if not path.is_file() or path.suffix not in _EXTENSIONS:
            continue
        inside = path.relative_to(router_root).parts
        if any(part in _SKIP for part in inside):
            continue
        if kind == "app":
            if path.stem == "page":
                yield path
        elif not path.stem.startswith("_") and inside[0] != "api":
            yield path


def _wrappers(page: pathlib.Path, router_root: pathlib.Path, kind: str) -> list[pathlib.Path]:
    """The App Router files Next.js renders around this page, nearest first."""
    if kind != "app":
        return []
    found: list[pathlib.Path] = []
    folder = page.parent
    while True:
        for stem in _WRAPPERS:
            found += [folder / f"{stem}{ext}" for ext in _EXTENSIONS if (folder / f"{stem}{ext}").is_file()]
        if folder == router_root or router_root not in folder.parents:
            return found
        folder = folder.parent


def _title(url: str) -> str:
    """The last fixed segment, as words. A dynamic segment names a value, not a page."""
    fixed = [segment for segment in url.split("/") if segment and not segment.startswith("[")]
    if not fixed:
        return "Home" if url == "/" else url
    return " ".join(word.capitalize() for word in re.split(r"[-_.]+", fixed[-1]) if word)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest seamcheck/tests/test_entries_nextjs.py seamcheck/tests/test_nextjs_adapter.py -v`
Expected: PASS

- [ ] **Step 5: Lint and commit, then do Task 4**

```bash
ruff check seamcheck/entries/nextjs.py seamcheck/tests/test_entries_nextjs.py
git add seamcheck/entries/nextjs.py seamcheck/tests/test_entries_nextjs.py
git commit -m "feat(entries): NextJSSource - App Router and Pages Router pages, keyed like the adapter's routes"
```

---

## Task 9: `api.py` — `page_entries`, and `page_files` computed from entries

**Files:**
- Modify: `seamcheck/api.py` — new `page_entries`, `_page_files_for`; `page_files` (line 933) rewritten over them; `scoped_findings` (line 580), `scoped_map_document` (line 628) and `_map_document` (line 1112) use the graph they already hold.
- Test: `seamcheck/tests/test_page_entries.py` (new)

**Interfaces:**
- Consumes: `seamcheck.entries.all_entries` (Task 4), `build_module_graph` (Task 3), `seamcheck.scancache.cached_scan(repo_root, *, refresh=False) -> tuple[Graph, str]`.
- Produces: `page_entries(repo_root: str, graph: Graph | None = None) -> list[Entry]`; `_page_files_for(repo_root: str, graph: Graph, entries=None) -> dict[str, set[str]]`; `page_files(repo_root: str) -> dict[str, set[str]]` — **signature unchanged**, keyed by `Entry.key`.

Follow `api.py`'s own style: import `seamcheck.entries`, `scancache` and `js_extractor` inside the functions that use them. `_config()` reads `_CONFIG_ROOT[0]`, not its argument, so `page_entries` sets it first (the same fix `scoped_findings` already carries at line 564).

- [ ] **Step 1: Write the failing tests**

```python
# seamcheck/tests/test_page_entries.py
import pathlib
import tempfile
import unittest
from unittest import mock

from seamcheck import api
from seamcheck.graph import Edge, Graph, Status, Symbol


def _repo(files: dict[str, str]) -> str:
    root = pathlib.Path(tempfile.mkdtemp())
    for name, body in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    return str(root)


def _symbol(id_, kind, label, file):
    return Symbol(id=id_, kind=kind, label=label, sub="", file=file, line=1,
                  status=Status.CONNECTED, snippet="", chain=[], note="")


EMPTY = Graph(symbols=[], edges=[])
NEXT_APP = {
    "next.config.js": "module.exports = {};",
    "tsconfig.json": '{"compilerOptions": {"baseUrl": ".", "paths": {"@/*": ["./*"]}}}',
    "app/layout.tsx": "import './globals.css'; export default function L({ children }) { return children; }",
    "app/globals.css": ".x { color: red }",
    "app/page.tsx": "import { greet } from '@/lib/greet'; export default function P() { return greet(); }",
    "lib/greet.ts": "export function greet() { return 'hi'; }",
}


class PageFilesForTests(unittest.TestCase):
    def test_a_nextjs_page_reaches_its_layout_its_alias_imports_and_its_stylesheet(self):
        root = _repo(NEXT_APP)
        pages = api._page_files_for(root, EMPTY)
        self.assertEqual(pages["next:/"],
                         {"app/page.tsx", "app/layout.tsx", "lib/greet.ts", "app/globals.css"})

    def test_a_python_handler_reaches_its_own_file(self):
        root = _repo({"app/views.py": "def x(request): pass"})
        graph = Graph(symbols=[_symbol("url:x/", "url", "x/", "app/urls.py"),
                               _symbol("view:app.views.x", "view", "x", "app/views.py")],
                      edges=[Edge("url:x/", "view:app.views.x", Status.CONNECTED)])
        self.assertEqual(api._page_files_for(root, graph)["server:app/views.py"], {"app/views.py"})


class PageEntriesTests(unittest.TestCase):
    def test_entries_from_an_already_scanned_graph_need_no_second_scan(self):
        root = _repo(NEXT_APP)
        with mock.patch("seamcheck.scancache.cached_scan") as cached:
            found = api.page_entries(root, EMPTY)
        cached.assert_not_called()
        self.assertEqual([entry.key for entry in found], ["next:/"])


class PageFilesTests(unittest.TestCase):
    def test_page_files_keeps_its_one_argument_shape(self):
        root = _repo(NEXT_APP)
        with mock.patch("seamcheck.scancache.cached_scan", return_value=(EMPTY, "memory")):
            pages = api.page_files(root)
        self.assertEqual(set(pages), {"next:/"})
        self.assertTrue(all(isinstance(files, set) for files in pages.values()))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest seamcheck/tests/test_page_entries.py -v`
Expected: FAIL — `AttributeError: module 'seamcheck.api' has no attribute '_page_files_for'`

- [ ] **Step 3: Implement**

Replace `page_files` (lines 933-954) with:

```python
def page_entries(repo_root: str, graph: Graph | None = None) -> list:
    """Every entry every matching source found - pages, server entries and, when no
    source found anything, first-party scripts nothing imports. See seamcheck/entries/.

    A caller holding a scanned graph passes it; otherwise the cached scan answers."""
    from seamcheck.entries import all_entries

    _CONFIG_ROOT[0] = repo_root
    if graph is None:
        from seamcheck.scancache import cached_scan

        graph, _how = cached_scan(repo_root)
    return all_entries(repo_root, _config(), graph)


def _page_files_for(repo_root: str, graph: Graph, entries=None) -> dict[str, set[str]]:
    """Which files each entry reaches: its roots, what they import, and the assets they pull in."""
    from seamcheck.extractors.js_extractor import build_module_graph

    if entries is None:
        entries = page_entries(repo_root, graph)
    root = os.path.abspath(repo_root)
    starts = {entry.key: [os.path.join(root, path) for path in entry.roots] for entry in entries}
    # One walk for every entry together: pages share most of their imports.
    modules = build_module_graph(root, sorted({p for paths in starts.values() for p in paths}))
    return {
        key: {_norm(path, repo_root)
              for path in modules.reachable_files(paths) | modules.reachable_assets(paths)}
        for key, paths in starts.items()
    }


def page_files(repo_root: str) -> dict[str, set[str]]:
    """Which files each page entry reaches, by entry key.

    Public: `changescope.py` reuses this exact mapping so "seamcheck thinks this commit
    touched a page" can never disagree with what a generated map shows for the same repo
    state - one definition of "page", not two. Entries come from seamcheck/entries/; a
    caller already holding a graph should use `_page_files_for` and skip the cache lookup.
    """
    from seamcheck.scancache import cached_scan

    _CONFIG_ROOT[0] = repo_root
    graph, _how = cached_scan(repo_root)
    return _page_files_for(repo_root, graph)
```

At the three callers, use the graph already in hand:

- `scoped_findings` line 580: `pages_map = page_files(repo_root)` → `pages_map = _page_files_for(repo_root, graph)`
- `scoped_map_document` line 627-630:
  ```python
      entries = page_entries(repo_root, graph)
      connectivity_map = build_map(
          graph, _page_files_for(repo_root, graph, entries), git_sha=sha,
          names=page_names(repo_root, _config(), graph),
      )
  ```
- `_map_document` line 1112: `page_files_map = page_files(repo_root)` →
  ```python
      entries = page_entries(repo_root, graph)
      page_files_map = _page_files_for(repo_root, graph, entries)
  ```

`names=page_names(...)` stays until Task 10, which gives `PageName` the fields to carry an entry's title. `entries` is computed here so Task 10 reuses it.

- [ ] **Step 4: Run the tests to verify they pass, and the scope features that depend on page membership**

Run: `python -m pytest seamcheck/tests/test_page_entries.py seamcheck/tests/test_scoped_findings.py seamcheck/tests/test_scoped_map_document.py seamcheck/tests/test_changescope.py seamcheck/tests/test_scopedserve.py -v -p no:cacheprovider`
Expected: PASS

- [ ] **Step 5: Lint and commit**

```bash
ruff check seamcheck/api.py seamcheck/tests/test_page_entries.py
git add seamcheck/api.py seamcheck/tests/test_page_entries.py
git commit -m "feat(api): page_files computed from entries; page_entries; callers reuse their graph"
```

---

## Task 10: `mapdata.py` — membership by file, wider seeds, no page dropped, count shown once, wording from the scan

**Files:**
- Modify: `seamcheck/pagenames.py` — `PageName` gains `group`, `evidence`, `note`, `kind`
- Modify: `seamcheck/mapdata.py`
- Modify: `seamcheck/api.py` — `_names_from_entries`; `scoped_map_document` and `_map_document` pass entry names and the detected backends
- Modify: `seamcheck/tests/test_mapdata.py` — two existing tests change on purpose (see Step 2)

**Interfaces:**
- Consumes: `entries` from Task 9's callers; `seamcheck.pipeline.LAST_ADAPTERS: list[dict]` (`name`/`confidence`/`language`), imported inside `api.py` functions as `api.py` already does at lines 990 and 1136. `mapdata.py` does not import `pipeline`.
- Produces: `PageName(title, where, entry, group="", evidence="", note="", kind="page")`; `PageMap` gains `group: str = ""`, `kind: str = "page"`, `reached: int = 0`; `unreached_groups(detected=frozenset(), script_symbols=()) -> tuple[tuple[str, str, frozenset[str]], ...]` replaces the `UNREACHED_GROUPS` constant (no other module reads it); `build_unreached_pages(..., detected_backends=frozenset())`; `build_map(..., detected_backends=frozenset())`.

**What each change is, against the current code:**
- `_SEED_KINDS` (line 90) widens to every "touch" kind the design doc lists (§2c). `_SEED_EXCLUDED_SUBS` (line 93) is unchanged: `class:apply` still never draws.
- The single-node drop (line 403) goes. An entry with nothing drawable stays in the picker; `PageMap.reached` counts the symbols its files hold, so the canvas can say so (Task 11).
- "Covered" (line 409) widens from drawn nodes to drawn nodes plus every symbol whose file some entry reaches.
- Sort (line 406) puts pages before server entries before anything else, then by title.
- A page's root node carries the entry's evidence and note in `note`. `note` is one of `map_html._DETAIL_FIELDS`, so the sheet shows it without a JavaScript change.
- The bucket `where` (line 369) drops `— {count}`. The picker already appends the count.
- Bucket wording is built from the detected backends and the languages in the bucket. With no backend detected, the backend blurb is today's text, so a caller that passes nothing sees no change there.

- [ ] **Step 1: Extend `PageName`**

In `seamcheck/pagenames.py` (lines 27-33), add after `entry`:

```python
    group: str = ""     # entries sharing a group are sections of one page
    evidence: str = ""  # why this is an entry at all: "a page file, routed by the filesystem"
    note: str = ""      # what a reader should know: "by convention, not declared"
    kind: str = "page"  # "page" | "server" | "entry_file" - decides picker order
```

Run: `python -m pytest seamcheck/tests/test_pagenames.py -q -p no:cacheprovider` — Expected: PASS, unchanged.

- [ ] **Step 2: Write the failing tests, and change the two tests that pin the old behaviour**

In `seamcheck/tests/test_mapdata.py`, change the imports to:

```python
from django.test import SimpleTestCase

from seamcheck.graph import Edge, Graph, Status, Symbol
from seamcheck.mapdata import UNREACHED_PAGE, build_map
from seamcheck.pagenames import PageName
```

Replace `PageScopingTests.test_a_page_with_no_symbols_is_dropped` with:

```python
    def test_a_page_with_nothing_to_draw_stays_in_the_picker(self):
        # Dropping it is how every Next.js page vanished: a page that disappears is the
        # failure, not a page with an empty canvas.
        built = build_map(_graph(), {"empty": {"nothing.js"}}, git_sha="abc", now="t")

        self.assertEqual([(p.page, p.reached) for p in _entries(built)], [("empty", 0)])
```

In `UnreachedTests.test_the_buckets_say_why_rather_than_naming_a_verdict`, replace `self.assertIn("—", bucket.where)` with:

```python
            # The picker appends the count; a count in `where` showed twice ("— 3 — 3 nodes").
            self.assertNotRegex(bucket.where, r"\d")
```

Add at the end of the file:

```python
def _apply(id_, file):
    return Symbol(id=id_, kind="dom_attr", label="x", sub="class:apply", file=file, line=1,
                  status=Status.CONNECTED, snippet="", chain=[], note="")


class MembershipByFileTests(SimpleTestCase):
    def test_a_store_use_on_a_page_file_is_drawn(self):
        graph = Graph(symbols=[_symbol("db:orders:a.ts:1", "db_table_use", "orders", file="a.ts")], edges=[])
        built = build_map(graph, {"home": {"a.ts"}}, git_sha="abc", now="t")

        self.assertIn("db:orders:a.ts:1", {n.id for n in _entries(built)[0].nodes})

    def test_a_symbol_on_a_reached_file_is_not_unreached_even_when_never_drawn(self):
        graph = Graph(symbols=[_apply("apply:a.tsx:1", "a.tsx")], edges=[])
        built = build_map(graph, {"home": {"a.tsx"}}, git_sha="abc", now="t")
        unreached = {n.id for p in built.pages if p.page.startswith(UNREACHED_PAGE + ":") for n in p.nodes}

        self.assertNotIn("apply:a.tsx:1", unreached)
        self.assertNotIn("apply:a.tsx:1", {n.id for n in _entries(built)[0].nodes})
        self.assertEqual(_entries(built)[0].reached, 1)


class EntryOrderAndEvidenceTests(SimpleTestCase):
    def test_pages_come_before_server_entries_whatever_their_titles(self):
        names = {"a": PageName(title="Zebra", where="", entry="a", kind="page"),
                 "b": PageName(title="Alpha", where="", entry="b", kind="server")}
        built = build_map(_graph(), {"a": {"a.js"}, "b": {"views.py"}}, git_sha="abc", now="t", names=names)

        self.assertEqual([p.page for p in _entries(built)], ["a", "b"])

    def test_the_page_node_carries_the_entrys_evidence_and_note(self):
        names = {"home": PageName(title="Home", where="/", entry="home",
                                  evidence="a page file, routed by the filesystem",
                                  note="by convention, not declared")}
        built = build_map(_graph(), {"home": {"a.js"}}, git_sha="abc", now="t", names=names)
        root = _entries(built)[0].nodes[0]

        self.assertEqual((root.kind, root.note),
                         ("page", "a page file, routed by the filesystem · by convention, not declared"))


class BucketWordingTests(SimpleTestCase):
    def _where(self, key, graph, **kwargs):
        built = build_map(graph, {"home": {"nothing.js"}}, git_sha="abc", now="t", **kwargs)
        return next(p.where for p in built.pages if p.page == f"{UNREACHED_PAGE}:{key}")

    def test_with_no_backend_detected_the_backend_bucket_reads_as_today(self):
        self.assertEqual(self._where("backend", _graph()),
                         "Reached by the framework, never by a browser page — "
                         "routes, handlers, models, signal receivers")

    def test_nextjs_is_described_in_its_own_terms(self):
        self.assertIn("route handlers and pages no entry reaches",
                      self._where("backend", _graph(), detected_backends=frozenset({"nextjs"})))

    def test_two_backends_are_both_named(self):
        where = self._where("backend", _graph(), detected_backends=frozenset({"django", "nextjs"}))
        self.assertIn("URLs, views, admin actions, signal receivers", where)
        self.assertIn("route handlers and pages no entry reaches", where)

    def test_the_script_bucket_names_the_languages_actually_in_it(self):
        graph = Graph(symbols=[_symbol("c1", "js_call", file="a.ts"), _symbol("c2", "js_call", file="b.js")], edges=[])
        self.assertEqual(self._where("js", graph), "TypeScript and JavaScript no entry imports")
```

- [ ] **Step 3: Run the tests to verify the new and changed ones fail**

Run: `python -m pytest seamcheck/tests/test_mapdata.py -v -p no:cacheprovider`
Expected: the new tests and the two changed ones FAIL; every other test passes.

- [ ] **Step 4: Implement in `seamcheck/mapdata.py`**

1. Replace `_SEED_KINDS` (line 90) with:

```python
# Symbols that belong to a module and start a chain outward: the calls and queries a
# page makes, the elements it selects - every "touch". A page that talks through a data
# client or a <Link> held none of the first four, so it was dropped as empty.
_SEED_KINDS = frozenset({
    "js_call", "fetch_target", "dom_selector", "multi_writer_element",
    "db_table_use", "db_column_use", "db_function_use", "url_reference", "env_read",
    "stripe_event", "stripe_webhook", "storage_bucket", "firestore_collection",
    "graphql_selection", "job_enqueue", "redis_key_use", "edge_function_use", "dom_attr",
})
# Pages, then server entries, then scripts no framework claims; a reader looks for the
# page first.
_KIND_ORDER = {"page": 0, "server": 1, "static_page": 2, "entry_file": 3}
```

2. Add to `PageMap` (after `where`):

```python
    # Entries sharing a group are sections of one page; see map_html._grouped.
    group: str = ""
    kind: str = "page"
    # Symbols in the files this entry reaches, drawn or not - what an empty canvas says
    # instead of saying nothing.
    reached: int = 0
```

3. Replace lines 314-334 (`UNREACHED_GROUPS`, `UNREACHED_OTHER`, `_unreached_group`) with:

```python
UNREACHED_PAGE = "unreached"
_GROUP_KINDS: tuple[tuple[str, frozenset[str]], ...] = (
    ("backend", frozenset({"url", "view", "admin_action", "signal_receiver",
                           "template_tag", "management_command"})),
    ("js", frozenset({"js_call", "fetch_target", "dom_selector", "multi_writer_element", "module"})),
    ("css", frozenset({"css_selector", "css_token_def", "css_token_use"})),
    ("template", frozenset({"dom_attr"})),
)
UNREACHED_OTHER = ("other", "Everything else the scan found off the page graph")

# What each backend calls the things a browser never reaches, in its own vocabulary.
# CLAUDE.md: never phrase a message in one framework's vocabulary for another's users.
_BACKEND_WORDS = {
    "django": "URLs, views, admin actions, signal receivers",
    "nextjs": "route handlers and pages no entry reaches",
    "express": "routes and middleware",
    "fastapi": "routes and their handlers",
}
_BACKEND_DEFAULT = "routes, handlers, models, signal receivers"
_TEMPLATE_BACKENDS = frozenset({"django", "flask"})


def unreached_groups(detected: frozenset[str] = frozenset(),
                     script_symbols=()) -> tuple[tuple[str, str, frozenset[str]], ...]:
    """The not-reached buckets as (key, blurb, kinds), worded from what the scan found."""
    words = "; ".join(_BACKEND_WORDS[name] for name in sorted(detected) if name in _BACKEND_WORDS)
    languages = sorted({language_of(s.file) for s in script_symbols} & {"TypeScript", "JavaScript"},
                       reverse=True)
    markup = "Template elements" if detected & _TEMPLATE_BACKENDS else "HTML elements"
    blurbs = {
        "backend": f"Reached by the framework, never by a browser page — {words or _BACKEND_DEFAULT}",
        "js": f"{' and '.join(languages) or 'JavaScript'} no entry imports",
        "css": "Stylesheet rules and design tokens that nothing on a page matched",
        "template": f"{markup} that no page's JavaScript selects",
    }
    return tuple((key, blurbs[key], kinds) for key, kinds in _GROUP_KINDS)


def _unreached_key(kind: str) -> str:
    for key, kinds in _GROUP_KINDS:
        if kind in kinds:
            return key
    return UNREACHED_OTHER[0]
```

   Check `grep -rn "Template elements\|JavaScript that no page" seamcheck/tests` first: a test asserting today's `js`/`template` wording changes with it — update that assertion to the new wording in this step, and name the test in the commit message.

4. In `build_unreached_pages`, change the signature to `(graph: Graph, covered: set[str], services=None, detected_backends: frozenset[str] = frozenset())`, and replace its first part (the bucket loop through `order = ...`) with:

```python
    buckets: dict[str, list[Symbol]] = {}
    for symbol in graph.symbols:
        if symbol.id not in covered:
            buckets.setdefault(_unreached_key(symbol.kind), []).append(symbol)
    groups = unreached_groups(detected_backends, buckets.get("js", ()))
    blurbs = {key: blurb for key, blurb, _ in groups}
    blurbs[UNREACHED_OTHER[0]] = UNREACHED_OTHER[1]
    order = [key for key, _, _ in groups] + [UNREACHED_OTHER[0]]
```

   and change `where=f"{blurbs[key]} — {len(nodes):,}",` to `where=blurbs[key],`.

5. In `build_map`, add `detected_backends: frozenset[str] = frozenset(),` as the last parameter, and replace lines 396-410 with:

```python
    per_file: dict[str, int] = {}
    for symbol in graph.symbols:
        if symbol.file:
            per_file[symbol.file] = per_file.get(symbol.file, 0) + 1
    page_maps = []
    for page, files in sorted(pages.items()):
        page_map = build_page_map(page, files, graph, adjacency, services=services)
        name = (names or {}).get(page)
        page_map.title = name.title if name else page
        page_map.where = name.where if name else ""
        page_map.group = name.group if name else ""
        page_map.kind = name.kind if name else "page"
        page_map.reached = sum(per_file.get(path, 0) for path in files)
        if name:
            # nodes[0] is the page itself (build_page_map creates it first). `note` rides
            # in the detail chunk, so the sheet shows why this is an entry.
            page_map.nodes[0].note = " · ".join(part for part in (name.evidence, name.note) if part)
        page_maps.append(page_map)
    # Pages, then server entries; inside each, by the name a reader recognises.
    page_maps.sort(key=lambda page_map: (_KIND_ORDER.get(page_map.kind, 9),
                                         page_map.title.lower(), page_map.page))
    # ...and last, everything no entry reaches. A symbol is on a page when an entry reaches
    # its FILE or the walk drew it: a class application on a page's own component is on
    # that page, drawn or not.
    reached_files = set().union(*pages.values()) if pages else set()
    covered = {node.id for page_map in page_maps for node in page_map.nodes}
    covered |= {symbol.id for symbol in graph.symbols if symbol.file in reached_files}
    page_maps += build_unreached_pages(graph, covered, services=services,
                                       detected_backends=detected_backends)
```

- [ ] **Step 5: Pass entry names and detected backends from `api.py`**

Add near `_page_files_for`:

```python
def _names_from_entries(entries) -> dict:
    """What the map calls each entry. Titles travel on entries now; pagenames is read only
    by LegacySource."""
    from seamcheck.pagenames import PageName

    return {entry.key: PageName(title=entry.title, where=entry.where, entry=entry.key,
                                group=entry.group, evidence=entry.evidence,
                                note=entry.note, kind=entry.kind)
            for entry in entries}


def _detected_backends() -> frozenset[str]:
    from seamcheck.pipeline import LAST_ADAPTERS

    return frozenset(adapter["name"] for adapter in LAST_ADAPTERS)
```

In `scoped_map_document` and `_map_document`, replace `names=page_names(repo_root, _config(), graph),` with `names=_names_from_entries(entries), detected_backends=_detected_backends(),`, and delete the now-unused `from seamcheck.pagenames import page_names` lines in both.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `python -m pytest seamcheck/tests/test_mapdata.py seamcheck/tests/test_pagenames.py seamcheck/tests/test_page_entries.py seamcheck/tests/test_scoped_map_document.py -v -p no:cacheprovider`
Expected: PASS

- [ ] **Step 7: Lint and commit**

```bash
ruff check seamcheck/
git add seamcheck/mapdata.py seamcheck/pagenames.py seamcheck/api.py seamcheck/tests/test_mapdata.py
git commit -m "feat(mapdata): membership by file, wider seeds, no entry dropped, count shown once

A symbol is on a page when an entry reaches its file or the walk draws it; drawing still
starts from seeds, now every touch kind. An entry with nothing drawable stays in the
picker. Bucket blurbs are worded from the detected backends and the languages in the
bucket, and no longer carry the count the picker already appends.

Changed on purpose: test_a_page_with_no_symbols_is_dropped (now: stays in the picker),
test_the_buckets_say_why_rather_than_naming_a_verdict (now: no count in where)."
```

---

## Task 11: `map_html.py` — groups, and an empty canvas that says what the entry reaches

**Files:**
- Modify: `seamcheck/renderers/map_html.py` — `_grouped` (line 6212), the page meta in `_payload` (line 6404), and the canvas empty state in the emitted JavaScript
- Test: `seamcheck/tests/test_map_entries.py` (new)

**Interfaces:**
- Consumes: `PageMap.group`, `PageMap.kind`, `PageMap.reached` (Task 10).
- Produces: page meta gains `"rf"` (symbols reached by file) when non-zero.

**Already done by Task 10, so not here:** picker order (the pages arrive sorted, and `_payload` keeps their order), and the evidence and note in the sheet (they ride on the page node's `note`, which `_DETAIL_FIELDS` already ships).

- [ ] **Step 1: Write the failing tests**

```python
# seamcheck/tests/test_map_entries.py
import json
import unittest

from seamcheck.mapdata import ConnectivityMap, MapNode, PageMap
from seamcheck.renderers.map_html import _grouped, _payload


def _page(page, title="T", where="", group="", reached=0):
    root = MapNode(f"page:{page}", page, "page", "connected")
    return PageMap(page=page, nodes=[root], edges=[], title=title, where=where,
                   group=group, reached=reached)


class GroupedTests(unittest.TestCase):
    def test_entries_sharing_a_group_are_one_group_whatever_their_titles(self):
        groups = _grouped([_page("a", "Overview", group="/"), _page("b", "KPIs", group="/")])
        self.assertEqual([[p.page for p in members] for _, members in groups], [["a", "b"]])

    def test_without_a_group_entries_group_by_title_and_address_as_before(self):
        groups = _grouped([_page("a", "Home", "/ - x.html"), _page("b", "Home", "/ - y.html"),
                           _page("c", "Other", "/other")])
        self.assertEqual([[p.page for p in members] for _, members in groups], [["a", "b"], ["c"]])


class ReachedCountTests(unittest.TestCase):
    def test_a_page_says_how_many_symbols_its_files_hold(self):
        meta, _chunks, _files = _payload(ConnectivityMap(git_sha="abc", generated_at="t",
                                                         pages=[_page("home", reached=42)]))
        [page] = [p for p in json.loads(meta)["pages"] if p["page"] == "home"]
        self.assertEqual(page["rf"], 42)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest seamcheck/tests/test_map_entries.py -v`
Expected: FAIL — the group test (grouped by title today) and `KeyError: 'rf'`. If `_payload`'s first return value is not a JSON string, read `_json` near line 6506 and adapt the test's parsing — do not change `_payload`'s return shape.

- [ ] **Step 3: Implement the Python side**

Replace the body of `_grouped`:

```python
    groups: dict[tuple[str, str], list] = {}
    for page in pages:
        if page.group:
            key = ("group", page.group)
        else:
            key = (page.title or page.page, (page.where or "").split(" - ")[0].strip())
        groups.setdefault(key, []).append(page)
    return list(enumerate(groups.values()))
```

and add to its docstring: "An entry that names its group (the screens of one page) is grouped by that alone."

In `_payload`, carry the count. The page tuples are `(name, title, where, layer, nodes, edges, group, union)`; rather than widen the tuple, look the count up by name. Before the `for index, (name, ...) in enumerate(pages)` loop at line 6360, add:

```python
    reached_by_page = {page.page: page.reached for page in connectivity_map.pages}
```

and after `meta = {...}` (line 6404-6408):

```python
        if reached_by_page.get(name):
            meta["rf"] = reached_by_page[name]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest seamcheck/tests/test_map_entries.py -v`
Expected: PASS

- [ ] **Step 5: Show the count on an empty canvas**

The empty canvas already has one message box, filled by the function ending at `map_html.py:3667`: a commit filter, a function filter and a filter combination each write their own sentence into `box.innerHTML`, and the last branch writes `"<b>Nothing to draw here.</b><span>Try another page.</span>"`. Change only that last branch: when the current page's meta carries `rf` (`PAGES[current].rf`), say what the page holds instead —

```js
    : (PAGES[current] && PAGES[current].rf
        ? `<b>Nothing on this page starts a chain to draw.</b>
           <span>Its files hold ${PAGES[current].rf.toLocaleString()} symbol${
             PAGES[current].rf === 1 ? "" : "s"}; none of them is a request, a query or an
           element it selects.</span>`
        : "<b>Nothing to draw here.</b><span>Try another page.</span>");
```

Same box, same markup as the sibling messages, no new element or style. `PAGES` and `current` are the globals `pickPage` (line 1918) already uses.

- [ ] **Step 6: `node --check` the emitted scripts**

```bash
python tools/verify_output.py --self
```

It renders this repository's map with the worktree's code and runs `node --check` on every emitted `<script>` block, then checks the payload against the graph. **Never run it without `--self` or a repository name**: with no argument it renders every cloned corpus repository, and it has no `--help` — an unknown flag is ignored and does exactly that. Expected: `1/1 repositories rendered a sound map.` A script that does not parse is a blank page in the browser; fix it before going on.

- [ ] **Step 7: Lint and commit**

```bash
ruff check seamcheck/renderers/map_html.py seamcheck/tests/test_map_entries.py
git add seamcheck/renderers/map_html.py seamcheck/tests/test_map_entries.py
git commit -m "feat(map): group entries by their group; an empty canvas says what the entry reaches"
```

Driving the real map in a browser happens in Task 15, on leanos-app, where there are entries worth looking at.

---

## Task 12: Removed

The first draft changed `seamcheck/autoconfig.py` to stop writing the `.js` sweep into `js_entry_files` when Next.js matched. That would have moved those files from the JS extractor's entry set to its extra set — a change to what the scan reads, which the design doc does not ask for. Its purpose (loose scripts are not pages beside Next.js) is met inside `LegacySource` in Task 5, without touching autoconfig. The number is kept so the other tasks' references stay valid.

---

## Task 13: `tools/corpus.py entries` — measure what the corpus `scan` cannot see

**Files:**
- Modify: `tools/corpus.py` — three commands: `entries`, `entries-one` (internal), `entries-compare`; docstring usage lines

**Interfaces:**
- Consumes: `api.scan`, `api.page_entries`, `api._page_files_for`, `api._names_from_entries` (Tasks 9-10), `mapdata.build_map`, `mapdata.UNREACHED_PAGE`. On code from before this branch, `api.page_files` and `pagenames.page_names` instead, so one command measures both sides of the change.
- Produces: `python tools/corpus.py entries [--only NAME | --path DIR] [--code CHECKOUT] [--out FILE]` writes one JSON row per repository; `python tools/corpus.py entries-compare BEFORE.json AFTER.json` prints what moved.

**Why a separate command.** `scan_one` runs the backend adapters and the GraphQL/Celery/Stripe readers, never the JS extractor, `page_files` or `build_map` — cheap enough for 46 repositories, and blind to this phase. `entries` does a full scan per repository, so it runs on a named set, and each repository in its own Python process: a full scan of cal.com or dub holds a lot of parse cache, and this machine has already had processes killed for low memory today. A process that dies costs that repository's row, not the run.

**`--code` decides which seamcheck is measured.** The child process gets `PYTHONPATH=<code>` and does not insert `ROOT`, so `--code` pointing at a clean snapshot of `main` measures `main`, and the default (this checkout) measures the branch. Each row records the directory it imported seamcheck from, so a mixed-up run shows on the page instead of in a wrong conclusion.

- [ ] **Step 1: Add the measurement set and the one-repository row**

After `REPOS`, add:

```python
# Phase 1 of docs/plans/2026-09-15-entry-sources-design.md, measured where `scan` cannot
# look. The repositories that design doc measured, minus the ones whose full scan is too
# slow or too large for a before-and-after run on one machine (sentry, n8n, saleor, ghost,
# misago, netbox, weblate, pretix). Folder names under CORPUS, not REPOS entries: three of
# the Next.js ones are cloned there without being in REPOS.
ENTRY_REPOS = (
    "cal.com", "dub", "commerce", "documenso", "nextjs-subscription-payments", "platforms",
    "healthchecks", "djangoproject.com", "wger", "django-debug-toolbar", "paperless-ngx", "bookwyrm",
    "full-stack-fastapi-template", "fastapi-realworld", "dispatch", "open-webui",
    "node-express-boilerplate", "node-express-realworld-example-app", "parse-server", "nodebb",
)


def entries_row(target: pathlib.Path) -> dict:
    """One repository: a full scan, its entries, what they reach, the not-reached buckets."""
    import seamcheck
    from seamcheck import api
    from seamcheck.mapdata import UNREACHED_PAGE, build_map
    from seamcheck.progress import null

    row: dict = {"name": target.name, "code": os.path.dirname(os.path.dirname(seamcheck.__file__))}
    started = time.time()
    try:
        graph = api.scan(str(target), null())
        if hasattr(api, "page_entries"):
            found = api.page_entries(str(target), graph)
            pages = api._page_files_for(str(target), graph, found)
            names = api._names_from_entries(found)
            row["kinds"] = dict(collections.Counter(entry.kind for entry in found))
            row["pages"] = sorted(entry.key for entry in found if entry.kind == "page")
        else:
            from seamcheck.pagenames import page_names

            pages = api.page_files(str(target))
            names = page_names(str(target), api._config(), graph)
            row["kinds"] = {"page": len(pages)}
            row["pages"] = sorted(pages)
        built = build_map(graph, pages, git_sha="corpus", names=names)
        reached = set().union(*pages.values()) if pages else set()
        row["symbols"] = len(graph.symbols)
        row["on_page"] = sum(1 for symbol in graph.symbols if symbol.file in reached)
        row["drawn"] = sum(1 for page in built.pages if not page.page.startswith(UNREACHED_PAGE + ":"))
        row["buckets"] = {page.page.split(":", 1)[1]: len(page.nodes) for page in built.pages
                          if page.page.startswith(UNREACHED_PAGE + ":")}
        row["gate"] = "ok"
    except Exception as error:  # noqa: BLE001 - a crash IS the result
        row["gate"] = f"CRASH: {type(error).__name__}: {str(error)[:90]}"
    row["seconds"] = round(time.time() - started, 1)
    return row
```

- [ ] **Step 2: Add the driver and the comparison**

```python
def entries(names, code: pathlib.Path, out: pathlib.Path, path: pathlib.Path | None = None) -> None:
    targets = [path] if path else [CORPUS / name for name in names]
    rows = []
    for target in targets:
        if not target.is_dir():
            rows.append({"name": target.name, "gate": "not cloned"})
            print(f"  {target.name:<36} not cloned")
            continue
        print(f"  {target.name:<36} scanning ...", end="", flush=True)
        done = subprocess.run(
            [sys.executable, str(pathlib.Path(__file__).resolve()), "entries-one", str(target)],
            env={**os.environ, "PYTHONPATH": str(code)}, capture_output=True, text=True, check=False,
        )
        lines = done.stdout.strip().splitlines()
        try:
            row = json.loads(lines[-1])
        except (IndexError, ValueError):
            tail = (done.stderr or "").strip().splitlines()[-1:] or [f"exit {done.returncode}"]
            row = {"name": target.name, "gate": f"CRASH: {tail[0][:90]}"}
        rows.append(row)
        kinds = " ".join(f"{kind} {count}" for kind, count in sorted(row.get("kinds", {}).items()))
        print(f"\r  {target.name:<36} {row['gate'][:40]:<12} {kinds:<32} "
              f"on page {row.get('on_page', 0):>7,} / {row.get('symbols', 0):>7,}  {row.get('seconds', '')}s")
    out.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    print(f"\n  wrote {out}")


def entries_compare(before_file: pathlib.Path, after_file: pathlib.Path) -> None:
    before = {row["name"]: row for row in json.loads(before_file.read_text(encoding="utf-8"))}
    after = {row["name"]: row for row in json.loads(after_file.read_text(encoding="utf-8"))}
    totals = [0, 0, 0, 0]  # on page before, symbols before, on page after, symbols after
    for name, now in after.items():
        was = before.get(name, {})
        if now.get("gate") != "ok" or was.get("gate") != "ok":
            print(f"  {name:<36} before: {was.get('gate', 'absent')}   after: {now.get('gate')}")
            continue
        gone = sorted(set(was["pages"]) - set(now["pages"]))
        new = sorted(set(now["pages"]) - set(was["pages"]))
        pages = "pages identical" if not gone and not new else f"pages -{len(gone)} +{len(new)}"
        kinds = " ".join(f"{kind} {count}" for kind, count in sorted(now["kinds"].items()))
        print(f"  {name:<36} {pages:<18} entries: {kinds}")
        print(f"      drawn {was['drawn']} -> {now['drawn']}   "
              f"on page {was['on_page']:,}/{was['symbols']:,} -> {now['on_page']:,}/{now['symbols']:,}")
        keys = sorted(set(was["buckets"]) | set(now["buckets"]))
        print("      buckets " + ", ".join(
            f"{key} {was['buckets'].get(key, 0):,} -> {now['buckets'].get(key, 0):,}" for key in keys))
        if gone:
            print(f"      pages gone: {', '.join(gone[:6])}{' ...' if len(gone) > 6 else ''}")
        if new:
            print(f"      pages new:  {', '.join(new[:6])}{' ...' if len(new) > 6 else ''}")
        totals = [totals[0] + was["on_page"], totals[1] + was["symbols"],
                  totals[2] + now["on_page"], totals[3] + now["symbols"]]
    if totals[1] and totals[3]:
        print(f"\n  on a page, all repositories: {100 * totals[0] / totals[1]:.1f}% -> "
              f"{100 * totals[2] / totals[3]:.1f}%")
```

- [ ] **Step 3: Wire the commands into `main`, and the usage lines**

Replace `main` with:

```python
def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["clone", "scan", "list", "entries", "entries-one", "entries-compare"])
    parser.add_argument("targets", nargs="*",
                        help="entries-one: a repository directory; entries-compare: BEFORE.json AFTER.json")
    parser.add_argument("--only", help="just this repo")
    parser.add_argument("--path", help="entries: measure this directory instead of the corpus set")
    parser.add_argument("--code", default=str(ROOT), help="entries: the seamcheck checkout to measure with")
    parser.add_argument("--out", help="entries: where to write the rows (default: <corpus>/entries.json)")
    args = parser.parse_args()
    if args.command == "list":
        for repo in REPOS:
            print(f"  {repo['name']:<32} {repo['adapter']:<10} {repo['why']}")
    elif args.command == "clone":
        clone(args.only)
    elif args.command == "entries":
        names = [args.only] if args.only else list(ENTRY_REPOS)
        out = pathlib.Path(args.out) if args.out else CORPUS / "entries.json"
        entries(names, pathlib.Path(args.code).resolve(), out,
                pathlib.Path(args.path).resolve() if args.path else None)
    elif args.command == "entries-one":
        print(json.dumps(entries_row(pathlib.Path(args.targets[0]).resolve())))
    elif args.command == "entries-compare":
        entries_compare(pathlib.Path(args.targets[0]), pathlib.Path(args.targets[1]))
    else:
        scan(args.only)
```

In the module docstring's `Usage:` block, add:

```
    python tools/corpus.py entries                # full scan + page entries on the phase-1 set
    python tools/corpus.py entries --code ../base --out before.json   # the same, on other code
    python tools/corpus.py entries-compare before.json after.json
```

- [ ] **Step 4: Run it on one small repository, on both sides**

```bash
SCRATCH=/private/tmp/claude-501/-Users-balazssimon-dev-seamcheck/fe1d79e8-9cc0-469b-a96c-70c171ae3fda/scratchpad
mkdir -p "$SCRATCH/base" && git archive b357d4de4 seamcheck tools | tar -x -C "$SCRATCH/base"
python tools/corpus.py entries --only healthchecks --code "$SCRATCH/base" --out "$SCRATCH/hc-before.json"
python tools/corpus.py entries --only healthchecks --out "$SCRATCH/hc-after.json"
python tools/corpus.py entries-compare "$SCRATCH/hc-before.json" "$SCRATCH/hc-after.json"
```

Expected: both rows `ok`; the before row's `code` is `$SCRATCH/base`, the after row's is this worktree (check the JSON); healthchecks is Django, so `pages identical`. If the page lists differ, stop and read why before going on: a Django page list must not change in this phase.

- [ ] **Step 5: Lint and commit**

```bash
ruff check seamcheck/ && python -m ruff check tools/corpus.py
git add tools/corpus.py
git commit -m "feat(corpus): entries - full-scan page measurement, before and after, where scan cannot look"
```

---

## Task 14: Docs — pages beyond templates, server entries, and how to measure them

**Files:**
- Modify: `docs/the-map.md` — the first paragraph of "A page, then its sections" (lines 40-44)
- Modify: `docs/coverage.md` — the reproduce block (lines 152-154)
- Modify: wherever `server_adapter` is documented — `grep -rn server_adapter docs README.md` first

- [ ] **Step 1: `docs/the-map.md`**

Replace the paragraph starting "Two pickers. **Page** lists the HTML pages a reader knows" (lines 40-44) with:

```markdown
Two pickers. **Page** lists the pages a reader knows — a Django or Vite page
(`Push Arena · /push_arena/`), a Next.js page (`Pricing · /pricing/[locale]`), each found the
way that framework declares it. Below the pages come **server entries**: every file that
handles a request, titled by the routes it serves (`/api/checkout`), whichever backend
wrote it. **Section** lists what a page loads: every script tag is its own section, named
after the script — `push-arena-main`, `cookie_consent` — and **Whole page** at the top is
all of them together. A page that loads one script has no Section picker at all.

A page stays in the list even when nothing on it starts a chain to draw; its canvas says
how many symbols its files hold instead. A page that silently vanished used to look exactly
like a page that did not exist. Click the page itself to see why it is an entry — "a page
file, routed by the filesystem", "a declared JavaScript entry point".
```

In the next paragraph, change "a symbol that no page's scripts ever reach lands in the **Not reached from any page** buckets" to "a symbol in a file no entry reaches lands in the **Not reached from any page** buckets".

- [ ] **Step 2: `seamcheck config` says which entry sources run** (replaces a docs paragraph; found while running)

No page in `docs/` or the README lists configuration keys — not `server_adapter`, not `js_entry_files`. Configuration is shown by `seamcheck config`: both printers (`cli._show_config_plain`, line 1267, and `management/commands/seamcheck.py:_show_config`, line 330) print every key of `autoconfig.effective()` with the reason it was set, so an `entry_sources` written in `SEAMCHECK_CONFIG` already appears there with its reason. What neither shows is which sources RUN — the design doc's section 1 asks for exactly that ("`seamcheck config` shows which sources ran and why"), and no earlier task implements it.

Add to `seamcheck/entries/__init__.py`, with a test in `seamcheck/tests/test_entries_registry.py`:

```python
def describe(repo_root: str, config: dict) -> list[str]:
    """One line per source: its confidence, and whether it runs. For `seamcheck config`."""
    config = config or {}
    running = {source.name for source, _ in select_all(repo_root, config)}
    forced = " (forced by entry_sources)" if config.get("entry_sources") else ""
    return [f"{source.name:<8} {confidence:4.2f}  {'runs' + forced if source.name in running else 'does not run'}"
            for source, confidence in _ranked(repo_root, config)]
```

and print its lines after the key list in both printers, under the heading `what the map starts from:`, before the tunnel setting. A `ValueError` from an unknown forced source is printed as that sentence, not raised: `config` is where a person goes to find out what is wrong.

- [ ] **Step 3: `docs/coverage.md`**

After line 154 (`python tools/recall.py ...`), add:

```
python tools/corpus.py entries                             # pages and entries, full scan per repo
```

- [ ] **Step 4: Stage for checkpoint 4**

```bash
python -m pytest seamcheck/tests/test_entries_registry.py seamcheck/tests/test_cli.py -q -p no:cacheprovider
ruff check seamcheck/
git add docs/the-map.md docs/coverage.md seamcheck/entries/__init__.py seamcheck/tests/test_entries_registry.py \
        seamcheck/cli.py seamcheck/management/commands/seamcheck.py
git status --short   # only the files Tasks 13 and 14 changed
```

Checkpoint 4 commits Tasks 13 and 14 together, after both gates.

---

## Task 15: Verification — every gate, before and after, and the map driven in a browser

No new code. Every command runs from the worktree root unless it says otherwise, and `SCRATCH` is this session's scratchpad (`/private/tmp/claude-501/-Users-balazssimon-dev-seamcheck/fe1d79e8-9cc0-469b-a96c-70c171ae3fda/scratchpad`). A number that moved gets read, not accepted.

**Memory:** run the heavy steps (4, 6, 7, 9) one at a time, never in parallel. Processes were killed for low memory earlier today.

- [ ] **Step 1: Lint and the whole suite**

```bash
ruff check seamcheck/
python -m pytest seamcheck/tests -q -p no:cacheprovider
```

Expected: ruff clean; 0 failed. Baseline in this worktree before any change: 1,718 passed.

- [ ] **Step 2: This repository's own gate**

```bash
python -m seamcheck.cli check
```

Expected: no crash, `unresolved 0`. It read `connected 0 unused 0 unresolved 0 uncertain 1` before this branch. If a count moved, find the symbol and say why in the final report.

- [ ] **Step 3: The parser bundles are untouched**

```bash
./build_parsers.sh && git diff --exit-code -- seamcheck/js_tools seamcheck/css_tools
```

Expected: exit 0.

- [ ] **Step 4: The corpus crash gate**

```bash
python tools/corpus.py scan > "$SCRATCH/corpus-scan-branch.txt" 2>&1
cp ../../../../seamcheck-corpus/results.json "$SCRATCH/results-branch.json"   # the corpus folder, beside the main checkout
```

Compare per repository against `$SCRATCH/results_after.json` (taken earlier today on the code `main` now holds): no `gate1` newly `CRASH`, no `gate2` newly `NO ROUTES`, `by_status` totals unchanged. This gate cannot see phase 1 (it never runs the JS extractor, `page_files` or `build_map`), so "unchanged" is the expected and only acceptable result here. Resolve the corpus path with `ls` before copying — do not trust the relative path above blindly.

- [ ] **Step 5: A clean snapshot of `main` to measure "before" with**

```bash
rm -rf "$SCRATCH/base" && mkdir -p "$SCRATCH/base"
git archive b357d4de4 seamcheck tools | tar -x -C "$SCRATCH/base"
```

`git archive` reads the commit, not a working tree, so another session's edits in the main checkout cannot leak into the "before" numbers.

- [ ] **Step 6: Entries, before and after, on the phase-1 set**

```bash
python tools/corpus.py entries --code "$SCRATCH/base" --out "$SCRATCH/entries-before.json"
python tools/corpus.py entries --out "$SCRATCH/entries-after.json"
python tools/corpus.py entries-compare "$SCRATCH/entries-before.json" "$SCRATCH/entries-after.json" | tee "$SCRATCH/entries-compare.txt"
```

Check, repository by repository:
- no row that was `ok` before is `CRASH` after;
- **Django, FastAPI, Express rows: `pages identical`.** A changed page list there is a bug in `LegacySource` until proven otherwise — stop and read it;
- **Next.js rows (commerce, documenso, nextjs-subscription-payments, platforms, and cal.com, dub):** page entries present after; "pages gone" lists only `.js` sweep stems (loose scripts, not routes) — read the list;
- bucket counts and `on page` moved; record them. The design doc accepts bucket changes on Django while its page contents stay the same.

- [ ] **Step 7: leanos-app, before and after**

```bash
LEANOS=/Users/balazssimon/dev/leanos/leanos-app
python tools/corpus.py entries --path "$LEANOS" --code "$SCRATCH/base" --out "$SCRATCH/leanos-before.json"
python tools/corpus.py entries --path "$LEANOS" --out "$SCRATCH/leanos-after.json"
python tools/corpus.py entries-compare "$SCRATCH/leanos-before.json" "$SCRATCH/leanos-after.json"
```

The "before" row should reproduce the design doc's measured table (0 pages drawn; `other` 870; `template` 4,708; 6,268 symbols). If it does not, the measurement is off — fix that before believing the "after" row.

Against the design doc's Phase 1 "Done when":
- `kinds`: `page 19`, `server 8`;
- `buckets.other` ≤ 62 — a projection from a thrown-away runtime patch, not a promise. Record the real number; investigate if it is far above, not merely different.

- [ ] **Step 8: leanos-app precision must not fall**

```bash
(cd "$SCRATCH/base" && python -c "import sys; sys.path.insert(0, 'tools'); import precision; t, f, _ = precision.score('leanos-app'); print('before', t, f)")
python -c "import sys; sys.path.insert(0, 'tools'); import precision; t, f, _ = precision.score('leanos-app'); print('after', t, f)"
```

`precision.py` puts its own checkout first on `sys.path`, which is why "before" runs from inside the snapshot. It scores `tools/labels/leanos-app.json`, whose `_root` points at the leanos-app checkout. Expected: after ≥ before, as a share of true claims. Do not run `precision.py` with no argument: it scores every labelled repository at once, and that run was killed for low memory earlier today.

- [ ] **Step 9: Render the leanos-app map and check the document**

```bash
python -c "
import json, pathlib, sys
sys.path.insert(0, 'tools')
import verify_output
row = verify_output.verify('leanos-app', pathlib.Path('/Users/balazssimon/dev/leanos/leanos-app'))
print(json.dumps(row, indent=2))
"
```

Expected: `problems` empty — every script parses, the payload agrees with the graph, pages exist, nothing leaked. Then write the map for the browser:

```bash
python -c "
import sys; sys.path.insert(0, '.')
from seamcheck import api
doc = api.map_document('/Users/balazssimon/dev/leanos/leanos-app')
print(api.write_map_document(doc, '$SCRATCH/leanos-map.html', bundle=False))
"
```

Rendering a map records a trend row inside the scanned repository (`_map_document` calls `record_trend`). Before and after this step, run `git -C /Users/balazssimon/dev/leanos/leanos-app status --short` and report any file this left behind — that repository belongs to another session.

- [ ] **Step 10: Drive the map in a browser**

Load the `claude-in-chrome` skill, open `file://$SCRATCH/leanos-map.html` in a new tab, and check, taking a screenshot of each:
1. The Page picker lists the Next.js pages by name first, then the route handlers, then the "Not reached from any page" buckets.
2. A bucket's option shows its count once ("… — 62 nodes", not "— 62 — 62 nodes").
3. `Pricing · /pricing/[locale]` draws.
4. A route handler, e.g. `/api/...`, draws.
5. A page with nothing to draw shows how many symbols its files hold, not a blank canvas.
6. Clicking a page's own node shows why it is an entry ("a page file, routed by the filesystem").
7. The console shows no errors (`read_console_messages`).

Anything wrong here is a failing gate: fix it, commit the fix on its own, and repeat Steps 9-10.

**What Steps 9-10 found (2026-09-15), fixed in checkpoint 5:**
- The page card and the readout showed entry keys verbatim (`next:/pricing/[locale] — pick a module`, `server:app/api/stripe-webhook/route.ts — …`). Legacy keys are filename stems, so no earlier test could see it. Entries now carry a `label` (Next.js: the address; server and fallback: the file; legacy: none), placed on the page node and sent as page meta for the readout.
- The bucket numbers, not the browser, showed the second: on fastapi-realworld all 8 server entries drew a single node, and their 33 routes and handlers sat in "On a page, nothing to draw" ("in files a page reaches" — on a project with no page). Drawing starts from seeds, and `url`/`view` are not touches. A server entry now also seeds from the `url` and `view` symbols in its files; pages still seed from touches only. dub: server entries drawing only themselves 63 → 0.
- Maps were checked on leanos-app, dub and fastapi-realworld. Step 9's render left a trend row in leanos-app's tracked `OTHER/seamcheck/trend.jsonl` each time; each row was removed, and its status is back to `?? docs/` (untracked before this task).

- [ ] **Step 11: The report**

The final commit (or the PR description, if one is opened) records, per `CLAUDE.md` "Record the before/after numbers":
- tests, ruff, `check` on this repository, `build_parsers` — each result;
- corpus `scan`: unchanged, and a plain statement that this gate cannot see phase 1;
- `entries-compare` on the phase-1 set: page lists per stack, bucket and on-page totals before → after;
- leanos-app: entries by kind, `other` bucket, precision before → after;
- the Phase 1 "Done when" rows, each marked met or not met, with the number.

A row not met is written as not met.

- [ ] **Step 12: Finish the branch**

Use `superpowers:finishing-a-development-branch`. Do not merge, push or open a PR without asking.
