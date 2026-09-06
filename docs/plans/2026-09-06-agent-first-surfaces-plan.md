# Agent-first CLI and MCP: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make both surfaces answer an agent's question in bounded, machine-typed JSON, from
one cached scan, with a CI recipe an agent can write unaided.

**Architecture:** One envelope module every answer passes through, one scan cache every
command reads from, and one bounded-list helper every listing uses. The CLI and the MCP
server become two thin adapters over the same functions, so they cannot drift. New
question-shaped commands replace "fetch the graph and filter it yourself".

**Tech Stack:** Python 3.10+, stdlib only for the new modules (`json`, `hashlib`,
`dataclasses`). MCP through the existing `mcp>=1.0` FastMCP wrapper. No new dependencies.

**Spec:** [2026-09-06-cli-mcp-review.md](2026-09-06-cli-mcp-review.md) — the measured review
this plan implements. Read it first; every task below argues from a numbered finding in it.

## Global Constraints

- **Python floor is 3.10** (`pyproject.toml`, `[tool.ruff] target-version = "py310"`). No
  `match`, no `tomllib`, no `X | Y` in isinstance.
- **No new runtime dependencies.** The whole point of this tool is that it costs nothing per
  run; a dependency for JSON shaping would be absurd.
- **Every new module gets a docstring that says WHY it exists**, in the voice of the rest of
  the codebase: prose, the measurement or the bug that motivated it, never a restatement of
  the code.
- **Ruff must pass**: `ruff check seamcheck/ tools/` with the repo's own select list
  (`E,F,I,UP,B,SIM,C4`).
- **The commit gate applies**: `python -m seamcheck.cli check` on this repo must stay
  `connected 0 unused 0 unresolved 0 uncertain 1`, and `python tools/corpus.py scan` must
  show no `CRASH` and no repo losing its routes.
- **stdout is for the answer, stderr is for humans.** Any prose, progress, advice or warning
  goes to stderr. A machine-mode command writes exactly one JSON document to stdout.
- **Every schema is versioned**: `"schema": 1` in every envelope, bumped only for a breaking
  change, with the change noted in CHANGELOG.md.

---

## The surface, before and after

### CLI

| command | today | after |
|---|---|---|
| `map` | serves forever, first in help, no JSON | unchanged for people; `--no-serve` unchanged; gains `--json` printing `{path, bytes, pages}` instead of prose |
| `check` | prose + a Python dict repr; **exits 0 on non-Django**; exit 2 only under `--since` | fixed exit codes 0/1/2 on every adapter; `--json` envelope; `--format sarif` and `--format github`; `--limit` on the finding lists |
| `scan` | prose totals, writes a 72 MB JSON as a side effect | `--json` envelope with counts and cache state; the side-effect write becomes opt-in `--write-graph` |
| `report` | terminal/markdown/html | unchanged, plus `--json` for the same digest as data |
| `json` | **72.6 MB, 18.2M tokens**, unbounded | becomes a summary by default; the full graph needs `--full --yes` and prints its size to stderr first |
| `explain` | 88.5 s for a wrong id, prose only | reads the scan cache (≈0.2 s warm); `--json`; suggests near-miss ids on a miss |
| `triage` | prose, one sentence | `--json` envelope `{ok, entry, message}` |
| `config` | aligned columns, box glyphs | `--json` with the resolved config and where each key came from |
| `backfill` | prose per commit | `--json` with one row per commit |
| `observe` | prose | `--json` with the pages visited and what was observed |
| `share` | markdown + banner; `--quiet` broken | `--quiet` fixed; `--json` already exists, keeps its shape |
| `serve` | alias of `map` | unchanged |
| **`symbols`** | *does not exist* | **new.** `seamcheck symbols --search push` → ids, kinds, files. The cheap way to turn a name into an id, so a typo costs 200 ms rather than 90 s |
| **`findings`** | *does not exist* | **new.** `--file`, `--kind`, `--status`, `--owner`, `--limit`, `--cursor`. The workhorse: "what is wrong in this file" |
| **`calls`** | *does not exist* | **new.** `seamcheck calls <id>` → who reaches it and what it reaches, one hop each way, with file and line |
| **`diff`** | *does not exist* (only `check --since`) | **new.** `seamcheck diff --since REF` → appeared, vanished, changed status. The primitive both CI and an agent actually want |

### MCP

| tool | today | after |
|---|---|---|
| `seamcheck_check` | unbounded lists, `passed` means something other than the description | `limit`/`cursor`, `since` parameter, description corrected, `outputSchema`, `readOnlyHint` |
| `seamcheck_explain` | a full scan per call | cached scan; near-miss suggestions on a miss |
| `seamcheck_triage` | `status` required even for `undo` | `status` optional when `undo=true`; enum on `status` and `why`; the only tool with `readOnlyHint: false` |
| `seamcheck_unverified` | good already | keeps `limit`/`kind`, gains `cursor` and `outputSchema` |
| `seamcheck_why_wrong` | fine | gains `outputSchema` |
| `seamcheck_report` | `fmt` passes through to `json`/`map`: 72 MB and 8.6 MB footguns | `fmt` enum restricted to `terminal|markdown|html`; the big formats are not reachable from MCP at all |
| `seamcheck_share` | fine | gains `outputSchema` |
| `seamcheck_services` | fine | gains `outputSchema` |
| **`seamcheck_findings`** | *does not exist* | **new.** The bounded, filterable list; the tool an agent should call first |
| **`seamcheck_symbols`** | *does not exist* | **new.** Name to id, cheap |
| **`seamcheck_diff`** | *does not exist* | **new.** What changed since a ref |
| **`seamcheck_snapshot`** | *does not exist* | **new.** Writes the baseline, so an agent can create the thing `check` needs instead of asking a human |
| *server instructions* | never sent | the loop from the module docstring, sent as `instructions=` |

---

## File structure

**New:**

- `seamcheck/envelope.py` — the one answer shape, the error codes, the truncation block.
- `seamcheck/scancache.py` — one scan per process and per input-stamp, on disk.
- `seamcheck/queries.py` — `symbols()`, `findings()`, `calls()`, `diff()`: the question-shaped
  functions both surfaces call. No printing, no argparse, no MCP.
- `seamcheck/renderers/sarif.py` — SARIF 2.1.0 for GitHub code scanning.
- `seamcheck/renderers/github.py` — `::warning file=…` workflow annotations.
- `docs/ci.md` — the copy-paste pipeline, with the traps named.
- `seamcheck/tests/test_envelope.py`, `test_scancache.py`, `test_queries.py`,
  `test_exit_codes.py`, `test_sarif.py`, `test_mcp_protocol.py`.

**Modified:**

- `seamcheck/cli.py` — new commands, `--json` plumbing, the non-Django parser gap.
- `seamcheck/management/commands/seamcheck.py` — exit codes, `--json` on every path.
- `seamcheck/mcp_server.py` — instructions, enums, schemas, annotations, four new tools.
- `README.md`, `llms.txt`, `docs/agents.md`, `docs/commands.md` — agents first.

**Phases.** Each is shippable on its own and each has its own review gate:

1. **Tasks 1-4**: the two bugs, the envelope, the cache. Correctness and cost.
2. **Tasks 5-8**: the new questions, bounded answers, CI formats.
3. **Tasks 9-11**: MCP alignment and the documentation.

---

### Task 1: Fix the gate that cannot fail

**Files:**
- Modify: `seamcheck/cli.py:569-572`
- Test: `seamcheck/tests/test_exit_codes.py` (create)

**Interfaces:**
- Consumes: `api.check(repo_root=…) -> dict` with keys `passed`, `message`,
  `new_unresolved`, `new_unused`, `triage_invalidated`, `returned`, `counts` (`api.py:421-433`).
- Produces: nothing new; fixes an exit code other tasks rely on.

- [ ] **Step 1: Write the failing test**

```python
# seamcheck/tests/test_exit_codes.py
"""The gate's exit code is the only thing CI reads, and it was wrong everywhere but Django.

`cli.py` asked `result.get("findings")` of a dict that has no `findings` key, so the
non-Django path returned 0 for every project that ever had a finding. Reproduced on redash:
47 unresolved, 53 unused, exit 0.
"""
from unittest import mock

from django.test import SimpleTestCase

from seamcheck import cli


class NonDjangoGateTests(SimpleTestCase):
    def test_a_project_with_findings_fails_the_gate(self):
        outcome = {"passed": False, "message": "2 new", "new_unresolved": [{"id": "url:x"}],
                   "new_unused": [], "triage_invalidated": [], "returned": [],
                   "counts": {"unresolved": 1}}
        with mock.patch("seamcheck.api.check", return_value=outcome), \
             mock.patch("seamcheck.api.report", return_value="digest"), \
             mock.patch("seamcheck.cli._worth_scanning", return_value=True):
            code = cli._run_without_django(["--check"], verbose=False)

        self.assertEqual(code, 1, "a project with findings must fail the gate")

    def test_a_clean_project_passes(self):
        outcome = {"passed": True, "message": "clean", "new_unresolved": [],
                   "new_unused": [], "triage_invalidated": [], "returned": [],
                   "counts": {}}
        with mock.patch("seamcheck.api.check", return_value=outcome), \
             mock.patch("seamcheck.api.report", return_value="digest"), \
             mock.patch("seamcheck.cli._worth_scanning", return_value=True):
            code = cli._run_without_django(["--check"], verbose=False)

        self.assertEqual(code, 0)
```

