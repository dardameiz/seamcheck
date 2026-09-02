"""Configuration keys: read by string in code, declared by string somewhere else.

Every framework in every language reads its configuration the same way - a string key into
a dictionary - and no compiler in any of them checks that the key exists. A missing one is
either a crash on boot or, far worse, a feature that quietly turns itself off: the classic
is a payment integration that no-ops in production because nobody added the key to the new
environment, which nothing detects until the first customer tries to pay.

The declaration lives in a file the repository already has - `.env.example`, a compose
file's `environment:` block, a Railway or Vercel manifest, a Helm values file. So this is
the same string-against-declaration seam as everything else here, and it is the cheapest
one to read: no framework knowledge, no AST for the declaration side, and it applies to
all seven backends and to repositories with no backend at all.

What is deliberately NOT claimed: that a key missing from the example file is missing in
production. `.env.example` is documentation and drifts, secrets are set in a dashboard, and
CI injects its own. So a key read in code and absent from every declaration is `uncertain`
with the list of places that were searched - useful, and not an accusation. The reverse -
declared and never read - is `unused`, which is safe to say because reading is something
this scan CAN see across the whole repository.
"""

from __future__ import annotations

import ast
import os
import pathlib
import re

from seamcheck.adapters.discovery import SKIP_DIRS
from seamcheck.graph import Edge, Status, Symbol

_JS_EXTENSIONS = (".js", ".mjs", ".cjs", ".jsx", ".ts", ".mts", ".cts", ".tsx", ".svelte", ".vue")
_MAX_BYTES = 400_000

# Where a key is DECLARED. Ordered by how much authority each carries, which is only used
# to name the best one in the note.
_DECLARATION_FILES = (
    ".env.example", ".env.sample", ".env.template", ".env.defaults", ".env.local.example",
    "docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml",
    "railway.json", "railway.toml", "vercel.json", "app.json", "fly.toml",
    ".env.test", ".env.development", ".env",
)
# A KEY=value line in a dotenv file, and an `- KEY=value` or `KEY: value` line in compose.
_DOTENV_RE = re.compile(r"^\s*(?:export\s+)?([A-Z][A-Z0-9_]{2,})\s*=", re.M)
_COMPOSE_ENV_RE = re.compile(r"^\s*-?\s*([A-Z][A-Z0-9_]{2,})\s*[:=]", re.M)

# How code READS one. `process.env.X`, `process.env['X']`, `import.meta.env.X`,
# `os.environ['X']`, `os.environ.get('X')`, `os.getenv('X')`, `env('X')`.
_JS_READ_RE = re.compile(
    r"""(?:process\.env|import\.meta\.env|Deno\.env\.get|\$env/static/(?:private|public))"""
    r"""(?:\.([A-Z][A-Z0-9_]{2,})|\[\s*['"]([A-Z][A-Z0-9_]{2,})['"]\s*\]|\(\s*['"]([A-Z][A-Z0-9_]{2,})['"]\s*\))"""
)
# Framework prefixes that are injected by the build rather than declared by a person.
_BUILT_IN = frozenset({
    "NODE_ENV", "PATH", "HOME", "PORT", "PWD", "USER", "SHELL", "LANG", "TZ", "CI",
    "HOSTNAME", "TERM", "DEBUG", "VERCEL", "VERCEL_URL", "VERCEL_ENV", "npm_package_name",
    "PYTHONPATH", "VIRTUAL_ENV", "AWS_REGION", "AWS_EXECUTION_ENV", "DYNO", "RAILWAY_ENVIRONMENT",
})

_UNDECLARED_NOTE_TEMPLATE = (
    "Read here and declared in none of the files this scan can see ({searched}). Not a "
    "claim that it is unset: real secrets are set in a dashboard or injected by CI, and an "
    "example file is documentation that drifts. Worth confirming for a new environment, "
    "because a missing key usually turns a feature off rather than raising."
)
_UNREAD_NOTE = (
    "Declared, and no source file in this repository reads it by name. Not a claim that "
    "it is unused: configuration is also consumed by container images, shell scripts, "
    "schema parsers, CI and deployment tooling, none of which this scan reads. Worth a "
    "look only if you already suspect it is left over."
)


_TEST_DIRS = frozenset({"test", "tests", "__tests__", "e2e", "spec", "specs"})
_TEST_FILE_RE = re.compile(r"\.(test|spec)\.[jt]sx?$|^test_|_test\.py$")


def _files(root: str, extensions: tuple[str, ...]) -> list[str]:
    """Source files only. A key read in a test fixture is a fact about the harness."""
    found: list[str] = []
    for current, directories, names in os.walk(root):
        directories[:] = [
            d for d in directories
            if d not in SKIP_DIRS and not d.startswith(".") and d not in _TEST_DIRS
        ]
        for name in names:
            if not name.endswith(extensions) or name.endswith((".min.js", ".d.ts")):
                continue
            if _TEST_FILE_RE.search(name):
                continue
            found.append(os.path.join(current, name))
    return found


def _declarations(root: str) -> dict[str, tuple[str, int, str]]:
    """key -> (file, line, which kind of file declared it)."""
    found: dict[str, tuple[str, int, str]] = {}
    base = pathlib.Path(root)
    candidates: list[pathlib.Path] = []
    for name in _DECLARATION_FILES:
        candidates += [p for p in (base / name, base / "docker" / name) if p.is_file()]
    # A monorepo declares per app; one level of subdirectory covers apps/* and services/*.
    for child in sorted(base.glob("*/*")):
        if child.name in _DECLARATION_FILES and child.is_file():
            candidates.append(child)

    for path in candidates:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        relative = os.path.relpath(path, root).replace(os.sep, "/")
        pattern = _COMPOSE_ENV_RE if path.name.startswith(("docker-compose", "compose")) else _DOTENV_RE
        for match in pattern.finditer(text):
            key = match.group(1)
            if key in _BUILT_IN:
                continue
            line = text.count("\n", 0, match.start()) + 1
            found.setdefault(key, (relative, line, path.name))
    return found


