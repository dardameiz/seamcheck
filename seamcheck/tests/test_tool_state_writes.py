"""Nothing writes a repo-root-relative path this package doesn't already know about.

`f3f2bff5b` fixed the cache-busting bug for two write paths (`_SCANS_DIR`, `_TRIAGE_FILE`).
One commit later, a third (`_MAP_FILE`, `api.write_map`'s connectivity map) shipped
unregistered and reintroduced the exact same bug: a hand-enumerated list has no way to
notice a FOURTH writer, because nothing ever asks the code itself which writes exist - it
only checks the ones someone remembered to name.

So this asks the code. It finds every `pathlib.Path(repo_root) / …` construction in
seamcheck's own source (directly, or one layer behind a helper that builds and returns
one - `snapshot._snapshot_path`, `observe.store_path`, `trend.path` are all exactly this
shape) that is later written through (`write_text`, `write_bytes`, `mkdir`, `open(...,
"w")`), and checks that whatever it resolves to is one of `scancache.TOOL_STATE_PATHS` -
or is named, with a reason, in the ALLOWLIST below.

Running this the first time (before TOOL_STATE_PATHS existed) is what found `_STORE_DIR`
(observe.py) and `_TREND_PATH` (trend.py) ALREADY shipping unregistered, busting the cache
for `seamcheck observe` and for every map render - real, pre-existing instances of the
same bug this test exists to stop the next one of.

What this does not, and cannot, catch: a write that never constructs its destination via
`pathlib.Path(repo_root) / …` at all (string concatenation, `os.path.join(repo_root, …)`),
or one reached through more than one layer of helper-calls-helper indirection. Both are
real gaps; neither is exercised by anything in this package today (verified by hand,
2026-09-07) - if one appears, widen `_rooted`/tier_b_refs below rather than adding a
special case to the allowlist for a shape this test could actually resolve.
"""
from __future__ import annotations

import ast
import importlib
import pathlib

from django.test import SimpleTestCase

from seamcheck.scancache import TOOL_STATE_PATHS

_PACKAGE_DIR = pathlib.Path(__file__).resolve().parent.parent
_SOURCE_ROOT = _PACKAGE_DIR.parent

_WRITE_METHODS = {"write_text", "write_bytes", "mkdir"}
_REPO_ROOT_NAME = "repo_root"

# (relative/path/to/file.py, enclosing function name): "why this destination is not, and
# cannot be, one fixed entry in TOOL_STATE_PATHS".
ALLOWLIST: dict[tuple[str, str], str] = {
    ("seamcheck/management/commands/seamcheck.py", "_format_report"): (
        "Writes the html/console report to SEAMCHECK_CONFIG['report_output' or "
        "'map_output'], falling back to a repo-relative default (docs/maps/…) when unset "
        "- a user-configurable destination, not a fixed constant this registry can name. "
        "Confirmed default-path collision with the cache is a REAL, separate, "
        "pre-existing gap (every `seamcheck map`/`--format html` render busts the cache "
        "for the default destination) - flagged to the coordinator rather than silently "
        "fixed here, since excluding a config-dependent path needs different machinery "
        "than a fixed tuple entry."
    ),
}


def _iter_py_files():
    for path in sorted(_PACKAGE_DIR.rglob("*.py")):
        rel = path.relative_to(_SOURCE_ROOT)
        if "tests" in rel.parts or "__pycache__" in rel.parts:
            continue
        yield path


def _module_for(py_file: pathlib.Path):
    rel = py_file.relative_to(_SOURCE_ROOT).with_suffix("")
    dotted = ".".join(rel.parts)
    try:
        return importlib.import_module(dotted)
    except ImportError:
        return None


def _is_path_call(node):
    """`pathlib.Path(repo_root)` or `Path(repo_root)` - bare, not yet `/`'d or `.resolve()`d."""
    if not isinstance(node, ast.Call) or len(node.args) != 1:
        return False
    func = node.func
    is_path_name = (isinstance(func, ast.Name) and func.id == "Path") or (
        isinstance(func, ast.Attribute) and func.attr == "Path")
    if not is_path_name:
        return False
    arg = node.args[0]
    return isinstance(arg, ast.Name) and arg.id == _REPO_ROOT_NAME


def _rooted(node, rooted_names: set) -> bool:
    """True if `node` is Path(repo_root), or built from it by `/`, by an attribute or
    method access (`.parent`, `.resolve()`), or via a local name already established as
    rooted earlier in this function.

    `repo_root` is recognised BY NAME, not by "is this a function parameter": a Django
    management command assigns `repo_root = options["repo_root"]` as a plain local and
    writes through it exactly like every other caller - restricting this to literal
    parameters would have missed that write silently instead of flagging it for a
    decision, which is the one failure mode this whole test exists to close off.
    """
    if _is_path_call(node):
        return True
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        return _rooted(node.left, rooted_names)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        return _rooted(node.func.value, rooted_names)
    if isinstance(node, ast.Attribute):
        return _rooted(node.value, rooted_names)
    if isinstance(node, ast.Name):
        return node.id in rooted_names
    return False


