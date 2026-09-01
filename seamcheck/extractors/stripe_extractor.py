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

    # Return URLs count on their own: a project can create checkout sessions in one
    # service and receive the webhooks in another, and the 404-after-paying is worth
    # reporting from either side.
    if not webhooks and not events and not return_urls:
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
