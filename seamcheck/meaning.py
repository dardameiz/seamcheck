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
    # ---- background work ----------------------------------------------------------
    "job_enqueue|unresolved": (
        "This code puts work on a queue under a name no handler in this repository "
        "registers.",
        "If the worker lives in this repository, the message goes onto the queue and "
        "nothing ever takes it off - no exception, no failed build, no failing test. It "
        "is found when somebody notices the work never happened.",
    ),
    "job|uncertain": (
        "A handler is registered for this job and nothing here puts work on it.",
        "Not evidence that it is dead: another service, a console or a scheduled trigger "
        "can enqueue by name, and none of those appear in any source file here.",
    ),
    "job_schedule|unresolved": (
        "This is not a schedule any cron parser will accept.",
        "The job it guards never runs. The usual cause is a five-field expression pasted "
        "into a library that wants six, or the reverse - which shifts every run by a "
        "factor of sixty rather than failing outright.",
    ),
    "env_read|uncertain": (
        "The code reads this configuration key and no file in the repository declares it.",
        "Not a claim that it is unset - secrets live in a dashboard and CI injects its "
        "own. It matters when a new environment is set up: a missing key usually turns a "
        "feature off silently rather than raising, which is why it is found by a customer.",
    ),
    "env_var|uncertain": (
        "Declared in an example or compose file, and no source file here reads it by name.",
        "Not evidence that it is unused. Container images, shell scripts, schema parsers "
        "and CI all consume configuration without a process.env in a source file, and the "
        "scan reads none of those.",
    ),
    # ---- the data layer -----------------------------------------------------------
    "db_table_use|unresolved": (
        "The client reads a table with this name and no migration in this repo declares "
        "one.",
        "A rename that the client did not follow, or a table created outside the "
        "migrations. PostgREST answers with an error the client usually swallows.",
    ),
    "db_column_use|unresolved": (
        "The client selects this column and the table it belongs to has not got it.",
        "This is the quiet one: PostgREST returns the rows WITHOUT the column, the client "
        "reads undefined, and a blank field ships. Nothing raises.",
    ),
    "db_table|unused": (
        "The schema declares this table and no client code in the repo reads or writes it.",
        "Fine if something outside this repo uses it - a worker, an admin tool, a report. "
        "Otherwise it is a table nobody has needed since it was added.",
    ),
    "db_function_use|unresolved": (
        "An rpc() call names a function no migration declares.",
        "Check the spelling and check that the function was actually committed; a function "
        "created by hand in the dashboard is not in the repo.",
    ),
    "db_policy|unresolved": (
        "Client code reads this table, and row level security on it is not set up.",
        "The anon key ships in the browser bundle, so 'only our app calls it' is not a "
        "control. Either enable RLS with a policy, or move the read behind a function.",
    ),
    "edge_function_use|unresolved": (
        "functions.invoke() names an edge function with no directory in supabase/functions.",
        "A rename, or a function deployed by hand and never committed.",
    ),
    "cloud_function_use|unresolved": (
        "httpsCallable() names a function the functions directory does not export.",
        "Check the export name. A callable is matched by the exported symbol, not by the "
        "file it lives in.",
    ),
    "firestore_collection|unresolved": (
        "No match block in firestore.rules covers this collection.",
        "Firestore denies by default, so these reads fail in production and usually fail "
        "silently - the promise rejects and the UI shows nothing.",
    ),
    "firestore_rule|unused": (
        "A rules block guards a collection no client code in this repo touches.",
        "Usually a collection that was renamed and left its rule behind. A stale rule is "
        "not harmless: it is one more path someone can reach.",
    ),
    "redis_key|unresolved": (
        "This key is read here and written nowhere in the repo.",
        "It can only ever miss. That fails silently - the read returns None, the code "
        "falls through to the slow path, and the only symptom is a value that never "
        "updates. Check the spelling against the writer.",
    ),
    "redis_key|unused": (
        "This key is written here and read nowhere in the repo.",
        "Either the reader was deleted and the write outlived it, or the reader spells the "
        "key differently. Both are worth a look before deleting.",
    ),
    "redis_ttl|unresolved": (
        "A key whose name says it is a cache, written with no expiry.",
        "Redis keeps it forever. Pass `ex=` (or use setex) - this is the leak nobody "
        "notices until the instance is full.",
    ),

    # ---- the frontend/backend seam ------------------------------------------------
    "fetch_target|unresolved": (
        # "URLconf" is Django's word for it, and this sentence is shown for Express, Flask,
        # FastAPI, NestJS and Next.js too - where it names a thing the reader does not have.
        "The frontend fetches this path and no route on the server matches it.",
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
    "dom_attr|unresolved": (
        "A template renders this element and no stylesheet rule matches its id or class.",
        "Usually fine: an element can be a JavaScript hook, styled by a parent or "
        "descendant rule, or styled inline. Worth a look only when you expected it to be "
        "styled and it is not.",
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
