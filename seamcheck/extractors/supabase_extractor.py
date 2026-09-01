"""Supabase: the names client code uses, against the schema the migrations declare.

A Supabase app usually has no backend routes of its own. The browser talks to Postgres
directly - `supabase.from('profiles').select('id, user_name')` - so the seam that matters is
not fetch-to-route, it is **string-to-schema**. The table name, the column names, the RPC
name and the edge-function name are all string literals in JavaScript, and nothing checks
any of them:

  · TypeScript does, IF you run `supabase gen types` - and those types go stale the moment
    someone writes a migration and does not regenerate. That drift IS the bug class.
  · A mistyped COLUMN is the worst of them. PostgREST returns the rows without it, the
    client reads `undefined`, and a blank field ships. Nothing raises.

So this is the same disease as a mistyped route, in the place most people building today
actually keep their data. It reads the declarations out of `supabase/migrations/*.sql` (see
sql_schema_extractor - a plain Postgres schema reader, not a Supabase one) and the uses out
of the JavaScript AST that is already being built.

Two findings here exist nowhere else:

  · A table the schema declares and NO client code touches. `unused`, in the ordinary sense.
  · A table client code reads that has row-level security ON and no policy, or OFF
    altogether. The first is readable by nobody, the second by anyone holding the anon key,
    which is published in the browser bundle. That is a security question answered from
    source.
"""

from __future__ import annotations

import os
import pathlib

from seamcheck.adapters.discovery import SKIP_DIRS
from seamcheck.extractors.js_extractor import _parse_files, _walk
from seamcheck.extractors.sql_schema_extractor import Schema, find_sql, read_schema
from seamcheck.graph import Edge, Status, Symbol

_EXTENSIONS = (".js", ".mjs", ".cjs", ".jsx", ".ts", ".mts", ".cts", ".tsx")

# `.select()` with no argument is `select('*')`, and a star names no column.
# A minified bundle is the same code already read from source, and megabytes of it.
_MAX_BYTES = 400_000
_STAR = ("*", "")
_NEEDLES = ("supabase", "Supabase", "createClient")



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
    """The string a node is, or None when it is assembled at runtime."""
    if not isinstance(node, dict):
        return None
    if node.get("type") == "Literal" and isinstance(node.get("value"), str):
        return node["value"]
    # A single-part template literal is a string with different quotes.
    if node.get("type") == "TemplateLiteral" and not node.get("expressions"):
        parts = node.get("quasis") or []
        if len(parts) == 1:
            return ((parts[0].get("value") or {}).get("cooked")) or None
    return None


def _line(node) -> int:
    return ((node.get("loc") or {}).get("start") or {}).get("line") or 0


def _receiver_name(callee: dict) -> str:
    """What `.from` was called ON: `supabase` (a table) or `supabase.storage` (a bucket)."""
    obj = callee.get("object") or {}
    if obj.get("type") == "MemberExpression":
        return ((obj.get("property") or {}).get("name")) or ""
    return ""


def _table_of_chain(node: dict) -> tuple[str | None, bool]:
    """Walk down a chained call to the `.from(table)` it hangs off.

    `supabase.from('profiles').select('id').eq('id', x)` - the select's columns belong to
    the table named further down the chain, not to anything the select itself says.
    Returns (table, dynamic) where dynamic means a `.from()` was found but its argument was
    not a literal.
    """
    current = node
    for _ in range(12):  # a chain longer than this is not a query, it is a mistake
        callee = current.get("callee") or {}
        if callee.get("type") != "MemberExpression":
            return None, False
        name = ((callee.get("property") or {}).get("name")) or ""
        if name == "from" and not _receiver_name(callee):
            args = current.get("arguments") or []
            if not args:
                return None, False
            literal = _literal(args[0])
            return (literal, literal is None)
        nxt = callee.get("object")
        if not isinstance(nxt, dict) or nxt.get("type") != "CallExpression":
            return None, False
        current = nxt
    return None, False


def _columns(raw: str) -> list[str]:
    """The column names in a `.select()` string.

    PostgREST select syntax is richer than a comma list - `id, author:profiles(name)`
    embeds a related table, and `count:id.count()` aliases an aggregate. Anything with a
    parenthesis or a colon in it is left alone rather than guessed at; a wrong guess here
    would invent an unresolved column that does not exist.
    """
    out: list[str] = []
    depth = 0
    current: list[str] = []
    for char in raw:
        if char == "(":
            depth += 1
        elif char == ")":
            depth = max(0, depth - 1)
        elif char == "," and depth == 0:
            out.append("".join(current))
            current = []
            continue
        current.append(char)
    out.append("".join(current))
    clean = []
    for piece in out:
        name = piece.strip()
        if not name or name in _STAR or "(" in name or ":" in name or "!" in name:
            continue
        clean.append(name.split("->")[0].split("::")[0].strip())
    return [c for c in clean if c and c not in _STAR]


