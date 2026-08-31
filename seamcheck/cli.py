"""The `seamcheck` command.

A Django management command needs a `manage.py`, a settings module on the environment and
the right virtualenv active. That is fine inside a project you already know; it is a poor
first thirty seconds for someone who has just run `pip install seamcheck`.

This finds the project itself - the manage.py beside you, or the settings module the
environment already names - then hands off to the same management command. It is a front
door, not a second implementation: every flag is parsed and executed in exactly one place.

It also owns the two things that make the front door usable rather than merely present:

* **What each command is for.** `seamcheck help scan` answers in prose with a worked
  example. Forwarding to argparse's flag dump answers a question nobody asked.
* **A quiet start.** Scanning means importing the host project, and importing a real
  Django project prints its own warnings and start-up logging first. That noise is not
  seamcheck's output and does not belong in it; `--verbose` puts it back.
"""

from __future__ import annotations

import argparse
import os
import pathlib
import re
import shutil
import sys
import textwrap
from dataclasses import dataclass, field

from seamcheck.quiet import quiet


@dataclass(frozen=True)
class Command:
    """One word a person types, and everything needed to explain and run it."""

    args: list[str]
    summary: str
    # Prose: what the command is for, what it produces, and when to reach for it.
    detail: str
    # Worked examples, shown under the prose. (command line, what it does)
    examples: list[tuple[str, str]] = field(default_factory=list)
    # A bare number after the command becomes this flag: `backfill 30` -> --backfill 30.
    # `default` is what the flag gets when no number is given, so the command works with
    # no arguments at all rather than dying on argparse's "expected one argument".
    takes_number: str | None = None
    number_default: str | None = None


