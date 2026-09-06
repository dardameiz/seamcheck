<!-- Review, 2026-09-06. Every number in the cost tables was measured by running the real
     command and counting the bytes it wrote (tools/cli_cost.py). The two bugs in §1 were
     each reproduced by hand before being written down. Line references are to the code as
     of commit da2fc84ef. -->

# The CLI and the MCP server: a review, and what "optimised for an agent" would mean

## Verdict

The surfaces work for a person and are actively hostile to a program. Two defects are
correctness bugs rather than ergonomics, and one of them means **the CI gate cannot fail on
any project that is not Django**. Everything else is shape: answers are unbounded, machine
form is missing exactly where an agent needs it, and the same question costs a full rescan
every time it is asked.

## 1. Two bugs, both reproduced

### 1.1 The gate never fails off Django

`cli.py:570-572`, the non-Django path:

```python
result = api.check(repo_root=root)
return 1 if result.get("findings") else 0
```

`api.check` returns no `findings` key. Its keys are `passed`, `message`, `new_unresolved`,
`new_unused`, `triage_invalidated`, `returned`, `counts` (`api.py:421-433`). The expression
is therefore always `0`.

**Reproduced.** On redash (Flask, 72k lines):

```
connected 1257  unused 53  unresolved 47  uncertain 493
EXIT 0
```

Forty-seven unresolved findings and a passing gate. The Django path gets it right
(`commands/seamcheck.py:449-450` uses `outcome["passed"]`), and the reference project
exits 1 on the same day with the same version. So the tool's headline promise, *the CI
gate*, silently does nothing on Express, Next, FastAPI, Flask and NestJS - which is most of
the corpus it advertises support for.

### 1.2 The documented exit 2 does not exist on the path people use

`seamcheck --help` says: *"check - The CI gate. Exit 1 on new findings, 2 if no baseline, 0
clean."* Also in `docs/commands.md:5` and `llms.txt:44`.

**Reproduced.** On the reference project with no snapshot for the current commit:

```
No baseline snapshot stored for 0c5fe616cb27 yet - nothing to diff against.
counts: {'connected': 46638, 'unused': 1383, 'unresolved': 2036, 'uncertain': 3169}
PB EXIT 1
```

Exit 1, not 2. The exit-2 branch exists only under `--since` (`commands/seamcheck.py:423-424`).
A CI job that distinguishes "regression" from "no baseline yet", which is the reason the
code was documented, cannot do so.

The same output shows a third problem: `counts: {'connected': …}` is a **Python dict repr**,
single quotes and all (`commands/seamcheck.py:448`). It is the only structured thing `check`
prints and it is not JSON.

## 2. What an answer costs

Measured by running each command and counting stdout. Tokens are bytes ÷ 4.

| command | 12k-line project | reference project (500k) | wall |
|---|---:|---:|---:|
| `--help` | 888 B · ~222 tok | 888 B | 0.0 s |
| `config` | 443 B | 2 KB · ~502 tok | 1.0 s |
| `scan` | 2.5 KB · ~628 tok | 794 B · ~198 tok | 89 s |
| `check` | 2.5 KB · ~628 tok | 811 B · ~202 tok | **163 s** |
| `report` | 3.5 KB · ~872 tok | 11.8 KB · ~2,941 tok | 86 s |
| **`json`** | **595 KB · ~148,851 tok** | **72.6 MB · ~18,158,103 tok** | 90 s |

One more row, because it is the whole problem in a single line. `explain` with a symbol id
that does not exist:

| | |
|---|---:|
| what it returned | `No symbol with id \`urls.py\` in the current scan.` |
| bytes | 49 |
| wall time on the reference project | **88.5 s** |

A full scan, ninety seconds, to be told the id was wrong. There is no index to check a name
against and no way to ask "did you mean", so the cost of a typo is the cost of the answer.

