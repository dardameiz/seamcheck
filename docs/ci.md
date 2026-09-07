# Wiring seamcheck into a pull request

`seamcheck check --format sarif` and `--format github` exist so a finding lands on the exact
line it is about, inside the review, instead of as text someone has to paste into a comment.
GitHub reads SARIF natively (Code Scanning turns each result into an inline annotation and
tracks it as new/existing/fixed across commits); `github` is the fallback for a runner that
never uploads SARIF at all - one `::error file=...,line=...::message` per finding, which
Actions turns into an annotation on the diff on its own.

This is the workflow to copy. It is given whole below, then explained trap by trap - two of
those traps are corrections to a first draft that looked right and silently was not.

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
      # graph is missing, and every fetch target on the JS side reads as unresolved.
      - uses: actions/setup-node@v4
        with: {node-version: "20"}

      - run: pip install "seamcheck==0.11.0"

      # A GitHub runner has 16 GB and shares it with your build. The AST cache defaults
      # to a quarter of PHYSICAL memory, which is more than a CI job should take.
      - run: echo "SEAMCHECK_AST_CACHE_MB=512" >> $GITHUB_ENV

      # The baseline. Without it `check` cannot tell a regression from a first run and
      # exits 2, which this job treats as "nothing to compare yet" rather than failure.
      # This is OTHER/seamcheck/scans, not .seamcheck - see "The cache path" below. This
      # cache only ever has something in it once the companion workflow at the bottom of
      # this doc ("Establishing the baseline") has run on your default branch at least
      # once - copy both files, not just this one, or every PR takes the "no baseline
      # yet" branch forever.
      - uses: actions/cache@v4
        with:
          path: OTHER/seamcheck/scans
          key: seamcheck-${{ github.event.pull_request.base.sha }}
          restore-keys: seamcheck-

      - name: Gate
        run: |
          # See "The exit code" below: default `bash -e` would abort this script on the
          # very first non-zero exit, before `code=$?` ever ran.
          set +e
          seamcheck check --since ${{ github.event.pull_request.base.sha }} \
                          --format sarif --out seamcheck.sarif
          code=$?
          if [ $code -eq 2 ]; then echo "no baseline yet"; exit 0; fi
          exit $code

      - uses: github/codeql-action/upload-sarif@v3
        if: always()
        with: {sarif_file: seamcheck.sarif}
