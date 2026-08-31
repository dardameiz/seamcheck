"""Stripe: the endpoint whose lack of callers is the design.

A webhook is called by Stripe's servers. Nothing in the project references it and nothing
should, so every dead-code tool - including this one, before now - is entitled to call it
unused. Then it dispatches on a STRING that lives in a dashboard somewhere else.

The boundary matters as much as the finding. This reads source, so it can say which events
the code handles and cannot say which events Stripe is configured to send. An event enabled
in the dashboard with no branch here is money silently dropped, and that needs the API.
Claiming it from source would be the tool inventing evidence.
"""

from __future__ import annotations

import pathlib
import tempfile
import unittest

from seamcheck.extractors.stripe_extractor import extract_stripe


def _repo(files: dict[str, str]) -> str:
    root = pathlib.Path(tempfile.mkdtemp())
    for name, body in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    return str(root)


def _events(root: str) -> set[str]:
    symbols, _ = extract_stripe(root)
    return {s.label for s in symbols if s.kind == "stripe_event"}


def _webhooks(root: str) -> list:
    symbols, _ = extract_stripe(root)
    return [s for s in symbols if s.kind == "stripe_webhook"]


INLINE = """
import stripe

def webhook(request):
    event = stripe.Webhook.construct_event(request.body, sig, secret)
    if event['type'] == 'payment_intent.succeeded':
        pass
    elif event['type'] == 'charge.refunded':
        pass
"""


class TheWebhook(unittest.TestCase):
    def test_the_handler_is_found_and_labelled_as_stripe_reached(self):
        hooks = _webhooks(_repo({"views.py": INLINE}))
        self.assertEqual(len(hooks), 1)
        self.assertEqual(hooks[0].label, "webhook")
        self.assertIn("Never read a webhook endpoint's lack of callers", hooks[0].note)

    def test_construct_event_passed_as_a_reference_still_counts(self):
        """An async view writes sync_to_async(stripe.Webhook.construct_event, ...)(...)."""
        root = _repo({"views.py": """
import stripe
async def webhook(request):
    event = await sync_to_async(stripe.Webhook.construct_event, thread_sensitive=False)(
        request.body, sig, secret)
    if event['type'] == 'charge.refunded':
        pass
"""})
        self.assertEqual(len(_webhooks(root)), 1)

    def test_a_project_with_no_stripe_claims_nothing(self):
        self.assertEqual(extract_stripe(_repo({"a.py": "print(1)\n"})), ([], []))


class EventDispatch(unittest.TestCase):
    def test_an_inline_comparison_is_read(self):
        self.assertEqual(_events(_repo({"views.py": INLINE}),),
                         {"payment_intent.succeeded", "charge.refunded"})

    def test_dispatch_through_a_variable_is_read(self):
        """Real handlers assign the type once and branch on the variable."""
        root = _repo({"views.py": """
import stripe
def webhook(request):
    event = stripe.Webhook.construct_event(b, s, k)
    event_type = event['type']
    if event_type == 'payment_intent.succeeded':
        pass
    elif event_type == 'charge.dispute.created':
        pass
"""})
        self.assertEqual(_events(root),
                         {"payment_intent.succeeded", "charge.dispute.created"})

    def test_attribute_access_on_the_event_is_read(self):
        root = _repo({"views.py": """
import stripe
def webhook(r):
    event = stripe.Webhook.construct_event(b, s, k)
    if event.type == 'invoice.paid':
        pass
"""})
        self.assertEqual(_events(root), {"invoice.paid"})

    def test_a_membership_test_names_several_events(self):
        root = _repo({"views.py": """
import stripe
def webhook(r):
    event = stripe.Webhook.construct_event(b, s, k)
    if event['type'] in ('charge.refunded', 'charge.succeeded'):
        pass
"""})
        self.assertEqual(_events(root), {"charge.refunded", "charge.succeeded"})

    def test_dict_dispatch_is_read(self):
        root = _repo({"views.py": """
import stripe
HANDLERS = {
    'payment_intent.succeeded': on_success,
    'charge.refunded': on_refund,
}
def webhook(r):
    event = stripe.Webhook.construct_event(b, s, k)
    HANDLERS[event['type']](event)
"""})
        self.assertEqual(_events(root), {"payment_intent.succeeded", "charge.refunded"})

    def test_an_unrelated_string_comparison_is_not_an_event(self):
        root = _repo({"views.py": """
import stripe
def webhook(r):
    event = stripe.Webhook.construct_event(b, s, k)
    if request.method == 'POST':
        pass
"""})
        self.assertEqual(_events(root), set())


class ReturnUrls(unittest.TestCase):
    def test_a_checkout_return_url_becomes_a_url_reference(self):
        """A customer sent back to a route that no longer exists is a 404 after paying."""
        root = _repo({"checkout.py": """
import stripe
stripe.checkout.Session.create(success_url='/orders/done/', cancel_url='/cart/')
"""})
        symbols, _ = extract_stripe(root)
        labels = {s.label for s in symbols if s.kind == "url_reference"}
        self.assertEqual(labels, {"/orders/done/", "/cart/"})


if __name__ == "__main__":
    unittest.main()
