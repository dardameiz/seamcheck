"""DOM elements JavaScript touches, and whether it writes them or only reads them."""

from __future__ import annotations

import os
import re

from signal_map.extractors.js_extractor import _parse_files, _walk
from signal_map.graph import Status, Symbol

_SELECTOR_CALLEES = frozenset({"getElementById", "querySelector", "querySelectorAll", "closest"})

# Assigning to one of these, or to anything under .style / .dataset, mutates the element.
_WRITE_PROPERTIES = frozenset(
    {"textContent", "innerHTML", "innerText", "value", "src", "href", "checked", "disabled"}
)
_WRITE_METHODS = frozenset(
    {"add", "remove", "toggle", "setAttribute", "append", "prepend", "replaceChildren", "insertAdjacentHTML"}
)
_WRITE_NAMESPACES = frozenset({"style", "dataset", "classList"})

_TOKEN_RE = re.compile(r"#([\w-]+)|\.([\w-]+)|\[data-([\w-]+)")


def _selector_tokens(callee_name: str, raw: str) -> list[tuple[str, str]]:
    """(sub, label) pairs a selector string pins down."""
    if callee_name == "getElementById":
        return [("id", raw)] if raw else []
    tokens: list[tuple[str, str]] = []
    for element_id, class_name, data_name in _TOKEN_RE.findall(raw):
        if element_id:
            tokens.append(("id", element_id))
        elif class_name:
            tokens.append(("class", class_name))
        elif data_name:
            tokens.append(("data", data_name))
    return tokens


def _property_names(node) -> set[str]:
    return {
        (inner.get("property") or {}).get("name")
        for inner, _ in _walk(node)
        if inner.get("type") == "MemberExpression" and (inner.get("property") or {}).get("name")
    }


def _selector_calls(node) -> list[dict]:
    return [
        inner
        for inner, _ in _walk(node)
        if inner.get("type") == "CallExpression"
        and (inner.get("callee") or {}).get("type") == "MemberExpression"
        and ((inner.get("callee") or {}).get("property") or {}).get("name") in _SELECTOR_CALLEES
    ]


def _base_object_name(node: dict) -> str | None:
    """The variable a member chain hangs off: `el.style.color` -> 'el', `this.box.x` -> 'this.box'."""
    current = node
    while isinstance(current, dict) and current.get("type") == "MemberExpression":
        obj = current.get("object") or {}
        if obj.get("type") == "Identifier":
            return obj["name"]
        if obj.get("type") == "MemberExpression" and (obj.get("object") or {}).get("type") == "ThisExpression":
            return f"this.{(obj.get('property') or {}).get('name')}"
        current = obj
    return None


def _selector_bindings(ast_root: dict) -> dict[str, dict]:
    """Variables (and `this.x` properties) holding the result of a selector call.

    Real code almost never writes in the same statement it queries in: it binds the
    element once and mutates it later. Matching only same-statement writes found 70 of
    them across this project's 2,300 selectors.
    """
    bindings: dict[str, dict] = {}
    for node, _ in _walk(ast_root):
        node_type = node.get("type")
        if node_type == "VariableDeclarator":
            name = (node.get("id") or {}).get("name")
            calls = _selector_calls(node.get("init") or {})
            if name and calls:
                bindings[name] = calls[0]
        elif node_type == "AssignmentExpression":
            target = node.get("left") or {}
            if (target.get("object") or {}).get("type") == "ThisExpression":
                calls = _selector_calls(node.get("right") or {})
                if calls:
                    bindings[f"this.{(target.get('property') or {}).get('name')}"] = calls[0]
    return bindings


def _writing_call_ids(ast_root: dict) -> set[int]:
    """Selector calls whose element is mutated, directly or through a binding."""
    bindings = _selector_bindings(ast_root)
    writing: set[int] = set()
    for node, _ in _walk(ast_root):
        node_type = node.get("type")
        if node_type == "AssignmentExpression":
            target = node.get("left") or {}
            names = _property_names(target)
            final = (target.get("property") or {}).get("name")
            if final in _WRITE_PROPERTIES or names & _WRITE_NAMESPACES:
                writing.update(id(call) for call in _selector_calls(target))
                bound = bindings.get(_base_object_name(target) or "")
                if bound is not None:
                    writing.add(id(bound))
        elif node_type == "CallExpression":
            callee = node.get("callee") or {}
            if (callee.get("property") or {}).get("name") in _WRITE_METHODS:
                receiver = callee.get("object") or {}
                writing.update(id(call) for call in _selector_calls(receiver))
                bound = bindings.get(_base_object_name(receiver) or "")
                if bound is not None:
                    writing.add(id(bound))
    return writing


def extract_dom_selectors(js_files: list[str]) -> list[Symbol]:
    symbols: list[Symbol] = []
    for path, ast_root in _parse_files([f for f in js_files if os.path.isfile(f)]).items():
        writing = _writing_call_ids(ast_root)
        basename = os.path.basename(path)

        for node, enclosing in _walk(ast_root):
            if node.get("type") != "CallExpression":
                continue
            callee = node.get("callee") or {}
            callee_name = (callee.get("property") or {}).get("name")
            if callee_name not in _SELECTOR_CALLEES:
                continue

            arguments = node.get("arguments") or []
            first = arguments[0] if arguments else {}
            line = ((node.get("loc") or {}).get("start") or {}).get("line")
            access = "write" if id(node) in writing else "read"
            chain = [basename, enclosing] if enclosing else [basename]

            if first.get("type") != "Literal" or not isinstance(first.get("value"), str):
                symbols.append(
                    Symbol(
                        id=f"dom_selector:dynamic:{path}:{line}", kind="dom_selector",
                        label="<dynamic>", sub=f"dynamic:{access}", file=path, line=line,
                        status=Status.UNCERTAIN, snippet=f"{callee_name}(<built at runtime>)",
                        chain=chain,
                        note="Selector built at runtime -- cannot be tied to a template element.",
                    )
                )
                continue

            raw = first["value"]
            for sub, label in _selector_tokens(callee_name, raw):
                symbols.append(
                    Symbol(
                        id=f"dom_selector:{sub}:{label}:{path}:{line}", kind="dom_selector",
                        label=label, sub=f"{sub}:{access}", file=path, line=line,
                        status=Status.UNCERTAIN, snippet=f"{callee_name}('{raw}')",
                        chain=chain, note="",
                    )
                )
    return symbols
