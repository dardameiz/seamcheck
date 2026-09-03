"""URL and view symbols, read from Django's own resolved URLconf tree."""

from __future__ import annotations

import importlib
import inspect
from collections.abc import Iterator

from django.urls.resolvers import URLPattern, URLResolver

from seamcheck.graph import Edge, Status, Symbol


def _walk_patterns(
    patterns, prefix: str = "", namespace: str = ""
) -> Iterator[tuple[str, object, str]]:
    """Yield (full_path, view_func, dotted route name) for every leaf under `patterns`.

    A URLconf is a tree, not a list: include() produces a URLResolver holding its own
    nested url_patterns behind a path prefix, and only URLPattern leaves carry a real
    callback. Iterating one level would miss every include()-mounted URL.

    The name is carried because that is the handle a project actually uses. `{% url %}`,
    `reverse()` and `redirect()` all reference a route by name, never by path, so a walk
    that drops `entry.name` cannot tell whether anything points at the route at all - and
    77% of one real project's routes sat unmeasured for exactly that reason.
    """
    for entry in patterns:
        entry_prefix = prefix + str(entry.pattern)
        if isinstance(entry, URLPattern):
            name = getattr(entry, "name", None) or ""
            yield entry_prefix, entry.callback, f"{namespace}:{name}" if namespace and name else name
        elif isinstance(entry, URLResolver):
            nested = getattr(entry, "namespace", None) or getattr(entry, "app_name", None)
            yield from _walk_patterns(
                entry.url_patterns, entry_prefix,
                f"{namespace}:{nested}" if namespace and nested else (nested or namespace),
            )


def _unwrap(view_func):
    """The real view behind any decorators.

    functools.wraps copies __module__ and __name__ onto the wrapper, so a decorated
    admin view claims to live in the app that decorated it while its code is in
    django/utils/decorators.py. Both the ownership test and the file:line evidence
    have to look through that.
    """
    return inspect.unwrap(view_func)


def _source_location(view_func) -> tuple[str, int | None]:
    view_func = _unwrap(view_func)
    try:
        return inspect.getsourcefile(view_func) or "", inspect.getsourcelines(view_func)[1]
    except (OSError, TypeError):
        return "", None


def _is_first_party(view_func, prefixes: list[str]) -> bool:
    root = (getattr(_unwrap(view_func), "__module__", "") or "").split(".")[0]
    return root in prefixes


def extract_django_urls_views(
    urlconf_module: str, first_party_prefixes: list[str] | None = None
) -> tuple[list[Symbol], list[Edge]]:
    """Walk the URLconf tree.

    With `first_party_prefixes`, third-party routes (Django admin, allauth) are skipped:
    they are 88% of this project's URL table and would drown the code the map is about.
    """
    module = importlib.import_module(urlconf_module)
    symbols: list[Symbol] = []
    edges: list[Edge] = []
    seen_view_ids: set[str] = set()

    for pattern_str, view_func, _name in _walk_patterns(module.urlpatterns):
        if first_party_prefixes and not _is_first_party(view_func, first_party_prefixes):
            continue
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
                    # A view IS a function. Naming it here is what lets the function
                    # filter draw everything `submit_push` touches beside the route that
                    # reaches it.
                    owner=view_name.rsplit(".", 1)[-1],
                )
            )
        edges.append(Edge(from_id=url_id, to_id=view_id, status=Status.CONNECTED))

    return symbols, edges


def route_name_index(urlconf_module: str) -> dict[str, str]:
    """`{% url %}` name -> the route path it resolves to.

    Both the bare name and the namespaced form are keys: a template may write
    `{% url 'profile' %}` or `{% url 'accounts:profile' %}` for the same route, and the
    reference is real either way. Unnamed routes contribute nothing - there is no handle
    to reference them by.

    Deliberately NOT filtered by first_party_prefixes. A reference is evidence about the
    route it points at, and dropping a third-party route here would leave a first-party
    template's `{% url %}` looking like a name that resolves to nothing.
    """
    module = importlib.import_module(urlconf_module)
    index: dict[str, str] = {}
    for pattern_str, _view, name in _walk_patterns(module.urlpatterns):
        if name:
            index.setdefault(name, pattern_str)
            index.setdefault(name.rsplit(":", 1)[-1], pattern_str)
    return index
