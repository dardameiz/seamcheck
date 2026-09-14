# The commands

```bash
seamcheck map        # scan, then open the canvas. Start here.
seamcheck check      # the CI gate. Exit 1 on findings, 0 clean; --since REF adds exit 2
                     # if REF has no stored snapshot to compare against.
seamcheck report     # the findings digest, as text or markdown
seamcheck explain    # why one symbol is classified the way it is
seamcheck triage     # record "this one is fine, and here is why"
seamcheck backfill   # scan the last N commits so the map has history
seamcheck observe    # drive your pages in a real browser and record what it saw
seamcheck config     # what was detected, and how it was worked out
seamcheck share      # a report about the scan containing none of your code
seamcheck scan       # scan and print the totals, no UI, no server - also writes
                     # the snapshot check/diff compare against
seamcheck serve      # the same command as map, under the name that reads better
                     # when the phone is the point
seamcheck json       # the whole graph, unfiltered, for jq or a script - 72 MB on
                     # a 500k-line project; the three below answer in a few KB
seamcheck symbols    # find a symbol by name - the cheap way to get an id
seamcheck findings   # what is wrong, filtered by file/kind/status/owner, bounded
seamcheck diff       # what appeared, vanished or changed status since --since REF
seamcheck scope      # what page(s) a commit/push touches, and what's wrong there NOW -
                     # not "what's new" (diff/check's question); "commit" scopes to
                     # staged files, "push" to everything not yet on the upstream branch
seamcheck install-hooks   # write git pre-commit/pre-push hooks that run `scope` and
                     # print a summary - advisory only, never blocks
```

`seamcheck help <command>` explains any of them with examples.

**`symbols`, `findings` and `diff` are the ones built for an agent to call directly**, not
just to be scripted around: unlike every other command here, each reads from one scan cache
shared only among the three of them (and the MCP `seamcheck_snapshot` tool) rather than
paying for a fresh scan every time, answers in a few KB, and takes `--limit`/`--cursor` to
page and `--refresh` to skip that cache for a tree it cannot judge on its own (a fresh
checkout, a restored backup, a clock that just got corrected). All three print one JSON
envelope (`{schema, ok, command, repo, sha, data, truncated, warnings, cost, error}`,
`seamcheck/envelope.py`) on stdout and nothing else: a bad `--status`, an unresolvable
`--since` ref, or an unknown symbol id is reported in the envelope's `error.code` (one of
`unknown_symbol, no_baseline, bad_argument, missing_dependency, no_git, too_large,
stale_snapshot` - `no_adapter` is a defined code with no scenario that reaches it through
these three; see below), and the process exit code matches it, through the same 0-4 table
every other command uses (`exitcodes.envelope_exit_code`). A directory with nothing this
tool recognises never reaches the envelope at all: it exits `4` with a plain stderr message
before any of the three starts.

**`scope` is a fourth, related but not identical agent command**: it shares the same scan
cache and the same JSON envelope shape as the three above (plus one more error code,
`no_upstream`, for `scope push` with no upstream branch configured), but is unbounded (no
`--limit`/`--cursor` - a scope is a handful of pages, not thousands of rows) and its exit
code is `EXIT_FINDINGS` (1) whenever any touched page has an unresolved/unused finding, not
only via `envelope_exit_code`'s ordinary `ok`/`error` mapping (`exitcodes._scope_exit_code`).

```bash
seamcheck findings --file app/views.py     # what is wrong here
seamcheck symbols --search push            # name -> id, before spending a call on explain
seamcheck diff --since origin/main         # what this branch changed
seamcheck scope commit                     # what's in the commit you're about to make
seamcheck scope commit --serve             # the same thing, as a map already opened on it
```

## Exit codes

The process exit code - what a shell or a CI step actually branches on - is a small,
closed set (`seamcheck/exitcodes.py`):

| code | name | means |
|---:|---|---|
| 0 | `EXIT_CLEAN` | the command ran and found nothing blocking |
| 1 | `EXIT_FINDINGS` | `check` found something new and blocking, or `scope` found an unresolved/unused finding in a touched page |
| 2 | `EXIT_NO_BASELINE` | `check --since REF` has no stored snapshot for `REF` to diff against |
| 3 | `EXIT_USAGE` | the command was wrong - an unknown flag, a bad enum value |
| 4 | `EXIT_ENVIRONMENT` | the machine was wrong - nothing here this knows how to read, a missing import, no `node` on PATH |

`check` is what actually walks this table, through `gate_code()`: 0 clean, 1 on new
findings, and - only with `--since`, since a bare `check` has nothing to diff against and
so cannot ask that question - 2 when `REF` has no stored snapshot. `2` is `check --since`'s
alone: a failed `triage` (an id the current scan does not have, a `--status`/`--wrong` word
outside the fixed set, or an `--undo` with no mark to remove) is a bad argument, not "no
baseline to compare against", so it returns 3, not 2. Any command, `check` and `triage`
included, can still stop with 3 or 4 before a scan even runs.

Useful flags: `--format terminal|markdown|html|map|json|sarif|github` · `--out FILE` ·
`--serve` / `--no-serve` · `--tunnel` (a temporary public HTTPS link, for your phone) ·
`--local-only` · `--since REF` · `--open` · `--bundle` · `--limit` / `--cursor` (paging on
`symbols`/`findings`/`diff`) · `--full --yes` (with `json`, print the whole graph past the
size gate - one flag alone still refuses). `sarif` and `github` render the
current findings as SARIF 2.1.0 or GitHub Actions annotations - see `docs/ci.md` for the
pull-request workflow that consumes them.

A map is one HTML file by default. `seamcheck map --out map/` (a folder, or `--bundle`)
writes a small `index.html` plus `data/*.js`, and each page's rows - and each review
list and the file tree - are fetched only when looked at; the form a very large
repository needs. A map that would pass 50 MB as one
file is written this way on its own, and the command says so.
