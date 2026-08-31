"""GraphQL: a query names fields, a schema defines them, and nothing checks that they agree.

This is the same bug as a `fetch` to a route that no longer exists, in a place the route
readers cannot see. A GraphQL API has ONE endpoint - `/graphql` - so every adapter in this
project reports it as a single connected route and stops. saleor is GraphQL-first, and a
scan of 847,000 lines found nine REST routes and said nothing about the actual API.

The seam:

    schema.graphql          type Query { orders(first: Int): OrderConnection }
    orders.ts               gql`query { orders(first: 10) { id totalPrice } }`

A field renamed in the schema breaks every query naming the old one, and neither file is
invalid on its own. That is exactly the cross-language gap this tool exists for.

**A schema field that nothing in this repository queries is NOT dead.** A GraphQL API is
usually consumed by clients that live somewhere else - a mobile app, a partner, a customer.
So an unqueried field is reported `uncertain` with the reason, never `unused`. Getting that
wrong would tell a team to delete their public API.
"""

from __future__ import annotations

import os
import pathlib
import re

from seamcheck.graph import Edge, Status, Symbol

_SCHEMA_EXTENSIONS = (".graphql", ".gql", ".graphqls")
_CLIENT_EXTENSIONS = (".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx", ".py")

_SKIP = {
    "node_modules", ".git", "dist", "build", "coverage", ".next", "__pycache__",
    "venv", ".venv", "site-packages", "vendor", "corpus",
}

# `type Query {` / `interface Node {` / `extend type Mutation {` - the HEAD of a block.
# The body is found by counting braces rather than by regex: a GraphQL description is a
# string that routinely contains one, and `[^}]*` stopped at the first, which truncated
# saleor's Mutation type and lost every field after it.
_TYPE_HEAD_RE = re.compile(r"^[ \t]*(?:extend\s+)?(type|interface|input)\s+(\w+)[^{\n]*\{", re.M)

# Descriptions are stripped before parsing: they are prose, and prose contains braces,
# colons and words that look exactly like field declarations.
_DESCRIPTION_RE = re.compile(r'"""(?:[^"]|"(?!""))*"""|"(?:\\.|[^"\\])*"', re.S)

# Introspection meta-fields, valid on every type in every GraphQL schema and declared in
# none of them. A client selecting __typename is not naming a field the schema forgot.
_META_FIELDS = frozenset({"__typename", "__schema", "__type", "__typeKind"})

# Fields are found by scanning at paren depth ZERO, not by regex. An argument list
# contains its own `name:` pairs, and a directive inside one - `@deprecated(reason: "...")`
# - nests a paren that any `\([^)]*\)` stops at, which silently lost every field declared
# with a deprecated argument.

# gql`...` / graphql`...` / graphql(`...`) - how an operation is written in JS and TS.
_TAGGED_RE = re.compile(r"(?:gql|graphql)\s*\(?\s*`([^`]*)`", re.S)
# Python clients keep operations in plain strings; a triple-quoted one containing an
# operation keyword is the only shape worth guessing at.
_PY_OPERATION_RE = re.compile(
    r'"""\s*((?:query|mutation|subscription|fragment)\b[^"]*)"""', re.S)

_UNQUERIED_NOTE = (
    "Defined in the schema and queried nowhere in this repository. That is NOT evidence "
    "it is unused: a GraphQL API is normally consumed by clients that live in another "
    "repository - a mobile app, a partner, a customer - and this scan cannot see them. "
    "Read it as 'no caller here', never as 'safe to delete'."
)
_UNKNOWN_NOTE = (
    "Selected by a query in this repository, and no type in the schema defines a field "
    "with this name. Either the schema dropped it, the query was written against a "
    "different service, or the field is provided by a schema this scan cannot see."
)


def _walk(root: str, extensions: tuple[str, ...]) -> list[str]:
    found: list[str] = []
    for directory, subdirectories, names in os.walk(root):
        subdirectories[:] = [d for d in subdirectories
                             if d not in _SKIP and not d.startswith(".")]
        for name in sorted(names):
            if name.endswith(extensions):
                found.append(os.path.join(directory, name))
    return found