def _iter_statements(body):
    """Every statement in `body`, in source order, descending into simple control-flow
    blocks (if/for/while/with/try) but not into a nested def - that is analyzed as its
    own separate unit by `_functions_in`."""
    for stmt in body:
        yield stmt
        if isinstance(stmt, (ast.If, ast.For, ast.AsyncFor, ast.While)):
            yield from _iter_statements(stmt.body)
            yield from _iter_statements(stmt.orelse)
        elif isinstance(stmt, (ast.With, ast.AsyncWith)):
            yield from _iter_statements(stmt.body)
        elif isinstance(stmt, ast.Try):
            yield from _iter_statements(stmt.body)
            for handler in stmt.handlers:
                yield from _iter_statements(handler.body)
            yield from _iter_statements(stmt.orelse)
            yield from _iter_statements(stmt.finalbody)


def _collect_refs(node, rooted: dict) -> set:
    """Every name referenced anywhere inside `node`, expanding a name that was ITSELF
    established as rooted (via an earlier assignment) into what IT was built from -
    `path.parent` after `path = Path(repo_root) / _MAP_FILE` must resolve to `_MAP_FILE`,
    not to the local variable name `path`, which resolves to nothing importable."""
    refs = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Name):
            refs |= rooted.get(n.id, {n.id})
    return refs


def _write_call_base(node):
    """If `node` is a write-shaped call, the path expression it writes through."""
    if not isinstance(node, ast.Call):
        return None
    if isinstance(node.func, ast.Attribute) and node.func.attr in _WRITE_METHODS:
        return node.func.value
    if isinstance(node.func, ast.Name) and node.func.id == "open" and node.args:
        mode = None
        if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
            mode = node.args[1].value
        for kw in node.keywords:
            if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
                mode = kw.value.value
        if mode and any(c in mode for c in "wax"):
            return node.args[0]
    return None


def _functions_in(tree):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield node


def _walk_own_body_only(fn):
    """Like ast.walk(fn), but does not descend into a nested def - `_functions_in`
    analyzes that separately, and folding its writes into the outer function's context
    here would double-count them under the wrong local-variable history."""
    stack = [n for n in ast.iter_child_nodes(fn)
            if n is not fn.args and n not in fn.decorator_list]
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        yield node
        stack.extend(ast.iter_child_nodes(node))


class Write:
    __slots__ = ("file", "function", "lineno", "refs")

    def __init__(self, file, function, lineno, refs):
        self.file = file
        self.function = function
        self.lineno = lineno
        self.refs = refs


def _tier_b_refs(files) -> dict:
    """Functions that construct and RETURN a repo_root-rooted path - one layer of helper
    indirection, resolved to a fixed point so a helper that calls another helper is
    still found (none in this codebase go two deep today, but nothing here assumes one)."""
    trees = {f: ast.parse(f.read_text(encoding="utf-8"), filename=str(f)) for f in files}
    tier_b: dict[str, set] = {}
    for _ in range(4):
        changed = False
        for tree in trees.values():
            for fn in _functions_in(tree):
                rooted: dict[str, set] = {}
                for stmt in _iter_statements(fn.body):
                    if (isinstance(stmt, ast.Assign) and len(stmt.targets) == 1
                            and isinstance(stmt.targets[0], ast.Name)):
                        refs = _expr_refs(stmt.value, rooted, tier_b)
                        if refs is not None:
                            rooted[stmt.targets[0].id] = refs
                    if isinstance(stmt, ast.Return) and stmt.value is not None:
                        refs = _expr_refs(stmt.value, rooted, tier_b)
                        if refs is not None and tier_b.get(fn.name) != refs:
                            tier_b[fn.name] = refs
                            changed = True
        if not changed:
            break
    return tier_b


def _expr_refs(node, rooted: dict, tier_b: dict):
    """None if `node` is not (as far as this can tell) a repo-root-rooted path; otherwise
    the set of names it was built from, for the covered-check to resolve."""
    if _rooted(node, set(rooted)):
        return _collect_refs(node, rooted)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in tier_b:
        return set(tier_b[node.func.id])
    return None


