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
    # Optional. Makes every file:line in the UI open at that line.
    # vscode (default) · cursor · windsurf · zed · sublime · pycharm · idea · webstorm · none
    "editor": "vscode",
}
```

Every project-specific path lives in that dict. Nothing is hardcoded anywhere in the
extractors — which is how this was lifted out of the project it grew in without touching
a line of it.

<details><summary>The rest of the keys</summary>

| key | what it is | default |
|---|---|---|
| `static_root` | where a template's `{% static_js 'a/b.js' %}` resolves from | `static` |
| `vite_config` | the bundler config, read for entry points | `vite.config.js` |
| `asgi_module` | scanned for WebSocket and ASGI routes | none |
| `app_configs` | apps whose models are pulled into the graph | none |
| `tailwind_build_output` | built CSS, so utility classes aren't read as dead | none |
| `map_output` | where `seamcheck map` writes | `docs/maps/connectivity-map.html` |
| `report_output` | where `--format html` writes | `docs/maps/connectivity-report.html` |
| `editor` | URL scheme for the clickable locations | `vscode` |

</details>

**You need Node on PATH.** The JS and CSS parsers run on it. You do *not* need npm or
`node_modules` — acorn and postcss ship inlined in the wheel. If Node is missing,
Seamcheck says so and gives you the Python half rather than dying.

## Use

```bash
seamcheck map               # scan, then open the UI. Start here.
seamcheck check             # exit 1 on new findings. This is the CI one.
seamcheck backfill          # give the map some commit history

seamcheck help              # all nine commands
seamcheck help map          # what one is for, with worked examples
seamcheck scan              # the totals, no UI, no server
seamcheck explain <id>      # one symbol, with the code around it
```

`seamcheck map` scans, writes the UI to a file, and then **serves that file** so you get a
link you can click:

```
  wrote  docs/maps/connectivity-map.html  (3.9 MB)

  open   http://127.0.0.1:49497/SW9FfTR4XybG
  phone  http://192.168.1.38:49497/SW9FfTR4XybG

  Ctrl-C to stop.
```

Two links because they answer different questions: the first is the one to click here,
the second is the one to type on a phone on the same wifi. It serves rather than printing
a `file://` path because a `file://` link is not much of a link — VS Code's terminal opens
it *inside VS Code*, and a phone can't use it at all.

Nothing is uploaded. While it runs, anyone on your network holding the link can read the
report; `--local-only` binds loopback instead and drops the phone link, `--tunnel` goes the
other way and opens a temporary public HTTPS address. `--no-serve` writes the file and
stops, which is what CI wants.

A scan takes half a minute on a large project, so it draws a progress bar — on **stderr**,
and only when that is a terminal. `seamcheck json > graph.json` gives you JSON and nothing
else; a CI log collects no carriage returns.

Two flags the front door owns rather than forwards:

| | |
|---|---|
| `-v`, `--verbose` | show the host project's own warnings and start-up logging. Importing a real Django project prints a screenful before Seamcheck says anything; that noise is off by default, and `ERROR` always gets through either way. |
| `-q`, `--quiet` | no progress bar |

Anything after `--` goes straight to the management command: `seamcheck map -- --help`
lists every flag it accepts.

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

Every `file:line` in it is a link: click to open that line in your editor, shift-click to
copy the absolute path. Set `editor` in the config to pick which one.

Every finding also says **what it means and what to check** — because `unresolved ·
css_token_use · button_badges.css:3` is precise and tells a newcomer nothing. Usually one
of the two or three explanations offered is "this is fine, and here's why the scan can't
tell".

There's a **Files** view too — your actual folder tree, with a bar per file showing how
many of its declarations Seamcheck reasoned about. Because "no findings" and "never
looked" are not the same sentence.

```bash
seamcheck map              # the link, and the phone link
seamcheck map --tunnel     # ...reachable from anywhere
```

Nothing is uploaded. The server is a socket on your machine that dies with the command,
and the URL carries a random token so nothing on your network stumbles into it.
(`seamcheck serve` is the same command under the name that comes to mind when the phone
is the point.)

### Per-commit

```bash
seamcheck backfill          # the last 20 commits
seamcheck backfill 100      # ...or as many as you like
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

Seamcheck ships an MCP server, so your assistant can check its own work before it hands
it to you. It speaks over stdin/stdout — no port, no daemon, no network.

**Claude Code**

```bash
claude mcp add seamcheck -- seamcheck-mcp
```

**Cursor, Windsurf, Claude Desktop** — in the MCP config:

```json
{
  "mcpServers": {
    "seamcheck": {
      "command": "seamcheck-mcp",
      "cwd": "/path/to/your/django/project"
    }
  }
}
```

`cwd` matters: Seamcheck reads a real project, so it needs to start in one. It finds the
settings module the same way the CLI does — the nearest `manage.py` — so the project root
is the right answer.

Four tools: `seamcheck_check` (scan, report findings new since the last snapshot),
`seamcheck_report` (the digest), `seamcheck_explain` (one symbol with its evidence), and
`seamcheck_triage` (record a disposition).

There is also an [`AGENTS.md`](seamcheck/AGENTS.md) with the one rule that matters:
**never delete something because it came back `uncertain`.** That is the scan saying it
has no evidence, not that the code is dead.

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