- [ ] **Step 2: Run it and watch the first test fail**

Run: `python -m pytest seamcheck/tests/test_exit_codes.py -v`
Expected: `test_a_project_with_findings_fails_the_gate` FAILS with `0 != 1`; the clean test passes.

- [ ] **Step 3: Fix the key**

```python
# seamcheck/cli.py, replacing lines 569-572
        if options["check"]:
            # The CI gate. `passed` is the key api.check() actually returns; this asked for
            # `findings`, which it never had, so every non-Django project passed no matter
            # what was in it - measured on redash: 47 unresolved, exit 0.
            result = api.check(repo_root=root)
            print(api.report(repo_root=root, fmt="terminal"))
            return 0 if result.get("passed") else 1
```

- [ ] **Step 4: Both tests pass**

Run: `python -m pytest seamcheck/tests/test_exit_codes.py -v`
Expected: 2 passed.

- [ ] **Step 5: Prove it on a real project**

Run: `cd ~/dev/seamcheck-corpus/redash && python -m seamcheck.cli check; echo "EXIT $?"`
Expected: the same 47 unresolved as before, and `EXIT 1`.

- [ ] **Step 6: Commit**

```bash
git add seamcheck/cli.py seamcheck/tests/test_exit_codes.py
git commit -m "fix(cli): the gate asked for a key that does not exist, so it never failed off Django"
```

---

### Task 2: The missing baseline exits 2, on every path

**Files:**
- Modify: `seamcheck/management/commands/seamcheck.py:437-450`
- Modify: `seamcheck/cli.py` (the non-Django `--check` branch from Task 1)
- Test: `seamcheck/tests/test_exit_codes.py` (extend)

**Interfaces:**
- Consumes: `api.check()`'s `message`, which begins `"No baseline snapshot stored"`
  (`api.py:381`) when there is nothing to diff against.
- Produces: `EXIT_CLEAN = 0`, `EXIT_FINDINGS = 1`, `EXIT_NO_BASELINE = 2`, `EXIT_USAGE = 3`,
  `EXIT_ENVIRONMENT = 4` in `seamcheck/envelope.py` — Task 3 creates that module, so define
  the constants here in `seamcheck/exitcodes.py` and have the envelope import them.

- [ ] **Step 1: Write the failing test**

```python
# seamcheck/tests/test_exit_codes.py, appended
class BaselineExitCodeTests(SimpleTestCase):
    """Help, docs/commands.md and llms.txt all promise "2 if no baseline". Reproduced on the
    reference project with no snapshot: exit 1. A CI job cannot tell a regression from a
    first run, which is the whole reason that code was documented."""

    def test_no_baseline_exits_two_not_one(self):
        outcome = {"passed": False,
                   "message": "No baseline snapshot stored for abc123 yet - nothing to diff against.",
                   "new_unresolved": [], "new_unused": [], "triage_invalidated": [],
                   "returned": [], "counts": {}}
        with mock.patch("seamcheck.api.check", return_value=outcome), \
             mock.patch("seamcheck.api.report", return_value="digest"), \
             mock.patch("seamcheck.cli._worth_scanning", return_value=True):
            code = cli._run_without_django(["--check"], verbose=False)

        self.assertEqual(code, 2, "no baseline is not the same answer as a regression")
```

- [ ] **Step 2: Run it and watch it fail**

Run: `python -m pytest seamcheck/tests/test_exit_codes.py::BaselineExitCodeTests -v`
Expected: FAIL, `1 != 2`.

- [ ] **Step 3: Create the exit-code module**

```python
# seamcheck/exitcodes.py
"""What the process says to CI, in one place.

These were spread across two engines and disagreed: the same failure exited 2 through
`seamcheck` and 1 through `manage.py seamcheck`, and the documented "2 if no baseline" only
existed on the `--since` branch. A CI job reads nothing but this number, so it is worth a
module of its own.
"""
EXIT_CLEAN = 0
EXIT_FINDINGS = 1
EXIT_NO_BASELINE = 2
EXIT_USAGE = 3          # the command was wrong: unknown flag, bad enum value
EXIT_ENVIRONMENT = 4    # the machine was wrong: no adapter, missing import, no node

# The one string api.check() uses to say it had nothing to compare against.
NO_BASELINE = "No baseline snapshot stored"


def gate_code(outcome: dict) -> int:
    """0 clean, 1 findings, 2 no baseline - the same answer on every path."""
    if str(outcome.get("message", "")).startswith(NO_BASELINE):
        return EXIT_NO_BASELINE
    return EXIT_CLEAN if outcome.get("passed") else EXIT_FINDINGS
```

- [ ] **Step 4: Use it on both paths**

```python
# seamcheck/cli.py, the --check branch
            from seamcheck.exitcodes import gate_code
            result = api.check(repo_root=root)
            print(api.report(repo_root=root, fmt="terminal"))
            return gate_code(result)
```

```python
# seamcheck/management/commands/seamcheck.py, replacing the bare-check exit at :449-450
        from seamcheck.exitcodes import EXIT_CLEAN, gate_code

        code = gate_code(outcome)
        if code != EXIT_CLEAN:
            raise SystemExit(code)
```

- [ ] **Step 5: Tests pass**

Run: `python -m pytest seamcheck/tests/test_exit_codes.py -v`
Expected: 3 passed.

- [ ] **Step 6: Commit**

```bash
git add seamcheck/exitcodes.py seamcheck/cli.py seamcheck/management/commands/seamcheck.py seamcheck/tests/test_exit_codes.py
git commit -m "fix(cli): a missing baseline is exit 2 on every path, as three documents promised"
```

---

### Task 3: The envelope

**Files:**
- Create: `seamcheck/envelope.py`
- Test: `seamcheck/tests/test_envelope.py`

**Interfaces:**
- Produces:
  - `answer(command: str, data: dict, *, repo: str = "", sha: str = "", warnings: list[str] | None = None, truncated: dict | None = None, cost: dict | None = None) -> dict`
  - `failure(command: str, code: str, message: str, *, hint: str = "") -> dict`
  - `page(rows: list, limit: int, cursor: str = "") -> tuple[list, dict]`
  - `ERRORS: dict[str, str]` — the code table.

- [ ] **Step 1: Write the failing test**

```python
# seamcheck/tests/test_envelope.py
"""One answer shape, so an agent parses seamcheck once rather than per command.

Today `check` prints a Python dict repr, `explain` prints prose, `triage` prints a sentence
and `json` prints 72 MB. Every one of those is a different parser to write, and two of them
are not parseable at all.
"""
import json

from django.test import SimpleTestCase

from seamcheck import envelope


class EnvelopeTests(SimpleTestCase):
    def test_an_answer_is_json_serialisable_and_versioned(self):
        out = envelope.answer("check", {"passed": True}, repo="/x", sha="abc123")

        text = json.dumps(out)  # must not raise
        self.assertEqual(out["schema"], 1)
        self.assertTrue(out["ok"])
        self.assertEqual(out["command"], "check")
        self.assertEqual(out["data"], {"passed": True})
        self.assertIsNone(out["error"])
        self.assertIn("abc123", text)

    def test_a_failure_carries_a_code_a_program_can_branch_on(self):
        out = envelope.failure("explain", "unknown_symbol", "No symbol with id `x`.",
                               hint="Try `seamcheck symbols --search x`.")

        self.assertFalse(out["ok"])
        self.assertEqual(out["error"]["code"], "unknown_symbol")
        self.assertIn("symbols --search", out["error"]["hint"])
        self.assertIsNone(out["data"])

    def test_every_error_code_is_documented(self):
        # A code an agent cannot look up is a string, not an interface.
        for code in ("unknown_symbol", "no_baseline", "no_adapter", "bad_argument"):
            self.assertIn(code, envelope.ERRORS)

    def test_a_page_reports_what_it_left_out(self):
        rows = [{"id": f"x{i}"} for i in range(100)]

        shown, cut = envelope.page(rows, limit=10)

        self.assertEqual(len(shown), 10)
        self.assertEqual(cut["returned"], 10)
        self.assertEqual(cut["total"], 100)
        self.assertTrue(cut["cursor"], "there is more, so there is a cursor")

    def test_the_cursor_continues_where_the_page_stopped(self):
        rows = [{"id": f"x{i}"} for i in range(30)]
        _, cut = envelope.page(rows, limit=10)

        shown, again = envelope.page(rows, limit=10, cursor=cut["cursor"])

        self.assertEqual(shown[0]["id"], "x10")
        self.assertEqual(again["returned"], 10)

    def test_the_last_page_has_no_cursor(self):
        rows = [{"id": "only"}]

        _, cut = envelope.page(rows, limit=10)

        self.assertEqual(cut["cursor"], "")
```

- [ ] **Step 2: Run it and watch every test fail**

Run: `python -m pytest seamcheck/tests/test_envelope.py -v`
Expected: 6 errors, `No module named 'seamcheck.envelope'`.

- [ ] **Step 3: Write the module**

