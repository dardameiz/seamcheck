"""The one entry point every consumer calls: extract -> match -> classify -> Graph."""

from __future__ import annotations

import ast
import dataclasses
import pathlib

from seamcheck.attribution import attribute_by_feature
from seamcheck.classifier import classify
from seamcheck.dom_matcher import (
    detect_multi_writers,
    match_css_selectors,
    match_css_tokens,
    match_dom_selectors,
)
from seamcheck.extractors.asgi_extractor import extract_asgi_routes
from seamcheck.extractors.css_extractor import extract_css, extract_template_css
from seamcheck.extractors.django_extractor import extract_django_urls_views
from seamcheck.extractors.django_models_extractor import extract_django_models
from seamcheck.extractors.dom_js_extractor import (
    extract_dom_selectors,
    extract_js_class_usages,
    extract_js_css_tokens,
)
from seamcheck.extractors.entry_points_extractor import extract_entry_points
from seamcheck.extractors.js_extractor import (
    discover_js_files,
    extract_js,
    extract_template_js,
)
from seamcheck.extractors.template_scanner import scan_templates
from seamcheck.field_matcher import match_json_response_fields
from seamcheck.graph import Edge, Graph, Status, Symbol
from seamcheck.matcher import match_js_to_django

_FUNCTION_NODES = (ast.FunctionDef, ast.AsyncFunctionDef)


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


def run_scan(
    urlconf_module: str,
    js_entry_files: list[str],
    js_project_root: str,
    entry_point_files: set[str] | None = None,
    asgi_file: str | None = None,
    first_party_prefixes: list[str] | None = None,
    app_labels: list[str] | None = None,
    template_files: list[str] | None = None,
    css_files: list[str] | None = None,
    tailwind_build_classes: set[str] | None = None,
) -> Graph:
    django_symbols, routing_edges = extract_django_urls_views(urlconf_module, first_party_prefixes)
    if asgi_file:
        django_symbols = django_symbols + extract_asgi_routes(asgi_file)

    if app_labels:
        # Model symbols were extracted and tested from the start but never reached the
        # graph, so the Django-internals view had no models in it at all.
        django_symbols = django_symbols + extract_django_models(app_labels)

    js_symbols, js_edges = extract_js(js_entry_files, js_project_root)
    # JavaScript a template writes inline is still JavaScript. This project keeps 200 KB
    # of it, calling five endpoints that no .js file mentions - which a scan of .js files
    # alone reported as endpoints nothing calls.
    inline_symbols, inline_edges = extract_template_js(template_files or [])
    known = {symbol.id for symbol in js_symbols}
    js_symbols += [symbol for symbol in inline_symbols if symbol.id not in known]
    js_edges += inline_edges
    match_edges = match_js_to_django(django_symbols, js_symbols)
    entry_point_symbols = extract_entry_points(entry_point_files or set())

    symbols = django_symbols + js_symbols + entry_point_symbols
    symbols += _field_symbols(symbols, routing_edges, match_edges)
    edges = routing_edges + js_edges + match_edges

    dom_attrs = scan_templates(template_files or [])
    js_files = discover_js_files(js_entry_files, js_project_root) if template_files else []
    # Classes applied at runtime are evidence a CSS rule is live; without them the
    # scan reported 5,318 selectors with no evidence either way.
    dom_selectors = extract_dom_selectors(js_files) + extract_js_class_usages(js_files)
    css_symbols = extract_css(css_files or [])
    # A <style> block in a template is a stylesheet. Reading only .css files reported
    # every element styled that way as one nothing reaches - this project keeps 1,016
    # class and id selectors in 29 templates. Merged by id so a selector that also
    # exists in a real stylesheet stays one symbol.
    by_id = {symbol.id: symbol for symbol in extract_template_css(template_files or [])}
    by_id.update({symbol.id: symbol for symbol in css_symbols})
    css_symbols = list(by_id.values())

    if dom_attrs or dom_selectors or css_symbols:
        selectors = [s for s in css_symbols if s.kind == "css_selector"]
        symbols += dom_attrs + dom_selectors + css_symbols
        symbols += detect_multi_writers(dom_selectors)
        edges += match_dom_selectors(dom_attrs, dom_selectors)
        edges += match_css_selectors(
            dom_selectors, dom_attrs, selectors, tailwind_build_classes or set()
        )
        # Tokens JavaScript sets at runtime are real definitions; without them half of
        # this project's "undefined var()" findings were false.
        js_tokens = extract_js_css_tokens(js_files)
        symbols += js_tokens
        edges += match_css_tokens(
            [s for s in css_symbols + js_tokens if s.kind == "css_token_def"],
            [s for s in css_symbols + js_tokens if s.kind == "css_token_use"],
        )

    graph = Graph(symbols=classify(symbols, edges), edges=edges)
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