COMMANDS: dict[str, Command] = {
    "map": Command(
        args=["--format", "map", "--serve"],
        summary="Scan, then open the UI. Start here.",
        detail=(
            "Scans, writes the whole UI as a single self-contained HTML file - the graph, "
            "the review sections, the file tree, the commit picker - and then serves that "
            "file from this machine so you have a link to click. Two links, in fact: one "
            "for this machine and one to type on a phone on the same wifi. Ctrl-C stops "
            "the server; the file stays where it was written.\n\n"
            "It serves rather than printing a file:// path because a file:// link is not "
            "much of a link: VS Code's terminal opens it inside VS Code, and a phone "
            "cannot use it at all. An http:// one gets handed to a real browser.\n\n"
            "Nothing is uploaded and nothing leaves your machine - but while it runs, "
            "anyone on this network holding the link can read the report. `--local-only` "
            "binds loopback instead, at the cost of the phone link. `--tunnel` goes the "
            "other way and opens a temporary public HTTPS address for a device that is "
            "not on this wifi."
        ),
        examples=[
            ("seamcheck map", "scan, write, serve, print the links"),
            ("seamcheck map --open", "...and open the browser for you"),
            ("seamcheck map --no-serve", "just write the file - for CI and scripts"),
            ("seamcheck map --local-only", "no phone link; loopback only"),
            ("seamcheck map --since main", "highlight what changed against main"),
            ("seamcheck map --out /tmp/map.html", "choose where it lands"),
        ],
    ),
    "check": Command(
        args=["--check"],
        summary="The CI gate. Exit 1 on new findings, 2 if no baseline, 0 clean.",
        detail=(
            "Scans, then compares against the stored snapshot for the baseline commit and "
            "fails on anything NEW. Existing findings do not fail the build - only ones "
            "this change introduced - so it can be turned on in a project that already has "
            "a backlog.\n\n"
            "Three exit codes, deliberately: 0 clean, 1 new findings, and 2 for 'no "
            "baseline to compare against'. A gate with nothing to compare against has not "
            "passed, it has not run, and reporting that as 0 is how a broken gate stays "
            "green for months."
        ),
        examples=[
            ("seamcheck check", "against the snapshot for HEAD"),
            ("seamcheck check --since $BASE_SHA", "against the commit the PR branched from"),
            ("seamcheck check --format markdown", "fail the build AND print a digest to comment with"),
        ],
    ),
    "backfill": Command(
        args=[],
        summary="Scan the last N commits so the map's commit picker has history.",
        detail=(
            "A scan describes one commit; two scans describe a change. With only today's "
            "snapshot on disk the commit picker has nothing to offer and `check` has no "
            "baseline. This walks back through history and scans each commit into its own "
            "snapshot, so both start working.\n\n"
            "Each commit is checked out into its own temporary git worktree and scanned "
            "there, with THIS version of seamcheck rather than the one that commit shipped "
            "with - otherwise a diff means 'the project changed, or the scanner did'. "
            "Budget roughly 30 seconds per commit; commits already scanned are skipped, so "
            "running it again is cheap."
        ),
        examples=[
            ("seamcheck backfill", "the last 20 commits"),
            ("seamcheck backfill 100", "the last 100"),
            ("seamcheck backfill 50 --backfill-ref main", "walk main rather than the branch you are on"),
        ],
        takes_number="--backfill",
        number_default="20",
    ),
    "scan": Command(
        args=[],
        summary="Scan and print the totals. No UI, no server.",
        detail=(
            "Reads the project's URLconf, templates, JavaScript and stylesheets, builds "
            "one graph of what reaches what, and prints the totals by status.\n\n"
            "It also writes two things you get for free: the graph as JSON under "
            "docs/maps/, and a snapshot keyed by the current commit. The snapshot is what "
            "later makes `check` and the map's commit picker able to say what changed - "
            "so running scan regularly is what builds the history."
        ),
        examples=[
            ("seamcheck scan", "the whole project, summarised"),
            ("seamcheck scan --repo-root ../other", "scan a project you are not standing in"),
        ],
    ),
    "report": Command(
        args=["--format", "markdown"],
        summary="A digest for a chat or a pull request.",
        detail=(
            "The same scan as `scan`, rendered as markdown you can paste into a PR comment "
            "or hand to an assistant. Grouped and worst-first, so the top of it is the part "
            "worth reading."
        ),
        examples=[
            ("seamcheck report", "print it"),
            ("seamcheck report --out findings.md", "write it to a file"),
            ("seamcheck report --since main", "only what changed against main"),
        ],
    ),
    "serve": Command(
        args=["--format", "map", "--serve"],
        summary="The same as `map`. The name to reach for when you mean the phone.",
        detail=(
            "Identical to `seamcheck map` - one implementation, two names - because "
            "serving is what map does now. Kept because `serve` is the word that comes to "
            "mind when the intent is 'get this onto my phone', and because a name that "
            "has been in the help should not simply vanish.\n\n"
            "`--tunnel` opens a temporary public HTTPS address through cloudflared, for a "
            "device that is not on this wifi. Anyone with that link can read the report, "
            "and it dies with the command."
        ),
        examples=[
            ("seamcheck serve", "same as `seamcheck map`"),
            ("seamcheck serve --tunnel", "plus a temporary public link"),
        ],
    ),
    "json": Command(
        args=["--json"],
        summary="The whole graph, as JSON.",
        detail=(
            "Every symbol and every edge, unfiltered, for piping into jq or feeding to "
            "something else. Progress goes to stderr, so redirecting stdout gives you "
            "clean JSON."
        ),
        examples=[
            ("seamcheck json > graph.json", "the whole graph"),
            ("seamcheck json | jq '.symbols[] | select(.status==\"unresolved\")'", "just the unresolved ones"),
        ],
    ),
    "explain": Command(
        args=["--explain"],
        summary="Everything known about one symbol, with its source.",
        detail=(
            "Takes a symbol id - the id shown on any row or node - and prints its status, "
            "where it lives, the chain that reaches it and the source it was read from."
        ),
        examples=[("seamcheck explain 'url:/api/get-user-stats/'", "one symbol, in full")],
    ),
    "triage": Command(
        args=["--triage"],
        summary="Record a disposition for a finding, so the gate stops raising it.",
        detail=(
            "Marks one symbol approved, confirmed or deferred, with a reason, and stores it "
            "against a fingerprint of the symbol. `check` then stops failing on it - until "
            "the symbol itself changes, at which point the triage is invalidated and it "
            "comes back. An 'approved' that survives the code it approved is how a "
            "suppression file becomes a lie."
        ),
        examples=[
            ("seamcheck triage 'url:/webhooks/stripe/' --status approved --reason 'called by Stripe'",
             "stop the gate failing on an endpoint only an external service calls"),
        ],
    ),
}


_SETTINGS_RE = re.compile(r"""["']DJANGO_SETTINGS_MODULE["']\s*,\s*["']([\w.]+)["']""")