```python
# seamcheck/envelope.py
"""The one shape every machine-readable answer takes.

Before this, each command answered in its own dialect: `check` printed a Python dict repr
(single quotes, not JSON), `explain` printed prose, `triage` printed one English sentence,
and `json` printed the entire graph - 72 MB and about 18 million tokens on the reference
project. An agent had to write a parser per command, and for two of them there was nothing
to parse.

The envelope is deliberately boring: a version, whether it worked, the answer, what was left
out, and what it cost. `error` is a CODE first and prose second, because a program branches
on the code and shows the prose to a person.
"""
from __future__ import annotations

SCHEMA = 1

# Every code a caller can branch on. A code that is not in here must not be emitted: an
# undocumented code is a string, not an interface.
ERRORS = {
    "unknown_symbol": "No symbol with that id in the current scan.",
    "no_baseline": "No snapshot to compare against yet.",
    "no_adapter": "Nothing here this knows how to read.",
    "bad_argument": "An argument was outside its allowed set.",
    "missing_dependency": "The project imports something that is not installed here.",
    "no_git": "This is not a git repository, or the ref could not be resolved.",
    "too_large": "The answer is bigger than the limit; ask for less or pass --full --yes.",
}


def answer(command: str, data, *, repo: str = "", sha: str = "",
           warnings: list[str] | None = None, truncated: dict | None = None,
           cost: dict | None = None) -> dict:
    """A successful answer, ready for json.dumps."""
    return {
        "schema": SCHEMA,
        "ok": True,
        "command": command,
        "repo": repo,
        "sha": sha,
        "data": data,
        "truncated": truncated,
        "warnings": warnings or [],
        "cost": cost or {},
        "error": None,
    }


def failure(command: str, code: str, message: str, *, hint: str = "") -> dict:
    """A failure. `code` must be a key of ERRORS."""
    if code not in ERRORS:
        raise ValueError(f"undocumented error code {code!r}; add it to envelope.ERRORS")
    return {
        "schema": SCHEMA,
        "ok": False,
        "command": command,
        "repo": "",
        "sha": "",
        "data": None,
        "truncated": None,
        "warnings": [],
        "cost": {},
        "error": {"code": code, "message": message, "hint": hint},
    }


def page(rows: list, limit: int, cursor: str = "") -> tuple[list, dict]:
    """One page of rows, and an honest account of what was left out.

    The cursor is the offset as a string rather than an opaque token: the row order is
    stable within a scan, an agent can read it, and there is nothing to keep server-side.
    """
    start = int(cursor) if cursor.isdigit() else 0
    shown = rows[start:start + limit]
    following = start + len(shown)
    return shown, {
        "returned": len(shown),
        "total": len(rows),
        "offset": start,
        "cursor": str(following) if following < len(rows) else "",
    }
```

- [ ] **Step 4: Tests pass**

Run: `python -m pytest seamcheck/tests/test_envelope.py -v`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add seamcheck/envelope.py seamcheck/tests/test_envelope.py
git commit -m "feat(envelope): one answer shape, with error codes a program can branch on"
```

---

### Task 4: The scan cache

**Files:**
- Create: `seamcheck/scancache.py`
- Modify: `seamcheck/api.py:220` (`scan`) to consult it
- Test: `seamcheck/tests/test_scancache.py`

**Interfaces:**
- Consumes: `api.scan(repo_root) -> Graph`, `snapshot.graph_to_dict` / `graph_from_dict`
  (`graph.py:70-86`).
- Produces:
  - `cached_scan(repo_root: str, *, refresh: bool = False) -> tuple[Graph, dict]` where the
    dict is `{"cached": bool, "key": str, "seconds": float}`.
  - `stamp(repo_root: str) -> str` — the content key.

- [ ] **Step 1: Write the failing test**

```python
# seamcheck/tests/test_scancache.py
"""Five explanations were five full scans.

Every MCP tool and every CLI command ran `api.scan` from scratch, so on the reference project
looking at five symbols cost 7.5 minutes, and a mistyped id cost the same 88.5 seconds as a
real one. The graph is a pure function of the files, the tool version and the config, so it
can be remembered.
"""
import pathlib
import tempfile
from unittest import mock

from django.test import SimpleTestCase

from seamcheck import scancache
from seamcheck.graph import Graph, Status, Symbol


def _graph():
    return Graph(symbols=[Symbol(id="url:x", kind="url", label="/x/", sub="", file="urls.py",
                                 line=1, status=Status.CONNECTED, snippet="", chain=[],
                                 note="")],
                 edges=[])


class ScanCacheTests(SimpleTestCase):
    def test_the_second_call_does_not_scan_again(self):
        with tempfile.TemporaryDirectory() as root:
            (pathlib.Path(root) / "urls.py").write_text("x = 1")
            with mock.patch("seamcheck.api.scan", return_value=_graph()) as scan:
                first, how_first = scancache.cached_scan(root)
                second, how_second = scancache.cached_scan(root)

            self.assertEqual(scan.call_count, 1, "the second call must not rescan")
            self.assertFalse(how_first["cached"])
            self.assertTrue(how_second["cached"])
            self.assertEqual([s.id for s in second.symbols], [s.id for s in first.symbols])

    def test_a_changed_file_invalidates_it(self):
        with tempfile.TemporaryDirectory() as root:
            source = pathlib.Path(root) / "urls.py"
            source.write_text("x = 1")
            with mock.patch("seamcheck.api.scan", return_value=_graph()) as scan:
                scancache.cached_scan(root)
                source.write_text("x = 2")
                scancache.cached_scan(root)

            self.assertEqual(scan.call_count, 2, "a changed file must be rescanned")

    def test_refresh_forces_a_scan(self):
        with tempfile.TemporaryDirectory() as root:
            (pathlib.Path(root) / "urls.py").write_text("x = 1")
            with mock.patch("seamcheck.api.scan", return_value=_graph()) as scan:
                scancache.cached_scan(root)
                scancache.cached_scan(root, refresh=True)

            self.assertEqual(scan.call_count, 2)

    def test_the_version_is_part_of_the_key(self):
        # A cache entry that survives an upgrade is a lie about what this tool would say.
        with tempfile.TemporaryDirectory() as root:
            (pathlib.Path(root) / "urls.py").write_text("x = 1")
            first = scancache.stamp(root)
            with mock.patch("seamcheck.scancache._version", return_value="99.0.0"):
                second = scancache.stamp(root)

            self.assertNotEqual(first, second)
```

- [ ] **Step 2: Run it and watch it fail**

Run: `python -m pytest seamcheck/tests/test_scancache.py -v`
Expected: 4 errors, `No module named 'seamcheck.scancache'`.

- [ ] **Step 3: Write the module**

```python
# seamcheck/scancache.py
"""One scan, remembered, so asking a second question is cheap.

Measured before this existed: every command and every MCP tool called `api.scan` afresh, so
five explanations on the reference project were five 90-second scans - and a mistyped symbol
id cost the same 88.5 seconds as a correct one, to be told the id was wrong.

The graph is a pure function of (the files, the tool version, the config), so the key is a
hash of exactly those three. Two layers: a process memo, for an MCP session answering
question after question, and a file under `.seamcheck/cache/`, for the next command in the
same shell. Both are invalidated by the same key, so neither can serve a stale answer.
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import time

from seamcheck.graph import Graph, graph_from_dict, graph_to_dict

_MEMO: dict[str, Graph] = {}
_CACHE_DIR = ".seamcheck/cache"
# Directories whose contents never change what a scan says.
_SKIP = {".git", "node_modules", "__pycache__", ".venv", "venv", ".seamcheck", "dist",
         "build", ".mypy_cache", ".ruff_cache", ".pytest_cache"}


def _version() -> str:
    from seamcheck import __version__

    return __version__


def stamp(repo_root: str) -> str:
    """The key: every input file's path, size and mtime, plus the version and the config.

    Size and mtime rather than content: hashing a 100M-line repository to decide whether to
    scan it would cost more than the scan. The pair is what every build tool trusts, and a
    tree that changes without either changing is a tree somebody is lying about.
    """
    digest = hashlib.sha256()
    digest.update(_version().encode())
    root = pathlib.Path(repo_root)
    for current, directories, files in os.walk(root):
        directories[:] = sorted(d for d in directories if d not in _SKIP and not d.startswith("."))
        for name in sorted(files):
            path = pathlib.Path(current) / name
            try:
                info = path.stat()
            except OSError:
                continue
            digest.update(str(path.relative_to(root)).encode())
            digest.update(f"{info.st_size}:{info.st_mtime_ns}".encode())
    return digest.hexdigest()[:32]


def _path(repo_root: str, key: str) -> pathlib.Path:
    return pathlib.Path(repo_root) / _CACHE_DIR / f"{key}.json"


def cached_scan(repo_root: str, *, refresh: bool = False) -> tuple[Graph, dict]:
    """The graph, from memory, from disk, or from a real scan - and which of the three."""
    from seamcheck import api

    key = stamp(repo_root)
    memo_key = f"{repo_root}\0{key}"
    if not refresh and memo_key in _MEMO:
        return _MEMO[memo_key], {"cached": True, "key": key, "seconds": 0.0, "from": "memory"}

    path = _path(repo_root, key)
    if not refresh and path.is_file():
        try:
            graph = graph_from_dict(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            graph = None
        if graph is not None:
            _MEMO[memo_key] = graph
            return graph, {"cached": True, "key": key, "seconds": 0.0, "from": "disk"}

    started = time.monotonic()
    graph = api.scan(repo_root)
    took = round(time.monotonic() - started, 2)
    _MEMO[memo_key] = graph
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(graph_to_dict(graph)), encoding="utf-8")
    except OSError:
        pass  # a read-only checkout still gets the process memo
    return graph, {"cached": False, "key": key, "seconds": took, "from": "scan"}


def clear(repo_root: str = "") -> None:
    """Forget everything, or everything for one repository."""
    if not repo_root:
        _MEMO.clear()
        return
    for memo_key in [k for k in _MEMO if k.startswith(f"{repo_root}\0")]:
        del _MEMO[memo_key]
```

- [ ] **Step 4: Tests pass**

Run: `python -m pytest seamcheck/tests/test_scancache.py -v`
Expected: 4 passed.

- [ ] **Step 5: Add `.seamcheck/cache/` to the repo's own gitignore**

```bash
grep -q "^.seamcheck/" .gitignore || echo ".seamcheck/" >> .gitignore
```

- [ ] **Step 6: Measure the win on a real project**

Run:
```bash
cd ~/dev/pointlessbutton
python -c "
from seamcheck import scancache; import time
for i in range(2):
    started = time.monotonic()
    graph, how = scancache.cached_scan('.')
    print(i, how['from'], round(time.monotonic() - started, 2), 's', len(graph.symbols), 'symbols')
"
```
Expected: first line `scan` and roughly 90 s; second line `disk` or `memory` and under 5 s.
Record both numbers in the commit message.

- [ ] **Step 7: Commit**

```bash
git add seamcheck/scancache.py seamcheck/tests/test_scancache.py .gitignore
git commit -m "feat(cache): one scan, remembered - a second question no longer costs a second scan"
```

---

### Task 5: `symbols`, so a name costs 200ms instead of 90 seconds

**Files:**
- Create: `seamcheck/queries.py`
- Modify: `seamcheck/cli.py` (COMMANDS table, `_plain_args`, dispatch)
- Test: `seamcheck/tests/test_queries.py`

**Interfaces:**
- Consumes: `scancache.cached_scan(repo_root) -> (Graph, dict)` from Task 4;
  `envelope.answer`, `envelope.page` from Task 3.
- Produces:
  - `symbols(repo_root: str, search: str = "", kind: str = "", limit: int = 25, cursor: str = "") -> dict`
  - `near(repo_root: str, symbol_id: str, limit: int = 5) -> list[str]`
  Both return envelopes; `near` returns bare ids for use inside other answers.

- [ ] **Step 1: Write the failing test**

```python
# seamcheck/tests/test_queries.py
"""The questions an agent actually asks, answered without handing over the graph.

