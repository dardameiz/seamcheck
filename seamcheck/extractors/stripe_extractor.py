"""Stripe: the webhook, the events it handles, and the URLs it sends people back to.

Stripe reaches into a codebase in a way no route reader sees. The webhook endpoint is
called by Stripe's servers, so nothing in the project references it and every dead-code
tool - including this one, before now - is entitled to call it unused. Then the handler
dispatches on a STRING that lives in a dashboard somewhere else entirely:

    event = stripe.Webhook.construct_event(payload, signature, secret)   the entry point
    if event['type'] == 'payment_intent.succeeded':   ...                a handled event
    elif event['type'] == 'charge.refunded':          ...

Three things are worth saying about that, and only the first two can be said from source:

  * The webhook view is reached BY STRIPE. Not unused, and now labelled as such.
  * These are the events this code handles. A human can compare that list against the
    dashboard in ten seconds; nobody can compare it against a codebase they have not read.
  * An event enabled in Stripe with no handler here is money silently dropped - and that
    needs the Stripe API, not the source. It is NOT claimed here.

`success_url` and `cancel_url` are ordinary URLs, so those go into the graph as references
like any other: a checkout that returns the customer to a route that no longer exists is a
404 at the worst possible moment.
"""

from __future__ import annotations

import ast
import os
import pathlib
import re

from seamcheck.graph import Edge, Status, Symbol

_SKIP = {
    "node_modules", ".git", "dist", "build", "__pycache__", "venv", ".venv",
    "site-packages", "migrations", "corpus", ".tox",
}

_WEBHOOK_NOTE = (
    "Reached by Stripe's servers, not by anything in this codebase. Nothing here "
    "references it and nothing should: that is what a webhook is. Never read a webhook "
    "endpoint's lack of callers as evidence it is dead."
)
_EVENT_NOTE = (
    "Handled here. This scan reads the source, so it can say which events this code "
    "handles and cannot say which events Stripe is configured to send. An event enabled "
    "in the dashboard with no branch here is money silently dropped, and checking that "
    "needs the Stripe API."
)


def _files(root: str) -> list[str]:
    found: list[str] = []
    for directory, subdirectories, names in os.walk(root):
        subdirectories[:] = [d for d in subdirectories
                             if d not in _SKIP and not d.startswith(".")]
        for name in sorted(names):
            path = os.path.join(directory, name)
            if name.endswith(".py") and not _is_test(path):
                found.append(path)
    return found


def _is_test(path: str) -> bool:
    name = os.path.basename(path)
    parts = pathlib.Path(path).parts
    return (name.startswith("test_") or name.endswith("_test.py")
            or "tests" in parts or "test" in parts)


def _string(node) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _is_event_type_read(node: ast.AST) -> bool:
    """Whether this expression reads the event's type: `event['type']` or `event.type`."""
    if isinstance(node, ast.Subscript):
        return _string(node.slice) == "type"
    if isinstance(node, ast.Attribute):
        return node.attr == "type"
    return False


def _type_variables(tree: ast.Module) -> set[str]:
    """Names bound to the event's type.

    Real handlers almost never compare `event['type']` inline. They write
    `event_type = event['type']` once and branch on the variable - which is better code
    and invisible to a reader that only looks for the subscript. The reference project
    does exactly this, and the first version of this extractor found nothing in it.
    """
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and _is_event_type_read(node.value):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
        elif (isinstance(node, ast.AnnAssign) and node.value is not None
                and _is_event_type_read(node.value) and isinstance(node.target, ast.Name)):
            names.add(node.target.id)
    return names


def _events_compared_to(node: ast.AST, variables: frozenset[str] = frozenset()) -> list[str]:
    """Event names this expression compares the event type against."""
    found: list[str] = []
    reads_type = (isinstance(node, ast.Compare)
                  and (_is_event_type_read(node.left)
                       or (isinstance(node.left, ast.Name) and node.left.id in variables)))
    if reads_type:
        for comparator in node.comparators:
            value = _string(comparator)
            if value:
                found.append(value)
            elif isinstance(comparator, (ast.List, ast.Tuple, ast.Set)):
                found += [v for v in (_string(e) for e in comparator.elts) if v]
    elif isinstance(node, ast.BoolOp):
        for value in node.values:
            found += _events_compared_to(value, variables)
    return found


