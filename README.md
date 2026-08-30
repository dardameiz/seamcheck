# Seamcheck

Find the code your project no longer connects to — and the connections it only *thinks*
it has.

Seamcheck builds a connectivity graph of a Django + vanilla-JS project by parsing it:
which URLs exist, which views they route to, which `fetch()` calls resolve to them, which
DOM elements the templates declare and the JavaScript writes, and which CSS rules and
design tokens anything still references. It reports what is connected, what resolves to
nothing, what nothing references — and, crucially, what it **cannot tell**.

## Why the fourth answer matters

Most dead-code tools have three verdicts: used, unused, and a crash. Seamcheck has four,
and the fourth is the one that makes it safe to act on:

| Status | Means |
|---|---|
| `connected` | Something reaches this, and here is the evidence |
| `unused` | Both sides of the contract are observable, and nothing uses it |
| `unresolved` | Something reaches for this and it does not exist |
| `uncertain` | The scan has no evidence either way — **not** a claim that it is dead |

A page URL reached by `<a href>` or browser navigation looks identical to a dead one if
you only parse `fetch()` calls. Seamcheck says `uncertain` and names the missing evidence
source instead of guessing. On a real 700-URL project that distinction is the difference
between a usable report and 668 false "delete me" findings.

## What it finds

- **Multi-writer DOM elements** — more than one module writing the same element. Whichever
  runs last wins, which is how a display bug survives being "fixed" in one of them.
- **`fetch()` calls that resolve to no route**, including routes short-circuited in
  `asgi.py` before Django's resolver ever runs.
- **Response fields a view sends that no consumer reads**, and fields read that no view sends.
- **CSS rules and custom-property tokens nothing references.**
- **Scan Coverage** — which files the scan actually reasoned about, so "no findings" can be
  distinguished from "never looked".

## Install

```bash
pip install seamcheck[models,mcp]
```

Add it to `INSTALLED_APPS` and point `SEAMCHECK_CONFIG` at your project:

```python
INSTALLED_APPS = [..., "django_extensions", "seamcheck"]

SEAMCHECK_CONFIG = {
    "urlconf_module": "myproject.urls",
    "asgi_module": "myproject.asgi",
    "first_party_prefixes": ["myapp", "myproject"],
    "templates_root": "myapp/templates",
    "js_source_root": "myapp/static/js",
    "js_vite_manifest": "myapp/static/dist/.vite/manifest.json",
    "css_source_root": "myapp/static/css",
    "tailwind_build_output": "myapp/static/css/tailwind-output.css",
    "management_commands_dirs": ["myapp/management/commands"],
    "celery_app_module": "myproject.celery",
    "app_configs": ["myapp.apps.MyAppConfig"],
}
```

Every project-specific path lives in that dict. There are no hardcoded project names
anywhere in the extractors.

### Node dependency, stated plainly

JavaScript and CSS extraction shell out to Node, using `acorn` and `postcss`. The `node`
extra bundles a Node runtime for machines that lack one, but **the two npm parsers still
have to be installed** (`npm install --save-dev acorn postcss`). Without Node, the Python
side — URLs, views, models, signals, admin actions, template tags, reachability, Scan
Coverage — still works; the JS/CSS extractors return nothing rather than failing.

## Use

```bash
python manage.py seamcheck                    # scan, write the map, print a summary
python manage.py seamcheck --json             # the graph, as JSON
python manage.py seamcheck --check            # diff vs the last snapshot; exit 1 on findings
python manage.py seamcheck --since main       # diff against another commit's snapshot
python manage.py seamcheck --format console        # the browsable console, 8 sections
python manage.py seamcheck --format map            # the visual connectivity map
python manage.py seamcheck --format map --since REF  # what changed between commits
python manage.py seamcheck --explain <symbol-id>
python manage.py seamcheck --triage <symbol-id> --status approved --reason "..."
```

```bash
python manage.py seamcheck --format markdown   # digest for a chat or a PR comment
python manage.py seamcheck --format html       # one self-contained file
python manage.py seamcheck --format markdown --out FINDINGS.md
```

The HTML report and the map are single files with no network requests — publish them
wherever you like. Seamcheck never uploads anything.

### Reading it on a phone

The report is self-contained, so getting it onto a phone is a file-transfer problem, not a
hosting one. Any of these work, and none of them send your code anywhere:

```bash
python manage.py seamcheck --format map --serve
# Open on any device on this network:
#     http://192.168.1.38:57071/GLb_D7GyG_Bk
```

`--serve` holds the page open on your local network until you press Ctrl-C. Nothing is
uploaded, nothing is written to disk, and the URL carries a random token so another device
cannot stumble into it. The response is sent `no-store` and `noindex`.

Or skip the server entirely: write the file with `--out`, then AirDrop it, drop it in a
synced folder, or attach it to a message. It opens standalone on any device, offline,
forever — that is the whole point of the single-file design.

If you want it permanently reachable, publish the file to your own hosting. That step is
deliberately yours: a static-analysis tool should never hold upload credentials.

`--check` is CI-ready: it exits 1 only on `unresolved`/`unused` findings that are untriaged
or explicitly confirmed. It never fails a build over `uncertain`.

### Triage

A finding you have looked at and accepted goes in `seamcheck/triage.json`, committed:

```bash
python manage.py seamcheck --triage "css_token_def:token:--legacy" \
  --status approved --reason "kept for the vendored theme"
```

The mark is keyed to a **content fingerprint of the evidence**, not to a symbol id. If the
snippet, the status, or (for a multi-writer element) the set of writers changes, the
approval expires and the finding comes back with a note saying why. You cannot silence a
finding and have it stay silent through a real change.

### MCP

```bash
python -m seamcheck.mcp_server
```

Exposes `seamcheck_check`, `seamcheck_explain`, `seamcheck_triage` and `seamcheck_report` so an agent can
check its own work before claiming a task is done.

## Limitations

Stated, not hidden:

- Not a CSS selector engine — combinator selectors (`.a .b`) match on segment presence.
- Selectors and fetch targets built at runtime are `uncertain`, never guessed.
- The CSS/JS extractor does not read `className = ...`, `classList.add(...)`,
  `setAttribute('class', ...)`, or class literals inside JS template strings, so a class
  applied by JavaScript looks unreferenced. `css_selector` findings over-report as a
  result; the report surfaces this as a caveat next to the group rather than hiding it.
- Field matching pairs one view function against a whole consuming module, so a field can
  be proven read but not proven unread.
- WebSocket payloads are out of scope.
- Reachability follows imports and dotted-string module references (`include("app.urls")`),
  but not modules loaded by a mechanism neither of those covers.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Commits follow
[Conventional Commits](https://www.conventionalcommits.org/).

## License

MIT — see [LICENSE](LICENSE).