`json` is the command `cli.py:346-349` names as the agent interface. On the smallest project
in the corpus it does not fit in a context window. On the reference project it is about
ninety context windows, and an agent that runs it has already lost.

Over MCP the same cliff is one argument away: `seamcheck_report(fmt=…)` passes `fmt` straight
through, so `fmt="json"` returns those 72 MB and `fmt="map"` returns the 8.6 MB HTML document.
Neither value is mentioned in the tool's description, which says "terminal, markdown or html",
and there is no enum to stop a reasonable guess.

## 3. The CLI, for an agent

**Flags that silently do nothing off Django.** `_plain_args` (`cli.py:700-762`) is a
hand-rolled second parser that recognises a subset. Missing: `--since`, `--repo-root`,
`--backfill`, `--backfill-ref`, `--observe`, `--base-url`, `--shots`, `--no-progress`. Each
is documented in the help of a command that accepts it, so `seamcheck check --since $BASE`
on an Express repo reads as working and compares against nothing. `seamcheck scan
--repo-root ../other` scans the current directory instead, because the root is hardcoded to
cwd at `cli.py:542`.

**No machine form where it matters.** `json` and `share --json` are the only JSON. `check`,
`explain`, `triage`, `config`, `backfill` and `observe` are prose. `api.check` builds a
complete dict and the command prints it one field at a time; `api.triage` returns
`{ok, message}` and only `ok` survives, as the exit code.

**Noise on stdout that an agent must strip.** `scan` appends a two-line "next" suggestion, a
conditional blind-spots paragraph and a `graph <path>` line; `map`/`serve` print a five-line
security notice; `backfill` prints a time estimate before working; `share` prints an issue
URL and a legal caution. The two engines even disagree about where a write is announced:
the Django path says `wrote …` on stdout, the non-Django path says `seamcheck: wrote …` on
stderr.

**`map` never returns.** It is the first command in the help and it ends in `serve_forever()`
(`commands/seamcheck.py:702`). An agent that runs it hangs until it is killed. `--no-serve`
is the escape and is not the default anywhere.

