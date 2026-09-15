<!-- Design document, 2026-09-15. Decisions taken with the owner are marked DECIDED.
     Numbers marked measured come from seamcheck 0.15.0 (an editable install of this
     repository) run on 2026-09-15 against leanos-app and the 46-repository corpus.
     Numbers marked projected come from a runtime patch that was thrown away, not from
     committed code, and say so. -->

# Pages for every stack: entry sources, and depth for Next.js, Django, FastAPI and Express

## Executive summary

1. **The map's idea of a page is hardwired to Vite and Django.** `api.page_files()` takes
   its roots from `roots.discover_js_roots()`, which reads a Vite config and `{% static %}`
   tags and nothing else. On leanos-app (Next.js 16, React 19, TypeScript, Supabase) it
   returns **zero** pages, so all **6,268** symbols land in "Not reached from any page"
   (measured). The backend was read correctly the whole time: the Next.js adapter found
   all 27 routes at 0.95 confidence.
2. **DECIDED: a second adapter layer, for entries.** `seamcheck/entries/` mirrors
   `seamcheck/adapters/`. Each `EntrySource` detects with a confidence, several can match
   one repository, and each returns entries that carry their own key, title, address and
   evidence. Today's Django/Vite behaviour becomes one source and does not change.
3. **DECIDED: four frameworks in depth — Next.js, Django, FastAPI, Express.** Everything
   else is a separate spec later. The deferrals and the evidence behind each are in
   [Scope](#scope-decided).
4. **The data seam barely exists outside Django and Supabase.** No reader exists for
   Prisma, SQLAlchemy, Mongoose or Knex (measured: no mention in non-test source). That is
   where cal.com, dub and documenso make **5,471** Prisma client calls, where dispatch and
   open-webui declare their SQLAlchemy tables, where ghost queries through Knex, and where
   node-express-boilerplate keeps Mongoose models.
5. **DECIDED: "reached" is decided by file; what gets drawn stays bounded by seeds.** A
   symbol is on a page when its file is reached by an entry or the page walk reached it
   along an edge. Drawing still starts only from seeds, so the 82,000-node failure recorded
   in `mapdata.py` cannot return. This changes bucket counts on Django projects too.
6. **Four phases, each with its own implementation plan, corpus gate and before/after
   numbers.** Phase 1 alone takes leanos-app from 0 pages to 27 entries and "Everything
   else" from 870 symbols to 62 (projected).

---

## What is hardwired, measured

### The shape of the failure

| Scan | Config detected | Page roots | Symbols | On a page |
|---|---|---:|---:|---:|
| `leanos-app/` | `templates_root=public`, `static_root=public`, `css_source_root=app` | **0** | 6,268 | **0** |
| `leanos/` (the folder above it) | *nothing* | **0** | **256** | 0 |

The folder above the project is worse, and silently so: autoconfig, CSS discovery and the
Supabase reader look only at the folder the command ran in, while route detection searches
three levels down. The scan found the routes and lost 96% of everything else, with no
warning.

Buckets on `leanos-app/` today (measured):

| Bucket | Symbols | What is actually in it |
|---|---:|---|
| "Reached by the framework… routes, handlers, models, signal receivers" | 54 | 27 `url` + 27 `view`: every Next.js page and route handler |
| "JavaScript that no page's bundle imports" | 357 | `.tsx` 178, `.html` 130, `.ts` 45 — 62% TypeScript under a JavaScript label |
| "Stylesheet rules and design tokens…" | 279 | |
| "Template elements that no page's JavaScript selects" | 4,708 | 4,689 from 7 static HTML pages under `public/` |
| "Everything else" | 870 | `db_table_use` 489, `db_column_use` 307, `url_reference` 67 — the whole data layer |

### The code that decides it

| Where | What it assumes |
|---|---|
| `roots.py:119` `discover_js_roots` | roots are Vite entries plus scripts a Django template loads |
| `roots.py:93` `vite_entry_map` | a Vite entry is a `.js` file — `.ts`/`.tsx` entries are dropped even on Django+Vite |
| `autoconfig.py:385` `_js_entries` | the only fallback: every `*.js` file becomes its own "page"; `.ts`/`.tsx` never |
| `js_extractor.py:316` `_resolve_import` | anything not starting with `.` is `node_modules`, so `@/components/X` is never followed. The walk from `app/page.tsx` reaches **1** file; with `tsconfig` paths it reaches **83** (measured) |
| `pagenames.py` | a page's name comes from template → view → URL |
| `mapdata.py:90` `_SEED_KINDS` | a page survives only if it holds a DOM query or a fetch; a React page that talks through a data client or `<Link>` has none |
| `mapdata.py:315` `UNREACHED_GROUPS` | bucket wording in Django vocabulary, which `CLAUDE.md` already forbids ("never phrase a message in one framework's vocabulary") |
| `mapdata.py:369` + `map_html.py` `fillPages` | the count is written into the bucket's `where` **and** appended by the picker: "— 123 — 123 nodes" |
| `api.py:103` | a template is `*.html`; `.hbs`, `.ejs`, `.tpl` are never listed |

### Entry roots today, for the four frameworks in scope (measured)

"Entries" counts whatever `_js_roots` returns. The legacy fallback turns every loose `.js`
file into an entry, so a high number is not the same as good pages.

| Framework | Repo: entries today |
|---|---|
| Next.js | cal.com 31 · dub 7 · commerce **0** · documenso **0** · nextjs-subscription-payments **0** · platforms **0** |
| Django | misago 380 · wagtail 221 · pretix 154 · django-cms 126 · healthchecks 28 · djangoproject.com 12 · weblate 9 · inventree 7 · debug-toolbar 7 · readthedocs 5 · oscar 4 · wger 3 · netbox 2 · **sentry 2** (4,000+ frontend files) · paperless-ngx 1 · bookwyrm **0** · saleor **0** |
| FastAPI | dispatch 132 · open-webui 6 · full-stack-fastapi-template **0** · fastapi-realworld **0** · fastapi-realworld-example-app **0** |
| Express | ghost 400 · nodebb 203 · parse-server 192 · node-express-boilerplate 38 · n8n **0** · node-express-realworld-example-app **0** |

---

## Scope (DECIDED)

**In depth:** Next.js, Django, FastAPI, Express (Fastify rides along, since the Express
adapter already reads it). Corpus adapter counts: Django 17, Next.js 7, Express 5,
FastAPI 4.

**Deferred to separate specs**, each for a stated reason:

| Deferred | Why not now (measured where there is a number) |
|---|---|
| Flask | Same SQLAlchemy reader as FastAPI, so it inherits most of phase 2 almost for free — a small follow-up spec |
| NestJS | Its corpus repos use TypeORM (nestjs-realworld 10 files, nest 10) and Kysely (immich 236), not the readers chosen here |
| SvelteKit | `.svelte` script blocks are not parsed at all; immich (3 `+page.svelte`) and open-webui (12) wait for that |
| Nuxt / Vue Router | `.vue` is not parsed; n8n's frontend (31 `vue-router` files) and dispatch's (`src/dispatch/static/dispatch`: Vite, Vue, Vuetify) wait for that |
| Angular | paperless-ngx only |
| Drizzle | **0** corpus repositories |
| Kysely, TypeORM, Sequelize | NestJS-side, see above |
| Pug | not HTML syntax; the template scanner reads attributes |
| FastAPI `Jinja2Templates` | **0** of 5 FastAPI corpus repositories use it |
| aiosql / raw asyncpg | the two FastAPI RealWorld apps (15 files each) keep queries as named SQL; stays `uncertain` |
| Rails, Laravel, Spring, ASP.NET | new backends, per `docs/specs/roadmap.md` |

The entry layer is generic, so every deferred item arrives later as one more source or one
more reader, not as a redesign.

---

## 1. The entry layer

### Interface

```python
# seamcheck/entries/base.py
@dataclass(frozen=True)
class Entry:
    key: str                  # unique, stable: "next:/pricing/[locale]", "server:app/api/checkout/route.ts"
    kind: str                 # "page" | "server" | "static_page" | "screen" | "script" | "entry_file"
    roots: tuple[str, ...]    # repo-relative files the import walk starts from
    title: str                # "Pricing"
    where: str                # "/pricing/[locale] - app/pricing/[locale]/page.tsx"
    group: str = ""           # entries sharing a group are sections of one page
    evidence: str = ""        # "filesystem route", "<script type=module> in index.html"
    note: str = ""            # shown in the sheet: "by convention, not declared"

@runtime_checkable
class EntrySource(Protocol):
    name: str
    def detect(self, repo_root: str, config: dict) -> float: ...
    def entries(self, repo_root: str, config: dict, graph: Graph) -> list[Entry]: ...
```

`entries()` receives the scanned graph because two sources need it: server entries read
`view` symbols, and route-linked titles read `url` symbols. A source never raises. One that
finds nothing returns `[]`, the same contract as `ServerAdapter.scan`.

### Selection

`entries.select_all(repo_root, config)` works like `adapters.select_all`: every source at or
above 0.5 confidence runs, and a monorepo gets all of them (leanos-app matches Next.js and
static HTML at once). The config key `entry_sources: ["nextjs", "static_html"]` forces a
choice the way `server_adapter` does, and `seamcheck config` shows which sources ran and
why.

### Keys, and what existing callers see

- **`api.page_files(repo_root) -> dict[str, set[str]]` keeps its signature.** It becomes a
  thin wrapper: `{entry.key: reached_files(entry)}`. `changescope.pages_touched`,
  `scoped_findings` and `scoped_map_document` do not change.
- **New `api.page_entries(repo_root) -> list[Entry]`** is the richer form for `build_map`.
- **`pagenames.page_names()` is called only by the legacy source.** Titles travel on
  entries.
- **Keys replace filename stems.** Today leanos-app's 19 `page.tsx` files would all collide
  under the key `"page"`.
- **`map_html._grouped` groups by `entry.group` when it is set**, and falls back to
  title + address as it does now.

### The legacy source (DECIDED: unchanged)

`LegacySource` returns exactly today's roots, in today's order, with today's filename-stem
keys and `pagenames` titles: Vite entries, template-loaded scripts, a configured
`js_entry_files`, and autoconfig's `.js` sweep. It detects at 0.5 whenever any of those
produce a root, so Django and Vite projects keep their current page lists in phase 1. A
test pins it against `discover_js_roots` on the existing fixtures.

**One deliberate exception.** Autoconfig's `.js` sweep was itself a fallback for a project
with "no bundler to ask" (its own docstring). Its entries are dropped when a
**page-producing** source also matched: Next.js, an SPA, static HTML, a bundler config.
Server entries do not count as page-producing, so an Express or FastAPI repository keeps
its sweep. In phase 1 the only page-producing new source is Next.js, so the only page lists
that change are Next.js repositories', whose sweep entries were loose files rather than
pages. Vite entries, template scripts and an explicit `js_entry_files` are never dropped.

### The fallback

It runs **only when every source returns nothing.** Roots are first-party script files that
no other first-party file imports. They get `kind="entry_file"`, the address "no framework
says this is a page", and the filename as title. No invented names — the same rule
`pagenames.py` already follows for a root no template loads.

---

## 2. What an entry reaches

### 2a. One import resolver

A new `seamcheck/resolve.py` replaces `_resolve_import` for every walk: the JS extractor,
the DOM extractor's file set, and the Express adapter's mount lookup. Resolution order:

1. **Relative paths**, as today.
2. **`tsconfig.json` / `jsconfig.json` `paths` and `baseUrl`**, from the nearest config,
   following `extends`. Present in 13 of the 15 modern frontend repositories surveyed.
3. **Workspace packages**: `@scope/ui` resolves to that package's `exports`, `source`,
   `main`, or `src/index.*`. Reuses the workspace globs `services.py` already parses.
   Workspaces are declared in 11 of the same 15.
4. **Non-script imports** (`import "./globals.css"`, CSS Modules, JSON) are recorded as
   **assets** the entry reaches, and not walked.
5. Anything left is a real package and stays third-party.

**Performance:** the module import graph is built once per scan from the parse cache, and
each entry's reach is a closure over that graph. `page_files` currently re-walks per root,
which its own docstring prices at about 13 s on the reference project.

### 2b. Server entries, from any adapter

Every distinct file that holds a `view` symbol becomes a `kind="server"` entry, titled by
the route or routes it serves. The Next.js, Express and FastAPI adapters set `file` on
their view symbols (read in their source). Django views carry it wherever
`pagenames.urls_by_template` already depends on it; the phase-1 plan confirms that in both
import and static mode. One source, not four:

- **JS/TS handlers** get an import walk: `route.ts → lib/stripe.ts → lib/supabaseAdmin.ts`.
- **Python handlers** keep the existing call-graph and store linking (`callgraph.py`,
  `storelink.py`).

Server entries appear in their own picker group, below pages.

### 2c. Membership by file, drawing by seed (DECIDED)

- **Bucket membership:** a symbol is *on a page* when its file, or an asset file, is reached
  by some entry, **or** the page walk reached it along an edge.
- **Drawing** still starts from seeds and stops at `_MAX_HOPS`. The seed set widens to every
  "touch" kind: `db_table_use`, `db_column_use`, `db_function_use`, `url_reference`,
  `env_read`, `stripe_event`, `stripe_webhook`, `storage_bucket`, `firestore_collection`,
  `graphql_selection`, `job_enqueue`, `redis_key_use`, `edge_function_use`, and JSX
  `dom_attr`. `class:apply` is still never drawn, but no longer counts as unreached:
  176 of the "unreached" DOM symbols in leanos-app files that routes do reach are exactly
  these and `class:string` (measured).
- **Consequence, accepted:** bucket counts on Django projects change, while their page
  contents do not. The corpus comparison records both.
- **An entry is never dropped for having nothing drawable.** `build_map` drops a page with
  a single node today. An entry stays in the picker, and its canvas says how many symbols
  it reaches by file. A page that silently vanishes is the failure this spec exists to end.

### 2d. Handler → store for TypeScript and JavaScript

A new linker joins a JS/TS handler to the store uses its imports make, the way `storelink.py`
does for Python:

- **Named imports narrow to owners.** `import { stripe } from "@/lib/stripe"` links only uses
  whose `Symbol.owner` is `stripe` or a function it calls within that module.
- **Default and namespace imports** link at module level. Those edges carry the note
  *"reached through an import, not a traced call"*.
- Like every store link, these edges are **evidence only**. No status changes because of them.

### Projected on leanos-app (runtime patch, thrown away)

| | Today | Aliases + wider seeds + Next.js entries |
|---|---:|---:|
| Pages and server entries drawn | 0 | 27 of 28 (28 included `proxy.ts`) |
| "Everything else" | 870 | 62 |
| Symbols whose file no entry reaches (projection without the asset rule) | 6,268 | 5,175 |

The 5,175 break down as measured: 5,041 symbols in the 7 static HTML pages under `public/`
(phase 3), 60 in `app/*.css` (phase 1's asset rule), 18 in `scripts/*.mjs` (phase 4), and
56 that are genuinely off every page — three components nothing imports (`Dashboard`,
`GlobalPanel`, `ProjectPortfolio`) and `next.config.ts`.

---

## 3. Depth, per framework

### Next.js

| Adds | Evidence | Phase |
|---|---|---|
| **Next.js source.** App Router: every `page.*`, rooted with its `layout.*`/`template.*` chain. Pages Router: `pages/**` except `_app`, `_document`, `api/`. URLs from `nextjs_adapter._url_from`, so route groups, `@slot` and `_private` are handled once. Keys qualified by app when a monorepo holds more than one, as the adapter's `url` ids already are | leanos-app 19 `page.tsx`; 4 of 6 corpus Next.js repos get zero roots today | 1 |
| **Prisma reader.** All `.prisma` files (a schema folder counts). `model User` → `db_table`, named by `@@map` when present; fields → `db_column`, named by `@map`. Uses: `<client>.<model>.<method>({...})`, where the accessor must match a declared model — that match is the guard, as `_looks_like_a_url` is for HTTP calls. `where`/`select`/`data`/`orderBy` keys become `db_column_use`, one level deep; relations are not followed, like the Django ORM reader. `$queryRaw` stays unread | Prisma client calls: dub 2,167 · cal.com 1,749 · documenso 1,555. `schema.prisma` files: dub 37, cal.com 2, documenso 1 | 2 |
| **Server Actions.** A `'use server'` module or function is a `server_action` symbol and a server entry. A client module importing and calling it is the seam, `connected`. An action nothing in the repository imports is `uncertain`, never `unused` — a form can post to it from a package this scan does not hold | `'use server'` files: dub 134, cal.com 12 | 4 |
| **`proxy.ts` / `middleware.ts`** as a server entry, with its `config.matcher` recorded on the symbol. `instrumentation.ts`, `opengraph-image`, `sitemap`, `robots` as framework-invoked entries | leanos-app `proxy.ts` matcher `/`; `docs/seamcheck-findings-from-leanos.md` D1 | 4 |
| **`next/font` variables.** `Geist({ variable: "--font-geist-sans" })` defines that CSS token | leanos-app's 2 `unresolved` `--font-geist-*` tokens, named in the leanos findings file | 4 |

### Django

| Adds | Evidence | Phase |
|---|---|---|
| Legacy source, unchanged roots; file-level membership; framework-aware bucket wording | — | 1–2 |
| **Vite TypeScript entries.** `vite_entry_map` stops dropping `.ts`/`.tsx` entries. This deliberately changes some Django+Vite maps, so it lands in phase 3, measured, not in phase 1 | `roots.py:93` | 3 |
| **webpack/rspack literal `entry` objects** as a source. Values are module specifiers resolved through `resolve.alias`/`resolve.modules`. A computed entry (a spread, a function, a glob) gets a note, never a guess | sentry `rspack.config.ts` `entry: { app: [...], gsAdmin: [...] }` — literal. wagtail `{...entry, ...sassEntry}` and misago `getEntries()` — computed | 3 |
| **Template bundle linking.** A template tag's string argument links that template (the page) to a bundle when it matches the bundler's `output.filename` pattern with `[name]` set to an entry name. When that pattern is not a readable literal, an argument whose file stem equals an entry name links it with the note "matched by name". This generalises today's `{% vite_asset %}` read | sentry: `frontend_app_asset_url "sentry" "entrypoints/app.js"` ×2 for entry `app`; the tag's signature is `(module, path)`, so the first argument names a module, not a bundle | 3 |

### FastAPI

| Adds | Evidence | Phase |
|---|---|---|
| Server entries from its view files; Python store linking already applies, and the phase-1 plan tests it on the `fastapi-sqlalchemy` fixture | — | 1 |
| **SQLAlchemy / SQLModel reader.** Declarations: `__tablename__`, `Table("x", metadata, Column(...))`, `mapped_column`/`Column` attributes, and SQLModel classes with `table=True` — including through a base class, as in `User(UserBase, table=True)`. Uses: `session.query(Model)`, `select(Model)`, `session.get(Model, …)`, and `Model.column` inside `filter`/`where`/`order_by`. Relations not followed. Emits the Django ORM reader's kinds and id shapes (`db_table:{table}`), so the store band and buckets need nothing new | dispatch: `session.query` 85 files, `Table()` 21, `__tablename__` 14. open-webui: `select()` 29, `__tablename__` 28. full-stack-fastapi-template: `User(UserBase, table=True)`, `Item(ItemBase, table=True)`, `select()` 6 | 2 |
| **Alembic migrations as the schema oracle.** `op.create_table("x", sa.Column("c", …))` and `op.add_column` are declarations, which gives these projects a `schema in repo` lane | Alembic files: dispatch 162, open-webui 61, full-stack-fastapi-template 6 | 2 |
| **`StaticFiles` mounts.** `app.mount("/x", StaticFiles(directory=…, html=True))` gives the static HTML source its URL prefix, and marks a built SPA as served | dispatch, open-webui | 3 |
| The SPA frontends these repos pair with come from the shared sources in section 4 | full-stack-fastapi-template: Vite + TanStack Router. dispatch (Vue) and open-webui (SvelteKit) are deferred | 3 |

### Express (and Fastify)

| Adds | Evidence | Phase |
|---|---|---|
| Server entries from its view files; mount chains are already read by the adapter | — | 1 |
| **Prisma reader**, shared with Next.js | node-express-realworld-example-app: 1 `schema.prisma` | 2 |
| **Mongoose reader.** `mongoose.model("User", schema)` declares a collection, named by Mongoose's own pluralisation or an explicit collection argument. Schema paths are its fields. Uses: `User.find/findOne/create/updateOne/…` with object keys as field uses. The guard is that the receiver is a binding of `mongoose.model`. A field outside the schema is the same quiet bug as a PostgREST column: strict mode drops it, nothing raises | node-express-boilerplate: `mongoose.model('User', userSchema)`, `mongoose.model('Token', tokenSchema)`, 9 files importing mongoose; nest: 15 | 2 |
| **Knex reader.** Uses: `knex("posts")`, `.from/.table/.into("posts")`, Bookshelf `tableName: "posts"`. Declarations: migration `schema.createTable("posts", t => t.string("title"))`, and a schema module that is a plain object literal of table → column → spec whose keys the same package passes to `createTable`. Any other schema shape stays `uncertain` | ghost: Knex 83 files, Bookshelf 23. Its schema is `data/schema/schema.js`, a plain `module.exports = { newsletters: { id: { type: 'string', … } } }`, consumed by `schema/commands.js` | 2 |
| **Server-rendered views.** `res.render("post")` with `app.set("views", …)` and `app.set("view engine", …)` makes a page entry: the route that renders a view names the page, as `pagenames.urls_by_template` does for Django. The template listing grows from `*.html` to `.hbs`, `.handlebars`, `.ejs`, `.tpl`. Handlebars `{{ }}` already scans correctly (roadmap measurement). EJS gets the `<%= %>` interpolation pair the roadmap names as one regex | `.hbs`: ghost 287, parse-server 4. `.tpl`: nodebb 268. `.handlebars`: n8n 12 | 3 |
| **`express.static(dir)`** under its mount prefix gives the static HTML source a served folder | ghost, nodebb, n8n, parse-server | 3 |

---

## 4. Frontend sources shared by all four

These read the client, so they apply whatever backend a repository has — including none
(saleor-dashboard, excalidraw).

| Source | Detected by | Roots | Title / address | Phase |
|---|---|---|---|---|
| **Vite / CRA SPA** | 1. `<script type="module" src>` in an `index.html`. 2. `vite-plugin-html` `createHtmlPlugin({ entry })`. 3. `build.rollupOptions.input`. 4. Convention only: `src/main.*` or `src/index.*`, with the note "by convention, not declared" | that script | `<title>`, `/` | 3 |
| **React Router** | `<Route>`, `createBrowserRouter`, route objects | each route's component | a literal `path`. A computed path shows as "path from `customerListPath`" — never evaluated, never guessed | 3 |
| **TanStack Router** | `routeTree.gen.ts` or `src/routes/` file routes | each route file | from the file path | 3 |
| **React Router v7 config** | `app/routes.ts` | each route module | from the config | 3 |
| **Static HTML** | `.html` under a served folder — `public/` (Next.js), `express.static`, a `StaticFiles` mount — that is not a server template | the file plus its local `<script src>` and `<link>` | `<title>`; the path under the served folder | 3 |
| **Screens inside a page** | ≥3 JSX branches guarded by the same variable compared to string literals (`{selected === "KPIs" && <Kpis/>}`, or a `switch`) in one page component | each guarded component's imports | the compared string | 3 |

Measured anchors: `index.html` module scripts in n8n (`/src/main.ts`), excalidraw-app
(`index.tsx`), full-stack-fastapi-template (`./src/main.tsx`) and ghost `apps/admin`
(`/src/main.tsx`); saleor-dashboard declares its entry only through
`createHtmlPlugin({ entry: "src/index.tsx" })`; React Router route files in
saleor-dashboard (32) and sentry (5); `app/routes.ts` in documenso and redash.

**Screens (DECIDED).** A page with screens gets sections named by the compared string, plus
a **Shell** section for the components rendered unguarded (`Sidebar`, `MobileChrome`,
`PageHeader` on leanos-app). **Whole page** stays the union. The ≥3 threshold keeps a modal
toggle from reading as a set of screens. This is what splits leanos-app's `/`, which draws
1,493 nodes as one page (projected).

**Unlinked static pages (DECIDED)** stay pages, with the note "nothing in this repository
links here". leanos-app's 7 `public/` pages are exactly this: real, deliberate, and linked
from nothing in the app.

---

## 5. What the map shows

### Bucket wording from what the scan detected (DECIDED)

The title stays "Not reached from any page". Each blurb is built from `LAST_ADAPTERS`, the
entry sources that ran, and the languages actually present in the bucket:

| Bucket | Wording |
|---|---|
| Server | per detected backend, joined when there are several. Django: "URLs, views, admin actions, signal receivers". Next.js: "route handlers and pages no entry reaches". Express: "routes and middleware". FastAPI: "routes and their handlers" |
| Scripts | from the languages in the bucket: "TypeScript and JavaScript no entry imports" |
| Styles | unchanged |
| Markup | "Template elements…" when server templates exist; "HTML elements…" otherwise |
| **Data (new)** | `db_*`, `redis_*`, `firestore_*`, `storage_bucket`, `edge_function*`, `cloud_function*`: "Queries, tables and keys no entry reaches". The data layer leaves "Everything else" |

`UNREACHED_GROUPS` becomes a function of the detected set. Bucket page keys stay
`unreached:<key>`, since the renderer keys off that prefix (`map_html.py:6322`).

### Picker and sheet

- **Count shown once.** The bucket's `where` drops its `— {count}`; the picker already
  appends it. `UnreachedTests.test_the_buckets_say_why_rather_than_naming_a_verdict` is
  updated to assert the count appears exactly once in the rendered option.
- **Order:** pages → server entries → static pages → not-reached buckets.
- **Sections** list screens by name, where a page has them.
- **The node sheet** shows an entry's `evidence` and `note`.
- `node --check` on every emitted `<script>` block after each `map_html.py` edit, per
  `CLAUDE.md`.

---

## 6. Running from the folder above a project (DECIDED: descend)

A "project roots" step runs before autoconfig. When the folder the command ran in has no
project marker of its own — no `package.json`, `pyproject.toml`, `setup.py`, `manage.py`,
`go.mod`, `Gemfile`, `composer.json`, or `.git` — but folders up to two levels below do,
each becomes a scan root:

- autoconfig, CSS discovery and every data-layer reader run per root;
- file ids keep their `leanos-app/` prefix, so links and page membership stay stable;
- one line is printed: *"scanning leanos-app/ — the folder you ran in holds no project of
  its own"*;
- several roots become services through `services.py`, so a wrapper holding a frontend and
  an API reads as two services.

Gate: `seamcheck map` run from `leanos/` finds the same 6,268 symbols as run from
`leanos-app/`.

---

## 7. Errors and the claim boundary

- **No source or reader raises.** Failures are reported once per subject through
  `nodetools.report`, and the scan continues.
- **A computed value is a note, never a guess:** a computed route path, a computed bundler
  entry, a dynamic table name.
- **Convention-only evidence says so** on the entry (`note`), and never becomes a finding.
- **New readers emit `uncertain` wherever the oracle is missing.** No schema means no
  `unresolved` table claims, as `supabase_extractor` already does.
- **Django stays optional:** no module-level `import django` anywhere under `entries/` or in
  the new readers.
- **Python floor 3.10:** no `X | Y` in `isinstance`, no `datetime.UTC`.

---

## 8. Verification

**Per source and reader, a tiny fixture repository** under
`seamcheck/tests/fixtures/entries/`:

| Fixture | Exercises |
|---|---|
| `nextjs-app` | App Router, a route group, `@/` alias, a workspace package, `proxy.ts`, a `'use server'` action, `schema.prisma` with `@@map` |
| `vite-spa-html` | `index.html` module script |
| `vite-plugin-html` | entry declared only through the plugin |
| `react-router-computed` | literal and computed `path` |
| `tanstack-routes` | `src/routes/` file routes |
| `static-site` | `public/*.html` with a local script and stylesheet |
| `one-pager-screens` | ≥3 guarded screens, a Shell, a 2-branch modal that must not become screens |
| `wrapper-folder` | a marker-less folder holding one project |
| `django-webpack` | literal and computed `entry`, `output.filename` with `[name]`, a template naming the output path |
| `express-views` | `res.render` + `.hbs`, `express.static`, a Mongoose model, a Knex migration, an object-literal schema module |
| `fastapi-sqlalchemy` | declarative models, SQLModel `table=True` through a base class, Alembic `create_table`, a `StaticFiles` mount, a handler whose helper queries a table |

**Legacy guard:** `LegacySource` equals `discover_js_roots` on the existing fixtures;
`test_roots.py` and `test_pagenames.py` stay green, unmodified.

**Gates, every phase** (from `CLAUDE.md`):

```bash
ruff check seamcheck/
python -m seamcheck.cli check     # never crashes, never reports unresolved against itself
python tools/corpus.py scan       # no CRASH, no new NO ROUTES, uncertain share not up
./build_parsers.sh && git diff --exit-code -- seamcheck/js_tools seamcheck/css_tools
```

**New corpus columns:** entries found, % of symbols on a page, data uses read. Before and
after numbers go in each commit message.

**leanos-app:** precision through `tools/precision.py` against `tools/labels/leanos-app.json`
must not fall; the map is built and driven in a browser.

**Corpus gap:** the corpus holds one Express repository with a React SPA (ghost `apps/admin`)
and one FastAPI repository with one (full-stack-fastapi-template; dispatch's frontend is
Vue, which is deferred). Phase 3 adds 2–3 Express + SPA and 1–2 FastAPI + SPA full-stack
repositories, chosen by `corpus.py`'s own both-sides-of-a-seam rule, before its gate is
read.

---

## 9. Phases

Each phase is its own implementation plan, written after the previous one's gate is read.

### Phase 1 — Entries, and Next.js pages

The entry layer, legacy source and fallback; the import resolver; server entries for all
four backends; membership by file and wider seeds; the Next.js source; the count shown once.

| Done when | |
|---|---|
| leanos-app | all 19 pages and 8 route handlers are entries in the picker; "Everything else" ≤ 62 (projected 62) |
| Next.js corpus | commerce, documenso, nextjs-subscription-payments and platforms each get App Router entries |
| Django / FastAPI / Express corpus | legacy page lists identical; bucket-count changes recorded |

### Phase 2 — The data seam, all four

Prisma, SQLAlchemy/SQLModel + Alembic, Mongoose, Knex/Bookshelf; the TS/JS handler → store
linker; the Data bucket; framework-aware wording.

| Done when | |
|---|---|
| leanos-app | fetch → route → handler → table chains drawn, covering the 25 Supabase table and column uses under `app/api/` (measured count) |
| Corpus | Prisma uses read on cal.com, dub, documenso; tables on dispatch, open-webui and full-stack-fastapi-template; Knex tables on ghost; Mongoose on node-express-boilerplate; uncertain share not up |

### Phase 3 — Frontend shapes and server-rendered views

Vite/CRA SPA, React Router, TanStack, React Router v7, webpack/rspack entries and template
bundle linking, Vite TS entries, static HTML, `StaticFiles` and `express.static` prefixes,
Express views and the template listing, screens, the wrapper-folder descent, the corpus
additions.

| Done when | |
|---|---|
| leanos-app | 7 static pages; `/` split into named screens and a Shell; run from `leanos/` finds 6,268 symbols |
| Corpus | SPA entries on full-stack-fastapi-template, saleor-dashboard, excalidraw, ghost `apps/admin`; sentry's `app` entry linked to the templates that load `entrypoints/app.js`; page entries on ghost's `.hbs` and nodebb's `.tpl` views |

### Phase 4 — Framework-invoked code

Server Actions; `proxy.ts`/`middleware.ts` with matchers; `instrumentation`, metadata
routes; `package.json` scripts as `kind="script"` entries; `next/font` tokens.

| Done when | |
|---|---|
| leanos-app | the 2 `--font-geist-*` tokens `connected`; `proxy.ts` a server entry with matcher `/`; symbols off every entry ≤ 56 (the three unimported components and `next.config.ts`) |
| Corpus | Server Actions read on dub and cal.com |

---

## 10. Files this touches

| File | Change |
|---|---|
| `seamcheck/entries/` (new) | `base.py`, `__init__.py` (registry, `select_all`), `legacy.py`, `nextjs.py`, `server.py`, `spa.py`, `routers.py`, `static_html.py`, `screens.py`, `bundlers.py`, `fallback.py` |
| `seamcheck/resolve.py` (new) | aliases, workspaces, assets |
| `seamcheck/extractors/` (new) | `prisma_extractor.py`, `sqlalchemy_extractor.py`, `mongoose_extractor.py`, `knex_extractor.py`, `server_action_extractor.py` |
| `seamcheck/tslink.py` (new) | TS/JS handler → store edges |
| `seamcheck/projectroots.py` (new) | wrapper-folder descent |
| `seamcheck/api.py` | `page_files` wraps entries; `page_entries`; `_map_document` and `scoped_map_document` pass entries |
| `seamcheck/mapdata.py` | membership by file; seed kinds; entries never dropped; `UNREACHED_GROUPS` as a function; Data bucket; `where` without count |
| `seamcheck/renderers/map_html.py` | group by `entry.group`; picker order; sheet shows evidence/note |
| `seamcheck/extractors/js_extractor.py` | walks through `resolve.py`; module graph built once |
| `seamcheck/roots.py` | Vite TS entries (phase 3); otherwise called only by `LegacySource` |
| `seamcheck/pagenames.py` | called only by `LegacySource` |
| `seamcheck/pipeline.py` | registers the new readers and linker |
| `seamcheck/autoconfig.py` | runs per project root; `entry_sources` key; `.js` sweep dropped when a page-producing source matched |
| `tools/corpus.py` | the three new columns |
| `docs/the-map.md` | pages beyond templates, server entries, screens, the Data bucket |
