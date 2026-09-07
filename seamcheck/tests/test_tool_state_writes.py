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
same bug this test exists to stop the next one of. A second pass, after `_format_report`'s
SEAMCHECK_CONFIG-driven html/map destination moved from an ALLOWLIST entry into the
registry (`scancache.resolve_report_output`/`resolve_map_output` +
`CONFIGURABLE_TOOL_STATE_DEFAULTS`), is what needed `_rooted`/`_expr_refs` to understand a
ternary (`X if cond else Y`, unioning both arms' refs - a bare `if`/`else` around the
SAME write call is understood too, each branch scoped to its own copy of the local
variable history, so one branch's assignment can never leak into the other's - see
`_walk_scoped`'s docstring) and to resolve a name against every analyzed module, not only
the write's own file (a tier-B helper's referenced constant can live in a different
module from the one that calls it).

What this still does not, and cannot, catch: a write that never constructs its
destination via `pathlib.Path(repo_root) / …` at all - string concatenation,
`os.path.join(repo_root, …)`, an f-string. Nothing here tracks string VALUES, only
path-construction AST shapes. A real gap; not exercised by anything in this package
today (verified by hand, 2026-09-07) - if one appears, widen `_rooted` rather than
adding a special case to the allowlist for a shape this test could actually resolve.
Helper-calls-helper indirection, by contrast, IS followed to a fixed point (`_tier_b_refs`
re-runs until nothing new is found), so a chain more than one function deep is not itself
a blind spot.
"""
from __future__ import annotations

import ast
import importlib
import pathlib

from django.test import SimpleTestCase

from seamcheck.scancache import CONFIGURABLE_TOOL_STATE_DEFAULTS, TOOL_STATE_PATHS

_PACKAGE_DIR = pathlib.Path(__file__).resolve().parent.parent
_SOURCE_ROOT = _PACKAGE_DIR.parent

_WRITE_METHODS = {"write_text", "write_bytes", "mkdir"}
_REPO_ROOT_NAME = "repo_root"

# (relative/path/to/file.py, enclosing function name): "why this destination is not, and
# cannot be, one fixed entry in TOOL_STATE_PATHS".
#
# `_format_report`'s CONFIG-DRIVEN write (the one that used to be here, SEAMCHECK_CONFIG
# ["report_output"/"map_output"] falling back to a repo-relative default) is gone from
# this list: it now calls `scancache.resolve_report_output`/`resolve_map_output`
# directly - the SAME functions `scancache._resolved_configured_paths` calls for the
# cache's own runtime exclusion check - so that write is REGISTERED, not allowlisted,
# and the audit verifies it like any other (see CONFIGURABLE_TOOL_STATE_DEFAULTS,
# imported below).
#
# What remains is a DIFFERENT write in the SAME function: an explicit `--out PATH`
# resolves to `pathlib.Path(repo_root) / PATH` - a destination the CALLER chooses at
# invocation time, exactly like every other `--out`/`destination` parameter in this
# codebase (`api.write_map_document`, the plain CLI's `--out`), none of which appear as
# findings at all because they never combine `repo_root` with a value this checker could
# statically resolve in the first place. This one only shows up as a distinct finding
# because it happens to spell that combination out (`Path(repo_root) / destination`)
# rather than taking an already-absolute path - there is no fixed value to register, and
# no way to have "isolated to this one branch" that isn't already true.
ALLOWLIST: dict[tuple[str, str], str] = {
    ("seamcheck/management/commands/seamcheck.py", "_format_report"): (
        "The --out branch only: `path = pathlib.Path(repo_root) / destination` where "
        "`destination` is options['out'], a value the CALLER chooses at invocation time "
        "- structurally identical to api.write_map_document's `destination` parameter "
        "and the plain CLI's --out, both of which are out of scope for the same reason "
        "and never even appear as findings, because neither combines repo_root with the "
        "caller's value the way this one does. Not the bug this ALLOWLIST used to record: "
        "the CONFIG-driven write in this same function is registered via "
        "scancache.resolve_report_output/resolve_map_output + "
        "CONFIGURABLE_TOOL_STATE_DEFAULTS, verified by test_scancache.py's dedicated "
        "tests, and no longer needs an entry here."
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


class Write:
    __slots__ = ("file", "function", "lineno", "refs")

    def __init__(self, file, function, lineno, refs):
        self.file = file
        self.function = function
        self.lineno = lineno
        self.refs = refs


def _walk_scoped(stmts, rooted: dict, tier_b: dict, visit_leaf) -> None:
    """Depth-first, in source order, calling `visit_leaf(stmt, rooted)` for every
    statement that is not itself a nested block - assignment tracking (the one thing
    both callers below need identically) happens here, once, rather than twice.

    Forks a COPY of `rooted` across each side of an `if`/`except` before recursing, so
    one branch's assignment can never leak into the other's: `path = A` in an `if` and
    `path = B` in its `else`, each followed by their OWN `path.write_text(...)`, must
    resolve to A and B respectively - a single shared dict (the first version of this
    checker) let whichever branch was scanned LAST silently win for BOTH write sites,
    which is worse than not tracking branches at all, because it looks precise while
    being wrong for one of them. `for`/`while`/`with`/`try.body` are not alternatives -
    code after them still ran through whatever the block set - so those keep sharing the
    same dict.
    """
    for stmt in stmts:
        if (isinstance(stmt, ast.Assign) and len(stmt.targets) == 1
                and isinstance(stmt.targets[0], ast.Name)):
            refs = _expr_refs(stmt.value, rooted, tier_b)
            if refs is not None:
                rooted[stmt.targets[0].id] = refs

        if isinstance(stmt, ast.If):
            _walk_scoped(stmt.body, dict(rooted), tier_b, visit_leaf)
            _walk_scoped(stmt.orelse, dict(rooted), tier_b, visit_leaf)
        elif isinstance(stmt, (ast.For, ast.AsyncFor, ast.While)):
            _walk_scoped(stmt.body, rooted, tier_b, visit_leaf)
            _walk_scoped(stmt.orelse, rooted, tier_b, visit_leaf)
        elif isinstance(stmt, (ast.With, ast.AsyncWith)):
            _walk_scoped(stmt.body, rooted, tier_b, visit_leaf)
        elif isinstance(stmt, ast.Try):
            _walk_scoped(stmt.body, rooted, tier_b, visit_leaf)
            for handler in stmt.handlers:
                _walk_scoped(handler.body, dict(rooted), tier_b, visit_leaf)
            _walk_scoped(stmt.orelse, rooted, tier_b, visit_leaf)
            _walk_scoped(stmt.finalbody, rooted, tier_b, visit_leaf)
        else:
            visit_leaf(stmt, rooted)


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
                def on_leaf(stmt, rooted, fn=fn):
                    nonlocal changed
                    if isinstance(stmt, ast.Return) and stmt.value is not None:
                        refs = _expr_refs(stmt.value, rooted, tier_b)
                        if refs is not None and tier_b.get(fn.name) != refs:
                            tier_b[fn.name] = refs
                            changed = True

                _walk_scoped(fn.body, {}, tier_b, on_leaf)
        if not changed:
            break
    return tier_b


def _expr_refs(node, rooted: dict, tier_b: dict):
    """None if `node` is not (as far as this can tell) a repo-root-rooted path; otherwise
    the set of names it was built from, for the covered-check to resolve."""
    if isinstance(node, ast.IfExp):
        # A ternary choosing between two constructions - `resolve_report_output(repo_root)
        # if fmt == "html" else resolve_map_output(repo_root)` - is exactly as reportable
        # as either branch alone: if EITHER resolves, the union of both branches' refs is
        # what a write through this expression could be built from. Handled here, not by
        # widening `_rooted` (which stays "is this ONE expression definitely a path"),
        # because an if-expression's two arms are alternatives, not a single structural
        # descent.
        body_refs = _expr_refs(node.body, rooted, tier_b)
        else_refs = _expr_refs(node.orelse, rooted, tier_b)
        if body_refs is None and else_refs is None:
            return None
        return (body_refs or set()) | (else_refs or set())
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
            def on_leaf(stmt, rooted, fn=fn, label=label):
                # A leaf statement is never itself a nested block (`_walk_scoped` only
                # calls back for those), so `ast.walk(stmt)` cannot re-discover a write
                # already handled by a separate, correctly-scoped recursive call -
                # except a nested def, which IS its own separate unit (`_functions_in`
                # visits it independently), so its writes must not be folded in here
                # under this function's local-variable history.
                for node in ast.walk(stmt):
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                        continue
                    base = _write_call_base(node)
                    if base is None:
                        continue
                    refs = _expr_refs(base, rooted, tier_b)
                    if refs is not None:
                        writes.append(Write(label, fn.name, getattr(node, "lineno", 0), refs))

            _walk_scoped(fn.body, {}, tier_b, on_leaf)
    return writes


def _is_covered(write: Write, known_paths, modules) -> bool:
    """True if ANY name this write was built from resolves, in ANY of `modules` (so it
    does not matter whether a name is defined in the write's OWN file or in a tier-B
    helper's - `resolve_report_output`'s refs include `_REPORT_OUTPUT_FALLBACK`, which
    lives in scancache.py, not in the management command that calls it), to one of
    `known_paths` - ordinarily `TOOL_STATE_PATHS + CONFIGURABLE_TOOL_STATE_DEFAULTS`, so
    a write is "covered" whether it matches a fixed tool-state path or the DEFAULT of a
    config-driven one (see scancache.py's own comment on `CONFIGURABLE_TOOL_STATE_
    DEFAULTS` for what that second half does and does not verify). A name is looked up
    via `getattr`, so it does not matter whether it is defined in a module or merely
    imported into it - Python already resolved that."""
    for name in write.refs:
        for module in modules:
            value = getattr(module, name, None)
            if isinstance(value, pathlib.Path) and value in known_paths:
                return True
    return False


def _uncovered(writes, files, known_paths, allowlist) -> list[tuple[Write, str | None]]:
    """(write, allowlist_reason_or_None) for every write that is neither registered nor
    allowlisted - allowlist_reason is None for a genuine, unexplained gap.

    `files` is every analyzed source file, not only the ones a write was found in: a
    tier-B function's refs can name a constant that lives in a DIFFERENT module from the
    one doing the writing (`resolve_report_output`'s `_REPORT_OUTPUT_FALLBACK` is defined
    in scancache.py, which itself constructs no repo-root write of its own and so would
    never appear in `writes` - only in `files`).
    """
    modules = [m for m in (_module_for(f) for f in files if isinstance(f, pathlib.Path))
              if m is not None]
    problems = []
    for write in writes:
        rel = str(write.file.relative_to(_SOURCE_ROOT)) if isinstance(write.file, pathlib.Path) else str(write.file)
        if _is_covered(write, known_paths, modules):
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

        problems = _uncovered(writes, files, TOOL_STATE_PATHS + CONFIGURABLE_TOOL_STATE_DEFAULTS, ALLOWLIST)
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

        problems = _uncovered(writes, files, shrunk + CONFIGURABLE_TOOL_STATE_DEFAULTS, ALLOWLIST)
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

        problems = _uncovered(writes, [synthetic], TOOL_STATE_PATHS + CONFIGURABLE_TOOL_STATE_DEFAULTS, ALLOWLIST)
        self.assertEqual(len(problems), 1,
                         "a write to a path nobody registered or allowlisted must be reported")
        self.assertEqual(problems[0][0].function, "leak")