def _is_test(path: str) -> bool:
    name = os.path.basename(path)
    parts = pathlib.Path(path).parts
    # Generated files are a COPY of what a tool derived from the schema, so reading them
    # asks the schema about itself: saleor-dashboard's hooks.generated.ts alone produced
    # 5,643 selections and 495 phantom fields.
    return (name.startswith("test_") or name.endswith(("_test.py", ".test.ts", ".test.js",
            ".spec.ts", ".spec.js", ".generated.ts", ".generated.tsx", ".gen.ts"))
            or "tests" in parts or "test" in parts or "__tests__" in parts
            or "__generated__" in parts or "generated" in parts or "benchmark" in parts)


def _read(path: str) -> str:
    try:
        return pathlib.Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _blank_descriptions(text: str) -> str:
    """Descriptions replaced by spaces of the same shape, so line numbers survive."""
    return _DESCRIPTION_RE.sub(
        lambda m: "".join(c if c == "\n" else " " for c in m.group(0)), text)


def _fields_in(body: str) -> list[tuple[str, int]]:
    """(field name, offset) for each declaration, ignoring anything inside an argument list."""
    found: list[tuple[str, int]] = []
    depth = 0
    index = 0
    length = len(body)
    while index < length:
        character = body[index]
        if character == "(":
            depth += 1
        elif character == ")":
            depth = max(0, depth - 1)
        elif depth == 0 and (character.isalpha() or character == "_"):
            start = index
            while index < length and (body[index].isalnum() or body[index] == "_"):
                index += 1
            # A name is a field when what follows it is `:` or an argument list.
            if body[index:].lstrip(" \t\n")[:1] in (":", "("):
                found.append((body[start:index], start))
            continue
        index += 1
    return found


