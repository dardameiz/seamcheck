"""Firebase: callable functions against their exports, and collections against the rules.

Firebase splits cleanly into a half this can check exactly and a half it cannot, and being
straight about which is which is the whole job here.

**Callable functions check exactly.** `httpsCallable(functions, 'sendEmail')` names a
function that `functions/index.js` either exports or does not. That is the same shape as a
fetch against a route, with the same verdicts.

**Firestore collections have no schema to check against.** Firestore is schemaless: there
is no migration, no DDL, nothing in the repository that declares `users` exists. Asking
"does this collection exist" of a static reader is asking a question the answer to which is
not in the files. So it is never called `unresolved`.

What IS declared is `firestore.rules`, and that turns out to be the more useful check
anyway. Every path the client touches should be matched by a `match /users/{id}` block; one
that is not is either denied at runtime - a feature that silently does nothing - or, in a
ruleset with a permissive catch-all, readable by the whole internet. That is a security
question, and it is answerable from source.

The reverse is worth saying too: a `match` block for a collection no client code touches is
a rule guarding nothing, which is usually a collection that got renamed.
"""

from __future__ import annotations

import json
import os
import pathlib
import re

from seamcheck.adapters.discovery import SKIP_DIRS
from seamcheck.extractors.js_extractor import _parse_files, _walk
from seamcheck.graph import Edge, Status, Symbol

_EXTENSIONS = (".js", ".mjs", ".cjs", ".jsx", ".ts", ".mts", ".cts", ".tsx")

# `match /users/{userId}` and `match /{document=**}`. The wildcard segments are what make a
# rule cover more than its literal text, so they are kept and compared as patterns.
_MATCH = re.compile(r"""match\s+(?P<path>/[^\s{]*)\s*\{""")
# The catch-all every starter ruleset ships with. A project that still has it has no
# per-collection rules at all, and saying "no rule for users" would be technically true and
# completely beside the point.
_CATCH_ALL = re.compile(r"""\{document\s*=\s*\*\*\}""")

# A minified bundle is the same code already read from source, and megabytes of it.
_MAX_BYTES = 400_000
_CALLABLE_WRAPPERS = ("onCall", "onRequest")
_NEEDLES = ("firebase", "Firebase", "firestore", "collection(", "httpsCallable")



def _mentions(path: str, needles: tuple[str, ...]) -> bool:
    """Whether a file is worth parsing at all.

    Reading a file as text costs microseconds; parsing it costs milliseconds and a
    subprocess. Feeding every JavaScript file in a large repo to the parser to look for a
    handful of call sites crashed it, and a crashed parser loses the ENTIRE JavaScript half
    of the graph - not just this extractor's part of it.
    """
    try:
        if os.path.getsize(path) > _MAX_BYTES:
            return False
        with open(path, encoding="utf-8", errors="replace") as handle:
            text = handle.read()
    except OSError:
        return False
    return any(needle in text for needle in needles)

def _files(root: str) -> list[str]:
    found: list[str] = []
    for here, subdirectories, names in os.walk(root):
        subdirectories[:] = [
            d for d in subdirectories if d not in SKIP_DIRS and not d.startswith(".")
        ]
        found.extend(
            os.path.join(here, name) for name in names if name.endswith(_EXTENSIONS)
        )
    return found


def _literal(node) -> str | None:
    if not isinstance(node, dict):
        return None
    if node.get("type") == "Literal" and isinstance(node.get("value"), str):
        return node["value"]
    if node.get("type") == "TemplateLiteral" and not node.get("expressions"):
        parts = node.get("quasis") or []
        if len(parts) == 1:
            return ((parts[0].get("value") or {}).get("cooked")) or None
    return None


def _line(node) -> int:
    return ((node.get("loc") or {}).get("start") or {}).get("line") or 0


def detected(root: str) -> bool:
    if pathlib.Path(root, "firebase.json").is_file():
        return True
    package = pathlib.Path(root, "package.json")
    if package.is_file():
        try:
            text = package.read_text(encoding="utf-8")
            return '"firebase"' in text or "firebase-admin" in text or "firebase-functions" in text
        except OSError:
            return False
    return False


def _functions_dir(root: str) -> str:
    """Where the Cloud Functions live. `firebase.json` says; the default is `functions`."""
    config = pathlib.Path(root, "firebase.json")
    if config.is_file():
        try:
            data = json.loads(config.read_text(encoding="utf-8"))
            entry = data.get("functions")
            if isinstance(entry, dict) and entry.get("source"):
                return str(entry["source"])
            if isinstance(entry, list) and entry and isinstance(entry[0], dict):
                return str(entry[0].get("source") or "functions")
        except (OSError, ValueError):
            pass
    return "functions"


