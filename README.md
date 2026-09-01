# Seamcheck

[![Sponsor](https://img.shields.io/badge/Sponsor-%E2%9D%A4-ea4aaa?logo=github&logoColor=white)](https://github.com/sponsors/dardameiz)
[![PyPI](https://img.shields.io/pypi/v/seamcheck?color=7c6cff)](https://pypi.org/project/seamcheck/)
[![Python](https://img.shields.io/pypi/pyversions/seamcheck?color=7c6cff)](https://pypi.org/project/seamcheck/)
[![License](https://img.shields.io/badge/license-MIT-informational)](LICENSE)

A small tool that looks for the bugs that sit **between** things — your frontend and your
backend, your code and your database schema, one file and another. It reads your source. It
never runs your code.

![The map](docs/images/map.png)

<sub>A small Express shop, scanned. Read it downwards: **the browser** at the top, **the
seam** where requests cross the network, **the server** underneath.</sub>

## Why I made it

I was building a game — a fairly large Django app with a lot of hand-written JavaScript —
and I kept losing afternoons to the same kind of bug. The Python was fine. The JavaScript
was fine. The route one asked for and the route the other served were one character apart,
and nothing I already had read both sides of that.

So I wrote something to find them, for myself, on that project. It kept catching things I
would not have found on my own, and after a while it seemed like other people might have
the same afternoons to lose. So here it is.

It does not catch everything, and I am sure there are things it gets wrong. When it cannot
tell, it tries to say `uncertain` rather than guess. **If you find it being confidently
wrong somewhere, please [open an issue](https://github.com/dardameiz/seamcheck/issues)** —
that is the most useful thing anyone can send me.

## Install

```bash
pip install seamcheck
seamcheck map
```

That is the whole setup. It opens a map of your project and prints a link you can open on
your phone. **Have a look around before reading any further** — it explains itself better
than this page does.

Needs **Python 3.10 or newer**, and nothing else.

**If your project is Django**, put it in the project's own virtualenv:

```bash
source .venv/bin/activate
pip install seamcheck
```

A Django project is the one thing seamcheck reads by *importing* it, so it has to run where
that project's imports resolve. Every other backend is read from source, so anywhere works.

For the agent server: `pip install 'seamcheck[mcp]'`.

<details>
<summary><b>If pip says <code>externally-managed-environment</code></b></summary>

That is [PEP 668](https://peps.python.org/pep-0668/). Homebrew's Python and most Linux
distro Pythons refuse to let pip install into them globally, on purpose — it is how you
break your OS. A virtualenv is the answer, and it is what you want here anyway:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install seamcheck
```

Do not reach for `--break-system-packages`. It does what it says.
</details>

<details>
<summary><b>macOS</b></summary>

The `python3` that ships with macOS is **3.9**, which is too old — and it is why
`pip3 install seamcheck` can report *"could not find a version that satisfies the
requirement seamcheck (from versions: none)"*. That message means "nothing here matches
your Python", not "no such package".

```bash
brew install python@3.12
cd your-project
python3.12 -m venv .venv
source .venv/bin/activate
pip install seamcheck
```
</details>

<details>
<summary><b>Debian / Ubuntu</b></summary>

```bash
sudo apt install python3-venv        # if `python3 -m venv` is missing
python3 -m venv .venv
source .venv/bin/activate
pip install seamcheck
```
</details>

<details>
<summary><b>Windows</b></summary>

```powershell
py -3.12 -m venv .venv
.venv\Scripts\activate
pip install seamcheck
```
</details>

<details>
<summary><b>pipx and <code>uv tool</code> — read this before you use them</b></summary>

Both work and both give you `seamcheck` on your PATH everywhere:

```bash
pipx install seamcheck
uv tool install seamcheck        # uv fetches its own Python, so no Homebrew needed
```

**But a pipx or uv-tool copy is isolated from your project on purpose, so it cannot scan a
Django project** — importing your settings needs your project's own dependencies, and they
are not in there. Seamcheck will say so rather than showing you a traceback.

They are fine for everything else, since nothing there has to be imported: Express,
Fastify, NestJS, Next.js, Flask, FastAPI, and Supabase, Firebase or Redis projects.
</details>

<details>
<summary><b>Upgrading and getting an old version</b></summary>

pip caches the package index, so shortly after a release you can be handed the previous
one:

```bash
pip install --no-cache-dir --upgrade seamcheck
seamcheck --version
```
</details>

## What it looks like

**Click a red one.** The chain that reaches it lights up and everything else recedes, so
you can see where the request came from and where it stopped — with the file and line for
every hop, and a sentence saying what it means and what to check.

![A broken request, clicked, with its chain lit](docs/images/chain.png)

<sub>`checkout.js` asks for `/api/shipping/quotes`. The server serves
`/api/shipping/quote`. One character, valid on both sides, and nothing else would have
told you.</sub>

Everything it is willing to claim, each one explained in a sentence, worst first.

![Findings](docs/images/findings.png)

It reads on a phone, because that is where you end up looking at it.

<img src="docs/images/phone.png" width="320" alt="The map on a phone">

Five looks, if you care. Aurora is the default.

![The design packs](docs/images/packs.png)

## Does it work with my stack?

No configuration. It works out which one you are using from what is in the repo.

| | detected by | reads |
|---|---|---|
| **Django** | `manage.py` | the URLconf, imported |
| **Flask** | `@app.route` | decorators, from source |
| **FastAPI** | `@app.get` | decorators, from source |
| **Express** | `app.get(...)` | call sites, from source |
| **Fastify** | `fastify.get(...)` | call sites, from source |
| **NestJS** | `@Controller` | decorators, composed with the controller prefix |
| **Next.js** | `pages/api`, `app/**/route.ts` | the file tree |

Each one is checked against a small app written for the purpose, with a deliberately
mistyped endpoint in it, and each one finds it:

```
flask     /api/orders            → the route is /api/order
fastapi   /api/sign-up           → the route is /api/signup
express   /api/does-not-exist    → there is no such route
fastify   /api/comments          → the route is /api/comment
nestjs    /api/orders/check-out  → the route is /api/orders/checkout
nextjs    /api/account/profil    → the file is pages/api/account/profile.js
```

Only Django is imported; the rest are read from source, so nothing has to run and none of
their dependencies have to be installed.

**Frontends** — plain JavaScript · TypeScript · Django templates · CSS and design tokens
**Also reads** — Stripe webhooks · Celery tasks and beat schedules · GraphQL schemas

Also tried against 20 open-source projects: 53,361 files, 9.5M lines, 2,435 routes. If
yours does not work, I would genuinely like to know.

## It reads your data layer too

The seam is a **name crossing a boundary nothing checks**. A route string is one instance.
Where you keep your data is another, and usually nobody is checking that at all.

### Supabase

Your client names a table, a column, a function and an edge function as **strings**, and
they are checked against `supabase/migrations/*.sql`.

```
UNRESOLVED  db_table_use       order              the migrations declare `orders`
UNRESOLVED  db_column_use      order.total        PostgREST returns the row WITHOUT it
UNRESOLVED  db_function_use    get_statistics     the function is `get_stats`
UNRESOLVED  edge_function_use  send-mail          the directory is `send-email`
UNUSED      db_table           audit_log          no client code touches it
UNRESOLVED  db_policy          orders             read by the client, RLS off
```

A mistyped **column** is the quiet one. PostgREST returns the rows without it, the client
reads `undefined`, and a blank field ships. Nothing raises. `supabase gen types` catches it
only if you regenerate after every migration — and that drift is the bug.

The last line is a security check: the anon key ships in your browser bundle, so a table the
client reads with row level security off is readable by anyone who opens devtools.

The schema reader is not Supabase-specific — it also reads `migrations/`, `db/migrate/`,
`database/migrations/` and `sql/`, so Alembic, dbmate, Sqitch and hand-rolled folders work.

### Firebase

`httpsCallable('sendEmail')` is checked against what your functions directory exports —
same shape as a fetch against a route.

Firestore is **schemaless**, so "does this collection exist" has no answer in your files and
is never claimed. But `firestore.rules` *is* a declaration: a collection with no `match`
block is denied by default and fails silently in production, and a `match` block for a
collection nobody touches is usually a rename left behind.

### Redis

No schema either, so a key is checked against its **counterpart**.

```
UNRESOLVED  redis_key  user:*:stat     read here, written nowhere — can only ever miss
UNUSED      redis_key  user:*:legacy   written here, read nowhere
UNRESOLVED  redis_ttl  cache:board:*   names itself a cache, written with no expiry
```

Key patterns are normalised before they are compared, so `user:{uid}:stats`,
`user:${id}:stats` and `user:%s:stats` are one key — a Python writer meets a JavaScript
reader.

## Four words, and it never says more than it can prove

| | |
|---|---|
| **connected** | Something reaches it, and the evidence is attached. |
| **unresolved** | Something reaches for it by name and it is not there. Usually a bug. |
| **unused** | Both ends are visible and nothing connects them. Usually a decision. |
| **uncertain** | The scan cannot tell. |

`uncertain` is a real answer rather than a cop-out. A route assembled at runtime genuinely
cannot be known by reading the source, and I would rather it admitted that than pretended
otherwise. It is also why the other three can be trusted.

## The commands

```bash
seamcheck map        # scan, then open the canvas. Start here.
seamcheck check      # the CI gate. Exit 1 on new findings, 2 with no baseline, 0 clean.
seamcheck report     # the findings digest, as text or markdown
seamcheck explain    # why one symbol is classified the way it is
seamcheck triage     # record "this one is fine, and here is why"
seamcheck backfill   # scan the last N commits so the map has history
seamcheck observe    # drive your pages in a real browser and record what it saw
seamcheck config     # what was detected, and how it was worked out
```

`seamcheck help <command>` explains any of them with examples.

Useful flags: `--format terminal|markdown|html|map|json` · `--out FILE` · `--serve` /
`--no-serve` · `--tunnel` (a temporary public HTTPS link, for your phone) · `--local-only` ·
`--since REF` · `--open`.

## If you build with an AI agent

This is where I have found it most useful, honestly. Asked *who writes this element?*, an
agent tends to grep, read a handful of files, and make a reasonable guess. On a big repo
that costs a lot of context and is still a guess.

And there is a failure I have run into more than once: asked to fix something on screen, an
agent will sometimes add a **second** place that writes the same element rather than finding
the one already there. The symptom moves, the next session adds another, and it slowly gets
worse.

So there is an MCP server. The agent asks, and gets the answer with the exact lines.

```bash
pip install 'seamcheck[mcp]'
claude mcp add seamcheck -- seamcheck-mcp
```

| tool | what the agent gets |
|---|---|
| `seamcheck_check` | every finding, with counts and what is new since the last scan |
| `seamcheck_explain` | one symbol: where it is, how it was reached, why it is classified so |
| `seamcheck_report` | the digest as markdown, to paste into a PR |
| `seamcheck_triage` | records "this one is fine, and here is why", so it stops being raised |

The server talks over stdin/stdout — no port, no daemon. Run it with the agent's working
directory set to the project root. **For a Django project it has to run inside that
project's virtualenv**, for the same reason the CLI does: it reads the project by importing
it. Every other backend is read from source, so anywhere works.

The tools are thin wrappers over the same functions the CLI runs. If the agent and your
terminal ever disagreed, neither would be worth trusting.

## In CI, if you want it there

```bash
seamcheck check --since $BASE_SHA
```

Exit 1 on new findings, 2 if there is no baseline yet, 0 when clean. It also writes a
markdown digest you can post as a PR comment with `--format markdown`.

## What it cannot see

Routes built at runtime from variables. Elements a framework renders from components rather
than templates — React and Vue handlers are props, not listeners, and the trail stops at the
module boundary. Anything reached only by a string it cannot resolve.

In the data layer: **Firestore collections**, because Firestore has no schema and there is
nothing in your files that declares one — only the rules are checked. **Storage buckets**,
which are usually created in a dashboard rather than in the repo. And a Redis key whose name
is assembled from variables end to end.

It says `uncertain` in all of those cases instead of guessing. That is the whole discipline:
`uncertain` is a real answer, and it is why the other three can be trusted.

## Contributing

Issues and pull requests welcome, especially "it got this wrong and here is the file".

## License

MIT. Take it, fork it, improve it.

---

<sub>A note on the copy: the wording on this page was drafted with an LLM, and partly *for*
them — realistically a coding agent is going to read this README before a person does, and
decide whether to install it. So it is written to be easy to parse as well as to read. The
tool itself does the opposite: it will not tell you anything without showing you the line it
came from.</sub>
