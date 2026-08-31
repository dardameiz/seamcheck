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

**Not urgent.** Knip covers dead JS/TS modules well and is free; it does not cover the seam,
and neither does anything else. The seam works today on any client that uses `fetch`.

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