def find_project(start: pathlib.Path) -> tuple[str, pathlib.Path] | None:
    """(settings module, project root) read out of the nearest manage.py, or None.

    Every manage.py names its settings module, so there is no need to make anyone repeat
    it. Walks upward, because running from inside an app directory is normal.

    Deliberately free of side effects. An earlier version chdir'd and edited sys.path from
    inside this lookup, which left the caller's process somewhere it never asked to be -
    and in the tests, inside a directory that had since been deleted.
    """
    for directory in [start, *start.parents]:
        manage = directory / "manage.py"
        if not manage.is_file():
            continue
        match = _SETTINGS_RE.search(manage.read_text(encoding="utf-8", errors="replace"))
        if match:
            return match.group(1), directory
    return None


# The three a person types, in the order they would type them. The rest are every bit as
# supported - an agent driving this over MCP or a shell uses `json`, `explain` and
# `triage` far more than a human does - but nine equal lines is a menu, not an answer to
# "what do I run". They are listed, on one line, with `help <command>` for each.
PRIMARY = ("map", "check", "backfill")


def _overview() -> str:
    width = max(len(name) for name in PRIMARY)
    listing = "\n".join(f"  {name:<{width}}  {COMMANDS[name].summary}" for name in PRIMARY)
    rest = " \u00b7 ".join(name for name in COMMANDS if name not in PRIMARY)
    return (
        f"commands:\n{listing}\n\n"
        f"also:\n  {rest}\n"
        "     seamcheck help <command>   what any of them is for, with examples\n\n"
        "options:\n"
        "  -v, --verbose   show the host project's own warnings and start-up logging\n"
        "  -q, --quiet     no progress bar (it is off already when output is redirected)\n\n"
        "Any flag the management command accepts also works here, e.g.\n"
        "  seamcheck map --since main --no-serve\n"
        "  seamcheck map --tunnel\n"
        "  seamcheck check --since $BASE_SHA\n"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="seamcheck",
        description="Find the code your project no longer connects to - and the "
                    "connections it only thinks it has.",
        epilog=_overview(),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        add_help=False,
    )
    parser.add_argument("command", nargs="?", default="scan", help=argparse.SUPPRESS)
    parser.add_argument("rest", nargs=argparse.REMAINDER, help=argparse.SUPPRESS)
    return parser


def _wrap(text: str) -> str:
    """Prose, at a width a terminal can actually read.

    Unwrapped it runs the full width of whatever window it lands in, which past about 100
    columns is a wall the eye loses its place in - the exact problem this help exists to
    solve. Paragraph breaks are preserved; the source strings own where those go.
    """
    width = min(max(shutil.get_terminal_size((80, 24)).columns - 2, 40), 92)
    return "\n\n".join(
        textwrap.fill(paragraph.strip(), width=width) for paragraph in text.split("\n\n")
    )


def command_help(name: str) -> str:
    """The long help for one command: what it is for, then how to type it."""
    entry = COMMANDS[name]
    passthrough = " ".join(entry.args)
    lines = [f"seamcheck {name} - {entry.summary}", "", _wrap(entry.detail), ""]
    if entry.examples:
        lines.append("examples:")
        width = max(len(cmd) for cmd, _ in entry.examples)
        lines += [f"  {cmd:<{width}}   # {what}" for cmd, what in entry.examples]
        lines.append("")
    if entry.takes_number:
        lines += [
            _wrap(f"A bare number is the count: `seamcheck {name} 30`. "
                  f"With none, it is {entry.number_default}."),
            "",
        ]
    lines.append(_wrap(
        f"Runs `manage.py seamcheck {passthrough}`. "
        f"`seamcheck {name} -- --help` lists every flag it accepts."
        if passthrough else
        "Every flag the management command accepts also works here: "
        f"`seamcheck {name} -- --help` lists them."
    ))
    return "\n".join(lines)


def _split_passthrough(argv: list[str]) -> tuple[list[str], list[str]]:
    """Everything before a literal `--`, and everything after it.

    Done before argparse sees the list, because argparse eats a lone `--` and leaves no
    way to tell `seamcheck map --help` (explain the map) from `seamcheck map -- --help`
    (list the management command's flags) - which made the second one impossible to type.
    Anything after `--` is forwarded verbatim, front-door flags included.
    """
    if "--" not in argv:
        return argv, []
    cut = argv.index("--")
    return argv[:cut], argv[cut + 1:]


