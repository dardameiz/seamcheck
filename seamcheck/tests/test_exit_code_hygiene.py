"""No exit code escapes exitcodes.py.

`triage`'s failure path returned a bare literal `2` on both doors, colliding with
`EXIT_NO_BASELINE` even though it was never routed through that constant at all - it just
happened to be the same number. Five more sites (`_resolve`'s unknown-command-name refusal,
`_set_tunnel_plain`'s bad `--set-tunnel` value, `help <bad-name>`, `main()`'s generic
`CommandError` handler, and `_format_report`'s bad `--format` `ValueError`) turned out to
share the exact same shape once found by hand, one grep and one file at a time. A bare digit
that happens to be numerically correct today reads, in a diff, exactly like the name it
should have been - visually indistinguishable - so finding the next one by re-reading both
files is not a plan. This asks the SOURCE instead.

The rule: neither front door (`seamcheck/cli.py`, `seamcheck/management/commands/
seamcheck.py`) may `return` or `raise SystemExit(...)` a bare, non-zero integer literal.
`0` is exempt - it is unambiguously `EXIT_CLEAN`, the one code nothing else in the table
could be confused with, so dozens of ordinary `return 0` success paths need no import to
stay exactly what they already are. Every OTHER exit code must be spelled as the name
`seamcheck.exitcodes` gives it (`EXIT_FINDINGS`, `EXIT_NO_BASELINE`, `EXIT_USAGE`,
`EXIT_ENVIRONMENT`) - imported however the surrounding code already imports it (a bare
name, or `exitcodes.EXIT_USAGE`), never typed as a digit that happens to be numerically
right today and silently wrong the moment the table's shape changes.

What this catches: a `return 2` or `raise SystemExit(2)` typed directly, anywhere in
either file, including on either arm of a ternary (`return 0 if ok else 2`). What it does
NOT catch, by design, and why that is still sound: a value built any other way - a `Name`
(the constant's own name), an `Attribute` (`exitcodes.EXIT_USAGE`), a `Call`
(`gate_code(...)`, `int(exit_code.code or 0)`) - is exactly the shape a correctly-named
exit code already has, so it is left alone rather than reported. It does not verify that a
NAMED reference actually resolves to a real `exitcodes` constant at import time (Python's
own `NameError`/`ImportError` at collection time already guarantees that for anything the
test suite ever imports) - only that nobody wrote the number out by hand instead.
"""
from __future__ import annotations

import ast
import pathlib
from collections.abc import Iterator
from unittest import mock

from django.core.management import call_command
from django.test import SimpleTestCase

from seamcheck import cli, exitcodes

_PACKAGE_DIR = pathlib.Path(__file__).resolve().parent.parent
_REPO_ROOT = _PACKAGE_DIR.parent
_DOORS = (
    _PACKAGE_DIR / "cli.py",
    _PACKAGE_DIR / "management" / "commands" / "seamcheck.py",
)


def _leaves(node: ast.AST) -> Iterator[ast.AST]:
    """`node` itself, or - if it is a ternary - every leaf reachable through it.

    `return 0 if not name else 2`'s AST is one `Return` whose value is one `IfExp`; the
    literal that matters is on `body` or `orelse`, never the `IfExp` node itself. A
    ternary nested inside a ternary is walked the same way, though nothing in either
    door does that today.
    """
    if isinstance(node, ast.IfExp):
        yield from _leaves(node.body)
        yield from _leaves(node.orelse)
    else:
        yield node


