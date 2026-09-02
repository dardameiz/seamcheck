"""The one entry point every consumer calls: extract -> match -> classify -> Graph."""

from __future__ import annotations

import ast
import dataclasses
import os
import pathlib

from seamcheck.adapters import select_all
from seamcheck.attribution import attribute_by_feature
from seamcheck.classifier import classify
from seamcheck.dom_matcher import (
    detect_multi_writers,
    match_css_selectors,
    match_css_tokens,
    match_dom_selectors,
)
from seamcheck.extractors.css_extractor import (
    extract_css,
    extract_css_attribute_selectors,
    extract_template_css,
)
from seamcheck.extractors.dom_js_extractor import (
    extract_dom_selectors,
    extract_js_class_usages,
    extract_js_css_tokens,
    extract_js_dom_definitions,
)
from seamcheck.extractors.entry_points_extractor import extract_entry_points
from seamcheck.extractors.js_extractor import (
    discover_js_files,
    extract_js,
    extract_template_js,
)
from seamcheck.extractors.template_scanner import scan_templates
from seamcheck.extractors.url_reference_extractor import (
    extract_url_references,
    find_js_files,
)
from seamcheck.field_matcher import match_json_response_fields
from seamcheck.extractors.preprocessor_extractor import preprocessor_classes
from seamcheck.graph import Edge, Graph, Status, Symbol
from seamcheck.matcher import match_js_to_django
from seamcheck.nodetools import report
from seamcheck.progress import Progress, null

_FUNCTION_NODES = (ast.FunctionDef, ast.AsyncFunctionDef)

# Every phase run_scan walks, in order, so a caller can size a progress bar before the
# first one starts. Steps are reported even when their input is empty - a phase that
# had nothing to do still happened, and a bar whose total changes as it runs is worse
# than one that moves in uneven jumps. test_progress pins this list against reality.
# The language each adapter reads, for the label. Not derived from the adapter, because
# the adapter's job is routes and this is a fact about the ecosystem it belongs to.
ADAPTER_LANGUAGE = {
    "django": "Python", "fastapi": "Python", "flask": "Python",
    "express": "JavaScript", "nestjs": "TypeScript", "nextjs": "TypeScript",
}

# Filled by the last run_scan: [{name, confidence, language}], highest confidence first.
LAST_ADAPTERS: list[dict] = []

SCAN_PHASES = (
    "URLs and views",
    "ASGI routes",
    "models",
    "JavaScript modules",
    "JavaScript in templates",
    "matching calls to endpoints",
    "GraphQL",
    "Celery",
    "Stripe",
    "Supabase",
    "Firebase",
    "Redis",
    "background jobs",
    "configuration",
    "Python entry points",
    "references to routes",
    "response fields",
    "template elements",
    "selectors used by JavaScript",
    "reading stylesheets",
    "matching DOM, CSS and tokens",
    "classifying",
)


def _function_source(file_path: str, function_name: str) -> str:
    """Just one view's source.

    Field matching against a whole module is meaningless: this project has 1,061
    JsonResponse calls across its views, and only the matched view's payload describes
    the response the matched fetch call actually reads.
    """
    try:
        source = pathlib.Path(file_path).read_text(encoding="utf-8")
        tree = ast.parse(source, filename=file_path)
    except (OSError, SyntaxError):
        return ""
    for node in ast.walk(tree):
        if isinstance(node, _FUNCTION_NODES) and node.name == function_name:
            return ast.get_source_segment(source, node) or ""
    return ""


