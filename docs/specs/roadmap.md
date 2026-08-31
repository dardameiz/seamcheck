# Roadmap — three axes, one idea

**Status:** plan. Nothing here is claimed publicly until the code earns it.

## The idea that unifies all of it

Every gap this tool exists to find has the same shape:

> **A name is written in one place. A handler lives in another. Something connects them at
> runtime, and no compiler ever checks that the two agree.**

`fetch('/api/orders')` and `path('api/orders/', ...)` is the version everyone has seen. But
it is not special — it is one transport among several, and the others are worse because they
are even less visible:

| the name | the handler | who connects them |
|---|---|---|
| `fetch('/api/x')` | a route | the browser |
| `{% url 'profile' %}` | a route | the template engine |
| `querySelector('#pay')` | an element | the DOM |
| `var(--accent)` | a custom property | the cascade |
| `'payment_intent.succeeded'` | an if-branch in a webhook | **Stripe** |
| `delay('send_email')` | a `@shared_task` | **Celery** |
| `publish('scores')` | a `subscribe('scores')` | **Redis** |
| `send({type:'push.submit'})` | a consumer method | **the WebSocket gateway** |
| `signal.send()` | a `@receiver` | **Django signals** |

Nine transports, one bug class. The tool already handles the first four. **The rest are the
ones its own output currently calls "a known blind spot".**

That is the whole roadmap: the same graph, more transports and more languages plugged into
it.

---

## Axis 1 — Server adapters (what serves a route)

Covered in `universal.md`. Short version: 96.7% of a real scan is already
framework-agnostic; the route reader is the one Django-specific component.

| adapter | detected by | cost | note |
|---|---|---|---|
| django | `manage.py`, `ROOT_URLCONF` | **done** | import mode and static mode both |
| fastapi | `@app.get(` decorators | small | same language, same `ast`, cheapest proof the seam is real |
| express | `app.get(` on an express import | small | acorn is already bundled |
| rails | `config/routes.rb` | medium | a DSL; `resources :books` expands by a known table |
| laravel | `routes/web.php` | medium | flat and greppable; Blade into the existing template scanner |

**FastAPI first.** It validates the `ServerAdapter` protocol without a new parser, which is
the only thing that needs proving.

---

## Axis 2 — Client adapters (what calls a route)

Today: vanilla JavaScript, read with acorn. Already framework-agnostic — a `fetch` is a
`fetch` whatever served the page.

| adapter | what it adds | cost |
|---|---|---|
| TypeScript | `.ts`/`.tsx` parsing; types on the response give a **stronger** field check than JS | medium |
| React / Vue | component graph, so a dead component is findable; JSX `className` | large |
| HTMX | `hx-get`/`hx-post` — **already read** by the URL reference extractor | done |

**Not urgent** was the earlier reading, and measurement has now moved it. Knip covers dead
JS/TS modules well and is free; it does not cover the seam, and neither does anything else.
But "the seam works on any client that uses `fetch`" is only true of clients we can *parse*.

### Measured: what the bundled parser actually reads

```
_JS_EXTENSIONS = ('.js', '.mjs', '.jsx')      <- no .ts, no .tsx
```

| file | discovered | parsed | |
|---|---|---|---|
| `plain.js` | ✅ | ✅ | works |
| `express.js` | ✅ | ✅ | **a Node backend parses today** |
| `react.jsx` | ✅ | ❌ | in the list, acorn core has no JSX |
| `types.ts` | ❌ | ❌ | not even discovered |
| `react.tsx` | ❌ | ❌ | neither |

**TypeScript coverage is zero.** Not discovered, and acorn cannot strip type annotations
even if it were. Since essentially every modern frontend is JS or TS, this is the single
largest gap in the tool — and it takes Next.js and NestJS with it, both TS by default.

**This is also how the silent-failure bug was found.** `.jsx` was discovered, failed to
parse, and was dropped by `if "ast" in record` with no `else`; the whole scan ran inside
`quiet()`, which disables WARNING logging. So a React project produced almost no JavaScript
symbols and still reported success. Fixed: parse failures now print to stderr regardless of
quiet, once per subject, naming the count and files.

### React is not a parser problem

Adding JSX parsing is necessary and nowhere near sufficient, because React does not use the
seams this tool reads:

| what we read | what React does |
|---|---|
| `class="cart"` in a template | `className={styles.cart}` — **computed**, not a literal |
| `querySelector('#cart')` | refs; React owns the DOM |
| a `.css` file with rules | CSS Modules, styled-components, Tailwind |

