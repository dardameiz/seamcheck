"""URL and view symbols, read from Django's own resolved URLconf tree."""

from __future__ import annotations

import importlib
import inspect
from collections.abc import Iterator

from django.urls.resolvers import URLPattern, URLResolver

from signal_map.graph import Edge, Status, Symbol


def _walk_patterns(patterns, prefix: str = "") -> Iterator[tuple[str, object]]:
    """Yield (full_path, view_func) for every leaf under `patterns`.

    A URLconf is a tree, not a list: include() produces a URLResolver holding its own
    nested url_patterns behind a path prefix, and only URLPattern leaves carry a real
    callback. Iterating one level would miss every include()-mounted URL.
    """
    for entry in patterns:
        entry_prefix = prefix + str(entry.pattern)
        if isinstance(entry, URLPattern):
            yield entry_prefix, entry.callback
        elif isinstance(entry, URLResolver):
            yield from _walk_patterns(entry.url_patterns, entry_prefix)


def _source_location(view_func) -> tuple[str, int | None]:
    try:
        return inspect.getsourcefile(view_func) or "", inspect.getsourcelines(view_func)[1]
    except (OSError, TypeError):
        return "", None


def extract_django_urls_views(urlconf_module: str) -> tuple[list[Symbol], list[Edge]]:
    module = importlib.import_module(urlconf_module)
    symbols: list[Symbol] = []
    edges: list[Edge] = []
    seen_view_ids: set[str] = set()

    for pattern_str, view_func in _walk_patterns(module.urlpatterns):
        view_name = getattr(view_func, "__name__", view_func.__class__.__name__)
        url_id = f"url:{pattern_str}"
        view_id = f"view:{view_func.__module__}.{view_name}"
        source_file, line_no = _source_location(view_func)

        symbols.append(
            Symbol(
                id=url_id,
                kind="url",
                label=pattern_str,
                sub="GET/POST",
                file=source_file,
                line=None,
                status=Status.CONNECTED,
                snippet=f'path("{pattern_str}", {view_name})',
                chain=[pattern_str, view_name],
                note="",
            )
        )
        if view_id not in seen_view_ids:
            seen_view_ids.add(view_id)
            symbols.append(
                Symbol(
                    id=view_id,
                    kind="view",
                    label=view_name,
                    sub=f"{source_file}:{line_no}" if line_no else source_file,
                    file=source_file,
                    line=line_no,
                    status=Status.CONNECTED,
                    snippet=f"def {view_name}(request): ...",
                    chain=[view_name],
                    note="",
                )
            )
        edges.append(Edge(from_id=url_id, to_id=view_id, status=Status.CONNECTED))

    return symbols, edges
