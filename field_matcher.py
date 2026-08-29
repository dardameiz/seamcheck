"""Field-level matching: what a view sends versus what the consumer actually reads."""

from __future__ import annotations

import ast
import re

from signal_map.extractors.js_extractor import parse_js_source
from signal_map.graph import Edge, Status, Symbol

_JSON_SCRIPT_TAG_RE = re.compile(r"json_script:\"([^\"]+)\"")
_GET_ELEMENT_RE = re.compile(r"getElementById\(['\"]([^'\"]+)['\"]\)")

_COMPUTED_NOTE = "Computed or spread access on the response -- cannot resolve per-field."
_NOT_SENT_NOTE = "Read by JS but never sent by this response."
_FUNCTION_TYPES = ("ArrowFunctionExpression", "FunctionExpression", "FunctionDeclaration")


def _walk(node):
    if isinstance(node, dict):
        if node.get("type"):
            yield node
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk(item)


def _is_json_call(node: dict) -> bool:
    callee = node.get("callee") or {}
    return (
        node.get("type") == "CallExpression"
        and callee.get("type") == "MemberExpression"
        and (callee.get("property") or {}).get("name") == "json"
    )


def _pattern_names(pattern: dict) -> tuple[set[str], set[str]]:
    """(bound_identifiers, destructured_keys) for the left-hand side of an assignment."""
    if pattern.get("type") == "Identifier":
        return {pattern["name"]}, set()
    if pattern.get("type") == "ObjectPattern":
        keys = {
            (prop.get("key") or {}).get("name")
            for prop in pattern.get("properties", [])
            if (prop.get("key") or {}).get("name")
        }
        return set(), keys
    return set(), set()


def _json_result_bindings(ast_root: dict) -> tuple[set[str], set[str]]:
    """Identifiers holding a .json() result, plus keys destructured straight off it.

    The response variable is named `data` less than half the time in real code; assuming
    that name marks every other field UNUSED, which is the one claim this tool must never
    make without evidence.
    """
    names: set[str] = set()
    destructured: set[str] = set()

    for node in _walk(ast_root):
        if node.get("type") == "VariableDeclarator":
            init = node.get("init") or {}
            inner = init.get("argument") if init.get("type") == "AwaitExpression" else init
            if isinstance(inner, dict) and _is_json_call(inner):
                bound, keys = _pattern_names(node.get("id") or {})
                names |= bound
                destructured |= keys
        elif node.get("type") == "CallExpression":
            callee = node.get("callee") or {}
            if (callee.get("property") or {}).get("name") != "then":
                continue
            if not any(_is_json_call(inner) for inner in _walk(callee.get("object") or {})):
                continue
            for argument in node.get("arguments") or []:
                if argument.get("type") not in _FUNCTION_TYPES:
                    continue
                parameters = argument.get("params") or []
                if parameters:
                    bound, keys = _pattern_names(parameters[0])
                    names |= bound
                    destructured |= keys
    return names, destructured


def _reads_and_uncertainty(js_source: str) -> tuple[set[str], bool]:
    """(field names read, whether access was computed/spread and so unresolvable)."""
    ast_root = parse_js_source(js_source)
    names, reads = _json_result_bindings(ast_root)
    if not names and not reads:
        return set(), False

    uncertain = False
    for node in _walk(ast_root):
        node_type = node.get("type")
        if node_type == "MemberExpression":
            obj = node.get("object") or {}
            if obj.get("type") == "Identifier" and obj.get("name") in names:
                if node.get("computed"):
                    uncertain = True
                elif (node.get("property") or {}).get("name"):
                    reads.add(node["property"]["name"])
        elif node_type == "SpreadElement":
            argument = node.get("argument") or {}
            if argument.get("type") == "Identifier" and argument.get("name") in names:
                uncertain = True
    return reads, uncertain


def _json_response_keys(view_source: str) -> list[str]:
    """Keys of the dict passed to JsonResponse(...), located by call, not by position."""
    tree = ast.parse(view_source)
    for node in ast.walk(tree):
        if node.__class__ is not ast.Call:
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
        if name != "JsonResponse":
            continue
        for argument in node.args:
            if isinstance(argument, ast.Dict):
                return [
                    key.value
                    for key in argument.keys
                    if isinstance(key, ast.Constant) and isinstance(key.value, str)
                ]
    return []


def _field_symbol(kind: str, key: str, status: Status, snippet: str, note: str = "") -> Symbol:
    return Symbol(
        id=f"{kind}:{key}", kind=kind, label=key, sub="", file="", line=None,
        status=status, snippet=snippet, chain=[key], note=note,
    )


def match_json_response_fields(view_source: str, js_source: str) -> tuple[list[Symbol], list[Edge]]:
    sent_keys = _json_response_keys(view_source)
    read_keys, uncertain = _reads_and_uncertainty(js_source)

    if uncertain:
        return [
            _field_symbol("json_field", key, Status.UNCERTAIN, f'"{key}": ...', _COMPUTED_NOTE)
            for key in sent_keys
        ], []

    symbols = [
        _field_symbol(
            "json_field", key,
            Status.CONNECTED if key in read_keys else Status.UNUSED,
            f'"{key}": ...',
        )
        for key in sent_keys
    ]
    symbols.extend(
        _field_symbol("json_field", key, Status.UNRESOLVED, f"data.{key}", _NOT_SENT_NOTE)
        for key in sorted(read_keys - set(sent_keys))
    )
    return symbols, []


def _render_context_keys(view_source: str) -> list[str]:
    tree = ast.parse(view_source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "render":
            for argument in node.args:
                if isinstance(argument, ast.Dict):
                    return [
                        key.value
                        for key in argument.keys
                        if isinstance(key, ast.Constant) and isinstance(key.value, str)
                    ]
    return []


def _is_rendered(key: str, template_source: str) -> bool:
    escaped = re.escape(key)
    # {{ key }}, {{ key.attr }}, {{ key|filter }}, and any {% tag ... key ... %}
    return bool(
        re.search(r"\{\{\s*" + escaped + r"\s*[.|}]", template_source)
        or re.search(r"\{%[^%]*\b" + escaped + r"\b[^%]*%\}", template_source)
    )


def match_template_context_fields(
    view_source: str, template_source: str
) -> tuple[list[Symbol], list[Edge]]:
    return [
        _field_symbol(
            "context_field", key,
            Status.CONNECTED if _is_rendered(key, template_source) else Status.UNUSED,
            f'"{key}": ...',
        )
        for key in _render_context_keys(view_source)
    ], []


def match_json_script_bridge(template_source: str, js_source: str) -> list[Edge]:
    template_ids = set(_JSON_SCRIPT_TAG_RE.findall(template_source))
    js_ids = set(_GET_ELEMENT_RE.findall(js_source))
    return [
        Edge(from_id=f"json_script:{element_id}", to_id=f"json_script:{element_id}", status=Status.CONNECTED)
        for element_id in sorted(template_ids & js_ids)
    ]