`seamcheck json` is 72.6 MB on the reference project - about 18 million tokens - and it is
the command the code names as the agent interface. These functions exist so that "what is
this called", "what is wrong in this file" and "what changed" each cost a few hundred tokens.
"""
from unittest import mock

from django.test import SimpleTestCase

from seamcheck import queries
from seamcheck.graph import Graph, Status, Symbol


def _symbol(kind, label, file, status=Status.CONNECTED, owner=""):
    return Symbol(id=f"{kind}:{label}", kind=kind, label=label, sub="", file=file, line=7,
                  status=status, snippet=f"def {label}", chain=[], note="", owner=owner)


GRAPH = Graph(symbols=[
    _symbol("view", "submit_push", "app/views.py"),
    _symbol("url", "api/submit/", "app/urls.py"),
    _symbol("redis_key", "user:*:pushes", "app/cache.py", Status.UNRESOLVED),
    _symbol("css_selector", "btn-push", "static/site.css", Status.UNUSED),
], edges=[])


class SymbolsTests(SimpleTestCase):
    def setUp(self):
        patch = mock.patch("seamcheck.scancache.cached_scan",
                           return_value=(GRAPH, {"cached": True, "seconds": 0.0}))
        patch.start()
        self.addCleanup(patch.stop)

    def test_a_search_finds_by_substring_and_says_where(self):
        out = queries.symbols(".", search="push")

        found = {row["id"]: row for row in out["data"]["symbols"]}
        self.assertIn("view:submit_push", found)
        self.assertEqual(found["view:submit_push"]["file"], "app/views.py")
        self.assertEqual(found["view:submit_push"]["line"], 7)

    def test_it_is_bounded_and_says_what_it_left_out(self):
        out = queries.symbols(".", search="", limit=2)

        self.assertEqual(len(out["data"]["symbols"]), 2)
        self.assertEqual(out["truncated"]["total"], 4)
        self.assertEqual(out["truncated"]["cursor"], "2")

    def test_a_kind_filter_narrows_it(self):
        out = queries.symbols(".", kind="url")

        self.assertEqual([row["id"] for row in out["data"]["symbols"]], ["url:api/submit/"])

    def test_near_suggests_ids_for_a_typo(self):
        # The 88.5-second "No symbol with id `urls.py`" is the thing this kills.
        self.assertIn("url:api/submit/", queries.near(".", "url:api/submit"))
```

- [ ] **Step 2: Run it and watch it fail**

Run: `python -m pytest seamcheck/tests/test_queries.py -v`
Expected: 4 errors, `No module named 'seamcheck.queries'`.

- [ ] **Step 3: Write the module**

```python
# seamcheck/queries.py
"""The questions, answered small.

An agent's questions are "what is this called", "what is wrong in this file", "who calls
this" and "what changed since main". Until now the only way to ask any of them was
`seamcheck json`, which is 72.6 MB on the reference project, so the agent paid about 18
million tokens for four answers it could have had for a few hundred each.

