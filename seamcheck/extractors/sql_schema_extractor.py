"""What a SQL schema DECLARES: tables, their columns, functions, and RLS policies.

This is a schema reader, not a Supabase reader. Supabase migrations are plain PostgreSQL
DDL in `supabase/migrations/*.sql`, and so are Alembic's, Sqitch's, dbmate's, golang-migrate's
and every hand-rolled `migrations/` folder in the world. One reader serves all of them, and
the thing that varies is only where the files live.

It is deliberately a text scanner rather than a SQL parser. A migration directory is DDL
plus whatever else the author needed - `DO $$` blocks, extensions, grants, seed data, psql
meta-commands - and a real parser that rejects one file gives up the schema. Reading the
statements it recognises and ignoring the rest degrades into "fewer tables known", which
costs precision. Refusing to parse costs the whole answer.

What it deliberately does NOT do is track a schema through time. `CREATE TABLE` then
`DROP TABLE` in a later migration leaves the table present here, and a rename leaves both
names. Migrations are applied in order and this reads them as a set, so the answer is
"every name this schema has ever declared" - which over-reports what exists and therefore
never invents an `unresolved` that is not real. Getting that backwards would mean telling
someone a table they use does not exist.
"""

from __future__ import annotations

import pathlib
import re

# `create table if not exists public."My Table" (` - schema-qualified, quoted, or neither.
_CREATE_TABLE = re.compile(
    r"""create\s+table\s+(?:if\s+not\s+exists\s+)?"""
    r"""(?:(?P<schema>[\w"]+)\s*\.\s*)?(?P<name>"[^"]+"|\w+)\s*\(""",
    re.I,
)
_ALTER_ADD = re.compile(
    r"""alter\s+table\s+(?:only\s+)?(?:(?:[\w"]+)\s*\.\s*)?(?P<table>"[^"]+"|\w+)\s+"""
    r"""add\s+column\s+(?:if\s+not\s+exists\s+)?(?P<column>"[^"]+"|\w+)""",
    re.I,
)
_CREATE_FUNCTION = re.compile(
    r"""create\s+(?:or\s+replace\s+)?function\s+"""
    r"""(?:(?:[\w"]+)\s*\.\s*)?(?P<name>"[^"]+"|\w+)\s*\(""",
    re.I,
)
_CREATE_POLICY = re.compile(
    r"""create\s+policy\s+(?P<name>"[^"]+"|\w+)\s+on\s+"""
    r"""(?:(?:[\w"]+)\s*\.\s*)?(?P<table>"[^"]+"|\w+)""",
    re.I,
)
_ENABLE_RLS = re.compile(
    r"""alter\s+table\s+(?:(?:[\w"]+)\s*\.\s*)?(?P<table>"[^"]+"|\w+)\s+"""
    r"""enable\s+row\s+level\s+security""",
    re.I,
)

# A column definition inside CREATE TABLE ( ... ). Table constraints share the syntax, so
# the words that can only start a constraint are refused rather than read as columns.
_CONSTRAINT_WORDS = frozenset({
    "primary", "foreign", "unique", "check", "constraint", "exclude", "like", "inherits",
})
_COLUMN = re.compile(r"""^\s*(?P<name>"[^"]+"|\w+)\s+\S""")


def _clean(name: str | None) -> str:
    return (name or "").strip().strip('"')


def _strip_comments(sql: str) -> str:
    """`--` to end of line, and /* */ blocks. A commented-out CREATE TABLE is not a table."""
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)
    return re.sub(r"--[^\n]*", "", sql)


def _body_of(sql: str, open_paren: int) -> tuple[str, int]:
    """The text between a `(` and its matching `)`, by depth. Quotes are respected so a
    parenthesis inside a default value or a dollar-quoted body does not end the block."""
    depth, i, out = 0, open_paren, []
    quote = None
    while i < len(sql):
        char = sql[i]
        if quote:
            out.append(char)
            if char == quote:
                quote = None
        elif char in "'\"":
            quote = char
            out.append(char)
        elif char == "(":
            depth += 1
            if depth > 1:
                out.append(char)
        elif char == ")":
            depth -= 1
            if depth == 0:
                return "".join(out), i
            out.append(char)
        else:
            out.append(char)
        i += 1
    return "".join(out), i


def _split_top_level(body: str) -> list[str]:
    """Commas that separate column definitions, not commas inside `numeric(10, 2)`."""
    parts, depth, current, quote = [], 0, [], None
    for char in body:
        if quote:
            current.append(char)
            if char == quote:
                quote = None
            continue
        if char in "'\"":
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        elif char == "," and depth == 0:
            parts.append("".join(current))
            current = []
            continue
        current.append(char)
    parts.append("".join(current))
    return parts


def _line_of(text: str, index: int) -> int:
    return text.count("\n", 0, index) + 1


class Schema:
    """Every name a SQL schema declares, and where it was declared."""

    def __init__(self) -> None:
        # table -> {"file": str, "line": int, "columns": {name: (file, line)}}
        self.tables: dict[str, dict] = {}
        self.functions: dict[str, tuple[str, int]] = {}
        # A table with RLS on and no policy is readable by nobody; with RLS off it is
        # readable by anyone holding the anon key. Both are worth saying out loud.
        self.rls_enabled: set[str] = set()
        self.policies: dict[str, list[str]] = {}

    def column(self, table: str, column: str) -> bool:
        entry = self.tables.get(table)
        return bool(entry) and column in entry["columns"]


def read_schema(paths: list[str]) -> Schema:
    """Read every .sql file given. Order does not matter; see the module docstring."""
    schema = Schema()
    for path in sorted(paths):
        try:
            raw = pathlib.Path(path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        sql = _strip_comments(raw)
        rel = path

        for match in _CREATE_TABLE.finditer(sql):
            name = _clean(match.group("name"))
            if not name:
                continue
            body, _ = _body_of(sql, match.end() - 1)
            entry = schema.tables.setdefault(
                name, {"file": rel, "line": _line_of(sql, match.start()), "columns": {}}
            )
            for piece in _split_top_level(body):
                column = _COLUMN.match(piece)
                if not column:
                    continue
                found = _clean(column.group("name"))
                if not found or found.lower() in _CONSTRAINT_WORDS:
                    continue
                entry["columns"].setdefault(found, (rel, entry["line"]))

        for match in _ALTER_ADD.finditer(sql):
            table, column = _clean(match.group("table")), _clean(match.group("column"))
            if not table or not column:
                continue
            entry = schema.tables.setdefault(
                table, {"file": rel, "line": _line_of(sql, match.start()), "columns": {}}
            )
            entry["columns"].setdefault(column, (rel, _line_of(sql, match.start())))

        for match in _CREATE_FUNCTION.finditer(sql):
            name = _clean(match.group("name"))
            if name:
                schema.functions.setdefault(name, (rel, _line_of(sql, match.start())))

        for match in _ENABLE_RLS.finditer(sql):
            schema.rls_enabled.add(_clean(match.group("table")))

        for match in _CREATE_POLICY.finditer(sql):
            table = _clean(match.group("table"))
            schema.policies.setdefault(table, []).append(_clean(match.group("name")))

    return schema


def find_sql(repo_root: str, *, folders: tuple[str, ...] = ("supabase/migrations",
                                                            "migrations", "db/migrate",
                                                            "database/migrations", "sql")) -> list[str]:
    """Migration files, looked for where migrations are kept."""
    root = pathlib.Path(repo_root)
    found: list[str] = []
    for folder in folders:
        directory = root / folder
        if directory.is_dir():
            found.extend(str(p) for p in sorted(directory.rglob("*.sql")))
    return found