def _enclosing_function(tree: ast.Module, target: ast.AST) -> str | None:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for inner in ast.walk(node):
                if inner is target:
                    return node.name
    return None


# ── the JavaScript side ─────────────────────────────────────────────────────
# 104 files in a 32-repository corpus import Stripe and nearly all of them are TypeScript,
# so a Python-only reader saw none of the money. The shapes are the same three:
#
#     stripe.webhooks.constructEvent(body, sig, secret)   the entry point
#     switch (event.type) { case 'checkout.session.completed': … }   handled events
#     stripe.checkout.sessions.create({...})              a call that CAUSES an event
_JS_EXTENSIONS = (".js", ".mjs", ".cjs", ".jsx", ".ts", ".mts", ".cts", ".tsx")
_JS_NEEDLES = ("stripe", "Stripe")

# An event name has to clear BOTH of these, and the first attempt had neither.
#
# Recognising an event by SHAPE alone - a dotted lowercase path - was measured on Ghost at
# roughly one true positive in eighteen: `users.id`, `gifts.id`, `post.authors` and every
# other column reference in the schema matched perfectly. So:
#
#   1. it must sit in a DISPATCH position - a `switch (event.type)` case, or a comparison
#      against something's `.type` - which is the same test the Python reader already
#      applies, and
#   2. it must begin with a resource Stripe actually emits events for.
#
# The prefix list is maintained by hand on purpose: it goes stale slowly, in the direction
# of missing a new event rather than inventing one, and that is the right way round.
_EVENT_NAME_RE = re.compile(r"\A[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_.]*){1,3}\Z")
_EVENT_PREFIXES = (
    "account.", "application_fee.", "balance.", "billing_portal.", "capability.",
    "cash_balance.", "charge.", "checkout.", "climate.", "coupon.", "credit_note.",
    "customer.", "customer_cash_balance_transaction.", "entitlements.", "file.",
    "financial_connections.", "identity.", "invoice.", "invoiceitem.", "issuing_",
    "mandate.", "order.", "payment_intent.", "payment_link.", "payment_method.",
    "payout.", "person.", "plan.", "price.", "product.", "promotion_code.", "quote.",
    "radar.", "refund.", "reporting.", "review.", "setup_intent.", "sigma.", "source.",
    "subscription_schedule.", "tax.", "tax_rate.", "terminal.", "test_helpers.",
    "topup.", "transfer.", "treasury.",
)


def _is_stripe_event_name(value: object) -> bool:
    return (
        isinstance(value, str)
        and value.startswith(_EVENT_PREFIXES)
        and bool(_EVENT_NAME_RE.match(value))
    )


def _reads_event_type(node: dict | None) -> bool:
    """Whether this expression reads `.type` off something - `event.type`, `evt.type`."""
    if not node:
        return False
    if node.get("type") == "MemberExpression":
        return (node.get("property") or {}).get("name") == "type"
    return False


def _dispatched_events(parsed: dict) -> list[tuple[str, int]]:
    """Event names this module BRANCHES on, with the line each was found at."""
    from seamcheck.extractors.js_extractor import _walk

    found: list[tuple[str, int]] = []

    def _collect(node: dict) -> None:
        if not isinstance(node, dict):
            return
        if node.get("type") == "Literal" and _is_stripe_event_name(node.get("value")):
            found.append((node["value"], _js_line(node)))

    for node, _ in _walk(parsed):
        node_type = node.get("type")
        if node_type == "SwitchStatement" and _reads_event_type(node.get("discriminant")):
            for case in node.get("cases") or []:
                _collect(case.get("test") or {})
        elif node_type == "BinaryExpression" and node.get("operator") in ("===", "==") and (
            _reads_event_type(node.get("left")) or _reads_event_type(node.get("right"))
        ):
            _collect(node.get("left") or {})
            _collect(node.get("right") or {})
        elif node_type == "ObjectExpression":
            # The handler-map form: `this.handlers = {'customer.subscription.deleted': fn}`.
            # Ghost dispatches this way and nothing else, so reading only switch and `===`
            # reported five events it handles perfectly well as never handled - five false
            # accusations about working payment code, which is the worst output this
            # module could produce.
            for prop in node.get("properties") or []:
                key = prop.get("key") or {}
                name = key.get("value") if key.get("type") == "Literal" else None
                if _is_stripe_event_name(name):
                    found.append((name, _js_line(key)))
        elif node_type == "CallExpression":
            # `relevantEvents.has('checkout.session.completed')` and
            # `[...].includes(event.type)` - the set-membership form, which three corpus
            # projects use instead of a switch.
            method = ((node.get("callee") or {}).get("property") or {}).get("name")
            if method in ("has", "includes") and _reads_event_type(
                (node.get("arguments") or [{}])[0]
            ):
                for element in ((node.get("callee") or {}).get("object") or {}).get(
                    "elements"
                ) or []:
                    _collect(element or {})
    return found

