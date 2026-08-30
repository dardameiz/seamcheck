# Seamcheck

**Your AI wrote 400 lines. Which of them are actually wired to anything?**

Seamcheck reads a Django + JavaScript project and tells you what connects to what — which
`fetch()` lands on which view, which template element the JS is reaching for, which CSS
rule nothing has referenced since 2023. Then it tells you what it *couldn't* work out,
which turns out to be the part that matters.

No SaaS. No upload. One command, one HTML file, and an exit code for CI.

## The four answers

Every dead-code tool ever written has told you something was unused and been wrong, and
you stopped trusting it. Seamcheck has a fourth answer:

| | |
|---|---|
| `connected` | something reaches this — here's the file and line |
| `unresolved` | something reaches for this and it isn't there |
| `unused` | both ends are observable and nothing uses it |
| **`uncertain`** | **no evidence either way. Not a claim it's dead.** |

That last row is the whole product. A page reached by an `<a href>` looks exactly like a
dead one if you only parse `fetch()` calls. Seamcheck says so instead of guessing. On a
700-URL project that's the difference between a report you act on and 668 lies.

It also reports **its own coverage** — how much of each file it actually reasoned about.
No other tool I've found will tell you where it wasn't looking.

## What it found in the project it was built on

Not hypotheticals. Real bugs, in a real 36,000-symbol codebase, found while writing this:

- Five CSS custom properties in a **loaded** stylesheet that resolve to nothing —
  `--text-primary`, `--border-color` and friends. Those declarations render nothing today.
- An endpoint reported missing that turned out to be **`<str:division_id>` matching a
  deliberate `'all'` sentinel** — so I fixed the matcher instead of "fixing" the code.
- 65 API routes whose path appears in no source file at all.
- 46 DOM elements written by more than one module — the reason a display bug survives
  being "fixed" in one of them.

## Install

```bash
pip install seamcheck
```

Then two things in `settings.py`:

```python
INSTALLED_APPS = [..., "seamcheck"]

SEAMCHECK_CONFIG = {
    "urlconf_module": "myproject.urls",
    "templates_root": "myapp/templates",
    "js_source_root": "myapp/static/js",
    "css_source_root": "myapp/static/css",
    "first_party_prefixes": ["myapp", "myproject"],
}
```

Every project-specific path lives in that dict. Nothing is hardcoded anywhere in the
extractors — which is how this was lifted out of the project it grew in without touching
a line of it.

**You need Node on PATH.** The JS and CSS parsers run on it. You do *not* need npm or
`node_modules` — acorn and postcss ship inlined in the wheel. If Node is missing,
Seamcheck says so and gives you the Python half rather than dying.

## Use

```bash
seamcheck help              # every command, one line each
seamcheck scan              # scan, summary, snapshot for later diffs
seamcheck check             # exit 1 on new findings. This is the CI one.
seamcheck map               # the UI, one self-contained HTML file
seamcheck serve             # ...opened from your phone
seamcheck explain <id>      # one symbol, with the code around it
```

It finds your project by walking up to the nearest `manage.py` and reading the settings
module out of it, so it works from anywhere inside the tree. Everything is also available
as `python manage.py seamcheck ...` if you prefer — same code, one implementation.

### The UI

One file, no network, opens on a phone. A left rail of views; a canvas that draws
**every symbol a page touches at once** — 1,366 of them on the biggest page here — with
the broken ones filled in red so they find you rather than the other way round.

Click any node and it lights the line through it: page → module → `fetch()` → URL →
view, each hop with the real source, and a button to show the whole enclosing function.
Or isolate that one chain and drop everything else.

There's a **Files** view too — your actual folder tree, with a bar per file showing how
many of its declarations Seamcheck reasoned about. Because "no findings" and "never
looked" are not the same sentence.

```bash
seamcheck serve            # open it from your phone
seamcheck serve --tunnel   # ...from anywhere
```

Nothing is uploaded. `--serve` is a socket on your machine that dies with the command.

### Per-commit

```bash
seamcheck backfill 20       # scan the last 20 commits
```

Now the map has a commit picker. Pick one and see what *that commit* changed — added,
removed, status flipped — including things it deleted, which no longer exist to be drawn
and get named instead.

### CI

```bash
seamcheck check --since $BASE_SHA
```

`1` = new findings. `2` = no baseline, so the gate didn't run. `0` = clean. That
distinction matters: a gate that never ran is not a gate that passed.

### Agents

`seamcheck_check`, `seamcheck_report`, `seamcheck_explain`, `seamcheck_triage` over MCP,
and an `AGENTS.md` that tells your agent the one rule that matters: **never delete
something because it's `uncertain`.**

## What it can't do

Written down because a tool that hides its blind spots is worse than no tool:

- **Django + vanilla JS.** No React, Vue, or TypeScript yet.
- **Celery, Redis, WebSockets and Stripe aren't traced.** Anything reached only through
  those is invisible, and the UI says so rather than showing a confident zero.
- **A URL built at runtime** stays `uncertain`. The prefix is recorded, never a guess.
- **It has been run against one real project.** Mine. That's one more than most tools at
  this stage and far fewer than you'd want.

## Contributing

Issues and PRs welcome. One house rule, and it's the reason the tool is worth anything:
**never make a claim the scan can't evidence.** If you can't prove it, it's `uncertain`,
and the note says which evidence source was missing.

## License

MIT.