def _body_of(text: str, open_brace: int) -> tuple[str, int]:
    """The block starting at `open_brace`, found by counting braces."""
    depth = 0
    for index in range(open_brace, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return text[open_brace + 1:index], open_brace + 1
    return text[open_brace + 1:], open_brace + 1


def schema_fields(root: str) -> dict[str, list[tuple[str, str, int]]]:
    """type name -> [(field, file, line)] for every type the schema declares."""
    types: dict[str, list[tuple[str, str, int]]] = {}
    for path in _walk(root, _SCHEMA_EXTENSIONS):
        text = _blank_descriptions(_read(path))
        relative = os.path.relpath(path, root)
        for match in _TYPE_HEAD_RE.finditer(text):
            type_name = match.group(2)
            body, start = _body_of(text, match.end() - 1)
            base_line = text.count("\n", 0, start) + 1
            for name, offset in _fields_in(body):
                line = base_line + body.count("\n", 0, offset)
                types.setdefault(type_name, []).append((name, relative, line))
    return types


# A file declaring types, enums, inputs or a schema block IS the schema. saleor's
# schema.graphql happens to contain the word `query`, and treating it as an operation file
# yielded 85,162 "selections" - every enum value and every word of every description.
_IS_SCHEMA_RE = re.compile(r"^\s*(?:extend\s+)?(?:type|enum|input|union|scalar|schema)\s", re.M)


def _selected_fields(operation: str) -> list[str]:
    """Field names selected anywhere in an operation body.

    Deliberately flat rather than type-resolved: following a selection set through the
    schema needs the schema, and the useful claim here does not. "No type defines a field
    with this name" is checkable and true; "Order has no field totalPrice" would need
    resolution this reader does not do, and guessing at it would produce confident
    nonsense on any aliased or fragmented query.
    """
    # `${AccountErrorFragmentDoc}` is a JavaScript reference spliced into the template -
    # the codegen way of composing fragments - and every one of them read as a field the
    # schema had never heard of. It is code, not a selection.
    body = re.sub(r"\$\{[^}]*\}", " ", operation)
    body = re.sub(r"#[^\n]*", " ", body)
    # `@include(if: $x)` and `@skip` are DIRECTIVES: they modify a selection, they are not
    # one. The name after the @ is not a field and no schema will ever define it.
    body = re.sub(r"@\w+\s*(?:\([^)]*\))?", " ", body)
    body = re.sub(r"\([^)]*\)", " ", body)          # arguments are not fields
    # Fragment spreads and type conditions name a FRAGMENT or a TYPE, never a field.
    body = re.sub(r"\.\.\.\s*on\s+\w+", " ", body)
    body = re.sub(r"\.\.\.\s*\w+", " ", body)
    body = re.sub(r"\bon\s+\w+", " ", body)
    # `query Orders {` names the OPERATION, not a field, and `fragment F on Order` names
    # the fragment. Reading either as a selection invents a field the schema will never
    # define - a false "unresolved" on every named query in the project.
    body = re.sub(r"\b(?:query|mutation|subscription)\s+\w+", " ", body)
    body = re.sub(r"\bfragment\s+\w+", " ", body)
    body = re.sub(r"\$\w+", " ", body)             # variables are not fields
    names = []
    depth = 0
    for token in re.finditer(r"[{}]|(\w+)\s*(?::\s*(\w+))?", body):
        if token.group(0) == "{":
            depth += 1
            continue
        if token.group(0) == "}":
            depth -= 1
            continue
        # Only inside a selection set. Outside the braces sit operation names, variable
        # declarations and, in a file that is not what it claimed to be, prose.
        if depth <= 0:
            continue
        alias, real = token.groups()
        name = real or alias
        if not name or name.isdigit():
            continue
        if name in ("query", "mutation", "subscription", "fragment", "on", "true",
                    "false", "null"):
            continue
        # SCREAMING_CASE is an enum value by every GraphQL convention, never a field.
        if name.isupper() and len(name) > 1:
            continue
        names.append(name)
    return names


def client_operations(root: str) -> list[tuple[str, str, int]]:
    """(field, file, line) for every field a query in this repository selects."""
    found: list[tuple[str, str, int]] = []
    for path in _walk(root, _CLIENT_EXTENSIONS + _SCHEMA_EXTENSIONS):
        if _is_test(path):
            # A test's query is written to exercise the schema, including on purpose the
            # shapes that should fail. Reading them as the application's own usage makes
            # every negative test look like a broken query.
            continue
        text = _read(path)
        if "gql" not in text and "graphql" not in text and "query" not in text:
            continue
        relative = os.path.relpath(path, root)
        patterns = [_TAGGED_RE] if not path.endswith(".py") else [_PY_OPERATION_RE]
        if path.endswith(_SCHEMA_EXTENSIONS):
            # A .graphql file holding an operation rather than a schema.
            if _IS_SCHEMA_RE.search(text):
                continue
            if re.search(r"^\s*(query|mutation|subscription)\b", text, re.M):
                patterns = [re.compile(r"((?:query|mutation|subscription)\b.*)", re.S)]
            else:
                continue
        for pattern in patterns:
            for match in pattern.finditer(text):
                operation = match.group(1)
                if not re.search(r"\b(query|mutation|subscription|fragment)\b", operation):
                    continue
                # An introspection query asks the SERVER about its schema, and its fields
                # - types, fields, interfaces, ofType - are defined by GraphQL itself, not
                # by this schema. Checking them against the SDL reports the protocol as
                # missing from the API that implements it.
                if "__schema" in operation or "__type" in operation:
                    continue
                base = text.count("\n", 0, match.start(1)) + 1
                for name in _selected_fields(operation):
                    found.append((name, relative, base))
    return found


def extract_graphql(root: str) -> tuple[list[Symbol], list[Edge]]:
    """Schema fields, the queries that select them, and the disagreements between them."""
    schema = schema_fields(root)
    if not schema:
        return [], []

    defined: dict[str, tuple[str, str, int]] = {}
    for type_name, fields in schema.items():
        for field, file, line in fields:
            defined.setdefault(field, (type_name, file, line))

    selected: dict[str, tuple[str, int]] = {}
    for name, file, line in client_operations(root):
        if name in _META_FIELDS:
            continue
        selected.setdefault(name, (file, line))

    symbols: list[Symbol] = []
    edges: list[Edge] = []
    for field, (type_name, file, line) in sorted(defined.items()):
        used = field in selected
        symbols.append(Symbol(
            id=f"graphql_field:{type_name}.{field}", kind="graphql_field",
            label=f"{type_name}.{field}", sub=type_name, file=file, line=line,
            status=Status.CONNECTED if used else Status.UNCERTAIN,
            snippet=f"{field}: ...", chain=[type_name, field],
            note="" if used else _UNQUERIED_NOTE,
        ))

    for field, (file, line) in sorted(selected.items()):
        known = field in defined
        symbol_id = f"graphql_selection:{field}"
        symbols.append(Symbol(
            id=symbol_id, kind="graphql_selection", label=field, sub="query",
            file=file, line=line,
            status=Status.CONNECTED if known else Status.UNRESOLVED,
            snippet=f"{{ {field} }}", chain=[field],
            note="" if known else _UNKNOWN_NOTE,
        ))
        if known:
            type_name = defined[field][0]
            edges.append(Edge(from_id=symbol_id,
                              to_id=f"graphql_field:{type_name}.{field}",
                              status=Status.CONNECTED))
    return symbols, edges