# What a call will make Stripe send back. This is the only inference in this module, and it
# is the one that pays for the rest: a project that creates checkout sessions and handles
# no `checkout.session.completed` is taking money and never recording it. Kept small and
# certain - one entry per call whose consequence is not in dispute.
_CAUSES = {
    "checkout.sessions.create": ("checkout.session.completed",),
    "subscriptions.create": ("customer.subscription.created",),
    "subscriptions.update": ("customer.subscription.updated",),
    "subscriptions.cancel": ("customer.subscription.deleted",),
    "subscriptions.del": ("customer.subscription.deleted",),
    "paymentIntents.create": ("payment_intent.succeeded",),
    "refunds.create": ("charge.refunded",),
    "invoices.create": ("invoice.created",),
}
_UNREADABLE_DISPATCH_NOTE = (
    "This code makes Stripe send this event, and no handler for ANY event could be found "
    "in this repository - so the dispatch is written in a form this reader does not "
    "recognise, rather than being absent. Worth checking by hand: if it really is "
    "unhandled, the payment succeeds and the work behind it never happens."
)
_UNHANDLED_NOTE = (
    "This code makes Stripe send this event and nothing here handles it. Stripe will "
    "deliver it, retry it, and eventually give up; the work behind it never happens. This "
    "is the failure that looks like nothing at all - the payment succeeds, the customer is "
    "charged, and the account is never updated."
)
# NOT reported: a webhook route that never verifies a signature. It is a real and serious
# finding - anyone who knows the URL could post a payment event - but it cannot be made
# from what this reader collects. A JavaScript webhook is DISCOVERED by finding
# constructEvent(), so every webhook it knows about is verified by construction and the
# check could never fire. Finding the unverified ones means finding webhook ROUTES first,
# from the route table, and asking whether each one verifies. Left undone and written down
# rather than shipped as a branch that always passes.


def _js_files(root: str) -> list[str]:
    found: list[str] = []
    for directory, subdirectories, names in os.walk(root):
        subdirectories[:] = [d for d in subdirectories
                             if d not in _SKIP and not d.startswith(".")]
        for name in sorted(names):
            if not name.endswith(_JS_EXTENSIONS) or name.endswith((".min.js", ".d.ts")):
                continue
            path = os.path.join(directory, name)
            if not _is_js_test(path):
                found.append(path)
    return found


def _is_js_test(path: str) -> bool:
    name = os.path.basename(path)
    parts = pathlib.Path(path).parts
    return (".test." in name or ".spec." in name
            or any(part in ("test", "tests", "__tests__", "e2e") for part in parts))


def _member_path(callee: dict) -> str:
    """`stripe.checkout.sessions.create` -> "checkout.sessions.create"."""
    parts: list[str] = []
    node = callee
    while node and node.get("type") == "MemberExpression":
        name = (node.get("property") or {}).get("name")
        if not name:
            return ""
        parts.append(name)
        node = node.get("object") or {}
    if node.get("type") == "Identifier":
        parts.append(node.get("name") or "")
    parts.reverse()
    # Drop whatever the client variable is called - `stripe`, `client`, `this.stripe`.
    return ".".join(parts[1:]) if len(parts) > 1 else ""


