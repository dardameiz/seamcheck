# CLAUDE.md

Guidance for Claude Code working in this repository.

## Commit messages — no tool watermarks

**Never add `Co-Authored-By: Claude ...` or `Claude-Session: ...` trailers to a commit.**
This is a public repository; its history is permanent, and the session URL is a private
link that means nothing to anyone who reads it.

Turned off globally in `~/.claude/settings.json` (`"includeCoAuthoredBy": false`), but that
setting only covers the `Co-Authored-By` line — do not hand-write either trailer.

Write the commit message and stop.

## What this is

Seamcheck reads a project's source and reports what connects to what — specifically across
the boundaries no compiler spans: a `fetch()` and the route that serves it, a JS selector
and the element it looks for, a CSS rule and the markup it styles.

**The house rule, which everything else follows from: never make a claim the scan cannot
evidence.** `uncertain` is a first-class answer, not a failure. A route assembled at runtime
genuinely cannot be known from source, and saying so is what makes the other three statuses
worth trusting. This binds the README and the marketing copy as much as the code.

## The four statuses

| | |
|---|---|
| `connected` | Something reaches it, and the evidence is attached. |
| `unresolved` | Something reaches for it by name and it is not there. |
| `unused` | Both ends are observable and nothing connects them. |
| `uncertain` | The scan cannot tell. Not a claim that anything is dead. |

## Architecture

- `seamcheck/adapters/` — one reader per backend framework. Only Django is *imported*;
  every other adapter reads source. `ADAPTERS` is built lazily so importing the registry
  does not require Django to be installed.
- `seamcheck/extractors/` — everything else that produces symbols: JavaScript, CSS, DOM,
  templates, Stripe, Celery, GraphQL, SQL schema.
- `seamcheck/pipeline.py` — `run_scan()`. Everything below the adapter is
  framework-agnostic and reads the graph, never the backend.
- `seamcheck/renderers/map_html.py` — the map. One file, ~4,000 lines, its own CSS and JS.

## Rules that came from real bugs

- **Django is optional.** Six of the seven backends have nothing to do with it. Do not
  reintroduce a module-level `import django` outside `django_adapter`, `apps.py` and the
  management command.
- **Page membership is compared by string against `symbol.file`**, which is always
  repo-relative. Normalise both ends (`api._norm`). Absolute-vs-relative silently drops
  every page and the map shows only "not reached from any page" buckets.
- **`requires-python` floor is 3.10.** No `X | Y` in `isinstance`, no `datetime.UTC`, no
  backslash inside an f-string expression. `ruff` `target-version` is held to `py310` and
  `UP038` is off for exactly this reason.
- **Never phrase a message in one framework's vocabulary.** "URLconf" was shown to Express
  and Flask users for a thing they do not have.
- **The map's JS is emitted from a Python string.** Run `node --check` on the emitted
  `<script>` blocks after editing `map_html.py`; a syntax error there is a blank page, and
  Python lint will not catch it.
- **Watch for temporal dead zones** in that script: a `const` read above its declaration is
  an error, not `undefined`. Declare shared state near the top.

## Verifying a change

```bash
ruff check seamcheck/
./build_parsers.sh && git diff --exit-code -- seamcheck/js_tools seamcheck/css_tools
```

The second is the check the release workflow runs; a stale bundle fails the release.

Test the real thing rather than trusting the unit tests alone — build a map against a real
project and drive it, and scan the demo repos for each backend.