def _field_symbols(
    symbols: list[Symbol], routing_edges: list[Edge], match_edges: list[Edge]
) -> list[Symbol]:
    by_id = {symbol.id: symbol for symbol in symbols}
    view_of_url = {edge.from_id: edge.to_id for edge in routing_edges}

    fields: list[Symbol] = []
    for edge in match_edges:
        if edge.status is not Status.CONNECTED:
            continue
        view = by_id.get(view_of_url.get(edge.to_id, ""))
        target = by_id.get(edge.from_id)
        if not view or not target or not view.file:
            continue

        view_source = _function_source(view.file, view.label)
        if not view_source:
            continue
        try:
            js_source = pathlib.Path(target.file).read_text(encoding="utf-8")
        except OSError:
            continue

        matched, _ = match_json_response_fields(view_source, js_source)
        for field in matched:
            # The JS side is a whole module, which talks to many endpoints, so only
            # CONNECTED survives as-is. 'Read but never sent' says nothing here (the
            # read belongs to another endpoint) and is dropped; 'sent but not read'
            # weakens to UNCERTAIN because a different module may consume it.
            if field.status is Status.UNRESOLVED:
                continue
            status = field.status if field.status is Status.CONNECTED else Status.UNCERTAIN
            note = field.note or (
                "" if status is Status.CONNECTED else
                f"Not read in {pathlib.Path(target.file).name}; another module may consume it."
            )
            fields.append(
                Symbol(
                    id=f"{field.id}@{view.id}", kind=field.kind, label=field.label,
                    sub=view.label, file=view.file, line=view.line, status=status,
                    snippet=field.snippet, chain=[view.label, field.label], note=note,
                )
            )
    return fields


# The kinds whose `unresolved` means "no route serves this". Every one of them is a claim
# about the route table, and is only a claim when the table is complete.
_ROUTE_CLAIM_KINDS = frozenset({"fetch_target", "js_call", "url_reference"})


def _withhold_route_claims(symbols: list[Symbol], why: str) -> list[Symbol]:
    """Downgrade every route-based `unresolved` to `uncertain` when the table is partial.

    The reader has just said it did not see every route. Reporting a call as reaching
    nothing is then a guess dressed as a finding - and it was made 127 times on one
    project, in the exact blind spot the warning named, with 12 of 12 sampled false.
    """
    note = (
        "Not claimed missing: " + (why or "the route table is known to be incomplete") +
        ", so a route this reaches may exist unread. Run from the project's own virtualenv "
        "for the complete table."
    )
    out = []
    for symbol in symbols:
        if symbol.kind in _ROUTE_CLAIM_KINDS and symbol.status is Status.UNRESOLVED:
            out.append(dataclasses.replace(symbol, status=Status.UNCERTAIN, note=note))
        else:
            out.append(symbol)
    return out