def _scan_js(root: str) -> tuple[list, dict, dict, set]:
    """(webhook sites, handled events, calls that cause events, files verifying signatures)."""
    from seamcheck.extractors.js_extractor import _parse_files, _walk

    candidates = []
    for path in _js_files(root):
        try:
            if os.path.getsize(path) > 400_000:
                continue
            text = pathlib.Path(path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if any(needle in text for needle in _JS_NEEDLES):
            candidates.append(path)

    webhooks: list[tuple[str, str, int]] = []
    events: dict[str, tuple[str, int]] = {}
    causes: dict[str, tuple[str, int, str]] = {}
    verified: set[str] = set()

    for path, parsed in _parse_files(candidates).items():
        relative = os.path.relpath(path, root)
        for name, line in _dispatched_events(parsed):
            events.setdefault(name, (relative, line))

        for node, enclosing in _walk(parsed):
            node_type = node.get("type")
            if node_type != "CallExpression":
                continue
            callee = node.get("callee") or {}
            if callee.get("type") != "MemberExpression":
                continue
            method = (callee.get("property") or {}).get("name") or ""
            path_name = _member_path(callee)

            if method == "constructEvent" or path_name.endswith("webhooks.constructEvent"):
                webhooks.append((enclosing or "webhook", relative, _js_line(node)))
                verified.add(relative)
                continue
            if path_name in _CAUSES:
                causes.setdefault(path_name, (relative, _js_line(node), path_name))
    return webhooks, events, causes, verified


def _js_line(node: dict) -> int:
    return ((node.get("loc") or {}).get("start") or {}).get("line") or 1


def extract_stripe(root: str) -> tuple[list[Symbol], list[Edge]]:
    """The webhook view, the events it dispatches on, and the return URLs it sets."""
    webhooks: list[tuple[str, str, int]] = []       # (function, file, line)
    events: dict[str, tuple[str, int]] = {}         # event name -> (file, line)
    return_urls: dict[str, tuple[str, int, str]] = {}   # url -> (file, line, kind)

    for path in _files(root):
        try:
            text = pathlib.Path(path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "stripe" not in text.lower():
            continue
        try:
            tree = ast.parse(text, filename=path)
        except (SyntaxError, ValueError):
            continue
        relative = os.path.relpath(path, root)
        variables = frozenset(_type_variables(tree))

        for node in ast.walk(tree):
            # `construct_event` is as often PASSED as called: an async view writes
            # `sync_to_async(stripe.Webhook.construct_event, ...)(...)`, where the name is
            # an argument and never appears as a call target at all.
            if isinstance(node, ast.Attribute) and node.attr == "construct_event":
                function = _enclosing_function(tree, node) or "<module>"
                webhooks.append((function, relative, node.lineno))
            if isinstance(node, ast.Call):
                for keyword in node.keywords:
                    if keyword.arg in ("success_url", "cancel_url", "return_url"):
                        url = _string(keyword.value)
                        if url:
                            return_urls.setdefault(url, (relative, node.lineno, keyword.arg))
            # `if event['type'] == '...'`, and every elif under it, which ast models as
            # nested If nodes - so walking every If covers the whole chain.
            if isinstance(node, ast.If):
                for event in _events_compared_to(node.test, variables):
                    events.setdefault(event, (relative, node.lineno))
            elif isinstance(node, ast.Match) and (
                    _is_event_type_read(node.subject)
                    or (isinstance(node.subject, ast.Name) and node.subject.id in variables)):
                for case in node.cases:
                    pattern = case.pattern
                    values = ([pattern] if isinstance(pattern, ast.MatchValue)
                              else getattr(pattern, "patterns", []))
                    for one in values:
                        value = _string(getattr(one, "value", None))
                        if value:
                            events.setdefault(value, (relative, node.lineno))
            elif isinstance(node, ast.Dict):
                # Dict dispatch: {'payment_intent.succeeded': handler, ...}
                keys = [_string(key) for key in node.keys]
                stripe_like = [k for k in keys if k and "." in k and k.islower()
                               and any(k.startswith(p) for p in
                                       ("payment_intent", "charge", "customer", "invoice",
                                        "checkout", "subscription", "payout", "setup_intent",
                                        "price", "product", "refund", "dispute"))]
                for key in stripe_like:
                    events.setdefault(key, (relative, node.lineno))

    # The JavaScript half. Merged rather than kept apart: a project's Stripe integration
    # is one integration even when the checkout is created in TypeScript and the webhook
    # is received in Python, and splitting it would report the events twice and pair them
    # with nothing.
    js_webhooks, js_events, causes, _verified = _scan_js(root)
    webhooks += js_webhooks
    for name, where in js_events.items():
        events.setdefault(name, where)

    # Return URLs count on their own: a project can create checkout sessions in one
    # service and receive the webhooks in another, and the 404-after-paying is worth
    # reporting from either side.
    if not webhooks and not events and not return_urls and not causes:
        return [], []

    symbols: list[Symbol] = []
    edges: list[Edge] = []
    for function, file, line in webhooks:
        symbols.append(Symbol(
            id=f"stripe_webhook:{file}:{function}", kind="stripe_webhook",
            label=function, sub="webhook", file=file, line=line,
            status=Status.CONNECTED, snippet="stripe.Webhook.construct_event(...)",
            chain=["Stripe", function], note=_WEBHOOK_NOTE,
        ))
    # Only where this repository actually TALKS to Stripe. A dict keyed by event names in
    # a project that neither receives a webhook nor calls the API is a lookup table, not a
    # dispatch - seamcheck's own source is the proof, since its `_CAUSES` constant made it
    # report itself as handling seven Stripe events.
    if not webhooks and not causes:
        events = {}
    for event, (file, line) in sorted(events.items()):
        symbol_id = f"stripe_event:{event}"
        symbols.append(Symbol(
            id=symbol_id, kind="stripe_event", label=event, sub="event",
            file=file, line=line, status=Status.CONNECTED,
            snippet=f"event['type'] == '{event}'", chain=["Stripe", event],
            note=_EVENT_NOTE,
        ))
        if webhooks:
            function, hook_file, _ = webhooks[0]
            edges.append(Edge(from_id=f"stripe_webhook:{hook_file}:{function}",
                              to_id=symbol_id, status=Status.CONNECTED))
    # The finding this module exists for: a call that MAKES Stripe send an event, with no
    # branch anywhere that handles it. Only claimed when a webhook receiver exists in this
    # repository - without one the events are plainly handled somewhere else, and saying
    # otherwise would be a guess about a service that is not here.
    #
    # And the status turns on ONE question: could this project's dispatch be read at all?
    # Four dispatch styles are understood - a switch, an `===`, a set membership test and
    # an object keyed by event name - and finding a fifth is a matter of time. Every style
    # missed turns a handled event into an accusation about working payment code, so where
    # NO handled event was found anywhere, the honest answer is that the dispatch could not
    # be read, not that the handler is absent. This is the Supabase lesson - a claim built
    # out of not finding something - applied before it ships rather than after.
    if webhooks:
        readable = bool(events)
        for call, (file, line, _name) in sorted(causes.items()):
            for event in _CAUSES[call]:
                if event in events:
                    continue
                symbols.append(Symbol(
                    id=f"stripe_event_unhandled:{event}", kind="stripe_event",
                    label=event,
                    sub="never handled" if readable else "handler not found",
                    file=file, line=line,
                    status=Status.UNRESOLVED if readable else Status.UNCERTAIN,
                    snippet=f"stripe.{call}(...)", chain=["Stripe", event],
                    note=_UNHANDLED_NOTE if readable else _UNREADABLE_DISPATCH_NOTE,
                ))

    for url, (file, line, kind) in sorted(return_urls.items()):
        # A URL like any other: the URL reference matcher resolves it against the routes,
        # and a checkout that returns a customer to a route that no longer exists is a 404
        # at the worst possible moment.
        symbols.append(Symbol(
            id=f"url_reference:{url}", kind="url_reference", label=url, sub=kind,
            file=file, line=line, status=Status.UNCERTAIN,
            snippet=f"{kind}='{url}'", chain=[kind, url],
            note="A Stripe checkout return URL.",
        ))
    return symbols, edges
