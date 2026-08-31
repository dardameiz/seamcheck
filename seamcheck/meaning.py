"""What a finding means, and what is usually actually true.

A row that says `unresolved · css_token_use · button_badges.css:3` is precise and tells a
newcomer nothing. It does not say what the scan observed, and - more importantly - it
does not say what the three likely explanations are, only one of which is a bug.

So each (kind, status) pair carries two sentences:

* **means** - what the scan actually observed. Only ever a statement about evidence, never
  a verdict. Seamcheck reads static source; it cannot run your code.
* **check** - the handful of things that are usually true when you see this, ordered by
  how often they turn out to be the answer. The first one is frequently "it is fine, and
  here is why the scan cannot tell".

Kept as data, in one module, for two reasons. The map ships it once as a lookup table
rather than repeating a paragraph on each of 1,455 rows, and `seamcheck explain` reads the
same text - so the terminal and the UI can never drift into explaining a finding two
different ways.
"""

from __future__ import annotations

# The blind spots that make a symbol look dead when it is not. Anything reached only
# through one of these is invisible to every extractor, so it lands in `unused`.
BLIND_SPOTS = (
    "Celery tasks, Redis subscribers, WebSocket handlers and Stripe webhooks are not "
    "traced yet, so anything reached only through one of those looks unused here."
)

_STATUS: dict[str, tuple[str, str]] = {
    "connected": (
        "Something reaches this, and the scan attached the evidence for it.",
        "Nothing to do. Open it to see the exact chain that reaches it.",
    ),
    "unresolved": (
        "Something in the code reaches for this by name, and the scan cannot find "
        "anything with that name.",
        "Usually one of: the name is built at runtime so no static reader can see it; "
        "the target was renamed and this reference was missed; or it genuinely no "
        "longer exists. The last one is the bug.",
    ),
    "unused": (
        "Both ends are visible to the scan, and nothing in the project references this.",
        "Check for a dynamic reference first (a name assembled from strings, a template "
        "the scan does not read). If there is none, this is a real deletion candidate.",
    ),
    "uncertain": (
        "The scan found no evidence either way. This is not a claim that it is dead.",
        "Uncertain means the extractor for this kind cannot see far enough. Treat it as "
        "unmeasured, not as a finding.",
    ),
}