def _exported_callables(root: str, folder: str) -> dict[str, tuple[str, int]]:
    """`exports.sendEmail = functions.https.onCall(...)` and its modern equivalents."""
    base = pathlib.Path(root, folder)
    if not base.is_dir():
        return {}
    paths = [
        str(p) for p in base.rglob("*")
        if p.suffix in (".js", ".mjs", ".cjs", ".ts") and "node_modules" not in p.parts
    ]
    if not paths:
        return {}
    found: dict[str, tuple[str, int]] = {}
    for path, tree in _parse_files(paths, report_failures=False).items():
        rel = os.path.relpath(path, root)
        for node, _ in _walk(tree):
            name = None
            value = None
            # exports.x = ...  /  module.exports.x = ...
            if node.get("type") == "AssignmentExpression":
                left = node.get("left") or {}
                if left.get("type") == "MemberExpression":
                    obj = left.get("object") or {}
                    root_name = obj.get("name") or ((obj.get("property") or {}).get("name"))
                    if root_name in ("exports", "module"):
                        name = ((left.get("property") or {}).get("name"))
                        value = node.get("right")
            # export const x = ...
            elif node.get("type") == "ExportNamedDeclaration":
                declaration = node.get("declaration") or {}
                for declarator in declaration.get("declarations") or []:
                    name = ((declarator.get("id") or {}).get("name"))
                    value = declarator.get("init")
            if not name or not isinstance(value, dict):
                continue
            if _wraps_callable(value):
                found.setdefault(name, (rel, _line(node)))
    return found


def _wraps_callable(node: dict) -> bool:
    """Whether an expression is a Cloud Function, however it was spelled.

    v1 is `functions.https.onCall(fn)`, v2 is `onCall(fn)` imported from
    `firebase-functions/v2/https`, and both appear in the same codebase during a migration.
    """
    for _ in range(6):
        if not isinstance(node, dict):
            return False
        if node.get("type") != "CallExpression":
            return False
        callee = node.get("callee") or {}
        name = (
            callee.get("name")
            or ((callee.get("property") or {}).get("name"))
            or ""
        )
        if name in _CALLABLE_WRAPPERS:
            return True
        # `functions.region('x').https.onCall(...)` - keep unwrapping.
        inner = callee.get("object")
        if not isinstance(inner, dict):
            return False
        node = inner if inner.get("type") == "CallExpression" else {}
    return False


def _rule_paths(root: str) -> tuple[list[str], bool, str]:
    """The collection paths firestore.rules matches, and whether it has a catch-all."""
    rules = pathlib.Path(root, "firestore.rules")
    if not rules.is_file():
        return [], False, ""
    try:
        text = rules.read_text(encoding="utf-8")
    except OSError:
        return [], False, ""
    paths = [m.group("path") for m in _MATCH.finditer(text)]
    # `/databases/{database}/documents` is the wrapper every ruleset has; it matches
    # nothing on its own.
    collections = [
        p.strip("/").split("/")[0]
        for p in paths
        if not p.startswith("/databases") and p.strip("/")
    ]
    return (
        [c for c in dict.fromkeys(collections) if not c.startswith("{")],
        bool(_CATCH_ALL.search(text)),
        "firestore.rules",
    )


def _client_uses(root: str) -> tuple[list, list]:
    """`collection(db, 'users')` and `httpsCallable(fns, 'sendEmail')`."""
    collections: list[tuple[str, str, int, bool]] = []
    callables: list[tuple[str, str, int, bool]] = []
    paths = [p for p in _files(root) if _mentions(p, _NEEDLES)]
    if not paths:
        return collections, callables
    for path, tree in _parse_files(paths, report_failures=False).items():
        rel = os.path.relpath(path, root)
        for node, _ in _walk(tree):
            if node.get("type") != "CallExpression":
                continue
            callee = node.get("callee") or {}
            name = callee.get("name") or ((callee.get("property") or {}).get("name")) or ""
            args = node.get("arguments") or []
            line = _line(node)
            if name in ("collection", "collectionGroup") and args:
                # collection(db, 'users') in the modular SDK; collection('users') in compat.
                target = args[1] if len(args) > 1 else args[0]
                value = _literal(target)
                first = (value or "").strip("/").split("/")[0]
                collections.append((first, rel, line, value is None))
            elif name == "doc" and len(args) > 1:
                value = _literal(args[1])
                if value:
                    collections.append((value.strip("/").split("/")[0], rel, line, False))
            elif name == "httpsCallable" and args:
                target = args[1] if len(args) > 1 else args[0]
                value = _literal(target)
                callables.append((value or "", rel, line, value is None))
    return collections, callables