def _reads(root: str) -> dict[str, tuple[str, int]]:
    """key -> (file, line) for the first place each key is read."""
    found: dict[str, tuple[str, int]] = {}

    for path in _files(root, _JS_EXTENSIONS):
        try:
            if os.path.getsize(path) > _MAX_BYTES:
                continue
            text = pathlib.Path(path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "env" not in text:
            continue
        relative = os.path.relpath(path, root).replace(os.sep, "/")
        for match in _JS_READ_RE.finditer(text):
            key = next((g for g in match.groups() if g), None)
            if key and key not in _BUILT_IN:
                found.setdefault(key, (relative, text.count("\n", 0, match.start()) + 1))

    for path in _files(root, (".py",)):
        try:
            if os.path.getsize(path) > _MAX_BYTES:
                continue
            text = pathlib.Path(path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "environ" not in text and "getenv" not in text:
            continue
        try:
            tree = ast.parse(text)
        except (SyntaxError, ValueError):
            continue
        relative = os.path.relpath(path, root).replace(os.sep, "/")
        for node in ast.walk(tree):
            key = _python_env_key(node)
            if key and key not in _BUILT_IN:
                found.setdefault(key, (relative, getattr(node, "lineno", 1)))
    return found


def _python_env_key(node: ast.AST) -> str:
    """The key in `os.environ['X']`, `os.environ.get('X')` or `os.getenv('X')`."""
    if isinstance(node, ast.Subscript):
        target = node.value
        name = getattr(target, "attr", "") or getattr(target, "id", "")
        if name == "environ" and isinstance(node.slice, ast.Constant):
            value = node.slice.value
            return value if isinstance(value, str) else ""
        return ""
    if isinstance(node, ast.Call):
        function = node.func
        name = getattr(function, "attr", "") or getattr(function, "id", "")
        if name not in ("get", "getenv"):
            return ""
        if name == "get":
            owner = getattr(function, "value", None)
            owner_name = getattr(owner, "attr", "") or getattr(owner, "id", "")
            if owner_name != "environ":
                return ""
        if node.args and isinstance(node.args[0], ast.Constant):
            value = node.args[0].value
            return value if isinstance(value, str) else ""
    return ""


def extract_env(root: str) -> tuple[list[Symbol], list[Edge]]:
    """Configuration keys, where they are declared, and where they are read."""
    declared = _declarations(root)
    read = _reads(root)
    if not declared and not read:
        return [], []

    searched = ", ".join(sorted({name for _, _, name in declared.values()})) or (
        "no .env.example, compose file or deploy manifest was found"
    )
    symbols: list[Symbol] = []
    edges: list[Edge] = []

    for key, (file, line, _which) in sorted(declared.items()):
        used = key in read
        symbols.append(Symbol(
            id=f"env_var:{key}", kind="env_var", label=key, sub="declared",
            file=file, line=line,
            # UNCERTAIN, never unused. This was `unused`, and hand-verification across
            # three real repositories scored it 0 for 20: a variable is consumed by the
            # official postgres image's entrypoint, by a Zod schema parsed against
            # process.env, by a Dockerfile ARG renamed to ENV, by an extensionless shell
            # script, by Terragrunt, by CI yaml, by Prisma reading .env on its own. The
            # list of things that read configuration without a `process.env.X` in a
            # source file is the list of everything in a deployment, and "nothing reads
            # this" is not a claim source can earn.
            status=Status.CONNECTED if used else Status.UNCERTAIN,
            snippet=f"{key}=", chain=[key],
            note="" if used else _UNREAD_NOTE,
        ))

    # Whether the declaration files are an INVENTORY of this project's configuration, or
    # just a starter file somebody wrote once. open-webui reads 872 keys and declares 12:
    # listing 860 of them as unverified is not a finding, it is the tool failing to notice
    # that the oracle it is using does not cover the subject. Below half, the per-key
    # verdict is withheld and replaced by one symbol that says so - the same shape as the
    # Supabase no-schema case, and for the same reason.
    covered = sum(1 for key in read if key in declared)
    comprehensive = bool(declared) and covered * 2 >= len(read)

    for key, (file, line) in sorted(read.items()):
        known = key in declared
        if not known and not comprehensive:
            continue
        symbol_id = f"env_read:{key}"
        symbols.append(Symbol(
            id=symbol_id, kind="env_read", label=key, sub="read by the code",
            file=file, line=line,
            status=Status.CONNECTED if known else Status.UNCERTAIN,
            snippet=f"env.{key}", chain=[key],
            note="" if known else _UNDECLARED_NOTE_TEMPLATE.format(searched=searched),
        ))
        if known:
            edges.append(Edge(from_id=symbol_id, to_id=f"env_var:{key}", status=Status.CONNECTED))

    if read and not comprehensive:
        first_file, first_line = next(iter(sorted(read.values())))
        symbols.append(Symbol(
            id="env_read:<not comparable>", kind="env_read",
            label=f"{len(read) - covered} of {len(read)} configuration keys not declared here",
            sub="nothing to check against", file=first_file, line=first_line,
            status=Status.UNCERTAIN, snippet="", chain=[],
            note=(
                f"This code reads {len(read)} configuration keys and the files that could "
                f"declare them ({searched}) cover {covered}. That is not an inventory of "
                "this project's configuration, so no per-key verdict is given: it would be "
                "hundreds of findings drawn from a file that was never meant to be "
                "complete. Keeping an example file current makes every one of them "
                "checkable."
            ),
        ))
    return symbols, edges