class _Use:
    __slots__ = ("name", "file", "line", "dynamic")

    def __init__(self, name: str, file: str, line: int, dynamic: bool = False):
        self.name, self.file, self.line, self.dynamic = name, file, line, dynamic


def _collect(root: str) -> dict[str, list]:
    """Every Supabase name the client code uses, by kind."""
    found: dict[str, list] = {
        "table": [], "column": [], "rpc": [], "edge": [], "bucket": [],
    }
    paths = [p for p in _files(root) if _mentions(p, _NEEDLES)]
    if not paths:
        return found
    trees = _parse_files(paths, report_failures=False)
    for path, tree in trees.items():
        rel = os.path.relpath(path, root)
        for node, _ in _walk(tree):
            if node.get("type") != "CallExpression":
                continue
            callee = node.get("callee") or {}
            if callee.get("type") != "MemberExpression":
                continue
            method = ((callee.get("property") or {}).get("name")) or ""
            args = node.get("arguments") or []
            receiver = _receiver_name(callee)
            line = _line(node)

            if method == "from" and args:
                value = _literal(args[0])
                if receiver == "storage":
                    if value:
                        found["bucket"].append(_Use(value, rel, line))
                elif not receiver:
                    found["table"].append(_Use(value or "", rel, line, value is None))
            elif method == "rpc" and args:
                value = _literal(args[0])
                found["rpc"].append(_Use(value or "", rel, line, value is None))
            elif method == "invoke" and receiver == "functions" and args:
                value = _literal(args[0])
                found["edge"].append(_Use(value or "", rel, line, value is None))
            elif method == "select" and args:
                raw = _literal(args[0])
                if raw is None:
                    continue
                table, dynamic = _table_of_chain(node)
                if dynamic or not table:
                    continue
                for column in _columns(raw):
                    found["column"].append(_Use(f"{table}.{column}", rel, line))
    return found


def _edge_functions(root: str) -> dict[str, str]:
    """The edge functions this project ships: `supabase/functions/<name>/`."""
    base = pathlib.Path(root, "supabase", "functions")
    if not base.is_dir():
        return {}
    return {
        d.name: str(d.relative_to(root))
        for d in sorted(base.iterdir())
        if d.is_dir() and not d.name.startswith("_")
    }


def detected(root: str) -> bool:
    """Whether this project uses Supabase at all."""
    if pathlib.Path(root, "supabase", "config.toml").is_file():
        return True
    package = pathlib.Path(root, "package.json")
    if package.is_file():
        try:
            return "@supabase/supabase-js" in package.read_text(encoding="utf-8")
        except OSError:
            return False
    return False


def _relativise(schema: Schema, root: str) -> None:
    def rel(path: str) -> str:
        try:
            return os.path.relpath(path, root)
        except ValueError:
            return path

    for entry in schema.tables.values():
        entry["file"] = rel(entry["file"])
        entry["columns"] = {
            name: (rel(file), line) for name, (file, line) in entry["columns"].items()
        }
    schema.functions = {
        name: (rel(file), line) for name, (file, line) in schema.functions.items()
    }