Everything here reads the cached scan, returns an envelope, and is bounded by default. The
CLI and the MCP server both call these functions and neither adds logic of its own, because
two implementations of one question is how two surfaces come to disagree.
"""
from __future__ import annotations

import difflib

from seamcheck import envelope, scancache


def _row(symbol) -> dict:
    return {"id": symbol.id, "kind": symbol.kind, "label": symbol.label,
            "status": symbol.status.value, "file": symbol.file, "line": symbol.line,
            "owner": symbol.owner or "", "note": symbol.note or ""}


def _scan(repo_root: str):
    graph, how = scancache.cached_scan(repo_root)
    return graph, {"scan_seconds": how.get("seconds", 0.0), "cached": how.get("cached", False)}


def symbols(repo_root: str = ".", search: str = "", kind: str = "", limit: int = 25,
            cursor: str = "") -> dict:
    """Find a symbol by substring. The cheap way to turn a name into an id."""
    graph, cost = _scan(repo_root)
    needle = search.lower()
    rows = [_row(s) for s in graph.symbols
            if (not needle or needle in s.id.lower() or needle in (s.label or "").lower())
            and (not kind or s.kind == kind)]
    rows.sort(key=lambda row: (row["kind"], row["id"]))
    shown, cut = envelope.page(rows, limit, cursor)
    return envelope.answer("symbols", {"symbols": shown}, repo=repo_root,
                           truncated=cut, cost=cost)


def near(repo_root: str = ".", symbol_id: str = "", limit: int = 5) -> list[str]:
    """Ids close to one that does not exist, for the hint on a miss."""
    graph, _ = _scan(repo_root)
    return difflib.get_close_matches(symbol_id, [s.id for s in graph.symbols],
                                     n=limit, cutoff=0.5)
```

- [ ] **Step 4: Tests pass**

Run: `python -m pytest seamcheck/tests/test_queries.py -v`
Expected: 4 passed.

- [ ] **Step 5: Wire the command into both parsers**

```python
# seamcheck/cli.py, in COMMANDS after "explain"
    "symbols": Command(
        args=["--symbols"],
        summary="Find a symbol by name. The cheap way to get an id.",
        detail=(
            "Every other command takes a symbol id, and getting one wrong used to cost a "
            "full scan to be told so - 88 seconds on a 500k-line project, to read `No "
            "symbol with id ...`. This answers from the cached scan in a fraction of a "
            "second, and it is the right first call before explain or triage."
        ),
        examples=[
            ("seamcheck symbols --search push", "everything whose id or label says push"),
            ("seamcheck symbols --search push --kind url", "...routes only"),
        ],
    ),
```

```python
# seamcheck/management/commands/seamcheck.py, in add_arguments
        parser.add_argument(
            "--symbols", action="store_true",
            help="Find symbols by name; --search and --kind narrow it.",
        )
        parser.add_argument("--search", default="", help="Substring to look for.")
        parser.add_argument("--kind", default="", help="Restrict to one kind.")
        parser.add_argument("--limit", type=int, default=25, help="How many rows at most.")
        parser.add_argument("--cursor", default="", help="Continue a previous page.")
```

```python
# seamcheck/management/commands/seamcheck.py, in handle() before show_config
        if options.get("symbols"):
            from seamcheck import queries

            out = queries.symbols(options["repo_root"], options["search"],
                                  options["kind"], options["limit"], options["cursor"])
            return self.stdout.write(json.dumps(out, indent=2))
```

```python
# seamcheck/cli.py, in _plain_args' option dict and loop
        "symbols": False, "search": "", "kind": "", "limit": 25, "cursor": "",
```
```python
        elif item == "--symbols":
            options["symbols"] = True
        elif item == "--search" and following:
            options["search"] = following
        elif item == "--kind" and following:
            options["kind"] = following
        elif item == "--limit" and following and following.isdigit():
            options["limit"] = int(following)
        elif item == "--cursor" and following:
            options["cursor"] = following
```
```python
# seamcheck/cli.py, in _run_without_django before show_config
    if options["symbols"]:
        from seamcheck import queries

        print(json.dumps(queries.symbols(root, options["search"], options["kind"],
                                         options["limit"], options["cursor"]), indent=2))
        return 0
```

- [ ] **Step 6: Prove it on a real project, warm**

Run:
```bash
cd ~/dev/pointlessbutton && time python -m seamcheck.cli symbols --search submit_push --limit 5
```
Expected: JSON with `view:submit_push` among the rows, and, with the cache warm from Task 4,
under five seconds. Record the time in the commit message.

- [ ] **Step 7: Commit**

```bash
git add seamcheck/queries.py seamcheck/tests/test_queries.py seamcheck/cli.py seamcheck/management/commands/seamcheck.py
git commit -m "feat(cli): symbols - find an id without paying for a scan to be told it is wrong"
```

---

### Task 6: `findings`, the workhorse

**Files:**
- Modify: `seamcheck/queries.py`
- Modify: `seamcheck/cli.py`, `seamcheck/management/commands/seamcheck.py`
- Test: `seamcheck/tests/test_queries.py`

**Interfaces:**
- Consumes: `_row`, `_scan`, `envelope.page` from Task 5.
- Produces: `findings(repo_root, file="", kind="", status="", owner="", limit=25, cursor="") -> dict`
  with `data = {"findings": [...], "by_kind": {...}, "by_status": {...}}`.

- [ ] **Step 1: Write the failing test**

```python
# seamcheck/tests/test_queries.py, appended
class FindingsTests(SimpleTestCase):
    def setUp(self):
        patch = mock.patch("seamcheck.scancache.cached_scan",
                           return_value=(GRAPH, {"cached": True, "seconds": 0.0}))
        patch.start()
        self.addCleanup(patch.stop)

    def test_only_findings_are_returned_not_the_graph(self):
        out = queries.findings(".")

        statuses = {row["status"] for row in out["data"]["findings"]}
        self.assertEqual(statuses, {"unresolved", "unused"},
                         "connected symbols are not findings")

    def test_a_file_filter_answers_what_is_wrong_in_this_file(self):
        out = queries.findings(".", file="app/cache.py")

        self.assertEqual([row["id"] for row in out["data"]["findings"]],
                         ["redis_key:user:*:pushes"])

    def test_the_census_tells_an_agent_the_vocabulary(self):
        out = queries.findings(".")

        self.assertEqual(out["data"]["by_status"], {"unresolved": 1, "unused": 1})
        self.assertIn("redis_key", out["data"]["by_kind"])

    def test_a_status_outside_the_vocabulary_is_a_coded_failure(self):
        out = queries.findings(".", status="wobbly")

        self.assertFalse(out["ok"])
        self.assertEqual(out["error"]["code"], "bad_argument")
        self.assertIn("unresolved", out["error"]["hint"])
```

- [ ] **Step 2: Run it and watch it fail**

Run: `python -m pytest seamcheck/tests/test_queries.py::FindingsTests -v`
Expected: 4 failures, `module 'seamcheck.queries' has no attribute 'findings'`.

- [ ] **Step 3: Implement**

```python
# seamcheck/queries.py, appended
# What counts as a finding. `uncertain` is deliberately not here: it is the tool saying it
# could not tell, and reporting it as a finding is how a guess gets laundered into a fact.
FINDING_STATUSES = ("unresolved", "unused")
ALL_STATUSES = ("unresolved", "unused", "uncertain", "connected")


def findings(repo_root: str = ".", file: str = "", kind: str = "", status: str = "",
             owner: str = "", limit: int = 25, cursor: str = "") -> dict:
    """What is wrong, narrowed by file, kind, status or owning function."""
    if status and status not in ALL_STATUSES:
        return envelope.failure(
            "findings", "bad_argument", f"Unknown status {status!r}.",
            hint="One of: " + ", ".join(ALL_STATUSES))
    graph, cost = _scan(repo_root)
    wanted = (status,) if status else FINDING_STATUSES
    rows = [_row(s) for s in graph.symbols
            if s.status.value in wanted
            and (not file or s.file == file)
            and (not kind or s.kind == kind)
            and (not owner or (s.owner or "") == owner)]
    rows.sort(key=lambda row: (row["status"], row["kind"], row["file"], row["line"] or 0))
    by_kind: dict[str, int] = {}
    by_status: dict[str, int] = {}
    for row in rows:
        by_kind[row["kind"]] = by_kind.get(row["kind"], 0) + 1
        by_status[row["status"]] = by_status.get(row["status"], 0) + 1
    shown, cut = envelope.page(rows, limit, cursor)
    return envelope.answer("findings",
                           {"findings": shown, "by_kind": by_kind, "by_status": by_status},
                           repo=repo_root, truncated=cut, cost=cost)
```

- [ ] **Step 4: Tests pass**

Run: `python -m pytest seamcheck/tests/test_queries.py -v`
Expected: 8 passed.

- [ ] **Step 5: Wire the command into all three places**

```python
# seamcheck/cli.py, in COMMANDS after "symbols"
    "findings": Command(
        args=["--findings"],
        summary="What is wrong, filtered and bounded. Start here.",
        detail=(
            "The whole graph is 72 MB on a 500k-line project and answers no question by "
            "itself. This answers the one an agent actually has - what is wrong, and "
            "where - narrowed by file, kind, status or owning function, and it says what "
            "it left out so nothing looks complete when it is not."
        ),
        examples=[
            ("seamcheck findings --file app/views.py", "what is wrong in the file I am editing"),
            ("seamcheck findings --kind redis_key --limit 50", "one kind, fifty rows"),
            ("seamcheck findings --cursor 50", "the next page"),
        ],
    ),
```

```python
# seamcheck/management/commands/seamcheck.py, in add_arguments
        parser.add_argument(
            "--findings", action="store_true",
            help="List findings; --file, --kind, --status and --owner narrow it.",
        )
        parser.add_argument("--file", default="", help="Only findings in this file.")
        parser.add_argument("--owner", default="", help="Only findings owned by this function.")
```

```python
# seamcheck/management/commands/seamcheck.py, in handle() beside the symbols branch
        if options.get("findings"):
            from seamcheck import queries

            out = queries.findings(options["repo_root"], options["file"], options["kind"],
                                   options["status"] or "", options["owner"],
                                   options["limit"], options["cursor"])
            self.stdout.write(json.dumps(out, indent=2))
            return None
```

```python
# seamcheck/cli.py, in _plain_args' option dict
        "findings": False, "file": "", "owner": "",
```
```python
# seamcheck/cli.py, in the _plain_args loop
        elif item == "--findings":
            options["findings"] = True
        elif item == "--file" and following:
            options["file"] = following
        elif item == "--owner" and following:
            options["owner"] = following
```
```python
# seamcheck/cli.py, in _run_without_django beside the symbols branch
    if options["findings"]:
        from seamcheck import queries

        print(json.dumps(queries.findings(root, options["file"], options["kind"],
                                          options["status"] or "", options["owner"],
                                          options["limit"], options["cursor"]), indent=2))
        return 0
```

- [ ] **Step 6: Measure the token cost against the old way**

Run:
```bash
cd ~/dev/pointlessbutton
python -m seamcheck.cli findings --limit 25 | wc -c
python -m seamcheck.cli json | wc -c
```
Expected: the first is a few thousand bytes, the second 72,632,414. Put both in the commit
message; that ratio is the entire justification for this task.

- [ ] **Step 7: Commit**

```bash
git add seamcheck/queries.py seamcheck/tests/test_queries.py seamcheck/cli.py seamcheck/management/commands/seamcheck.py
git commit -m "feat(cli): findings - the question an agent asks, in a few thousand bytes instead of 72 MB"
```

---

### Task 7: `diff`, and `json` behind a gate

**Files:**
- Modify: `seamcheck/queries.py`, `seamcheck/cli.py`,
  `seamcheck/management/commands/seamcheck.py:465-470`
- Test: `seamcheck/tests/test_queries.py`

**Interfaces:**
- Consumes: `api.diff_against(ref, repo_root)` (`api.py:377`), `snapshot.load_snapshot`.
- Produces: `diff(repo_root, since="HEAD~1", limit=25, cursor="") -> dict` with
  `data = {"appeared": [...], "vanished": [...], "changed": [...], "baseline": "<sha>"}`.

- [ ] **Step 1: Write the failing test**

```python
# seamcheck/tests/test_queries.py, appended
class DiffTests(SimpleTestCase):
    """"What did this commit break" is the question CI and an agent both ask, and there was
    no command that answered it: only `check --since`, which mixes the answer into a gate."""

    def test_it_names_what_appeared_and_what_went(self):
        before = Graph(symbols=[_symbol("url", "api/old/", "app/urls.py")], edges=[])
        after = Graph(symbols=[_symbol("url", "api/new/", "app/urls.py")], edges=[])
        with mock.patch("seamcheck.scancache.cached_scan",
                        return_value=(after, {"cached": True, "seconds": 0.0})), \
             mock.patch("seamcheck.snapshot.load_snapshot", return_value=before), \
             mock.patch("seamcheck.queries._resolve", return_value="abc123"):
            out = queries.diff(".", since="main")

        self.assertEqual([row["id"] for row in out["data"]["appeared"]], ["url:api/new/"])
        self.assertEqual([row["id"] for row in out["data"]["vanished"]], ["url:api/old/"])
        self.assertEqual(out["data"]["baseline"], "abc123")

    def test_no_baseline_is_a_coded_failure_not_a_crash(self):
        with mock.patch("seamcheck.scancache.cached_scan",
                        return_value=(GRAPH, {"cached": True, "seconds": 0.0})), \
             mock.patch("seamcheck.snapshot.load_snapshot", return_value=None), \
             mock.patch("seamcheck.queries._resolve", return_value="abc123"):
            out = queries.diff(".", since="main")

        self.assertFalse(out["ok"])
        self.assertEqual(out["error"]["code"], "no_baseline")
        self.assertIn("seamcheck scan", out["error"]["hint"])
```

- [ ] **Step 2: Run it and watch it fail**

Run: `python -m pytest seamcheck/tests/test_queries.py::DiffTests -v`
Expected: 2 failures, no attribute `diff`.

- [ ] **Step 3: Implement**

```python
# seamcheck/queries.py, appended
import subprocess


def _resolve(repo_root: str, ref: str) -> str:
    """The sha a ref points at, or "" when git cannot say."""
    try:
        done = subprocess.run(["git", "-C", repo_root, "rev-parse", ref],
                              capture_output=True, text=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        return ""
    return done.stdout.strip()


def diff(repo_root: str = ".", since: str = "HEAD~1", limit: int = 25,
         cursor: str = "") -> dict:
    """What appeared, what vanished and what changed status since a ref."""
    from seamcheck import snapshot

    sha = _resolve(repo_root, since)
    if not sha:
        return envelope.failure("diff", "no_git", f"Could not resolve {since!r}.",
                                hint="Pass a ref this repository has, e.g. --since origin/main.")
    before = snapshot.load_snapshot(sha, repo_root)
    if before is None:
        return envelope.failure(
            "diff", "no_baseline", f"No snapshot for {sha[:12]}.",
            hint=f"Run `seamcheck scan` at {since} once, or `seamcheck backfill 20`.")
    graph, cost = _scan(repo_root)
    was = {s.id: s for s in before.symbols}
    now = {s.id: s for s in graph.symbols}
    appeared = [_row(now[i]) for i in now.keys() - was.keys()]
    vanished = [_row(was[i]) for i in was.keys() - now.keys()]
    changed = [dict(_row(now[i]), was=was[i].status.value)
               for i in now.keys() & was.keys()
               if now[i].status.value != was[i].status.value]
    for rows in (appeared, vanished, changed):
        rows.sort(key=lambda row: (row["kind"], row["id"]))
    shown, cut = envelope.page(appeared + vanished + changed, limit, cursor)
    ids = {row["id"] for row in shown}
    return envelope.answer(
        "diff",
        {"baseline": sha, "since": since,
         "appeared": [r for r in appeared if r["id"] in ids],
         "vanished": [r for r in vanished if r["id"] in ids],
         "changed": [r for r in changed if r["id"] in ids]},
        repo=repo_root, truncated=cut, cost=cost)
```

- [ ] **Step 4: Tests pass**

Run: `python -m pytest seamcheck/tests/test_queries.py -v`
Expected: 10 passed.

- [ ] **Step 5: Put `json` behind a gate**

```python
# seamcheck/management/commands/seamcheck.py, replacing the --json branch at :465-470
        if fmt == "json":
            from seamcheck import envelope

            text = json.dumps(graph_to_dict(graph))
            if len(text) > _JSON_WARN and not options.get("full"):
                self.stderr.write(
                    f"  The whole graph is {len(text) / 1e6:.1f} MB "
                    f"(~{len(text) // 4:,} tokens). Refusing to print it.\n"
                    "  `seamcheck findings` answers most questions in a few KB.\n"
                    "  `--full --yes` prints it anyway; `--out FILE` writes it to disk.")
                raise SystemExit(EXIT_USAGE)
            return self.stdout.write(text)
```
with `_JSON_WARN = 2_000_000` defined beside the other module constants and a comment
recording that the reference project's graph is 72.6 MB, about 18 million tokens.

- [ ] **Step 6: Prove the gate**

Run: `cd ~/dev/pointlessbutton && python -m seamcheck.cli json; echo "EXIT $?"`
Expected: the refusal on stderr, nothing on stdout, `EXIT 3`.

- [ ] **Step 7: Make machine output byte-identical between runs**

Two runs of a read command must produce the same bytes, or a caller cannot diff them and a
cache cannot key on them. Today the terminal and HTML renderers stamp `generated_at`
(`renderers/terminal.py:40`).

```python
# seamcheck/tests/test_queries.py, appended
class DeterminismTests(SimpleTestCase):
    def test_two_identical_runs_of_a_machine_command_agree(self):
        with mock.patch("seamcheck.scancache.cached_scan",
                        return_value=(GRAPH, {"cached": True, "seconds": 0.0})):
            first = json.dumps(queries.findings("."))
            second = json.dumps(queries.findings("."))

        self.assertEqual(first, second, "a machine answer may not carry a clock")
```

The envelope from Task 3 has no timestamp field, so this passes for the new commands by
construction; the test exists to keep it that way. Add `import json` to the test module's
imports.

- [ ] **Step 8: Commit**

```bash
git add seamcheck/queries.py seamcheck/tests/test_queries.py seamcheck/management/commands/seamcheck.py
git commit -m "feat(cli): diff since a ref, and json refuses to print 18 million tokens by accident"
```

---

### Task 8: CI formats and the pipeline an agent can copy

**Files:**
- Create: `seamcheck/renderers/sarif.py`, `docs/ci.md`
- Modify: `seamcheck/management/commands/seamcheck.py` (`--format sarif|github`)
- Test: `seamcheck/tests/test_sarif.py`

**Interfaces:**
- Consumes: `queries.findings(...)["data"]["findings"]`.
- Produces: `sarif.render(findings: list[dict], *, sha: str, repo: str) -> str`,
  `github.render(findings: list[dict]) -> str`.

- [ ] **Step 1: Write the failing test**

```python
# seamcheck/tests/test_sarif.py
"""GitHub reads SARIF, and an agent writing a pipeline knows that.