def run_scan(
    urlconf_module: str,
    js_entry_files: list[str],
    js_project_root: str,
    js_extra_files: list[str] | None = None,
    entry_point_files: set[str] | None = None,
    asgi_file: str | None = None,
    first_party_prefixes: list[str] | None = None,
    app_labels: list[str] | None = None,
    template_files: list[str] | None = None,
    css_files: list[str] | None = None,
    preprocessor_sources: list[str] | None = None,
    tailwind_build_classes: set[str] | None = None,
    progress: Progress | None = None,
    repo_root: str = ".",
    static_urls: bool = False,
    server_adapter: str | None = None,
) -> Graph:
    progress = progress or null()

    # The one framework-specific component in the whole pipeline. Everything below this
    # block - JavaScript, CSS, DOM, templates, matching, classification - reads the graph
    # and never the backend, which is why 97% of a real scan is already framework-agnostic.
    adapter_config = {
        "urlconf_module": urlconf_module,
        "first_party_prefixes": first_party_prefixes,
        "asgi_file": asgi_file,
        "app_labels": app_labels,
        "static_urls": static_urls,
    }
    chosen = select_all(repo_root, {**adapter_config, "server_adapter": server_adapter})
    adapter = chosen[0][0]
    # What this scan actually read, so the map can say so. A monorepo is not one
    # application - cal.com serves Next.js and NestJS from one repository - and a reader
    # looking at "Backend Internals" deserves to know which backend, in which language.
    LAST_ADAPTERS.clear()
    LAST_ADAPTERS.extend(
        {"name": one.name, "confidence": confidence, "language": ADAPTER_LANGUAGE.get(one.name, "")}
        for one, confidence in chosen
    )
    server_symbols: list = []
    routing_edges: list = []
    route_names: dict[str, str] = {}
    seen_ids: set[str] = set()
    # Whether EVERY adapter saw its whole route table. One that did not taints every
    # route-based verdict below, because "no route serves this" is only a finding when
    # the reader looked at all the routes.
    routes_complete, coverage_note = True, ""
    for one, _confidence in chosen:
        part = one.scan(repo_root, adapter_config, progress)
        if not getattr(part, "complete", True):
            routes_complete, coverage_note = False, getattr(part, "coverage_note", "")
        for symbol in part.symbols:
            if symbol.id not in seen_ids:
                seen_ids.add(symbol.id)
                server_symbols.append(symbol)
        routing_edges.extend(part.edges)
        # First adapter wins a name collision: it is the more confident one.
        route_names = {**part.route_names, **route_names}
    if not server_symbols:
        # Silence here would be the worst possible failure: with no routes, every fetch
        # target resolves to nothing and is reported unresolved - actively wrong on 100%
        # of endpoints rather than merely unknown.
        #
        # But NAME the adapter only if one was actually detected. select() deliberately
        # returns its best candidate even at zero confidence, so on a repository with no
        # backend at all this said "the fastapi adapter found no routes" about a project
        # with no Python in it - which reads as a broken reader rather than as an absent
        # framework.
        confident = chosen and chosen[0][1] > 0
        if confident:
            report(
                "no-routes",
                "the %s adapter found no routes, so every fetch target will be reported "
                "unresolved. The frontend half of the graph is still built.", adapter.name,
            )
        else:
            report(
                "no-backend",
                "no backend framework detected here, so any fetch target will be reported "
                "unresolved. Everything that does not need one - the DOM, CSS, and any "
                "data layer found - is still read.",
            )

    progress.step("JavaScript modules")
    js_symbols, js_edges = extract_js(js_entry_files, js_project_root, js_extra_files)
    # JavaScript a template writes inline is still JavaScript. This project keeps 200 KB
    # of it, calling five endpoints that no .js file mentions - which a scan of .js files
    # alone reported as endpoints nothing calls.
    progress.step("JavaScript in templates")
    inline_symbols, inline_edges = extract_template_js(template_files or [])
    known = {symbol.id for symbol in js_symbols}
    js_symbols += [symbol for symbol in inline_symbols if symbol.id not in known]
    js_edges += inline_edges
    progress.step("matching calls to endpoints")
    match_edges = match_js_to_django(server_symbols, js_symbols)
    progress.step("GraphQL")
    # A GraphQL API has ONE route, so every adapter reports it as a single connected
    # endpoint and stops. The real API is the schema, and the seam is a query naming a
    # field the schema does not define - the same bug as a dead fetch, in a place the
    # route readers cannot see.
    from seamcheck.extractors.graphql_extractor import extract_graphql

    graphql_symbols, graphql_edges = extract_graphql(repo_root)

    progress.step("Celery")
    # Code reached with no HTTP request at all. A beat entry naming a task that does not
    # exist raises where nobody is looking and the job silently never runs - the reference
    # project lost challenges and season rollover for fourteen days that way.
    from seamcheck.extractors.celery_extractor import extract_celery

    celery_symbols, celery_edges = extract_celery(repo_root)
    graphql_symbols += celery_symbols
    graphql_edges += celery_edges

    progress.step("Stripe")
    # A webhook is called by Stripe's servers, so nothing in the project references it and
    # every dead-code tool is entitled to call it unused. It is not; it is the one endpoint
    # whose lack of callers is the design.
    from seamcheck.extractors.stripe_extractor import extract_stripe

    stripe_symbols, stripe_edges = extract_stripe(repo_root)
    known = {symbol.id for symbol in graphql_symbols}
    graphql_symbols += [s for s in stripe_symbols if s.id not in known]
    graphql_edges += stripe_edges

    progress.step("Supabase")
    # A Supabase app has no routes of its own: the browser talks to Postgres directly, so
    # the seam is a STRING against a schema rather than a fetch against a URLconf. Same
    # disease, different boundary.
    from seamcheck.extractors.supabase_extractor import extract_supabase

    supabase_symbols, supabase_edges = extract_supabase(repo_root)
    known = {symbol.id for symbol in graphql_symbols}
    graphql_symbols += [s for s in supabase_symbols if s.id not in known]
    graphql_edges += supabase_edges

    progress.step("Firebase")
    from seamcheck.extractors.firebase_extractor import extract_firebase

    firebase_symbols, firebase_edges = extract_firebase(repo_root)
    known = {symbol.id for symbol in graphql_symbols}
    graphql_symbols += [s for s in firebase_symbols if s.id not in known]
    graphql_edges += firebase_edges

    progress.step("Redis")
    from seamcheck.extractors.redis_extractor import extract_redis

    redis_symbols, redis_edges = extract_redis(repo_root)
    known = {symbol.id for symbol in graphql_symbols}
    graphql_symbols += [s for s in redis_symbols if s.id not in known]
    graphql_edges += redis_edges

    progress.step("background jobs")
    # Celery's seam, in every other ecosystem: BullMQ, Agenda, Inngest, pg-boss, Temporal,
    # RQ, Dramatiq, arq. A job named by a string on one side and registered by a string on
    # the other, with nothing checking that the two agree.
    from seamcheck.extractors.job_queue_extractor import extract_jobs

    job_symbols, job_edges = extract_jobs(repo_root)
    known = {symbol.id for symbol in graphql_symbols}
    graphql_symbols += [s for s in job_symbols if s.id not in known]
    graphql_edges += job_edges

    progress.step("configuration")
    # The one seam every backend shares, and the cheapest to read: a string key into a
    # dictionary, declared in a file the repository already has.
    from seamcheck.extractors.env_extractor import extract_env

    env_symbols, env_edges = extract_env(repo_root)
    known = {symbol.id for symbol in graphql_symbols}
    graphql_symbols += [s for s in env_symbols if s.id not in known]
    graphql_edges += env_edges

    progress.step("Python entry points")
    entry_point_symbols = extract_entry_points(entry_point_files or set())

    symbols = server_symbols + js_symbols + entry_point_symbols + graphql_symbols
    edges = routing_edges + js_edges + match_edges + graphql_edges
    progress.step("references to routes")
    # Every other way the project points at its own routes. Without this, only a fetch()
    # counted as reaching a route, so every server-rendered page read as unmeasured.
    reference_symbols, reference_edges = extract_url_references(
        template_files or [], sorted(entry_point_files or []),
        route_names, server_symbols,
        # The whole repository, not the import graph from a JS entry point: a Next.js app
        # directory has no entry file to walk from, and its pages are exactly where the
        # links live.
        js_files=find_js_files(repo_root),
    )
    symbols += reference_symbols
    edges += reference_edges

    progress.step("response fields")
    symbols += _field_symbols(symbols, routing_edges, match_edges)

    progress.step("template elements")
    dom_attrs = scan_templates(template_files or [])
    progress.step("selectors used by JavaScript")
    # NOT gated on templates any more. It was - `if template_files else []` - which meant a
    # React or Vue project with no server-rendered templates had NO JavaScript read for
    # className, classList, querySelector or CSS tokens. Every stylesheet rule in such a
    # project then looked unused, because the only thing that could reference it was never
    # opened. Measured on a Flask+React repo: 8 of 15 "unused CSS" claims were classes sitting
    # in a className= one directory away. JSX is markup; it is read as markup.
    #
    # And the whole first-party tree, not just the import graph, for the same reason the
    # call reader takes it: a page routed to by the filesystem is imported by nothing.
    js_files = discover_js_files(js_entry_files, js_project_root)
    # Two file sets, on purpose, and the distinction is claims versus evidence.
    #
    # The entry graph - what the pages actually load - is where DOM CLAIMS may come from:
    # "this script queries an element no template has" is only a finding when the script
    # is one the page runs. The whole first-party tree is read for EVIDENCE only: a class
    # applied, a data attribute set, a token defined. Evidence can only ever connect
    # things; it cannot invent a failure.
    #
    # Reading the whole tree for claims as well was tried and measured on one project:
    # claims went from 3,862 to 17,417, with 14,439 of them `dom_selector` findings from
    # button files, test plugins and admin tooling querying elements they build at
    # runtime. Every one a fresh accusation, none of them true.
    js_evidence_files = sorted(dict.fromkeys(js_files + list(js_extra_files or [])))
    # Classes applied at runtime are evidence a CSS rule is live; without them the
    # scan reported 5,318 selectors with no evidence either way.
    # template_files too: the JavaScript a template writes inline queries the DOM like any
    # other JavaScript, and reading only .js files made 202 KB of it invisible.
    # Selectors from the entry graph can make CLAIMS - "this script queries an element no
    # template has" is a finding when the script is one the pages run. Selectors from the
    # rest of the tree are EVIDENCE only: a `querySelectorAll('.js-open-achievements')` in
    # a file the bundler entry never reaches still proves the class is read, and six copies
    # of that class in a template were being reported as markup nothing touches while the
    # line that touches them sat one directory away.
    entry_selectors = extract_dom_selectors(js_files, template_files or [])
    seen_selector_ids = {symbol.id for symbol in entry_selectors}
    evidence_selectors = [
        dataclasses.replace(symbol, sub=f"{symbol.sub}:evidence")
        for symbol in extract_dom_selectors(
            [f for f in js_evidence_files if f not in set(js_files)], [])
        if symbol.id not in seen_selector_ids
    ] if js_evidence_files != js_files else []
    dom_selectors = (
        entry_selectors + evidence_selectors
        + extract_js_class_usages(js_evidence_files, template_files or [])
    )
    # Multi-writer detection reads the WHOLE tree, not the entry graph. A write site is an
    # observation, not a claim - "these two files both write #levelName" is true whether or
    # not a bundler entry happens to reach them, and the verified live bugs on the
    # reference project (level-name written by both level_progress_bridge.js and
    # stats_manager.js) sit in files the entry walk does not reach. Narrowing this to the
    # entry graph lost every one of them while the false positives stayed.
    dom_writes = (
        dom_selectors if js_evidence_files == js_files
        else dom_selectors + extract_dom_selectors(js_evidence_files, template_files or [])
    )
    progress.step("reading stylesheets")
    css_symbols = extract_css(css_files or [])
    # A <style> block in a template is a stylesheet. Reading only .css files reported
    # every element styled that way as one nothing reaches - this project keeps 1,016
    # class and id selectors in 29 templates. Merged by id so a selector that also
    # exists in a real stylesheet stays one symbol.
    by_id = {symbol.id: symbol for symbol in extract_template_css(template_files or [])}
    by_id.update({symbol.id: symbol for symbol in css_symbols})
    css_symbols = list(by_id.values())
    # Framework-shipped stylesheets, as an oracle. Their rules make the classes in a
    # project's admin templates resolve; the rules themselves are never reported.
    from seamcheck.roots import framework_stylesheets

    vendor_dir = os.path.dirname(template_files[0]) if template_files else None
    while vendor_dir and os.path.basename(vendor_dir) != "templates" and os.sep in vendor_dir:
        parent = os.path.dirname(vendor_dir)
        if parent == vendor_dir:
            break
        vendor_dir = parent
    for symbol in extract_css(framework_stylesheets(vendor_dir)):
        if symbol.id not in by_id:
            css_symbols.append(dataclasses.replace(symbol, sub=f"vendor:{symbol.sub}"))

    # Elements the JavaScript brings into existence. Half of what a modern page renders is
    # never in a template, and reading definitions from templates alone made every query
    # for one of those look like a query for nothing.
    js_dom_attrs = extract_js_dom_definitions(js_evidence_files, template_files or [])
    # A stylesheet asking for [data-x] is asking for the same thing a script does.
    dom_selectors += extract_css_attribute_selectors(css_files or [])

    progress.step("matching DOM, CSS and tokens")
    if dom_attrs or dom_selectors or css_symbols:
        selectors = [s for s in css_symbols if s.kind == "css_selector"]
        symbols += dom_attrs + dom_selectors + css_symbols
        symbols += detect_multi_writers(
            dom_writes, repo_root, frozenset(tailwind_build_classes or ()))
        # Element matching sees the JS-created ones; CSS matching deliberately does not. An
        # element JavaScript builds is often styled inline or by an injected stylesheet, so
        # demanding a hand-written rule for it trades one false finding for another.
        dom_edges = match_dom_selectors(dom_attrs + js_dom_attrs, dom_selectors)
        edges += dom_edges
        # Only the JS-created elements something actually queries become symbols. All 4,744
        # of them did at first, and 4,511 arrived with nothing pointing at them - because
        # JavaScript builds markup to DISPLAY, not to be queried, so "no selector reaches
        # this div" is neither a finding nor evidence. It inflated the graph by 12% and
        # `uncertain` by 4,511 rows that meant nothing. The 233 that resolve a query are
        # exactly the ones worth carrying: each explains why a getElementById is not broken.
        resolved = {edge.to_id for edge in dom_edges}
        symbols += [attr for attr in js_dom_attrs if attr.id in resolved]
        # Whether this project HAS stylesheets of its own. With none, every class in
        # every template is unstyled as far as the scan can see, and saying so would be a
        # claim about a file that is not here.
        # Sass sources say which classes ARE defined; they cannot say which are not, so
        # they join the build-time evidence and are deliberately kept OUT of the oracle
        # test below. NetBox is the case that settles it: its own .scss defines 103
        # classes and @imports Tabler and Bootstrap from a node_modules that is not in the
        # repository. Counting .scss as "there are local styles here" would turn ~4,600
        # perfectly real vendor classes into unresolved findings - trading an honest
        # `uncertain` for a wall of false claims, which is the one trade this tool exists
        # to refuse.
        scss_classes, scss_stems = preprocessor_classes(preprocessor_sources or [])
        edges += match_css_selectors(
            dom_selectors, dom_attrs, selectors,
            (tailwind_build_classes or set()) | scss_classes | scss_stems,
            usage_only=js_dom_attrs,
            styles_are_local=bool(css_files) or bool(tailwind_build_classes),
        )
        # Tokens JavaScript sets at runtime are real definitions; without them half of
        # this project's "undefined var()" findings were false.
        js_tokens = extract_js_css_tokens(js_evidence_files, template_files or [])
        symbols += js_tokens
        edges += match_css_tokens(
            [s for s in css_symbols + js_tokens if s.kind == "css_token_def"],
            [s for s in css_symbols + js_tokens if s.kind == "css_token_use"],
        )

    progress.step("classifying")
    classified = classify(symbols, edges)
    if not routes_complete:
        classified = _withhold_route_claims(classified, coverage_note)
    graph = Graph(symbols=classified, edges=edges)
    return _with_feature_labels(graph, dom_attrs)


def _with_feature_labels(graph: Graph, dom_roots: list[Symbol]) -> Graph:
    if not dom_roots:
        return graph
    labels = attribute_by_feature(graph, [r for r in dom_roots if r.sub == "id"])
    symbols = [
        dataclasses.replace(symbol, sub=f"{symbol.sub} [{labels[symbol.id][0]}]")
        if symbol.id in labels and labels[symbol.id]
        else symbol
        for symbol in graph.symbols
    ]
    return Graph(symbols=symbols, edges=graph.edges, schema_version=graph.schema_version)