def extract_supabase(root: str) -> tuple[list[Symbol], list[Edge]]:
    """Schema declarations, the client's uses of them, and the verdicts between."""
    if not detected(root):
        return [], []

    # Relativised here, not in the reader: every other symbol in the graph carries a
    # repo-relative file and the map joins it to the repo root itself. An absolute path
    # renders as an unreadable line and breaks the editor link.
    schema: Schema = read_schema(find_sql(root))
    _relativise(schema, root)
    edge_dirs = _edge_functions(root)
    uses = _collect(root)
    symbols: list[Symbol] = []
    edges: list[Edge] = []

    used_tables = {u.name for u in uses["table"] if u.name}
    used_rpcs = {u.name for u in uses["rpc"] if u.name}
    used_edges = {u.name for u in uses["edge"] if u.name}
    used_columns = {u.name for u in uses["column"]}

    # ── what the schema declares ────────────────────────────────────────
    for table, entry in schema.tables.items():
        touched = table in used_tables
        symbols.append(Symbol(
            id=f"db_table:{table}", kind="db_table", label=table, sub="table",
            file=entry["file"], line=entry["line"],
            status=Status.CONNECTED if touched else Status.UNUSED,
            snippet=f"create table {table}", chain=[table],
            note="" if touched else "No client code reads or writes this table.",
        ))
        for column, (cfile, cline) in entry["columns"].items():
            read = f"{table}.{column}" in used_columns
            symbols.append(Symbol(
                id=f"db_column:{table}.{column}", kind="db_column",
                label=f"{table}.{column}", sub="column", file=cfile, line=cline,
                # A column nothing selects is NOT unused: `select('*')` reads every column
                # and names none of them, and most code does that. Saying "unused" here
                # would be a guess dressed as a finding.
                status=Status.CONNECTED if read else Status.UNCERTAIN,
                snippet=f"{table}.{column}", chain=[table, column],
                note="" if read else "Not named in any select(); select('*') reads it "
                                    "without naming it.",
            ))
        # RLS. The anon key ships in the browser bundle, so a table the client reads with
        # no policy is a question worth asking from source.
        if touched:
            if table not in schema.rls_enabled:
                symbols.append(Symbol(
                    id=f"db_rls:{table}", kind="db_policy", label=table,
                    sub="no row level security", file=entry["file"], line=entry["line"],
                    status=Status.UNRESOLVED, snippet=f"alter table {table} enable row level security",
                    chain=[table],
                    note="Client code reads this table and row level security is not "
                         "enabled on it. The anon key is public.",
                ))
            elif not schema.policies.get(table):
                symbols.append(Symbol(
                    id=f"db_rls:{table}", kind="db_policy", label=table,
                    sub="no policy", file=entry["file"], line=entry["line"],
                    status=Status.UNRESOLVED, snippet=f"create policy ... on {table}",
                    chain=[table],
                    note="Row level security is on and no policy grants anything, so this "
                         "table is readable by nobody.",
                ))

    for name, (file, line) in schema.functions.items():
        called = name in used_rpcs
        symbols.append(Symbol(
            id=f"db_function:{name}", kind="db_function", label=name, sub="sql function",
            file=file, line=line,
            status=Status.CONNECTED if called else Status.UNUSED,
            snippet=f"create function {name}()", chain=[name],
            note="" if called else "No rpc() call names this function.",
        ))

    for name, folder in edge_dirs.items():
        called = name in used_edges
        symbols.append(Symbol(
            id=f"edge_function:{name}", kind="edge_function", label=name,
            sub="edge function", file=f"{folder}/index.ts", line=1,
            status=Status.CONNECTED if called else Status.UNCERTAIN,
            snippet=f"supabase/functions/{name}", chain=[name],
            note="" if called else "No functions.invoke() names it. An edge function can "
                                  "also be called over HTTP from outside this repo.",
        ))

    # ── what the client uses ────────────────────────────────────────────
    def use_symbol(use: _Use, kind: str, known: bool, missing: str, sub: str) -> Symbol:
        if use.dynamic or not use.name:
            return Symbol(
                id=f"{kind}:<dynamic>:{use.file}:{use.line}", kind=kind,
                label="<assembled at runtime>", sub=sub, file=use.file, line=use.line,
                status=Status.UNCERTAIN, snippet="", chain=[],
                note="The name is a variable, so the scan cannot tell which one this is.",
            )
        return Symbol(
            id=f"{kind}:{use.name}:{use.file}:{use.line}", kind=kind, label=use.name,
            sub=sub, file=use.file, line=use.line,
            status=Status.CONNECTED if known else Status.UNRESOLVED,
            snippet=use.name, chain=[use.name], note="" if known else missing,
        )

    for use in uses["table"]:
        known = use.name in schema.tables
        symbol = use_symbol(
            use, "db_table_use", known,
            "No migration in this repo declares a table with that name.", "reads a table")
        symbols.append(symbol)
        if known:
            edges.append(Edge(symbol.id, f"db_table:{use.name}", Status.CONNECTED))

    for use in uses["column"]:
        table, _, column = use.name.partition(".")
        known = schema.column(table, column)
        symbol = use_symbol(
            use, "db_column_use", known,
            f"Table {table} has no column with that name. PostgREST returns the row "
            "without it and the client reads undefined.", "selects a column")
        symbols.append(symbol)
        if known:
            edges.append(Edge(symbol.id, f"db_column:{use.name}", Status.CONNECTED))

    for use in uses["rpc"]:
        known = use.name in schema.functions
        symbol = use_symbol(
            use, "db_function_use", known,
            "No migration declares a function with that name.", "calls rpc()")
        symbols.append(symbol)
        if known:
            edges.append(Edge(symbol.id, f"db_function:{use.name}", Status.CONNECTED))

    for use in uses["edge"]:
        known = use.name in edge_dirs
        symbol = use_symbol(
            use, "edge_function_use", known,
            "No supabase/functions directory of that name.", "invokes an edge function")
        symbols.append(symbol)
        if known:
            edges.append(Edge(symbol.id, f"edge_function:{use.name}", Status.CONNECTED))

    for use in uses["bucket"]:
        # Buckets are created through the dashboard or a seed script, so there is usually
        # nothing in the repo to compare against. Saying "unresolved" would be inventing a
        # finding out of a thing the scan simply cannot see.
        symbols.append(Symbol(
            id=f"storage_bucket_use:{use.name}:{use.file}:{use.line}",
            kind="storage_bucket", label=use.name, sub="storage bucket",
            file=use.file, line=use.line, status=Status.UNCERTAIN,
            snippet=use.name, chain=[use.name],
            note="Buckets are usually created outside the repo, so there is nothing here "
                 "to check the name against.",
        ))

    return symbols, edges