def _split_flags(argv: list[str]) -> tuple[list[str], bool, bool]:
    """Pull the front door's own flags out of the argument list.

    They are removed rather than forwarded: the management command has never heard of
    --verbose, and passing it through turns a convenience into an error.
    """
    verbose = quiet_off = False
    rest = []
    for argument in argv:
        if argument in ("-v", "--verbose"):
            verbose = True
        elif argument in ("-q", "--quiet"):
            quiet_off = True
        else:
            rest.append(argument)
    return rest, verbose, quiet_off


def _resolve(known, parser, passthrough: list[str] | None = None) -> tuple[str, list[str]] | int:
    """(command, arguments to forward), or an exit code when there is nothing to run."""
    passthrough = passthrough or []
    name = known.command
    if name not in COMMANDS:
        # An unknown word is far more likely a typo than a flag for the default command,
        # and guessing wrong here runs a scan the user did not ask for.
        if not name.startswith("-"):
            print(f"seamcheck: no command named {name!r}\n", file=sys.stderr)
            parser.print_help(sys.stderr)
            return 2
        return "scan", [name, *known.rest, *passthrough]

    entry = COMMANDS[name]
    rest = list(known.rest)
    # `seamcheck map --help` should explain the map, not dump the management command's
    # flags - that is what the user is asking for, and argparse's answer is a wall of
    # every flag every command shares. `-- --help` still reaches the real thing.
    if not passthrough and rest and rest[0] in ("-h", "--help", "help"):
        print(command_help(name))
        return 0

    arguments = list(entry.args)
    if entry.takes_number:
        # A bare number is the count. Without this, `seamcheck backfill` forwarded a
        # valueless --backfill and argparse answered "expected one argument" - a front
        # door that fails on being opened.
        if rest and rest[0].isdigit():
            arguments += [entry.takes_number, rest.pop(0)]
        elif entry.takes_number not in rest and entry.takes_number not in passthrough:
            arguments += [entry.takes_number, entry.number_default]
    return name, arguments + rest + passthrough


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    argv, passthrough = _split_passthrough(argv)
    argv, verbose, no_progress = _split_flags(argv)
    parser = build_parser()

    if not argv or argv[0] in ("-h", "--help"):
        parser.print_help()
        return 0
    if argv[0] == "help":
        name = argv[1] if len(argv) > 1 else ""
        if name in COMMANDS:
            print(command_help(name))
            return 0
        if name:
            print(f"seamcheck: no command named {name!r}\n", file=sys.stderr)
        parser.print_help()
        return 0 if not name else 2

    known = parser.parse_args(argv)
    resolved = _resolve(known, parser, passthrough)
    if isinstance(resolved, int):
        return resolved
    _, arguments = resolved

    found = find_project(pathlib.Path.cwd())
    if found:
        settings_module, root = found
        # Seamcheck reads a project by relative path, so it has to run from the root.
        sys.path.insert(0, str(root))
        os.chdir(root)
    else:
        settings_module, root = os.environ.get("DJANGO_SETTINGS_MODULE"), None
    if not settings_module:
        print(
            "seamcheck: no Django project here.\n\n"
            "Run it from a directory with a manage.py in it or above it, or set\n"
            "DJANGO_SETTINGS_MODULE. Seamcheck reads a project's URLconf, templates and\n"
            "static files, so it needs to know which project it is looking at.",
            file=sys.stderr,
        )
        return 2
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", settings_module)
    if no_progress:
        # Read back by the management command, which owns the bar. An environment
        # variable rather than a flag because the flag would have to be understood by
        # both parsers, and only one of them should own the vocabulary.
        os.environ["SEAMCHECK_NO_PROGRESS"] = "1"

    import django
    from django.core.management import call_command
    from django.core.management.base import CommandError

    # The whole run, not just setup(): a project logs on import, and again the first time
    # the scan touches its app registry.
    with quiet(not verbose):
        django.setup()
        try:
            call_command("seamcheck", *arguments)
        except CommandError as error:
            print(f"seamcheck: {error}", file=sys.stderr)
            return 2
        except SystemExit as exit_code:  # --check and friends signal through the exit code
            return int(exit_code.code or 0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