Without it the only way to surface a finding in a pull request is to paste text into a
comment, so the tool's output lives outside the review rather than on the line it is about.
"""
import json

from django.test import SimpleTestCase

from seamcheck.renderers import sarif

FINDINGS = [{"id": "url:api/x/", "kind": "url", "label": "api/x/", "status": "unresolved",
             "file": "app/urls.py", "line": 12, "owner": "", "note": "nothing calls it"}]


class SarifTests(SimpleTestCase):
    def test_it_is_valid_sarif_with_one_result_per_finding(self):
        out = json.loads(sarif.render(FINDINGS, sha="abc123", repo="."))

        self.assertEqual(out["version"], "2.1.0")
        run = out["runs"][0]
        self.assertEqual(run["tool"]["driver"]["name"], "seamcheck")
        self.assertEqual(len(run["results"]), 1)

    def test_a_result_points_at_the_exact_line(self):
        run = json.loads(sarif.render(FINDINGS, sha="abc123", repo="."))["runs"][0]
        location = run["results"][0]["locations"][0]["physicalLocation"]

        self.assertEqual(location["artifactLocation"]["uri"], "app/urls.py")
        self.assertEqual(location["region"]["startLine"], 12)

    def test_the_rule_id_is_the_kind_so_findings_group_in_the_ui(self):
        run = json.loads(sarif.render(FINDINGS, sha="abc123", repo="."))["runs"][0]

        self.assertEqual(run["results"][0]["ruleId"], "seamcheck/url/unresolved")
        self.assertIn("seamcheck/url/unresolved",
                      [rule["id"] for rule in run["tool"]["driver"]["rules"]])
```

- [ ] **Step 2: Run it and watch it fail**

Run: `python -m pytest seamcheck/tests/test_sarif.py -v`
Expected: 3 errors, no module `seamcheck.renderers.sarif`.

- [ ] **Step 3: Implement both renderers**

```python
# seamcheck/renderers/sarif.py
"""Findings as SARIF 2.1.0, so GitHub puts them on the line they are about.