def extract_firebase(root: str) -> tuple[list[Symbol], list[Edge]]:
    if not detected(root):
        return [], []

    folder = _functions_dir(root)
    exported = _exported_callables(root, folder)
    rules, catch_all, rules_file = _rule_paths(root)
    collections, callables = _client_uses(root)
    symbols: list[Symbol] = []
    edges: list[Edge] = []

    called = {name for name, _, _, dynamic in callables if not dynamic and name}
    touched = {name for name, _, _, dynamic in collections if not dynamic and name}

    for name, (file, line) in exported.items():
        used = name in called
        symbols.append(Symbol(
            id=f"cloud_function:{name}", kind="cloud_function", label=name,
            sub="callable", file=file, line=line,
            # A callable can also be invoked from another client, a mobile app or a
            # scheduled job that is not in this repository, so "nothing calls it" is not
            # the same as "dead".
            status=Status.CONNECTED if used else Status.UNCERTAIN,
            snippet=f"exports.{name} = ...onCall(...)", chain=[name],
            note="" if used else "No httpsCallable() in this repo names it. It may be "
                                "called from a mobile client or another project.",
        ))

    for name, file, line, dynamic in callables:
        if dynamic or not name:
            symbols.append(Symbol(
                id=f"cloud_function_use:<dynamic>:{file}:{line}", kind="cloud_function_use",
                label="<assembled at runtime>", sub="httpsCallable", file=file, line=line,
                status=Status.UNCERTAIN, snippet="", chain=[],
                note="The function name is a variable.",
            ))
            continue
        known = name in exported
        symbols.append(Symbol(
            id=f"cloud_function_use:{name}:{file}:{line}", kind="cloud_function_use",
            label=name, sub="httpsCallable", file=file, line=line,
            status=Status.CONNECTED if known else Status.UNRESOLVED,
            snippet=f"httpsCallable(fns, '{name}')", chain=[name],
            note="" if known else f"Nothing in {folder}/ exports a callable with that name.",
        ))
        if known:
            edges.append(Edge(
                f"cloud_function_use:{name}:{file}:{line}", f"cloud_function:{name}",
                Status.CONNECTED,
            ))

    # ── collections, judged against the RULES rather than against a schema ──
    for name, file, line, dynamic in collections:
        if dynamic or not name:
            symbols.append(Symbol(
                id=f"firestore_collection:<dynamic>:{file}:{line}",
                kind="firestore_collection", label="<assembled at runtime>",
                sub="collection", file=file, line=line, status=Status.UNCERTAIN,
                snippet="", chain=[], note="The collection name is a variable.",
            ))
            continue
        covered = name in rules
        if covered:
            status, note = Status.CONNECTED, ""
        elif catch_all:
            status, note = (
                Status.UNCERTAIN,
                "No rule names this collection; the ruleset has a {document=**} catch-all, "
                "so whatever that grants is what applies.",
            )
        elif rules:
            status, note = (
                Status.UNRESOLVED,
                "No match block in firestore.rules covers this collection. Firestore "
                "denies by default, so these reads fail silently in production.",
            )
        else:
            status, note = (
                Status.UNCERTAIN,
                "No firestore.rules in this repo, so there is nothing to check the "
                "collection against.",
            )
        symbols.append(Symbol(
            id=f"firestore_collection:{name}:{file}:{line}", kind="firestore_collection",
            label=name, sub="collection", file=file, line=line, status=status,
            snippet=f"collection(db, '{name}')", chain=[name], note=note,
        ))
        if covered:
            edges.append(Edge(
                f"firestore_collection:{name}:{file}:{line}", f"firestore_rule:{name}",
                Status.CONNECTED,
            ))

    for name in rules:
        used = name in touched
        symbols.append(Symbol(
            id=f"firestore_rule:{name}", kind="firestore_rule", label=name,
            sub="match block", file=rules_file, line=1,
            status=Status.CONNECTED if used else Status.UNUSED,
            snippet=f"match /{name}/...", chain=[name],
            note="" if used else "No client code in this repo touches this collection. A "
                                "rule guarding nothing is usually a rename left behind.",
        ))

    return symbols, edges
