from __future__ import annotations

from signal_map.graph import Edge, Status, Symbol


def _normalize(path: str) -> str:
    return path.strip("/")


def match_js_to_django(django_symbols: list[Symbol], js_symbols: list[Symbol]) -> list[Edge]:
    url_by_path = {_normalize(s.label): s for s in django_symbols if s.kind == "url"}

    edges: list[Edge] = []
    for target in js_symbols:
        if target.kind != "fetch_target":
            continue

        matched_url = url_by_path.get(_normalize(target.label))
        if matched_url:
            edges.append(Edge(from_id=target.id, to_id=matched_url.id, status=Status.CONNECTED))
        else:
            edges.append(Edge(from_id=target.id, to_id=target.id, status=Status.UNRESOLVED))

    return edges
