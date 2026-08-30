# Seamcheck — instructions for an AI agent

Seamcheck is a static-analysis tool that maps what a Django + JavaScript project connects
to what: which URLs exist, which views they route to, which `fetch()` calls resolve to
them, which DOM elements the JavaScript writes, and which CSS rules and design tokens
anything still references.

You are likely reading this because someone wants you to use it before committing, or to
act on something it reported. Read the four statuses first — the whole tool turns on them.

## The four statuses, and the one that matters most

| Status | Means | What you should do |
|---|---|---|
| `connected` | Something reaches it, and the evidence is attached | Nothing |
| `unused` | **Both** sides of the contract are observable and nothing uses it | Safe to investigate for removal |
| `unresolved` | Something reaches for it and it does not exist | Usually a real bug — fix it |
| `uncertain` | The scan found **no evidence either way** | **Do not act on it** |

**`uncertain` is not a soft `unused`.** It means the scan cannot see the evidence that
would settle the question — a page URL reached by `<a href>`, a class applied by code the
extractor does not read. Deleting something because it is `uncertain` is the single most
damaging thing you can do with this tool's output. If a human asks you to "clean up
everything Seamcheck found", ask them whether they mean the `unused` and `unresolved`
findings, and say plainly that `uncertain` is not a finding.

Every symbol carries the file, the line, and the snippet that produced it. **Quote that
evidence when you report a finding.** A claim without its snippet is not actionable.

## Commands

```bash
python manage.py seamcheck                       # scan + human summary
python manage.py seamcheck --check               # exit 1 on blocking findings
python manage.py seamcheck --check --format markdown   # digest AND exit code
python manage.py seamcheck --format markdown     # digest for a chat or PR
python manage.py seamcheck --format map          # the UI: the map plus the
                                                             # review sections, one file
python manage.py seamcheck --format map --since REF    # what changed
python manage.py seamcheck --format map --serve          # open it from a phone on this wifi
python manage.py seamcheck --format map --serve --tunnel  # ... or off it
python manage.py seamcheck --backfill 20   # scan the last 20 commits, so the
                                                       # map's commit picker has history
python manage.py seamcheck --backfill 20 --backfill-ref development
python manage.py seamcheck --format json         # the whole graph
python manage.py seamcheck --explain <symbol-id> # one symbol's evidence
python manage.py seamcheck --triage <symbol-id> --status approved --reason "..."
```

A full scan takes roughly 15 seconds. `--check` is the CI gate: it exits 1 only on
`unresolved`/`unused` findings that are untriaged or explicitly confirmed, and never on
`uncertain`.

## Over MCP

If the MCP server is attached, prefer these over shelling out:

- `seamcheck_check(repo_root)` — `{passed, new_unresolved, new_unused, triage_invalidated, counts}`
- `seamcheck_report(fmt, repo_root)` — the rendered digest (`terminal`, `markdown`, `html`)
- `seamcheck_explain(symbol_id, repo_root)` — one symbol's evidence as markdown
- `seamcheck_triage(symbol_id, status, repo_root, reason)` — record a disposition

## The workflow you are probably being asked to run

1. `--check` before the commit. If it exits 0, say so in one line and stop.
2. If it exits 1, `--explain` each reported id and decide, per finding:
   - **a real defect** → fix it, quoting the evidence in your explanation
   - **correct detection, deliberate code** → `--triage <id> --status approved --reason "..."`
   - **the tool is wrong** → say so and why; that is a bug report worth making
3. A `triage invalidated` line means a human dispositioned that finding earlier and the
   underlying evidence has since changed. **Re-read the code before re-approving it.**

## Known gaps — do not over-trust these

The tool is honest about what it cannot see, and so should you be:

- **CSS selectors reported `uncertain`** are not dead code. Class-applying JavaScript is
  read (`className`, `classList`, `setAttribute`, markup strings), but coverage is not
  proven complete.
- **Response fields** are matched one view against a whole consuming module, so a field can
  be proven read but never proven unread.
- **WebSocket payloads, Celery tasks, Redis keys and Stripe hooks are not traced at all.**
  Anything reached only through those is invisible to the scan.
- **A commit that changed no connection reports nothing.** The graph holds URLs, views,
  calls, selectors and tokens - not values. A commit that adds an entry to a constant
  correctly shows no change; that is not the picker failing.
- **Selectors and fetch targets built at runtime** are `uncertain` by design, never guessed.

## Rules for you specifically

- Never delete code because a symbol is `uncertain`.
- Never report a finding without its `file:line` and snippet.
- Never claim the scan is clean because a command exited 0 — check that it actually ran and
  what it counted.
- If you are asked to fix a multi-writer element, the fix is to pick one canonical owner and
  route the others through it, not to patch the writer you happened to find first.