# kind|status -> (means, check). Falls back to the status-only text above.
_SPECIFIC: dict[str, tuple[str, str]] = {
    # ---- the frontend/backend seam ------------------------------------------------
    "fetch_target|unresolved": (
        "The frontend fetches this path and no URL pattern in the URLconf matches it.",
        "Either the route was renamed or removed (a real 404 waiting to happen), or the "
        "path is assembled at runtime - `/api/user/${id}/` cannot be matched statically.",
    ),
    "fetch_target|unused": (
        "This endpoint is reachable, and no JavaScript the scan read ever calls it.",
        "Fine if a mobile client, an external service or a webhook calls it. Otherwise "
        "the endpoint and its view are dead weight.",
    ),
    "js_call|unused": (
        "This function is defined and nothing in the bundle calls it.",
        "Check for a call by string name (`handlers[name]()`), an event-listener wiring, "
        "or a template that references it inline. If none, it is dead.",
    ),
    "json_field|uncertain": (
        "The view puts this key in its JSON response; the JS module matched to that "
        "endpoint never reads it.",
        "Often fine - a second module may consume it, and the scan only matched one. "
        "Worth a look when the field is expensive to compute.",
    ),
    "json_field|unresolved": (
        "The frontend reads this key out of the response and the view never sends it.",
        "This one is usually real: it renders as `undefined` in the browser. Check "
        "whether the key was renamed on the backend.",
    ),
    # ---- URLs, views and the rest of Django ----------------------------------------
    "url|unused": (
        "No fetch call, template link or reverse() the scan can see points at this route.",
        f"Fine for a route hit from outside the app - a webhook, a mobile client, a "
        f"bookmark, an admin URL. {BLIND_SPOTS}",
    ),
    "view|unused": (
        "No URL pattern the scan resolved reaches this view.",
        "A view no route reaches cannot be called over HTTP. Check for a route built at "
        "runtime, or an include() the scan could not follow.",
    ),
    "model|unused": (
        "No view, admin action or signal receiver the scan read touches this model.",
        f"Models are frequently used from places the scan does not follow. {BLIND_SPOTS}",
    ),
    "signal_receiver|unused": (
        "This receiver is registered and the scan saw nothing send its signal.",
        "Django itself sends most signals, and the scan does not model that. Only "
        "suspicious for a signal your own code defines.",
    ),
    "management_command|unused": (
        "Nothing in the project invokes this command.",
        "Expected - management commands are typed by a human or run by cron or CI. "
        "Listed for completeness, not as a finding.",
    ),
    # ---- DOM wiring ------------------------------------------------------------------
    "dom_selector|unresolved": (
        "JavaScript queries for this selector and no template renders a matching element.",
        "Either the element is created at runtime by JavaScript, or it comes from a "
        "third-party widget, or the markup was changed and this query now returns null.",
    ),
    "dom_attr|unused": (
        "A template renders this element and no JavaScript ever selects it.",
        "Fine for something styled but never scripted. A `data-` attribute nothing reads "
        "is usually a leftover.",
    ),
    # detect_multi_writers() hardcodes Status.UNRESOLVED, and the generic unresolved text
    # ("something reaches for this and it is not there") is the opposite of true here: the
    # element exists and is found by MORE than one writer. The status is a known misfit -
    # this is a design finding, not a reachability one, and the four statuses are a
    # reachability axis - but it is genuinely actionable, so it stays in the findings list
    # rather than being demoted to uncertain and quietly dropped from the CI gate. Logged
    # OPEN in CONSOLIDATED-FINDINGS; the explanation is correct in the meantime.
    "multi_writer_element|unresolved": (
        "More than one JavaScript file writes to this same element.",
        "Two writers on one element is the classic source of flicker and of a value that "
        "reverts: they overwrite each other in whatever order they happen to run. Decide "
        "which one owns it and route the others through it. Not a broken reference - the "
        "element is there, and found twice.",
    ),
    "multi_writer_element|uncertain": (
        "More than one JavaScript file writes to this same element.",
        "Two writers on one element is the classic source of flicker and of a value that "
        "reverts: they overwrite each other in whatever order they happen to run. Decide "
        "which one owns it and route the others through it.",
    ),
    # ---- CSS -------------------------------------------------------------------------
    "css_selector|unused": (
        "Nothing matches this rule: no template attribute, no className, no classList.add, "
        "no setAttribute, and no class in any markup JavaScript builds.",
        "A name assembled at runtime is already excluded - those stay uncertain. What is "
        "left is a third-party script applying its own classes (Stripe Elements, a chart "
        "library) that you wrote overrides for, and markup in a template outside the "
        "configured root. Otherwise it is deletable CSS.",
    ),
    "css_selector|uncertain": (
        "Nothing references this rule, but its name could be assembled at runtime from a "
        "prefix that does appear in the JavaScript.",
        "`'pb-badge-' + kind` puts pb-badge-success on an element while only the stem is in "
        "the source, so the rule is live and unprovable at the same time. Grep the stem to "
        "settle it; do not delete on this alone.",
    ),
    "css_token_use|unresolved": (
        "This `var()` names a custom property that nothing in the scanned CSS defines.",
        "Very often fine: the property is defined in a stylesheet outside the configured "
        "CSS root, by an inline style, or by a library. When it is real, the declaration "
        "silently falls back and the colour or size is simply wrong.",
    ),
    "css_token_def|unused": (
        "A custom property is declared and no `var()` reads it.",
        "A design token nobody uses. Safe to delete once you have checked it is not read "
        "from JavaScript with getComputedStyle.",
    ),
}


def meaning(kind: str, status: str) -> tuple[str, str]:
    """(what the scan observed, what is usually true) for one finding."""
    return _SPECIFIC.get(f"{kind}|{status}") or _STATUS.get(status) or ("", "")


def table() -> dict[str, dict[str, str]]:
    """Every explanation, as the lookup the map ships once and reads per row."""
    keys = set(_SPECIFIC) | {f"*|{status}" for status in _STATUS}
    out = {}
    for key in sorted(keys):
        kind, _, status = key.partition("|")
        means, check = _SPECIFIC[key] if key in _SPECIFIC else _STATUS[status]
        out[key] = {"means": means, "check": check}
    return out