def _bare_nonzero_int_exits(tree: ast.AST) -> Iterator[tuple[int, int]]:
    """Every `return`/`raise SystemExit(...)` in `tree` whose value is a bare int
    literal other than `0`, as `(line, literal)`.

    Deliberately narrow: only a `Return`'s direct value, or a `SystemExit(...)` call's
    first argument, is inspected (through `_leaves` for a ternary) - never an arbitrary
    expression nested inside a tuple, a subscript, or a call's OTHER arguments, none of
    which either door ever uses to carry an exit code. Widening this to full data-flow
    (tracing a literal assigned to a local several lines above a bare `return code`)
    would matter the day either file starts doing that - neither does, checked by hand
    against `_bare_nonzero_int_exits`'s own docstring claim every time this file changes.
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.Return) and node.value is not None:
            candidates = [node.value]
        elif (
            isinstance(node, ast.Raise)
            and isinstance(node.exc, ast.Call)
            and isinstance(node.exc.func, ast.Name)
            and node.exc.func.id == "SystemExit"
            and node.exc.args
        ):
            candidates = [node.exc.args[0]]
        else:
            continue
        for value in candidates:
            for leaf in _leaves(value):
                if (
                    isinstance(leaf, ast.Constant)
                    and isinstance(leaf.value, int)
                    and not isinstance(leaf.value, bool)
                    and leaf.value != 0
                ):
                    yield node.lineno, leaf.value


class NoRawExitCodesTests(SimpleTestCase):
    def test_neither_door_returns_a_bare_nonzero_exit_code(self):
        findings = []
        for path in _DOORS:
            tree = ast.parse(path.read_text(), filename=str(path))
            for lineno, value in _bare_nonzero_int_exits(tree):
                findings.append(f"{path.relative_to(_REPO_ROOT)}:{lineno}: bare {value!r}")

        self.assertEqual(
            findings, [],
            "a bare, non-zero exit code literal was returned/raised outside "
            "exitcodes.py - name it instead (seamcheck.exitcodes.EXIT_*):\n"
            + "\n".join(findings),
        )

    def test_every_exit_code_constant_has_a_distinct_value(self):
        # The other way this bug could reappear: a new constant added to exitcodes.py
        # that quietly collides with an existing one, even though every call site
        # spells its own code by name correctly.
        by_value: dict[int, list[str]] = {}
        for name in dir(exitcodes):
            if name.startswith("EXIT_"):
                by_value.setdefault(getattr(exitcodes, name), []).append(name)
        collisions = {value: names for value, names in by_value.items() if len(names) > 1}

        self.assertEqual(collisions, {}, f"exit codes sharing a value: {collisions}")


class ExplainExitsOnAWrongId(SimpleTestCase):
    """`explain` answers in prose, so it has no envelope to carry a code - and it printed
    "No symbol with id ..." and exited 0. That is the same "failure in the body, success to
    the shell" that findings/symbols/diff were fixed for, in the one command whose whole
    job is being pointed at an id that might be wrong."""

    def test_an_unknown_id_is_a_usage_error_on_both_doors(self):
        from seamcheck.exitcodes import EXIT_USAGE, UNKNOWN_SYMBOL

        miss = f"{UNKNOWN_SYMBOL} `nope` in the current scan."
        with mock.patch("seamcheck.cli._worth_scanning", return_value=True), \
             mock.patch("seamcheck.scancache.cached_scan", return_value=(object(), {})), \
             mock.patch("seamcheck.api.explain_with_hint", return_value=miss):
            self.assertEqual(cli._run_without_django(["--explain", "nope"], verbose=False),
                             EXIT_USAGE)

        with mock.patch("seamcheck.scancache.cached_scan", return_value=(object(), {})), \
             mock.patch("seamcheck.api.explain_with_hint", return_value=miss), \
             self.assertRaises(SystemExit) as raised:
            call_command("seamcheck", "--explain", "nope")
        self.assertEqual(raised.exception.code, EXIT_USAGE)

    def test_a_real_id_still_exits_clean(self):
        with mock.patch("seamcheck.cli._worth_scanning", return_value=True), \
             mock.patch("seamcheck.scancache.cached_scan", return_value=(object(), {})), \
             mock.patch("seamcheck.api.explain_with_hint", return_value="## thing (url)"):
            self.assertEqual(cli._run_without_django(["--explain", "thing"], verbose=False), 0)