The `fetch` ↔ route seam survives React **untouched**. The DOM/CSS seam — 97% of the symbol
volume — does not. React's equivalents are real and equally valuable, but they are **new
extractors, not a new parser**: a component imported and never rendered, a `styles.foo` that
no CSS Module defines, a prop passed that the component does not accept.

### Revised client plan, in cost order

| step | what it unblocks | cost |
|---|---|---|
| 1. `acorn-jsx` | `.jsx`/`.tsx` syntax parses at all | small — pure-JS official plugin |
| 2. TS type-stripper (`sucrase`, pure JS) then acorn | **all of TypeScript**, incl. Next.js and NestJS | medium — **the gate on everything modern** |
| 3. Node route adapter | Express, Fastify; Next's filesystem routes need no parser | small, and step 3 is why step 1–2 pay |
| 4. React DOM extractors | component graph, CSS Modules, props | **a project, not a task** |

---

## Axis 1b — JavaScript backends

The cheapest adapter in the entire plan, because **the AST is already built**. An Express app
is plain JavaScript; we parse the file today and simply never look for `app.get('/x', h)`.

| framework | route shape | cost |
|---|---|---|
| Express / Fastify | `app.get('/api/orders/', handler)` | small — pattern-match an AST we have |
| **Next.js App Router** | the **filesystem**: `app/api/orders/route.ts` → `/api/orders` | **smallest of all — a directory walk, no parsing** |
| NestJS | `@Get('orders')` under `@Controller('api')` | medium — compose two decorators |

Next.js is the interesting one: its routes need no parser at all, so the adapter is trivial —
but its files are `.ts`, so it is **blocked entirely on the TypeScript gate above.** That
single dependency decides the order of the whole client axis.

---

## Axis 3 — Transport adapters (what reaches code with no HTTP request)

**This is the treasure, and it is the least contested ground in the whole plan.** Nothing
else looks at it, and every one of these is a real outage class.

### 3a. Stripe

Grounded in a real integration rather than invented. The shape found in a live codebase:

```python
event = stripe.Webhook.construct_event(payload, signature, secret)   # the entry point
event_type = event['type']
if   event_type == 'payment_intent.succeeded':      ...
elif event_type == 'payment_intent.payment_failed': ...
elif event_type == 'charge.refunded':               ...
elif event_type == 'charge.dispute.created':        ...
```

**What the extractor reads:** the view containing `construct_event` is the webhook route;
the dispatch chain on `event['type']` gives one symbol per handled event, each edged to the
function that handles it. Dict dispatch and `match` need the same treatment.

**What it makes findable, and none of it is findable today:**

| finding | why it matters |
|---|---|
| the webhook route is **not** unused | it is reached by Stripe, and today it is a false `unused` candidate |
| a **handler with no matching event** enabled | dead branch, will never run |
| an **event enabled in Stripe with no handler** | **money silently dropped** — the worst bug in the list |
| `success_url` / `cancel_url` pointing at a route that does not exist | a live 404 after payment |
| a `price`/`product` id in code that no longer exists in Stripe | checkout fails at runtime |

The last three need the Stripe API, not the source. That is a **`seamcheck stripe`**
enrichment step, exactly like `observe`: reads with a restricted key, merges as `observed`
provenance, never required. `success_url` is checkable statically today — it is a URL string,
and `UrlIndex` already resolves those.

**Do the static half first.** Webhook route + dispatch chain + `success_url` needs no
credentials and no network, and it removes a false-positive class immediately.

### 3b. Celery

```python
@shared_task
def send_receipt(...): ...

send_receipt.delay(...)          # or .apply_async(), or a name string
CELERY_BEAT_SCHEDULE = {"nightly": {"task": "app.tasks.send_receipt", ...}}
```

- **A task nothing calls and no beat entry schedules** — dead worker code.
- **A beat entry naming a task that does not exist** — the scheduler raises and the job never
  runs. This is the 14-day silent outage class, and it is statically detectable.
- **A `.delay()` on a name the worker does not register** — same.

### 3c. Redis pub/sub and keys

- `publish('channel')` against `subscribe('channel')` — a publisher with no subscriber, or a
  subscriber for a channel nobody publishes.
- Key-prefix families (`user:{id}:stats`) written in one module and read in another. Reuses
  the class-name **stem** machinery already built for CSS.

### 3d. WebSocket handlers

A gateway that dispatches on a message type is string dispatch again:

```javascript
socket.send(JSON.stringify({type: 'push.submit', ...}))   // the name
```
```python
async def push_submit(self, content): ...                  # the handler
```

- A client sending a type no consumer handles — silently ignored, no error anywhere.
- A consumer method no client ever sends — dead.

### 3e. Django signals and management commands

`@receiver(post_save, sender=Book)` against `Book.objects.create()`. Partly covered:
receivers are extracted, senders are not. Management commands are already extracted and
correctly reported as *expected* to have no in-project caller.