def find_writes(files, tier_b: dict) -> list[Write]:
    """Every repo-root-relative write in `files` - real source files (a real Write.file),
    or synthetic ones a caller made up (any hashable label) for `ast.parse` alone."""
    writes = []
    for file in files:
        source = file.read_text(encoding="utf-8") if isinstance(file, pathlib.Path) else file[1]
        label = file if isinstance(file, pathlib.Path) else file[0]
        tree = ast.parse(source, filename=str(label))
        for fn in _functions_in(tree):
            rooted: dict[str, set] = {}
            for stmt in _iter_statements(fn.body):
                if (isinstance(stmt, ast.Assign) and len(stmt.targets) == 1
                        and isinstance(stmt.targets[0], ast.Name)):
                    refs = _expr_refs(stmt.value, rooted, tier_b)
                    if refs is not None:
                        rooted[stmt.targets[0].id] = refs
            for node in _walk_own_body_only(fn):
                base = _write_call_base(node)
                if base is None:
                    continue
                refs = _expr_refs(base, rooted, tier_b)
                if refs is not None:
                    writes.append(Write(label, fn.name, getattr(node, "lineno", 0), refs))
    return writes


def _is_covered(write: Write, tool_state_paths, file_module) -> bool:
    for name in write.refs:
        value = getattr(file_module, name, None)
        if isinstance(value, pathlib.Path) and value in tool_state_paths:
            return True
    return False


def _uncovered(writes, tool_state_paths, allowlist) -> list[tuple[Write, str | None]]:
    """(write, allowlist_reason_or_None) for every write that is neither registered nor
    allowlisted - allowlist_reason is None for a genuine, unexplained gap."""
    problems = []
    for write in writes:
        rel = str(write.file.relative_to(_SOURCE_ROOT)) if isinstance(write.file, pathlib.Path) else str(write.file)
        module = _module_for(write.file) if isinstance(write.file, pathlib.Path) else None
        if _is_covered(write, tool_state_paths, module):
            continue
        reason = allowlist.get((rel, write.function))
        if reason is not None:
            continue
        problems.append((write, rel))
    return problems


def _format_failure(problems) -> str:
    lines = ["Found a write to a repo-root-relative path this package does not know about:", ""]
    for write, rel in problems:
        lines.append(f"  {rel}:{write.lineno} in {write.function}() - built from {sorted(write.refs)}")
    lines.append("")
    lines.append(
        "Add it to scancache.TOOL_STATE_PATHS if this is genuine tool state (never scanner "
        "input) - the fix for the exact same finding twice already. Otherwise add "
        "(<relative path>, \"<function name>\") to ALLOWLIST in this file with a comment "
        "saying why this destination should legitimately invalidate the cache."
    )
    return "\n".join(lines)


class ToolStateWritesTests(SimpleTestCase):
    def test_every_repo_root_write_is_registered_or_allowlisted(self):
        files = list(_iter_py_files())
        tier_b = _tier_b_refs(files)
        writes = find_writes(files, tier_b)
        # A write really was found - if this drops to zero, the detector broke silently
        # and every other assertion in this file would pass for the wrong reason.
        self.assertGreaterEqual(len(writes), 5)

        problems = _uncovered(writes, TOOL_STATE_PATHS, ALLOWLIST)
        self.assertEqual(problems, [], _format_failure(problems))

    def test_removing_a_registered_path_is_caught(self):
        # Falsifies the check itself: with _MAP_FILE dropped from the registry,
        # api.write_map's real, unmocked write must be reported uncovered again -
        # the exact regression `f3f2bff5b` -> unregistered `_MAP_FILE` was.
        files = list(_iter_py_files())
        tier_b = _tier_b_refs(files)
        writes = find_writes(files, tier_b)

        shrunk = tuple(p for p in TOOL_STATE_PATHS if p.name != "connectivity-map.json")
        self.assertLess(len(shrunk), len(TOOL_STATE_PATHS), "the constant to remove wasn't found")

        problems = _uncovered(writes, shrunk, ALLOWLIST)
        offending_functions = {write.function for write, _ in problems}
        self.assertIn("write_map", offending_functions,
                      "removing _MAP_FILE from the registry must un-cover write_map's write")

    def test_a_new_unregistered_writer_is_caught(self):
        # Falsifies the check the other way: a brand new, made-up write to a path that
        # is not, and was never meant to be, in TOOL_STATE_PATHS - synthetic source, no
        # real file touched - must be reported, not silently accepted.
        synthetic = (
            "seamcheck/_scratch_example.py",
            "def leak(repo_root):\n"
            "    import pathlib\n"
            "    path = pathlib.Path(repo_root) / 'docs' / 'a-brand-new-file.json'\n"
            "    path.write_text('{}')\n",
        )
        tier_b = _tier_b_refs([])
        writes = find_writes([synthetic], tier_b)
        self.assertEqual(len(writes), 1, "the synthetic writer itself was not even found")

        problems = _uncovered(writes, TOOL_STATE_PATHS, ALLOWLIST)
        self.assertEqual(len(problems), 1,
                         "a write to a path nobody registered or allowlisted must be reported")
        self.assertEqual(problems[0][0].function, "leak")