A pull request is where a finding is cheapest to act on, and the only way into that view is
this format. Everything else this tool prints is for a terminal or a browser; this one is
for the review.
"""
from __future__ import annotations

import json

# unresolved is an error: something reaches for what is not there. unused is a warning: it
# may be reached from outside the repository, and the tool says so rather than guessing.
_LEVEL = {"unresolved": "error", "unused": "warning", "uncertain": "note"}


def render(findings: list[dict], *, sha: str = "", repo: str = ".") -> str:
    rules: dict[str, dict] = {}
    results = []
    for finding in findings:
        rule_id = f"seamcheck/{finding['kind']}/{finding['status']}"
        rules.setdefault(rule_id, {
            "id": rule_id,
            "shortDescription": {"text": f"{finding['kind']} {finding['status']}"},
            "defaultConfiguration": {"level": _LEVEL.get(finding["status"], "note")},
        })
        results.append({
            "ruleId": rule_id,
            "level": _LEVEL.get(finding["status"], "note"),
            "message": {"text": finding.get("note") or f"{finding['label']} is {finding['status']}."},
            "locations": [{"physicalLocation": {
                "artifactLocation": {"uri": finding["file"]},
                "region": {"startLine": finding.get("line") or 1},
            }}],
            "partialFingerprints": {"seamcheckId": finding["id"]},
        })
    return json.dumps({
        "version": "2.1.0",
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "runs": [{
            "tool": {"driver": {"name": "seamcheck",
                                "informationUri": "https://github.com/dardameiz/seamcheck",
                                "rules": list(rules.values())}},
            "versionControlProvenance": [{"revisionId": sha}] if sha else [],
            "results": results,
        }],
    }, indent=2)
```

```python
# seamcheck/renderers/github.py
"""Findings as workflow commands, for a job that has no SARIF upload.

One line each, on stdout, in the format Actions turns into an annotation on the diff.
"""
from __future__ import annotations

_KIND = {"unresolved": "error", "unused": "warning", "uncertain": "notice"}


def render(findings: list[dict]) -> str:
    lines = []
    for finding in findings:
        level = _KIND.get(finding["status"], "notice")
        message = (finding.get("note") or f"{finding['label']} is {finding['status']}").replace("\n", " ")
        lines.append(f"::{level} file={finding['file']},line={finding.get('line') or 1}"
                     f",title=seamcheck {finding['kind']}::{message}")
    return "\n".join(lines)
```

- [ ] **Step 4: Tests pass**

Run: `python -m pytest seamcheck/tests/test_sarif.py -v`
Expected: 3 passed.

- [ ] **Step 5: Wire the formats into `check`**

```python
# seamcheck/management/commands/seamcheck.py, in _format_report before the api.report call
        if fmt in ("sarif", "github"):
            from seamcheck import queries
            from seamcheck.renderers import github as github_renderer
            from seamcheck.renderers import sarif as sarif_renderer

            # A high limit rather than none: a pull request that would annotate 10,000
            # lines is telling you something other than what any one line says.
            rows = queries.findings(repo_root, limit=10_000)["data"]["findings"]
            sha = api.current_sha(repo_root) if hasattr(api, "current_sha") else ""
            text = (sarif_renderer.render(rows, sha=sha, repo=repo_root)
                    if fmt == "sarif" else github_renderer.render(rows))
            if options.get("out"):
                pathlib.Path(options["out"]).write_text(text, encoding="utf-8")
                self.stderr.write(f"  wrote  {options['out']}")
            else:
                self.stdout.write(text)
            return None
```

and extend the `--format` help string at `commands/seamcheck.py:82-90` to read
`"terminal, markdown, html, json, map, console, sarif, github"`.

- [ ] **Step 6: Write the pipeline document**

Create `docs/ci.md` containing this workflow verbatim, plus a paragraph per trap named in it:

```yaml
name: seamcheck
on: pull_request

jobs:
  seams:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      security-events: write        # required to upload SARIF
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0            # seamcheck --since needs the merge base

      - uses: actions/setup-python@v5
        with: {python-version: "3.12"}

      # Seamcheck parses JavaScript with node. Without this step the JS half of the
      # graph is silently missing and every fetch target reads as unresolved.
      - uses: actions/setup-node@v4
        with: {node-version: "20"}

      - run: pip install "seamcheck==0.11.0"

      # A GitHub runner has 16 GB and shares it with your build. The AST cache defaults
      # to a quarter of PHYSICAL memory, which is more than a CI job should take.
      - run: echo "SEAMCHECK_AST_CACHE_MB=512" >> $GITHUB_ENV

      # The baseline. Without it `check` cannot tell a regression from a first run and
      # exits 2, which this job treats as "nothing to compare yet" rather than failure.
      - uses: actions/cache@v4
        with:
          path: .seamcheck
          key: seamcheck-${{ github.event.pull_request.base.sha }}
          restore-keys: seamcheck-

      - name: Gate
        run: |
          seamcheck check --since ${{ github.event.pull_request.base.sha }} \
                          --format sarif --out seamcheck.sarif
          code=$?
          if [ $code -eq 2 ]; then echo "no baseline yet"; exit 0; fi
          exit $code

      - uses: github/codeql-action/upload-sarif@v3
        if: always()
        with: {sarif_file: seamcheck.sarif}
```

- [ ] **Step 7: Commit**

```bash
git add seamcheck/renderers/sarif.py seamcheck/renderers/github.py seamcheck/tests/test_sarif.py seamcheck/management/commands/seamcheck.py docs/ci.md
git commit -m "feat(ci): SARIF and workflow annotations, and a pipeline that names its own traps"
```

---

### Task 9: The MCP server says what it is for

**Files:**
- Modify: `seamcheck/mcp_server.py:46`, and every `@mcp.tool()` signature
- Test: `seamcheck/tests/test_mcp_protocol.py`

**Interfaces:**
- Consumes: `queries.symbols/findings/diff` (Tasks 5-7), `envelope` (Task 3).
- Produces: four new tools `seamcheck_findings`, `seamcheck_symbols`, `seamcheck_diff`,
  `seamcheck_snapshot`; enums and `outputSchema` on the existing eight.

- [ ] **Step 1: Write the failing test**

```python
# seamcheck/tests/test_mcp_protocol.py
"""Through the protocol, not around it.