---

## What each axis does to the numbers

Measured on the reference project, current state: **2,077 uncertain, 4,105 findings.**

| work | effect |
|---|---|
| runtime probe (built) | closes the ~1,353 runtime-built uncertains — the statically unreachable floor |
| Stripe static | removes the webhook-route false positives; adds a handler/event check |
| Celery | resolves 65 `model` uncertains partly, plus a new dead-task class |
| Redis + WS | the last two entries in `BLIND_SPOTS` |
| transport adapters as a whole | **lets `BLIND_SPOTS` shrink for the first time** — the sentence in every `unused` note that currently reads "Celery, Redis, WebSockets and Stripe are not traced" |

That last row is the point. `BLIND_SPOTS` is quoted in the UI, the README and every finding's
explanation. Shrinking it is the most visible possible proof the tool got better.

---

## Order of work

1. **Live-run the runtime probe.** Built, unit-tested, never driven a real page.
2. **`ServerAdapter` seam**, Django as sole implementation. A refactor, guarded by 600 tests.
3. **Stripe, static half.** Highest value per line in the whole plan: it is the one transport
   where a miss means money, and the extractor is one `ast` walk over a dispatch chain.
4. **Celery.** Second highest, same technique, and it is the known outage class.
5. **FastAPI adapter.** Proves axis 1 cheaply.
6. **Reposition publicly** — only once 2, 3 and 5 are true.
7. **Rails or Laravel.** The convincing one.
8. **Redis + WebSocket.** Finishes `BLIND_SPOTS`.
9. **The corpus**, now multi-framework and multi-transport.
10. **The clickable page** (`page-capture.md`) — the human half, once the graph is worth
    pointing at.

**Steps 3 and 4 are the ones I would not reorder.** They are cheap, they are unclaimed by any
other tool, and a payment webhook silently dropping an event is a worse day than any amount
of dead CSS.

---

## The claim boundary, restated

Every one of these is written as a plan. None of it goes in the README, the PyPI description
or the GitHub topics until the code exists and has been measured on a real project. The tool's
one rule is that it never asserts more than its evidence; that rule binds its marketing too,
and the first person who tries an advertised adapter and gets nothing back is right never to
return.

---

## Measured: what a new backend actually costs

Two experiments, run rather than estimated.

**1. Does the frontend half carry over to another backend?** The template scanner was given
the same markup as Django, Twig, Blade (PHP), ERB (Ruby) and Handlebars:

| engine | ids | classes | data attrs |
|---|---|---|---|
| Django, Twig, Blade, ERB, Handlebars | ✅ | ✅ | ✅ |

**All five identical.** It reads HTML *attributes*, and an attribute is an attribute whatever
generated it. That is the 97% figure demonstrated rather than asserted.

**2. Does it know a class is dynamic?** `class="flag flag-{{ code }}"` must yield `flag` and
never the fragment `flag-`, or every such line is a false positive:

| engine | result | |
|---|---|---|
| Django, Twig, **Blade** | `['flag']` | ✅ correct |
| ERB (`<%= %>`) | `['code', 'flag', 'flag-<%=']` | ❌ two junk symbols |
| JSX (`className`) | `[]` | ❌ finds nothing |

So interpolation-awareness *is* per-engine, but it is a **regex pair, not an adapter**:
`<%= %>` and `className=` are one line each. **Blade already works** — Laravel copied
Django/Jinja's `{{ }}`, so PHP is the best-supported non-Django frontend by accident.

**3. Is a backend adapter optional?** No. With no route list, `match_js_to_django` marks
**100% of fetch targets `unresolved`** — not "unknown", actively wrong on every endpoint.
The route reader is mandatory for the static seam check.

**But `observe` needs no adapter at all.** A recorded `fetch` that returns 404 *is* the
finding, in any language, with nothing parsed. That makes the runtime half genuinely
framework-free today — a second reason to land the probe first.

### Revised per-backend cost

| backend | route reader | frontend | verdict |
|---|---|---|---|
| Laravel (PHP) | `Route::get('/x', [C::class,'m'])` — a **flat** list | Blade works today | **easier than Django** |
| Symfony (PHP) | `#[Route('/x')]` attributes — structured | Twig works today | small |
| FastAPI | decorators, same `ast` | n/a | smallest |
| Rails | `config/routes.rb` DSL, `resources` expands | +1 regex for ERB | medium |
| Express | `app.get()`, acorn already bundled | n/a | small |

Django's URLconf — a tree with `include()`, namespaces, converters and `urlpatterns +=` — is
**the hardest one in the table, and it is the one already done.**
