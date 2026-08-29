"""The one entry point every consumer calls: extract -> match -> classify -> Graph."""

from __future__ import annotations

import ast
import pathlib

from signal_map.classifier import classify
from signal_map.extractors.asgi_extractor import extract_asgi_routes
from signal_map.extractors.django_extractor import extract_django_urls_views
from signal_map.extractors.entry_points_extractor import extract_entry_points
from signal_map.extractors.js_extractor import extract_js
from signal_map.field_matcher import match_json_response_fields
from signal_map.graph import Edge, Graph, Status, Symbol
from signal_map.matcher import match_js_to_django

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
) -> Graph:
    django_symbols, routing_edges = extract_django_urls_views(urlconf_module, first_party_prefixes)
    if asgi_file:
        django_symbols = django_symbols + extract_asgi_routes(asgi_file)

    js_symbols, js_edges = extract_js(js_entry_files, js_project_root)
    match_edges = match_js_to_django(django_symbols, js_symbols)
    entry_point_symbols = extract_entry_points(entry_point_files or set())

    symbols = django_symbols + js_symbols + entry_point_symbols
    symbols += _field_symbols(symbols, routing_edges, match_edges)
    edges = routing_edges + js_edges + match_edges

    return Graph(symbols=classify(symbols, edges), edges=edges)
