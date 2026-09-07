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
import contextlib
import json
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


def _why_reasons_block() -> str:
    """The nine `WhyWrong` reasons, as the aligned table `seamcheck help triage` shows -
    generated from `triage.WHY_HELP` (kept beside the enum "so the two can never drift",
    per that module's own comment) rather than retyped here a third time. This used to be
    a hand-typed copy with its own one-line descriptions that were never checked against
    `WHY_HELP` - diffed word-for-word, 7 of the 9 already read differently (DEPENDENCY, for
    one: this file said "a CDN bundle, a package, the framework itself"; `WHY_HELP` said "a
    CDN bundle, a package, the framework's own code"). One vocabulary, one place it is
    worded - this function is the ONLY thing standing between the enum and the reader.
    """
    from seamcheck.triage import WHY_HELP

    width = max(len(word) for word in WHY_HELP)
    return "\n".join(f"  {word:<{width}}  {sentence}" for word, sentence in WHY_HELP.items())


COMMANDS: dict[str, Command] = {
    "triage": Command(
        args=["--triage"],
        summary="Record \"this one is fine, and here is why\".",
        detail=(
            "Marks one finding so it stops being raised, and records WHY. The mark is "
            "keyed to the evidence rather than to the symbol, so it expires by itself the "
            "moment that evidence changes - a third writer appearing on an element is a "
            "new problem, not the one you approved. An 'approved' that survives the "
            "code it approved is how a suppression file becomes a lie.\n\n"
            "`--reason` is your own words and never leaves this machine. `--wrong` is one "
            "of nine fixed words, and it is the only part `seamcheck share` can pass on: "
            "free text is exactly where a path or a table name would escape, so the "
            "vocabulary is fixed rather than trusted.\n\n"
            "The nine, each a false-positive class measured on a real repository:\n"
            f"{_why_reasons_block()}\n\n"
            "`genuinely-dead` matters as much as the rest: a finding confirmed RIGHT is "
            "evidence too.\n\n"
            "The mark is remembered. When the evidence changes it is kept, stamped with "
            "the day, and the finding is raised as RETURNED - with who marked it, when, "
            "and why - rather than as something new. Look once more; then re-mark it, or "
            "`--undo` to take the mark off for good."
        ),
        examples=[
            ("seamcheck triage 'css_selector:class:x' --wrong consumed-by-dependency",
             "mark it wrong, and say why in a word that can be shared"),
            ("seamcheck triage 'url:/api/x' --wrong genuinely-dead --reason 'removing in PR 412'",
             "confirm it IS dead; the prose stays local"),
            ("seamcheck triage 'url:/api/x' --undo",
             "take the mark off; the finding is raised again"),
        ],
    ),
    "share": Command(
        args=[],
        summary="A report about the scan that contains none of your code.",
        detail=(
            "Scans, then reduces the result to counts and fixed words: how many findings "
            "of each kind, in each status, and why the uncertain ones are uncertain. No "
            "file paths, no symbol, table, column or route names, no code, no repository "
            "identity, no git SHA. Every value is a number or a word seamcheck itself "
            "defines, which is a property you can check by reading one file - "
            "seamcheck/share.py - rather than a promise.\n\n"
            "Nothing is sent. Seamcheck makes no network calls at all. The report is "
            "printed, written to seamcheck-share.md, and accompanied by a link that opens "
            "a pre-filled GitHub issue in your browser - which submits nothing until you "
            "press the button.\n\n"
            "It exists because the scans worth learning from are the ones that got a "
            "private repository wrong, and those are exactly the ones nobody can send. "
            "One aggregate line - a data layer detected, no schema present, hundreds of "
            "findings against it - is enough to find a bug like that, and it says nothing "
            "about the code.\n\n"
            "`--with-deps` adds one thing the strict payload cannot carry: the public "
            "packages you depend on. Counts say an extractor is missing a dependency's "
            "markup; they cannot say WHICH library, and nobody can write the handling for "
            "a library they cannot name. It is opt-in because a scoped package name can "
            "belong to a private registry and this cannot tell offline - so you see the "
            "list first.\n\n"
            "If the repository belongs to an employer or a client, sharing metrics about "
            "it is their decision rather than yours."
        ),
        examples=[
            ("seamcheck share", "print the report, write seamcheck-share.md, show the link"),
            ("seamcheck share --json", "the raw payload, for a script or an agent"),
            ("seamcheck share --quiet", "just the report, no explanation or link"),
            ("seamcheck share --with-deps",
             "also list your public dependencies - the one thing that makes a "
             "\"consumed-by-dependency\" report actionable"),
        ],
    ),
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
            "anyone on this network holding the link can read the report. A phone that is "
            "NOT on this wifi cannot open the second link at all; "
            "`seamcheck config --tunnel always` remembers, for this machine, that every "
            "run should also print a public HTTPS one. `--local-only` "
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
            ("seamcheck map --out /tmp/map/", "a folder: small index.html, data loaded as needed"),
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
    "observe": Command(
        args=["--observe"],
        summary="Drive the running app and record what it really queried and fetched.",
        detail=(
            "A third of a real graph is runtime-built: a selector assembled from a "
            "variable, a fetch target concatenated at call time. No reader of source will "
            "ever resolve those - it is the floor of what static analysis can know.\n\n"
            "The browser knows. This visits the app with a probe installed ahead of its own "
            "scripts, and records every selector actually queried (and whether it found "
            "anything), every URL actually requested, and every class actually applied.\n\n"
            "The evidence is keyed to the current commit and says nothing about pages the "
            "run did not visit - a route nobody clicked leaves no trace and looks exactly "
            "like one that is broken. Everything it promotes is labelled as observed for "
            "that reason. Needs the app running, and `pip install 'seamcheck[observe]'`."
        ),
        examples=[
            ("seamcheck observe", "visit every page the graph knows about"),
            ("seamcheck observe -- --base-url http://localhost:8000", "somewhere else"),
            ("seamcheck observe http://127.0.0.1:8080/store/", "just these pages"),
            ("seamcheck observe -- --shots OTHER/shots", "...and screenshot each one"),
        ],
    ),
    "config": Command(
        args=["--show-config"],
        summary="Show the paths a scan will use, and where each came from.",
        detail=(
            "Seamcheck works out most of its config from the project: Django already knows "
            "where its URLconf, templates, apps and static dirs are, so asking the settings "
            "and the app registry is exact rather than a guess. Anything you set in "
            "SEAMCHECK_CONFIG wins over what was detected.\n\n"
            "Run this first on a project you have not scanned before. A wrong path is the "
            "difference between a real report and an invented one - a CSS root set narrow "
            "enough to exclude stylesheets whose templates are still being read will report "
            "working CSS as broken, and the only way to catch that is to look at the paths."
            "\n\n"
            "One setting here is not about this project at all. `--tunnel always` is "
            "remembered for this MACHINE, and every later `map` or `serve` on it also "
            "prints a public HTTPS link - the one that works on a phone that is not on "
            "this wifi, which is where a link usually gets opened. It is stored rather "
            "than defaulted because it is the one thing seamcheck does that leaves the "
            "machine: anyone holding that link can read the report while the command "
            "runs. `--tunnel never` puts it back, and `--local-only` overrules it for a "
            "single run."
        ),
        examples=[
            ("seamcheck config", "what this project resolved to"),
            ("seamcheck config --tunnel always", "every map also gets a link that works off this wifi"),
            ("seamcheck config --tunnel never", "...turn that back off"),
            ("seamcheck config --repo-root ../other", "for a project you are not standing in"),
        ],
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
    "symbols": Command(
        args=["--symbols"],
        summary="Find a symbol by name. The cheap way to get an id.",
        detail=(
            "Every other command takes a symbol id, and getting one wrong used to cost a "
            "full scan to be told so - 88 seconds on a 500k-line project, to read `No "
            "symbol with id ...`. This answers from the cached scan in a fraction of a "
            "second, and it is the right first call before explain or triage.\n\n"
            "`--refresh` skips that cache in both directions, for a tree it cannot judge on "
            "its own - a fresh checkout, a restored backup, a clock that just got corrected."
        ),
        examples=[
            ("seamcheck symbols --search push", "everything whose id or label says push"),
            ("seamcheck symbols --search push --kind url", "...routes only"),
        ],
    ),
    "findings": Command(
        args=["--findings"],
        summary="What is wrong, filtered and bounded. Start here.",
        detail=(
            "The whole graph is 72 MB on a 500k-line project and answers no question by "
            "itself. This answers the one an agent actually has - what is wrong, and "
            "where - narrowed by file, kind, status or owning function, and it says what "
            "it left out so nothing looks complete when it is not. By default it only "
            "shows what the tool calls broken; pass --status uncertain or --status "
            "connected to see those too, and the answer names which statuses it searched. "
            "A finding carrying any triage mark is also left out by default - the same "
            "'what is wrong' as --check and --format sarif/github answer - "
            "--include-triaged lists everything, marks included.\n\n"
            "`--refresh` skips the scan cache in both directions - the escape for a tree "
            "it cannot judge on its own."
        ),
        examples=[
            ("seamcheck findings --file app/views.py", "what is wrong in the file I am editing"),
            ("seamcheck findings --kind redis_key --limit 50", "one kind, fifty rows"),
            ("seamcheck findings --cursor 50", "the next page"),
        ],
    ),
    "diff": Command(
        args=["--diff"],
        summary="What appeared, vanished or changed status since a ref.",
        detail=(
            "\"What did this commit break\" is a question CI and an agent both ask, and "
            "until now the only way to ask it was `check --since`, which folds the answer "
            "into a pass/fail gate - there was no way to just SEE the list. This is a "
            "separate comparison from the one `check` uses, not a shared implementation: "
            "it is unfiltered by triage, so a caller diffing two arbitrary points sees the "
            "raw graph difference rather than a CI-gate's opinion.\n\n"
            "`--refresh` skips the scan cache in both directions - the escape for a tree "
            "it cannot judge on its own."
        ),
        examples=[
            ("seamcheck diff --since origin/main", "what this branch changed"),
            ("seamcheck diff --since HEAD~5 --limit 50", "the last 5 commits, fifty rows"),
            ("seamcheck diff --since main --cursor 50", "the next page"),
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


def version_line() -> str:
    """The installed version, plus a warning when that number can be stale.

    An editable install records its version at install time and never revisits it, so a
    checkout whose pyproject has moved on reports the old number while running the new
    code. That looks exactly like a failed upgrade, and the only way to tell is to see
    where the module is being imported from - so it is printed.
    """
    from importlib.metadata import PackageNotFoundError, version

    try:
        installed = version("seamcheck")
    except PackageNotFoundError:  # running straight from a source tree
        installed = "unknown"

    here = pathlib.Path(__file__).resolve().parent
    from_site_packages = "site-packages" in here.parts or "dist-packages" in here.parts
    line = f"seamcheck {installed}"
    if not from_site_packages:
        line += (
            f"\n  running from {here}"
            "\n  This is a source or editable install: the version above was recorded when"
            "\n  it was installed and may lag the code. `pip install -e <path>` refreshes it."
        )
    return line


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
        "  -q, --quiet     no progress bar (it is off already when output is redirected)\n"
        "  -V, --version   which seamcheck this is, and where it is running from\n\n"
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

    def one(paragraph: str) -> str:
        # A paragraph whose lines are INDENTED is a table, not prose - a list of flag
        # values with their meanings, aligned in columns. Filling it turns the columns
        # into a run-on sentence, which is what happened to the triage vocabulary.
        if any(line.startswith("  ") for line in paragraph.splitlines() if line.strip()):
            return "\n".join(line.rstrip() for line in paragraph.splitlines() if line.strip())
        return textwrap.fill(paragraph.strip(), width=width)

    return "\n\n".join(one(paragraph) for paragraph in text.split("\n\n"))


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
            from seamcheck.exitcodes import EXIT_USAGE

            print(f"seamcheck: no command named {name!r}\n", file=sys.stderr)
            parser.print_help(sys.stderr)
            return EXIT_USAGE
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
    if name == "config":
        # `seamcheck config --tunnel always` is the sentence a person types; the flag
        # that STORES the answer has to be spelled differently from the one that opens a
        # tunnel for a single run, or `map --tunnel` and `config --tunnel always` would
        # be the same word meaning two things. Translated here so only one of them is
        # ever typed.
        rest = ["--set-tunnel" if item == "--tunnel" else item for item in rest]
    if entry.takes_number:
        # A bare number is the count. Without this, `seamcheck backfill` forwarded a
        # valueless --backfill and argparse answered "expected one argument" - a front
        # door that fails on being opened.
        if rest and rest[0].isdigit():
            arguments += [entry.takes_number, rest.pop(0)]
        elif entry.takes_number not in rest and entry.takes_number not in passthrough:
            arguments += [entry.takes_number, entry.number_default]
    return name, arguments + rest + passthrough


def _run_without_django(arguments, verbose: bool) -> int:
    """Scan a repository that is not a Django project.

    Everything below the adapter is framework-agnostic already - JavaScript, CSS, DOM,
    templates, matching, classification all read the graph and never the backend. The only
    thing Django was providing was the settings module the management command needed, so
    this path goes to the API directly and skips the management layer entirely.

    Flags are read from `_plain_args`, which parses against the SAME table
    (`seamcheck.cliflags.FLAGS`) the Django door's `add_arguments` generates its parser
    from - see that module's docstring. A flag this door cannot honour (a typo, or one of
    the few real flags that genuinely need Django - `--backfill`, `--observe`) is refused
    with EXIT_USAGE rather than silently dropped: silently dropping is how `--since` came
    to read as working on every non-Django project while comparing against nothing.
    """
    from seamcheck import api
    from seamcheck.exitcodes import EXIT_ENVIRONMENT, EXIT_USAGE

    options = _plain_args(arguments)
    if options["missing_value"]:
        # Checked before "unknown": `--explain --foo` reports BOTH ("--explain" is
        # missing_value since "--foo" looks flag-shaped, and "--foo" is separately
        # unknown once it is evaluated in its own right) - argparse itself never gets
        # that far, since it fails fast on the FIRST problem, which is --explain's
        # missing argument. This ordering is the closer match to what it would say.
        # `--search --limit 5` is the motivating case: it used to consume "--limit" as
        # --search's value and silently drop "5" too, an entire flag+value pair
        # vanishing with no error - refusing here, naming the first one, is what stops
        # that.
        print(f"seamcheck: argument {options['missing_value'][0]}: expected one argument",
              file=sys.stderr)
        return EXIT_USAGE
    if options["bad_value"]:
        # `--limit=banana` / `--limit=` - argparse's own "invalid int value", not "no
        # such flag" and not the SPACED form's tolerant "keep the default" (that
        # tolerance is pinned by LimitFlagParityTests for `--limit banana`; the `=` form
        # is new here and matches Django exactly instead, verified against the real
        # parser).
        name, value = options["bad_value"][0]
        print(f"seamcheck: argument {name}: invalid int value: {value!r}", file=sys.stderr)
        return EXIT_USAGE
    if options["unexpected_value"]:
        # `--serve=1` / `--check=` - argparse's own "ignored explicit argument": the
        # flag takes no value at all, verified against the real parser.
        name, value = options["unexpected_value"][0]
        print(f"seamcheck: argument {name}: ignored explicit argument {value!r}",
              file=sys.stderr)
        return EXIT_USAGE
    if options["unknown"]:
        flags = ", ".join(options["unknown"])
        verb = "is" if len(options["unknown"]) == 1 else "are"
        print(
            f"seamcheck: {flags} {verb} not supported on this project type "
            "(no Django settings module found).",
            file=sys.stderr,
        )
        return EXIT_USAGE

    # `--repo-root` used to be ignored outright here - every call below read
    # `pathlib.Path.cwd()` instead, so `seamcheck scan --repo-root ../other` silently
    # scanned the wrong project. Resolved against the current directory (a relative
    # --repo-root is relative to where the command was run) and checked, so a typo'd path
    # fails clearly instead of quietly scanning nothing.
    root = _resolve_repo_root(options["repo_root"])
    if root is None:
        print(f"seamcheck: --repo-root {options['repo_root']!r} does not exist.",
              file=sys.stderr)
        return EXIT_USAGE

    if not _worth_scanning(root):
        print(
            "seamcheck: nothing here that this knows how to read.\n\n"
            "It looks for a backend it recognises - Django, FastAPI, Flask, Express,\n"
            "Fastify, NestJS, Next.js - or a data layer it recognises: Supabase\n"
            "migrations, Firebase functions and rules, or Redis keys. Failing all of\n"
            "those, some JavaScript to read. Run it from the root of a project.",
            file=sys.stderr,
        )
        # The machine is wrong for this tool, not the invocation - EXIT_NO_BASELINE (2)
        # is a CI-gate answer about findings history, and this ran before any scan could
        # even start. EXIT_ENVIRONMENT is the one nothing else was using for exactly this.
        return EXIT_ENVIRONMENT

    if options["set_tunnel"]:
        return _set_tunnel_plain(options["set_tunnel"])
    if options["symbols"]:
        from seamcheck import queries

        print(json.dumps(queries.symbols(root, options["search"], options["kind"],
                                         options["limit"], options["cursor"],
                                         refresh=options["refresh"]), indent=2))
        return 0
    if options["findings"]:
        from seamcheck import queries

        print(json.dumps(queries.findings(root, options["file"], options["kind"],
                                          options["status"] or "", options["owner"],
                                          options["limit"], options["cursor"],
                                          refresh=options["refresh"],
                                          include_triaged=options["include_triaged"]),
                         indent=2))
        return 0
    if options["diff"]:
        from seamcheck import queries

        print(json.dumps(queries.diff(root, options["since"] or "HEAD~1",
                                      options["limit"], options["cursor"],
                                      refresh=options["refresh"]), indent=2))
        return 0
    if options["show_config"]:
        return _show_config_plain(root)
    with quiet(not verbose):
        if options["triage"]:
            # A failed triage (an id the current scan does not have, a status/why word
            # outside the fixed set, an --undo with no mark to remove) is the command
            # being wrong, not "no baseline to compare against" - EXIT_USAGE is the code
            # that means that; a bare literal `2` here collided with EXIT_NO_BASELINE,
            # which is check --since's own, unrelated question.
            result = api.triage(options["triage"], options["status"] or "approved",
                                root, options["reason"], options["why"], undo=options["undo"])
            print(result["message"])
            return 0 if result.get("ok") else EXIT_USAGE
        if options["explain"]:
            # Through the cache, not a fresh scan - this is the exact call the review
            # measured at 88.5s for a mistyped id, and explain is the second step of the
            # documented unverified -> explain -> triage -> check agent loop.
            from seamcheck.scancache import cached_scan

            graph, _how = cached_scan(root)
            print(api.explain_with_hint(graph, options["explain"], root))
            return 0
        if options["check"]:
            # The CI gate. `passed` is the key api.check() actually returns; this asked for
            # `findings`, which it never had, so every non-Django project passed no matter
            # what was in it - measured on redash: 47 unresolved, exit 0.
            from seamcheck.exitcodes import EXIT_USAGE, gate_code
            since = options["since"]
            result = api.check(repo_root=root, since=since)
            if result.get("bad_ref"):
                # The ref itself could not be resolved - a typo, a CI variable that came
                # through empty. Not "no baseline yet" (that needs a real commit with
                # nothing stored for it): the command was wrong, not the machine, so this
                # is EXIT_USAGE, not the gate_code() ladder at all.
                print(result["message"], file=sys.stderr)
                return EXIT_USAGE
            if options["format"] in ("sarif", "github"):
                # A CI gate wants the annotation format it asked for, not the terminal
                # digest - this branch used to ignore --format entirely, so `seamcheck
                # check --format sarif --out FILE` on a non-Django project (redash: 47
                # unresolved) printed the terminal report and never wrote FILE.
                text = api.report(repo_root=root, fmt=options["format"])
                if options["out"]:
                    pathlib.Path(options["out"]).write_text(text, encoding="utf-8")
                    print(f"seamcheck: wrote {options['out']}", file=sys.stderr)
                else:
                    print(text)
            else:
                print(api.report(repo_root=root, fmt="terminal", ref=since or "HEAD"))
            return gate_code(result, comparing=bool(since))
        if options["format"] in ("map", "console"):
            document = api.map_document(repo_root=root)
        else:
            from seamcheck.envelope import TooLarge

            # `--out FILE` writes to disk rather than a terminal, so it is exempt from the
            # size gate `api.report` raises `TooLarge` for - the same escape hatch the
            # Django door's `--out` is, and the one the refusal below points to.
            going_to_disk = bool(options["out"])
            try:
                rendered = api.report(
                    repo_root=root, fmt=options["format"], ref=options["since"] or "HEAD",
                    full=going_to_disk or (options["full"] and options["yes"]),
                )
            except TooLarge as error:
                from seamcheck.exitcodes import EXIT_USAGE

                print(
                    f"  The whole graph is {error.size_bytes / 1e6:.1f} MB "
                    f"(~{error.tokens:,} tokens). Refusing to print it.\n"
                    "  `seamcheck findings` answers most questions in a few KB.\n"
                    "  --full alone still refuses - it takes --full --yes together to "
                    "print it anyway, so an agent needs a second, deliberate keystroke "
                    "to do this. `--out FILE` writes it to disk instead.",
                    file=sys.stderr,
                )
                return EXIT_USAGE
            except ValueError as error:
                # An unrecognised --format value ("Unknown format 'bogus'...", raised by
                # api._report's own choices check). Previously uncaught here - a Python
                # traceback instead of a clean refusal - while the Django door already
                # caught the identical ValueError and exited cleanly. The command was
                # wrong, not the machine: EXIT_USAGE, matching the Django door.
                from seamcheck.exitcodes import EXIT_USAGE

                print(f"seamcheck: {error}", file=sys.stderr)
                return EXIT_USAGE

    if options["format"] in ("map", "console"):
        return _map_plain(document, root, options)
    if options["out"]:
        pathlib.Path(options["out"]).write_text(rendered, encoding="utf-8")
        print(f"seamcheck: wrote {options['out']}", file=sys.stderr)
        return 0
    if options["serve"]:
        return _serve_plain(rendered, root, options)
    print(rendered)
    return 0


def _map_plain(document, root: str, options: dict) -> int:
    """The map: one file, or a folder when it is asked for or the file would be too big.

    Served, it is always the folder form held in memory - the page arrives at once and
    each chunk is one small request when it is looked at - so a map of any size opens as
    fast as a small one.
    """
    from seamcheck import api

    if options["out"]:
        written, note = api.write_map_document(document, options["out"], bundle=options["bundle"] or None)
        print(f"seamcheck: wrote {written}", file=sys.stderr)
        if note:
            print(f"seamcheck: {note}", file=sys.stderr)
        return 0
    if options["serve"]:
        index, assets = document.bundle()
        return _serve_plain(index, root, options, assets=assets)
    print(document.single_file())
    return 0


def _worth_scanning(root: str) -> bool:
    """Whether there is anything here at all.

    A backend adapter is not the only reason to run. A Supabase project has no routes of
    its own - the browser talks to Postgres - and refusing it because no adapter fit was
    the same mistake as refusing an Express repo because it had no manage.py, one layer
    further out.
    """
    from seamcheck.adapters import select
    from seamcheck.extractors.firebase_extractor import detected as firebase_here
    from seamcheck.extractors.supabase_extractor import detected as supabase_here

    if select(root, {})[1] > 0:
        return True
    if supabase_here(root) or firebase_here(root):
        return True
    # Failing everything else: is there any first-party JavaScript? The frontend half of
    # the graph - selectors against elements, rules against markup - stands on its own.
    from seamcheck.adapters.discovery import SKIP_DIRS

    for _here, subdirectories, names in os.walk(root):
        subdirectories[:] = [
            d for d in subdirectories if d not in SKIP_DIRS and not d.startswith(".")
        ]
        if any(name.endswith((".js", ".mjs", ".ts", ".jsx", ".tsx")) for name in names):
            return True
    return False


def _serve_plain(rendered: str, root: str, options: dict, assets=None) -> int:
    """The same serving the Django path does, named the same way."""
    from seamcheck import api
    from seamcheck.serve import public_tunnel, serve_addresses

    server, addresses = serve_addresses(
        rendered,
        host="127.0.0.1" if options["local_only"] else "0.0.0.0",
        sources=set(api.LAST_MAP_FILES), repo_root=root, assets=assets,
    )
    print("")
    print(f"  open   {addresses['local']}")
    if "lan" in addresses:
        print(f"  phone  {addresses['lan']}")
    proxy = None
    # The flag is one rung of a ladder that starts at --local-only and ends at this
    # machine's stored answer, so a person who turned the public link on once gets it
    # here too. The two serving paths must never disagree about that.
    from seamcheck.usersettings import wants_tunnel

    tunnel, why = wants_tunnel(flag=options["tunnel"], local_only=options["local_only"])
    opened = False
    if tunnel:
        try:
            proxy, public = public_tunnel(server.server_port)
        except RuntimeError as error:
            # A tunnel that will not open must not take the local server down with it.
            print(str(error), file=sys.stderr)
            print("  no public link; the addresses above still work.", file=sys.stderr)
        else:
            path = addresses["local"][addresses["local"].index("/", 8):]
            print(f"  public {public}{path}   ({why})")
            opened = True
    print("")
    if opened:
        print("  The public link is readable by ANYONE who has it, from anywhere, while"
              "\n  this runs. `seamcheck config --tunnel never` turns it off for good.")
    elif not options["local_only"] and not tunnel:
        print("  A phone off this wifi cannot reach that address:"
              "\n  `seamcheck config --tunnel always` gives every run a link that can.")
    # The link must reach the terminal now, not when the buffer fills: stdout is block-
    # buffered when it goes to a file, and serve_forever never lets it fill. A run whose
    # output was redirected served for hours with its address unseen.
    print("  Ctrl-C to stop.", flush=True)
    if options["open_it"]:
        import webbrowser

        webbrowser.open(addresses["local"])
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("")
    finally:
        server.shutdown()
        if proxy:
            proxy.terminate()
    return 0


# Argparse's OWN rule for whether a token can be consumed as a preceding option's value
# (`argparse.ArgumentParser._parse_optional`): a negative-number-shaped token IS a value,
# never an option - but only because THIS parser registers no option that itself looks
# like a negative number (`--1`, say). If one ever does, this stops being correct and
# needs the same `_has_negative_number_optionals` check argparse does.
_NEGATIVE_NUMBER_RE = re.compile(r"^-\d+$|^-\d*\.\d+$")


def _looks_like_a_flag(token: str) -> bool:
    """Whether argparse would refuse to consume `token` as a preceding option's value.

    This is `_parse_optional`'s real rule, not "is it a NAME this table recognises" - a
    naive version of this bug fix once shipped exactly that narrower check, and it let
    `seamcheck explain --foo` claim to accept `--foo` as `--explain`'s value while the
    real Django door refuses it with "expected one argument" (verified directly against
    `Command().create_parser(...).parse_args(...)`, not assumed). Argparse treats ANY
    token starting with a prefix character as option-shaped and unavailable as a value,
    REGARDLESS of whether it is a flag anyone registered - with two exceptions: a token
    containing a space (no real flag ever does), and a negative number (see above). A
    bare single "-" is also a value (too short to be an option).
    """
    if not token or not token.startswith("-"):
        return False
    if len(token) == 1:
        return False
    if " " in token:
        return False
    return not _NEGATIVE_NUMBER_RE.match(token)


def _parse_against_table(arguments) -> tuple[dict, list[str], list[str], list[str], list[str]]:
    """Parse argv against `seamcheck.cliflags.FLAGS` - the same table `add_arguments`
    builds the Django door's parser from - instead of a second, hand-written `elif`
    ladder that has to be kept in sync with it by hand.

    Returns `(parsed, unknown, missing_value, bad_value, unexpected_value)`. `parsed` is
    keyed by each flag's `dest` (the same name argparse's `Namespace` would use), holding
    only the flags actually seen - a store_true flag maps to `True`, a value flag to the
    last value it was given (later occurrence wins, same as argparse).

    * `unknown` - every `--flag`-shaped token this call could not resolve to a
      plain-supported entry: either genuinely unrecognised, or recognised in the table but
      marked `plain=False` because honouring it needs Django.
    * `missing_value` - a recognised, plain-supported, value-taking flag (spaced form)
      whose value was absent or itself flag-shaped - argparse's "expected one argument".
    * `bad_value` - `(flag, value)` for `--flag=value` where `value` fails the flag's own
      type conversion (`int`, so far) - argparse's "invalid int value".
    * `unexpected_value` - `(flag, value)` for `--flag=value` given to a boolean
      (store_true) flag, which takes no value at all - argparse's "ignored explicit
      argument".

    None of the five may be silently absorbed - see `_run_without_django`'s refusal for
    each.
    """
    from seamcheck.cliflags import FLAGS

    by_name = {name: flag for flag in FLAGS for name in flag.names}
    parsed: dict = {}
    unknown: list[str] = []
    missing_value: list[str] = []
    bad_value: list[tuple[str, str]] = []
    unexpected_value: list[tuple[str, str]] = []
    items = list(arguments)
    index = 0
    while index < len(items):
        item = items[index]

        # `--flag=value` form. Argparse only ever tries this split for a token that
        # already starts with a prefix character (`_parse_optional`) - `--reason a=b`
        # (the spaced form, "a=b" as reason's VALUE) never reaches this branch at all,
        # because it is consumed as a value below, never re-examined as a flag in its
        # own right. `=` removes the ambiguity `_looks_like_a_flag` exists to resolve -
        # verified against the real parser (`--limit=-5` and `--explain=--foo` are both
        # accepted, literally, where the spaced forms would refuse or need the check
        # above) - so the value after "=" is taken as-is, never flag-shape-checked.
        name, sep, rhs = item.partition("=")
        if sep and name.startswith("-"):
            flag = by_name.get(name)
            if flag is not None and flag.plain:
                if flag.kind == "flag":
                    # `--serve=1`/`--check=` - argparse refuses ANY explicit value on a
                    # store_true flag, verified against the real parser; it does not
                    # matter what the value is, or whether it is empty.
                    unexpected_value.append((name, rhs))
                elif flag.kind == "int":
                    try:
                        parsed[flag.dest] = int(rhs)
                    except ValueError:
                        # `--limit=` (empty) and `--limit=banana` both land here -
                        # verified against the real parser, which refuses both
                        # ("invalid int value: ''" / "invalid int value: 'banana'")
                        # rather than falling back to a default the way the SPACED
                        # form's `--limit banana` deliberately does (see
                        # LimitFlagParityTests) - the `=` form is new here and has no
                        # legacy tolerance to preserve, so it matches Django exactly.
                        bad_value.append((name, rhs))
                else:
                    parsed[flag.dest] = rhs
                    if name == "--wrong":
                        parsed.setdefault("status", "approved")
                index += 1
                continue
            # `name` is unrecognised, or plain=False (needs Django) - reported by NAME
            # (not the whole "--flag=value" token), the same shape "unknown" already
            # uses for the spaced form; this bucket's message was never a byte-for-byte
            # mirror of argparse's own "unrecognized arguments: --flag=value" phrasing
            # in the first place.
            if name.startswith("--"):
                unknown.append(name)
            index += 1
            continue

        flag = by_name.get(item)
        if flag is None:
            if item.startswith("--"):
                unknown.append(item)
            index += 1
            continue
        if not flag.plain:
            unknown.append(item)
            index += 1
            continue
        if flag.kind == "flag":
            parsed[flag.dest] = True
            index += 1
            continue
        following = items[index + 1] if index + 1 < len(items) else None
        if following is None or _looks_like_a_flag(following):
            # `--search --limit 5` used to consume "--limit" as --search's value and then
            # skip past "5" as if IT had been consumed too - an entire real flag+value
            # pair vanished with no error, silently answering a different question than
            # the one typed. Refuse instead, exactly where argparse would: only the
            # flag-shaped/absent token is skipped, so a genuine following flag (here,
            # --limit) is still parsed on the next iteration rather than swallowed.
            missing_value.append(item)
            index += 1
            continue
        if flag.kind == "int":
            # argparse's `type=int` on the Django door accepts a negative value and lets
            # envelope.page() clamp it to 1; a non-numeric value there raises inside
            # argparse and the run never starts. Here a non-numeric value silently keeps
            # the default instead (see LimitFlagParityTests - only the negative-number
            # case is pinned to agree between doors; int() accepts exactly what
            # argparse's type=int does, so it does for --limit too).
            with contextlib.suppress(ValueError):
                parsed[flag.dest] = int(following)
        else:
            parsed[flag.dest] = following
            if item == "--wrong":
                # `--wrong X` is the short way to say "approved because X": the reason a
                # finding was wrong is the whole point of marking it. Only a default -
                # an explicit --status elsewhere on the line still wins, in either order,
                # since this is keyed by dest rather than applied token-by-token.
                parsed.setdefault("status", "approved")
        # The value is consumed HERE, not re-scanned as its own token next iteration - a
        # value that does NOT look flag-shaped (`seamcheck explain url:foo`, or one that
        # happens to contain a space) used to be picked up a second time by `enumerate`'s
        # next step and land in `unknown`, refusing an otherwise-valid command. Two tokens
        # consumed, so the index advances by 2.
        index += 2
    return parsed, unknown, missing_value, bad_value, unexpected_value


def _resolve_repo_root(value: str) -> str | None:
    """`--repo-root`, resolved against the current directory and checked - the flag
    `_run_without_django` used to ignore outright (every call there read
    `pathlib.Path.cwd()` directly), so `seamcheck scan --repo-root ../other` silently
    scanned the wrong project. `None` means the path does not exist, so the caller can
    fail clearly instead of scanning nothing under a typo'd path.
    """
    root_path = (pathlib.Path.cwd() / value).resolve()
    if not root_path.is_dir():
        return None
    return str(root_path)


def _plain_args(arguments) -> dict:
    """The flags this path understands, parsed against `seamcheck.cliflags.FLAGS` - the
    same table the Django door's `add_arguments` builds its parser from (see that
    module's docstring). Keys match each flag's `dest`, so `_plain_args(...)["since"]`
    reads the same name the Django parser's `Namespace.since` does.

    `options["unknown"]` lists every `--flag` this call could not resolve - genuinely
    unrecognised, or a real seamcheck flag this door cannot honour without Django
    (`plain=False` in the table). `options["missing_value"]` lists every RECOGNISED,
    plain-supported, value-taking flag (spaced form) whose value was absent or itself
    flag-shaped - argparse's "expected one argument". `options["bad_value"]` and
    `options["unexpected_value"]` are the `--flag=value` equivalents: a value that fails
    its flag's own type conversion, and a value given to a flag that takes none at all
    (each a list of `(flag, value)` pairs). `_run_without_django` refuses on any of them
    rather than guessing: a flag that works on a Django project and is silently ignored
    (or, worse, silently eats the WRONG token) on an Express one is worse than one that
    does not exist at all - that is exactly how `--since` came to read as working on
    every non-Django project while comparing against nothing, and how `--search --limit
    5` once made `--limit 5` vanish with no error.

    Built FROM `FLAGS`, not as a second hand-written literal: every `plain=True` entry's
    `dest` becomes a key here, defaulted from the table and overlaid with whatever was
    actually parsed. A 32-key hand-written `parsed.get(dest, default)` literal used to sit
    here instead - so a NEW flag added to `FLAGS` with `plain=True` would parse correctly
    and pass `FlagTableParityTests` (it is not "unknown"), and its value would still never
    reach a caller until someone edited this literal too. The same drift this whole module
    exists to close, one call frame lower. `--json`/`--format` and `--serve`/`--no-serve`
    are the only two exceptions: each is two flags folding into one answer (`--json` sets
    `format` only when `--format` was not given explicitly; `--no-serve` always beats
    `--serve`), so they get one small, explicit correction on top - applied once, after
    both inputs are known, rather than per-token, so the answer no longer depends on which
    of the pair came first on the command line the way the old per-token version did.
    """
    from seamcheck.cliflags import FLAGS

    parsed, unknown, missing_value, bad_value, unexpected_value = _parse_against_table(arguments)

    options = {
        flag.dest: (False if flag.kind == "flag" else flag.default)
        for flag in FLAGS
        if flag.plain
    }
    options.update(parsed)

    # --json is --format json under another name (kept for existing callers) - the SAME
    # fold the Django door's handle() applies.
    if options["json"] and options["format"] is None:
        options["format"] = "json"
    if options["format"] is None:
        options["format"] = "terminal"

    # --no-serve always wins over --serve regardless of order, the same
    # `serve and not no_serve` the Django door computes in _format_report/_write_map.
    options["serve"] = bool(options["serve"]) and not bool(options["no_serve"])

    options["unknown"] = unknown
    options["missing_value"] = missing_value
    options["bad_value"] = bad_value
    options["unexpected_value"] = unexpected_value
    return options


def _show_config_plain(root: str) -> int:
    """What the scan will use and why - the same answer the Django path gives."""
    from seamcheck.autoconfig import effective

    config, why = effective(root)
    if not config:
        print("No config, and nothing detected.")
        # Still printed: the phone-link setting belongs to the MACHINE, so it is the one
        # answer this command can always give, including in a directory that is not a
        # project at all.
        _print_tunnel_setting()
        return 0
    width = max(len(key) for key in config)
    print("The config this scan will use:\n")
    for key in sorted(config):
        value = config[key]
        if isinstance(value, list) and len(value) > 3:
            value = f"[{len(value)} items] {value[:3]} ..."
        print(f"  {key:<{width}}  {value}")
        print(f"  {'':<{width}}  \u2514\u2500 {why.get(key, 'default')}")
    _print_tunnel_setting()
    return 0


def _print_tunnel_setting() -> None:
    """The one setting that belongs to the machine rather than to this project.

    Shown beside the detected paths because a person looking for "why is there no public
    link" looks here, and because a setting that is invisible is one nobody can undo.
    """
    from seamcheck.usersettings import wants_tunnel

    wanted, why = wants_tunnel()
    print(f"\n  public link on this machine: {'yes' if wanted else 'no'}")
    print(f"  \u2514\u2500 {why}")
    print("  seamcheck config --tunnel always|never   changes it")


def _set_tunnel_plain(when: str) -> int:
    """Store this machine's answer, the same way the Django path stores it."""
    from seamcheck.exitcodes import EXIT_USAGE
    from seamcheck.usersettings import ALWAYS, NEVER, settings_path, write

    if when not in (ALWAYS, NEVER):
        print(f"seamcheck: --set-tunnel takes {ALWAYS} or {NEVER}, not {when!r}.",
              file=sys.stderr)
        return EXIT_USAGE
    write("tunnel", when, None)
    print(f"  tunnel {when}   written to {settings_path()}")
    if when == ALWAYS:
        print("  Every `seamcheck map` on this machine now also prints a public HTTPS"
              "\n  link, readable by anyone who has it while the command runs."
              "\n  --local-only still overrules it, run by run.")
    return 0


def _setup_django_if_any() -> None:
    """Bootstrap Django when this is a Django project, and shrug when it is not.

    The same thing the main path does, factored out so a command handled before that path
    still scans the project the same way it would.
    """
    found = find_project(pathlib.Path.cwd())
    if not found:
        return
    settings_module, root = found
    sys.path.insert(0, str(root))
    os.chdir(root)
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", settings_module)
    try:
        import django

        django.setup()
    except Exception:  # noqa: BLE001 - a project that will not import is read from source
        pass


def _share(rest: list[str]) -> int:
    """Build the shareable report, write it, and print where to send it.

    No network call is made here or anywhere in this package. The report is printed and
    written to a file; the person decides what to do with it.
    """
    from seamcheck import share

    want_json = "--json" in rest
    quiet_mode = "--quiet" in rest or "-q" in rest
    with_deps = "--with-deps" in rest
    with quiet():
        markdown, payload = share.report(".", with_deps=with_deps)

    if want_json:
        print(share.as_json(payload))
        return 0

    print(markdown)
    target = pathlib.Path("seamcheck-share.md")
    try:
        target.write_text(markdown, encoding="utf-8")
        written = f"  written to  {target}"
    except OSError:
        written = "  (could not write seamcheck-share.md - copy the text above instead)"

    if not quiet_mode:
        print("\n" + "-" * 72)
        print("This report contains no file paths, names, routes, snippets or repository")
        print("identity. Nothing has been sent anywhere - seamcheck makes no network calls.")
        print(written)
        print("\n  Open a pre-filled issue (nothing is submitted until you press the button):")
        print("  " + share.issue_url(payload))
        print("\n  Or paste the report above into an email, a chat, or an issue by hand.")
        print("\n  Only share a repository you are allowed to share metrics about - if it")
        print("  belongs to an employer or a client, that is their decision, not yours.")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    argv, passthrough = _split_passthrough(argv)
    argv, verbose, no_progress = _split_flags(argv)
    parser = build_parser()

    if argv and argv[0] in ("-V", "--version", "version"):
        print(version_line())
        return 0
    if not argv or argv[0] in ("-h", "--help"):
        parser.print_help()
        return 0
    if argv[0] == "help":
        from seamcheck.exitcodes import EXIT_USAGE

        name = argv[1] if len(argv) > 1 else ""
        if name in COMMANDS:
            print(command_help(name))
            return 0
        if name:
            print(f"seamcheck: no command named {name!r}\n", file=sys.stderr)
        parser.print_help()
        return 0 if not name else EXIT_USAGE

    if argv[0] == "share":
        # Handled here rather than through the management command: it needs no server and
        # no arguments, and routing it through the generic path would make a report about
        # a repository depend on that repository being a Django project.
        #
        # It DOES set Django up first where there is a Django project, which it did not.
        # Sitting above the bootstrap meant `share` scanned by reading source while
        # `check` in the same shell scanned by importing - so the one command whose whole
        # purpose is to be trustworthy reported 46,309 symbols where every other command
        # reported 47,834, and said `ModuleNotFoundError` on the way past.
        _setup_django_if_any()
        return _share(argv[1:])

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
        # NOT an error. Five of the six backend adapters have nothing to do with Django,
        # and this gate is why none of them were reachable: `seamcheck check` on a perfectly
        # good Express repository answered "no Django project here" and stopped. There is no
        # settings module to set up, so the Django bootstrap below is skipped and the scan
        # runs straight against the repository.
        return _run_without_django(arguments, verbose)
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", settings_module)
    if no_progress:
        # Read back by the management command, which owns the bar. An environment
        # variable rather than a flag because the flag would have to be understood by
        # both parsers, and only one of them should own the vocabulary.
        os.environ["SEAMCHECK_NO_PROGRESS"] = "1"

    # The whole run, not just setup(): a project logs on import, and again the first time
    # the scan touches its app registry.
    with quiet(not verbose):
        try:
            # Django itself is the first thing that can be missing: a global install
            # pointed at a Django project has no Django at all, and used to answer with
            # a bare traceback from this line while the explanation sat five lines below.
            import django
            from django.core.management import call_command
            from django.core.management.base import CommandError

            django.setup()
        except ModuleNotFoundError as error:
            # The project is here and importable-looking, but one of ITS dependencies is
            # not installed - which is what happens when seamcheck was installed globally
            # (pipx, uv tool, a system pip) instead of into the project's own environment.
            # Seamcheck reads a Django project by importing it, so it has to live where
            # the project's imports resolve. A raw traceback here reads as seamcheck being
            # broken; it is an install-location problem with a one-line fix.
            print(
                f"seamcheck: this project imports {error.name!r}, and that is not "
                "installed here.\n\n"
                "Seamcheck reads a Django project by importing it, so it has to run "
                "inside\nthe project's own environment - not from a global install.\n\n"
                "    source .venv/bin/activate      # your project's virtualenv\n"
                "    pip install seamcheck\n"
                "    seamcheck map\n\n"
                "If you installed with pipx or `uv tool`, that copy is isolated from your\n"
                "project on purpose and cannot see its dependencies.",
                file=sys.stderr,
            )
            # A missing dependency is the machine being wrong, not a CI gate finding no
            # baseline - EXIT_ENVIRONMENT is the constant that exists for exactly this and
            # nothing was returning it.
            from seamcheck.exitcodes import EXIT_ENVIRONMENT

            return EXIT_ENVIRONMENT
        try:
            call_command("seamcheck", *arguments)
        except CommandError as error:
            # Everything that reaches here as a CommandError - argparse's own validation
            # (an unrecognised flag, a bad --set-tunnel choice), or an explicit
            # `raise CommandError(...)` in handle() (--out without --format, an empty
            # --format, --serve on a non-renderable format, --observe finding no page
            # URLs) - is the command being wrong. The one exception, --observe's
            # BrowserUnavailable (the machine has no Playwright browser, not a usage
            # error), no longer raises CommandError at all - see its own SystemExit(
            # EXIT_ENVIRONMENT) in _observe - so it never reaches this branch.
            from seamcheck.exitcodes import EXIT_USAGE

            print(f"seamcheck: {error}", file=sys.stderr)
            return EXIT_USAGE
        except SystemExit as exit_code:  # --check and friends signal through the exit code
            return int(exit_code.code or 0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