Every existing MCP test calls the decorated functions as plain Python, so nothing exercises
the layer a client actually talks to: not the schemas, not isError, not structuredContent.
"""
import asyncio

from django.test import SimpleTestCase

from seamcheck import mcp_server


def _tools():
    return asyncio.run(mcp_server.mcp.list_tools())


class ProtocolTests(SimpleTestCase):
    def test_the_server_tells_a_client_what_the_loop_is(self):
        # The docstring that explains when to use what was never sent to anyone.
        instructions = mcp_server.mcp._mcp_server.instructions or ""

        self.assertIn("unverified", instructions)
        self.assertIn("evidence", instructions)

    def test_the_closed_vocabularies_are_enums(self):
        by_name = {tool.name: tool for tool in _tools()}

        triage = by_name["seamcheck_triage"].inputSchema["properties"]
        self.assertIn("enum", triage["status"])
        self.assertIn("approved", triage["status"]["enum"])

    def test_report_cannot_be_asked_for_the_whole_graph(self):
        # fmt="json" returned 72 MB and fmt="map" 8.6 MB, neither documented.
        fmt = {t.name: t for t in _tools()}["seamcheck_report"].inputSchema["properties"]["fmt"]

        self.assertEqual(set(fmt["enum"]), {"terminal", "markdown", "html"})

    def test_the_reading_tools_say_they_only_read(self):
        by_name = {tool.name: tool for tool in _tools()}

        self.assertTrue(by_name["seamcheck_findings"].annotations.readOnlyHint)
        self.assertFalse(by_name["seamcheck_triage"].annotations.readOnlyHint)

    def test_the_agent_entry_points_exist(self):
        names = {tool.name for tool in _tools()}

        self.assertLessEqual({"seamcheck_findings", "seamcheck_symbols", "seamcheck_diff",
                              "seamcheck_snapshot"}, names)
```

- [ ] **Step 2: Run it and watch it fail**

Run: `python -m pytest seamcheck/tests/test_mcp_protocol.py -v`
Expected: 5 failures.

- [ ] **Step 3: Send the instructions**

```python
# seamcheck/mcp_server.py, replacing line 46
# The loop at the top of this file is the whole point, and until now no client ever saw it:
# FastMCP only transmits what is passed here.
mcp = _Server("seamcheck", instructions=__doc__)
```

- [ ] **Step 4: Add the enums, the schemas and the annotations**

Give every tool a `Literal` type for its closed vocabularies and a `TypedDict` return so
FastMCP emits an `outputSchema`:

```python
from typing import Literal, TypedDict

Status = Literal["approved", "confirmed", "deferred", "untriaged"]
Why = Literal["framework-implicit", "built-at-runtime", "consumed-by-dependency",
              "genuinely-dead", "other"]        # keep in step with triage.WHY_HELP
Fmt = Literal["terminal", "markdown", "html"]   # NOT json or map: 72 MB and 8.6 MB


class Finding(TypedDict):
    id: str
    kind: str
    label: str
    status: str
    file: str
    line: int | None
    owner: str
    note: str
```

and mark the readers:

```python
@mcp.tool(annotations={"readOnlyHint": True, "destructiveHint": False})
```
on all but `seamcheck_triage`, which takes `{"readOnlyHint": False}`.

- [ ] **Step 5: Add the four tools**

```python
@mcp.tool(annotations={"readOnlyHint": True, "destructiveHint": False})
def seamcheck_findings(repo_root: str = ".", file: str = "", kind: str = "",
                       status: str = "", limit: int = 25, cursor: str = "") -> dict:
    """What is wrong, filtered and bounded. START HERE.

    `file` answers "what is wrong in the file I am editing". The reply carries by_kind and
    by_status, so the vocabulary for the next call comes from this one. Costs one cached
    scan; asking again is free until the files change.
    """
    from seamcheck import queries

    return queries.findings(repo_root, file, kind, status, limit=limit, cursor=cursor)


@mcp.tool(annotations={"readOnlyHint": True, "destructiveHint": False})
def seamcheck_symbols(repo_root: str = ".", search: str = "", kind: str = "",
                      limit: int = 25) -> dict:
    """Find a symbol id by name, before spending a call on explain or triage."""
    from seamcheck import queries

    return queries.symbols(repo_root, search, kind, limit)


@mcp.tool(annotations={"readOnlyHint": True, "destructiveHint": False})
def seamcheck_diff(repo_root: str = ".", since: str = "HEAD~1", limit: int = 25) -> dict:
    """What appeared, vanished or changed status since a ref. The "what did I break" call."""
    from seamcheck import queries

    return queries.diff(repo_root, since, limit)


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
def seamcheck_snapshot(repo_root: str = ".") -> dict:
    """Record the current graph as the baseline that check and diff compare against.

    Without this an agent could never make a baseline: only the CLI wrote one, so
    seamcheck_check answered "no baseline" forever until a human ran a terminal command.
    """
    from seamcheck import api, envelope, snapshot
    from seamcheck.scancache import cached_scan

    graph, how = cached_scan(repo_root)
    sha = snapshot.current_git_sha(repo_root)
    path = snapshot.save_snapshot(graph, sha, repo_root)
    return envelope.answer("snapshot", {"sha": sha, "path": path,
                                        "symbols": len(graph.symbols)},
                           repo=repo_root, cost={"cached": how["cached"]})
```

- [ ] **Step 6: Tests pass**

Run: `python -m pytest seamcheck/tests/test_mcp_protocol.py seamcheck/tests/test_mcp_server.py -v`
Expected: all pass, including the existing name-set test once the four names are added to it.

- [ ] **Step 7: Commit**

```bash
git add seamcheck/mcp_server.py seamcheck/tests/test_mcp_protocol.py seamcheck/tests/test_mcp_server.py
git commit -m "feat(mcp): instructions, enums, schemas, and the four tools an agent was missing"
```

---

### Task 10: Close the two-parser gap

**Files:**
- Modify: `seamcheck/cli.py:700-762` (`_plain_args`)
- Test: `seamcheck/tests/test_cli_entrypoint.py`

**Interfaces:**
- Consumes: `exitcodes.EXIT_USAGE` (Task 2).
- Produces: `_plain_args` returns `(options, unknown: list[str])`.

- [ ] **Step 1: Write the failing test**

```python
# seamcheck/tests/test_cli_entrypoint.py, appended
class UnknownFlagTests(SimpleTestCase):
    """A flag that works on Django and is ignored on Express is worse than one that does not
    exist: `check --since $BASE` read as working and compared against nothing."""

    def test_a_flag_the_plain_path_cannot_honour_is_refused(self):
        with mock.patch("seamcheck.cli._worth_scanning", return_value=True):
            code = cli._run_without_django(["--since", "main", "--check"], verbose=False)

        self.assertEqual(code, 3, "refuse it rather than silently ignoring it")
```

- [ ] **Step 2: Run it and watch it fail**

Run: `python -m pytest seamcheck/tests/test_cli_entrypoint.py::UnknownFlagTests -v`
Expected: FAIL — today the flag is dropped and the run exits 0 or 1.

- [ ] **Step 3: Collect and refuse**

Make `_plain_args` append anything beginning with `--` that it did not consume to an
`unknown` list, and in `_run_without_django`:

```python
    options, unknown = _plain_args(arguments)
    if unknown:
        # Silently ignoring a flag is how `--since` came to read as working on every
        # non-Django project while comparing against nothing.
        print(f"seamcheck: {', '.join(unknown)} is not supported on this project type "
              "(no Django settings module found).", file=sys.stderr)
        return EXIT_USAGE
```

- [ ] **Step 4: Test passes, and the suite still does**

Run: `python -m pytest seamcheck/tests/test_cli_entrypoint.py seamcheck/tests/test_cli.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add seamcheck/cli.py seamcheck/tests/test_cli_entrypoint.py
git commit -m "fix(cli): a flag the plain path cannot honour is refused, not ignored"
```

---

### Task 11: Agents first, in the documents

**Files:**
- Modify: `README.md`, `llms.txt`, `docs/agents.md`, `docs/commands.md`
- Create: nothing
- Test: `seamcheck/tests/test_docs_promises.py`

**Interfaces:**
- Consumes: the command list from `cli.COMMANDS`, the tool list from `mcp.list_tools()`.

- [ ] **Step 1: Write the failing test**

```python
# seamcheck/tests/test_docs_promises.py
"""The documents promise things the code has to keep.

`docs/agents.md` listed seven tools and omitted seamcheck_unverified - the one designed to
be an agent's first call - and three files promised an exit code the code did not produce.
"""
import asyncio
import pathlib

from django.test import SimpleTestCase

from seamcheck import cli, mcp_server

ROOT = pathlib.Path(__file__).resolve().parents[2]


class DocsTests(SimpleTestCase):
    def test_every_mcp_tool_is_documented(self):
        names = {tool.name for tool in asyncio.run(mcp_server.mcp.list_tools())}
        text = (ROOT / "docs/agents.md").read_text()

        missing = sorted(name for name in names if name not in text)
        self.assertEqual(missing, [], f"undocumented tools: {missing}")

    def test_every_command_is_documented(self):
        text = (ROOT / "docs/commands.md").read_text()

        missing = sorted(name for name in cli.COMMANDS if name not in text)
        self.assertEqual(missing, [], f"undocumented commands: {missing}")

    def test_the_readme_puts_agents_before_the_footer(self):
        text = (ROOT / "README.md").read_text()

        self.assertIn("## For agents", text)
        self.assertLess(text.index("## For agents"), len(text) // 2,
                        "an agent reading the top of the README must find this")
```

- [ ] **Step 2: Run it and watch it fail**

Run: `python -m pytest seamcheck/tests/test_docs_promises.py -v`
Expected: 3 failures.

- [ ] **Step 3: Write the README section**

Insert after the opening pitch, before "Why I made it":

```markdown
## For agents

This tool is built for you as much as for a person. Every question has a bounded,
machine-readable answer, and none of them cost a model call.

```bash
seamcheck findings --file app/views.py     # what is wrong here            ~2 KB
seamcheck symbols --search submit_push     # name -> id                    ~1 KB
seamcheck diff --since origin/main         # what this branch changed      ~3 KB
seamcheck check --since origin/main        # the gate: 0 clean, 1 findings, 2 no baseline
```

Every command takes `--limit` and `--cursor`, prints one JSON envelope on stdout and
nothing else, and puts prose on stderr. Errors carry a code you can branch on. The whole
graph is available with `--full --yes`, and it is 72 MB on a 500k-line project, so prefer
the questions above.

There is an MCP server with the same functions behind it:
`seamcheck_findings`, `seamcheck_symbols`, `seamcheck_diff`, `seamcheck_explain`,
`seamcheck_check`, `seamcheck_snapshot`, `seamcheck_triage`, `seamcheck_unverified`,
`seamcheck_why_wrong`, `seamcheck_report`, `seamcheck_share`, `seamcheck_services`.
One scan is cached and reused, so the second question is nearly free.

[The pipeline recipe](docs/ci.md) · [Using it from an agent](docs/agents.md)
```

- [ ] **Step 4: Update the other three**

Add the four new tools to `docs/agents.md`, the four new commands to `docs/commands.md` with
their exit codes, and replace the exit-code line in `llms.txt` with the table from
`seamcheck/exitcodes.py`.

- [ ] **Step 5: Tests pass**

Run: `python -m pytest seamcheck/tests/test_docs_promises.py -v`
Expected: 3 passed.

- [ ] **Step 6: Commit**

```bash
git add README.md llms.txt docs/agents.md docs/commands.md seamcheck/tests/test_docs_promises.py
git commit -m "docs: agents first, and a test that the documents keep their promises"
```

---

## Self-review of this plan

**Spec coverage.** Review §1.1 → Task 1. §1.2 → Task 2. §2 (cost) → Tasks 4, 6, 7.
§3 flags → Task 10; machine form → Tasks 3, 5, 6, 7; exit codes → Task 2; side effects →
Task 7's `--write-graph`; determinism → Task 7 step 7, added during this review because it
had no task. §4 MCP → Task 9. §5 properties → Tasks 3-9. §6 order → the phases.

**`map` never returning is deliberately not fixed.** `--no-serve` exists, and changing the
default would break every person who types `seamcheck map` today. The README section in
Task 11 tells an agent to pass the flag, which is the cheaper half of the problem.

**Placeholders.** Tasks 6 and 8 originally said "the same shape Task 5 used" for wiring a
command into three files. An implementer reading one task must not have to read another, so
both were expanded into full code blocks during this review.

**Type consistency.** `_row`, `_scan`, `envelope.page`, `envelope.answer`, `envelope.failure`
and `scancache.cached_scan` keep the same signatures across Tasks 3-9. `gate_code` is used by
both engines. `Finding` in Task 9 matches `_row` in Task 5 field for field.