**Exit codes are inconsistent across engines.** The same failure exits 2 through `seamcheck`
and 1 through `manage.py seamcheck` (`cli.py:979` against Django's own handler).

**Reads mutate the repository.** `scan` writes a snapshot and a 72 MB JSON as a side effect
(`api.py:615-618`), so the second `check` on a commit behaves differently from the first.
Reading triage rewrites `triage.json` with today's date when a mark expires
(`api.py:395-406`).

**Non-determinism in output.** The terminal and HTML renderers stamp `generated_at`
(`renderers/terminal.py:40`), so two identical runs differ. `seamcheck --version` prints the
absolute install path, which on a developer machine is a home directory.

**`share --quiet` cannot work**: `_split_flags` removes `-q`/`--quiet` from argv
(`cli.py:481-485`) before `_share` looks for it (`cli.py:850`).

## 4. The MCP server, for an agent

Eight tools, all in `mcp_server.py`. What is right: ids are semantic and stable across
`unverified` → `triage` → `explain`; `seamcheck_unverified` is genuinely well shaped, with a
`limit` (default 25), a `kind` filter and a `by_kind` census that teaches the vocabulary from
the response; errors for bad arguments come back as a field rather than an exception.

What is wrong, in the order it hurts:

| | |
|---|---|
| **The loop is never sent.** | The module docstring (`mcp_server.py:3-24`) explains when to use what, including "the note is what the tool THINKS; it is not evidence". `_Server("seamcheck")` (:46) passes no `instructions=`, so no client ever sees it. |
| **Five of eight tools return a bare `dict`** | so they get no `outputSchema` and arrive as one JSON-stringified text block. The richest answers are the least machine-typed. |
| **No enums** | on `status`, `why`, `fmt`, `kind`, though every one has a closed vocabulary in the code. A first call is a guess and recovery costs a round trip. |
| **No annotations** | so a client cannot tell that seven tools are read-only and `seamcheck_triage` is the only writer. |
| **`seamcheck_check` is unbounded** | every new finding, every returned mark, no `limit`, no filter. ~499 KB on the reference project. |
| **Every tool re-scans** | there is no graph cache. `explain` is `api.explain(api.scan(...), id)`. Five explanations on the reference project are five 90-second scans: **7.5 minutes to look at five symbols**, and a mistyped id costs the same 90 seconds as a real one. |
| **No baseline can be made over MCP** | only `scan` writes a snapshot and there is no `scan` tool, so `seamcheck_check` reports "no baseline" forever until a human runs the CLI. |
| **No `--since` anywhere** | every tool is hardwired to `HEAD`, so "what did this branch change" is unreachable. |
| **`passed` does not mean what the description says.** | The description says findings "new since the last snapshot"; `passed` is `has_blocking_findings`, true for any untriaged finding, new or not. On a repo with a backlog an agent draws the wrong conclusion. |
| **Docs omit the entry point** | `docs/agents.md:36-44` lists seven tools and leaves out `seamcheck_unverified`, the one designed to be the agent's first call. |
| **Untested** | `unverified`, `share`, `services`, `why_wrong` have no behavioural test; the suite calls the undecorated functions, so no test exercises the protocol layer at all. |

## 5. What "optimised for an agent" should mean here

Six properties, each with a test that would fail today.

1. **Every answer is bounded by default.** A default `limit`, an explicit `total`, and a
   cursor. No command may return the whole graph unless asked with `--full`, and `--full`
   on a large project prints the size and refuses without `--yes`.
2. **One envelope, everywhere.** `{"schema": 1, "ok": true, "data": …, "warnings": […]}` on
   stdout, nothing else on stdout, prose on stderr. Same shape for CLI JSON and MCP
   structured content, generated from the same function, so the two surfaces cannot drift.
3. **Errors are codes.** `{"ok": false, "error": {"code": "no_baseline", "message": …}}`,
   with the code table in the docs. Exit codes documented per command, and identical
   between `seamcheck` and `manage.py seamcheck`.
4. **A scan is reused.** One scan per process, keyed by repo root and file stamps, so five
   explanations cost one scan. This is the single largest efficiency win available: it turns
   7.5 minutes into 90 seconds.
5. **Questions, not dumps.** The questions an agent actually has are "what breaks if I change
   this file", "what calls this", "what is unresolved in this diff". Today those are
   answerable only by fetching everything. Each becomes a narrow command with a small answer.
6. **The vocabulary is in the schema.** Enums for `status`, `why`, `kind`, `fmt`; the loop in
   `instructions`; `outputSchema` on every tool; `readOnlyHint` on the seven that read.

## 6. Order of work

1. **The two bugs** (§1). Small, and one of them is a false green in CI.
2. **The scan cache** (§5.4). One change, and it is what makes an agent session affordable.
3. **The envelope and bounded answers** (§5.1, §5.2, §5.3) across both surfaces.
4. **The MCP schema work** (§5.6): instructions, enums, outputSchema, annotations, a
   snapshot tool, `--since`.
5. **The question-shaped commands** (§5.5).
6. **Close the two-parser gap**: `_plain_args` should be the same parser as the Django path,
   or the flags it does not understand should be rejected rather than ignored.

## 7. How we would know it worked

- A gate test per adapter family: a fixture repo with one unresolved finding must exit 1 on
  Django, Flask, Express and FastAPI alike. Today three of the four exit 0.
- A cost test: every command's stdout on the reference project stays under a stated ceiling,
  and `json` without `--full` is refused.
- A protocol test that calls the MCP tools through a real session rather than as Python
  functions, asserting `structuredContent`, `isError` and the enums.
- A determinism test: two runs of every read command produce byte-identical stdout.
- A "no side effects" test: a read command leaves `git status` clean.