```

A companion job is not optional either - see **Establishing the baseline** at the end. Skip
it and this workflow runs forever without ever having anything to compare against.

## The traps, one at a time

### `fetch-depth: 0`

`actions/checkout@v4` defaults to a shallow clone - one commit deep. `--since REF` resolves
`REF` with `git rev-parse` and needs its history to be present locally to do that; a PR's
base commit is not reachable from a depth-1 clone of the PR branch. Without this, every run
reports "no baseline" for a reason that has nothing to do with whether a baseline exists.

### `actions/setup-node@v4`

Seamcheck ships its JS/CSS parsers as bundled Node scripts and shells out to `node` to run
them. A parser that cannot run is reported once, as a warning on stderr, not swallowed - but
a CI log is easy to skim past, and the practical effect is the same as if it were silent: the
JS half of the graph is simply gone, so every JS-side fetch target and DOM selector reads as
`unresolved`, and the run still exits 0 if nothing else was wrong. Install Node or don't
bother running this at all.

### `pip install "seamcheck==0.11.0"`

Pin an exact version, checked against `pyproject.toml` at the time this doc was written, not
copied from a stale README. Seamcheck's own `seamcheck.cli.version_line()` exists precisely
because an editable install can drift from the pinned version silently; a CI runner doing a
fresh `pip install` every time does not have that failure mode, but an unpinned `seamcheck`
install does - a release with a new format name, a changed exit code, or a stricter check
lands in your gate with no warning and no diff to review.

### `SEAMCHECK_AST_CACHE_MB=512`

With no setting, the parsed-AST cache sizes itself to a quarter of the machine's *physical*
memory (`seamcheck/extractors/js_extractor.py:_ast_budget`) - sane on a laptop, wasteful
next to a build step on a shared 16 GB runner. 512 MB is comfortably more than one repository
of ASTs; the number is a ceiling, not a requirement, so a smaller project pays for less.

### The cache path: `OTHER/seamcheck/scans`, not `.seamcheck`

A baseline snapshot is a JSON file under `OTHER/seamcheck/scans/<sha>.json`
(`seamcheck/snapshot.py:_SCANS_DIR`) - `OTHER/` is the directory this project's own tooling
already treats as gitignored, ad-hoc, and safe to cache. `.seamcheck/` is not where anything
in the current code reads or writes a snapshot; caching it restores nothing and saves
nothing, and the workflow would look correct - the `actions/cache` step runs, logs a hit or a
miss, nothing errors - while quietly never having a baseline to find. Point the cache at the
real path, or every PR gate is a first run forever, always taking the "no baseline yet" exit.

### The exit code: `set +e` before the command

GitHub's `bash` shell runs a multi-line `run:` block with `-e` on by default: the script
aborts at the first command that exits non-zero. `seamcheck check` exiting 2 (no baseline) or
1 (new findings) is exactly such a command, and it is the *first* line of this script - so
`code=$?` never executes, `if [ $code -eq 2 ]` never runs, and the step just fails with
whatever raw exit code `seamcheck` returned. Exit 2 was supposed to be a soft "nothing to
compare yet, don't fail the build"; instead every first PR against a base commit with no
cached snapshot fails outright, for a reason that looks indistinguishable from a real
finding in the Actions UI.

Two ways to fix it, and this workflow takes the first:

- **`set +e` before the command** (what's above). One line, keeps the whole gate in one
  step, and the existing `code=$?` / `if` / `exit $code` logic then runs exactly as written -
  nothing else about the script changes.
- **`continue-on-error: true` on the step**, checking `steps.<id>.outcome` afterward. This
  is the idiomatic Actions pattern for "a step is allowed to fail" - but `outcome` is only
  ever `success` or `failure`, never the process's actual exit code, so it cannot by itself
  tell exit 1 apart from exit 2. Doing it properly means giving the step an `id`, capturing
  `$?` into `$GITHUB_OUTPUT` before `set -e` can act on it, and reading that output in a
  second step - more moving parts for the same result. Reach for this form only if something
  else about the job structure already wants the step split in two (for example, a later
  step that needs to run regardless of the gate and inspect the exact code itself).

A copied pipeline that silently cannot do what its own comment claims is worse than no
pipeline: the comment says "exit 2 never gets its special treatment" is fixed, so leaving
either the `set +e` or the `continue-on-error` half undone (comment without the code, or
code without the shell semantics actually changing) ships a gate that looks like it has a
three-way exit ladder and does not.

### `if: always()` on the SARIF upload

Without it, a failing Gate step (real findings, exit 1) skips every later step by Actions'
default `if: success()` - so the one run where you most want the annotations, the run that
just failed the build, is the run that never uploads them. `always()` uploads on a clean run,
a failing run, and the "no baseline yet" run alike; the Gate step's own exit code is still
what fails the job, independent of whether the upload happened.

## Establishing the baseline

Nothing above ever *writes* a snapshot - `check` only ever reads one. The command that writes
`OTHER/seamcheck/scans/<sha>.json` is `seamcheck scan` (a bare invocation, no flags): see
`seamcheck help scan` - "it also writes two things you get for free... a snapshot keyed by
the current commit... so running scan regularly is what builds the history." `seamcheck map`
does not do this; only the bare command and `--backfill` do.

**This only works on a Django project today.** `seamcheck scan`'s snapshot-writing is
`api.write_map()` (`seamcheck/api.py`), called from the Django management command's own
bare-invocation handler (`_summary()`) - the non-Django front door (`cli._run_without_django`,
what runs on a Flask/Express/FastAPI/Next repo with no `manage.py`) never calls it for any
command, `scan` included. Found proving this section end to end against a non-Django
project (redash): `seamcheck scan` there exits 0 and prints totals, same as on a Django
project, but writes no `OTHER/seamcheck/scans/*.json` at all - so `--since` on that kind of
project can never find a baseline, no matter how often this workflow runs. `--since` itself
is correctly wired through to `api.check(since=...)` on that door now (see below); it is the
place a snapshot could come FROM that is still missing there. Real gap, out of scope for
this fix, tracked separately - if your project has no `manage.py`, this companion workflow
will not do what it says until that closes.

Run the `pull_request` workflow above on its own, on a repository that has never scanned its
default branch, and every single PR takes the "no baseline yet" branch forever - not a bug in
the Gate job, just nothing ever having populated what it reads. A second, small workflow
closes that gap:

```yaml
name: seamcheck-baseline
on:
  push:
    branches: [main]        # your default branch

jobs:
  baseline:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      security-events: write
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: {python-version: "3.12"}
      - uses: actions/setup-node@v4
        with: {node-version: "20"}
      - run: pip install "seamcheck==0.11.0"
      - run: echo "SEAMCHECK_AST_CACHE_MB=512" >> $GITHUB_ENV
      - uses: actions/cache@v4
        with:
          path: OTHER/seamcheck/scans
          key: seamcheck-${{ github.sha }}
          restore-keys: seamcheck-
      - run: seamcheck scan             # writes OTHER/seamcheck/scans/<sha>.json
      - name: Also give Code Scanning its own baseline
        run: seamcheck check --format sarif --out seamcheck.sarif || true
      - uses: github/codeql-action/upload-sarif@v3
        if: always()
        with: {sarif_file: seamcheck.sarif}
```

Two things this buys you, for the price of one more workflow:

- **`--since` has something to find.** Each push to the default branch saves a snapshot
  under a fresh cache key (`seamcheck-<that commit's sha>`); a later PR's Gate job asks for
  `seamcheck-<base sha>` by exact match, falling back through `restore-keys` to the most
  recent one cached if the exact commit was never the tip of a push (a squash-merge, a
  rebase). Cache entries accumulate this way until GitHub's own per-repository cap evicts
  the oldest ones - ordinary LRU behaviour, not something this workflow manages itself.
- **GitHub Code Scanning has its own baseline.** Its inline "new vs. already-existing" alert
  distinction (independent of seamcheck's own `--since` diff) is computed from the SARIF
  uploaded for the default branch. Skip this job and every alert in every PR's SARIF reads
  as new to GitHub, regardless of what seamcheck's own exit code says - the two "new/existing"
  answers come from two different mechanisms and only one of them is seamcheck's.

The baseline job's own `check` is intentionally not gated (`|| true`, no `set +e`/`code`
dance): its job is to hand Code Scanning a SARIF for the default branch, not to fail a build
that already merged.
