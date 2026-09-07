"""One declarative table of every flag `manage.py seamcheck` accepts - the single source
of truth both front doors read, instead of drifting copies of each other.

Seamcheck has two front doors. `manage.py seamcheck` parses flags with argparse, in
`seamcheck/management/commands/seamcheck.py`'s `add_arguments`. The plain `seamcheck` CLI
- used on every non-Django project - parses the same flags by hand, in
`seamcheck/cli.py`'s `_plain_args`. Historically the two were declared TWICE: once as
`parser.add_argument(...)` calls, once as an `elif item == "--whatever"` ladder. Every time
a flag was added to one and not the other, the missing door did not error - it silently
answered a different question. `--since` did this: `check --since $BASE` on an Express repo
read as working and compared against nothing, with no warning at all. `--limit` and
`--format` did it before that, and fixing each occurrence by hand-widening the second
parser is exactly how the drift kept recurring - the fix looked identical to the bug it
was fixing.

This module is the actual fix: ONE table (`FLAGS`), read by both.

* `seamcheck.management.commands.seamcheck.Command.add_arguments` calls
  `add_django_arguments(parser)`, which turns each `Flag` into the `parser.add_argument()`
  call argparse needs. Nothing about a flag's name, default, or shape is declared a second
  time in that file.
* `seamcheck.cli._plain_args` parses against this same table (see `_parse_against_table`
  in `cli.py`), so a flag this table declares is either genuinely understood on the plain
  door, or - when it is not, because honouring it needs Django (`--backfill` needs
  `django.setup()` to read `SEAMCHECK_CONFIG` and re-scans each commit with `django.setup()`
  too; `--observe`'s auto-discovery reads the URLconf) - `Flag.plain=False` makes the plain
  door refuse it BY NAME rather than silently drop it. A refusal is honest about the gap;
  a silent drop is the bug this table exists to close.

`seamcheck/tests/test_cli_entrypoint.py::FlagTableParityTests` is what keeps this table
complete: it compares `FLAGS` against argparse's OWN introspection of the parser
`add_django_arguments` builds (via a vanilla `BaseCommand`'s parser, subtracted out, so
Django's own `--settings`/`--verbosity`/etc. never enter the comparison) - not against a
second hand-typed list of names, which would just be an eighth copy of the same drift.
"""

from __future__ import annotations

from dataclasses import dataclass

# The complete, canonical set of values `--format` accepts. This used to be expressed
# three ways that could each say something different: `api.py`'s validity check
# (`renderers.keys()` unioned with a second, hand-typed tuple), its refusal message (built
# from `renderers` ALONE, so a typo was told only `html`, `markdown`, `terminal` existed -
# five of the eight real formats never appeared in their own error message), and this
# file's `--format` help text (a third, hand-typed copy). One tuple, read by all three:
# `api._report`'s validity check and its ValueError, and the help text below.
FORMATS: tuple[str, ...] = (
    "terminal", "markdown", "html", "json", "map", "console", "sarif", "github",
)


@dataclass(frozen=True)
class Flag:
    """One flag, in full: every argparse detail the Django door needs, and whether the
    plain (non-Django) door can honour it at all.

    `names`: every spelling argparse should register (`("--why", "--wrong")` share one
    destination). `dest`: the attribute name - on `Namespace` for the Django door, and the
    key `_plain_args` returns it under, so the two doors read the same value by the same
    name. `kind`: `"flag"` (`action="store_true"`), `"str"` (a plain value), or `"int"`
    (`type=int` on the Django side; the plain side parses tolerantly - see `--limit`'s
    note in `cli.py`, which is intentional and pinned by `LimitFlagParityTests`).
    `plain=False` means the plain door recognises the NAME but cannot carry out what it
    asks, and must refuse rather than accept-and-ignore.
    """

    names: tuple[str, ...]
    dest: str
    kind: str = "str"  # "flag" | "str" | "int"
    default: object = None
    choices: tuple[str, ...] | None = None
    metavar: str | None = None
    nargs: str | None = None
    help: str = ""
    plain: bool = True


FLAGS: tuple[Flag, ...] = (
    Flag(("--json",), "json", "flag", help="Print the graph as JSON."),
    Flag(("--check",), "check", "flag", help="Diff against HEAD; exit 1 on findings."),
    Flag(("--since",), "since", metavar="REF", help="Diff against the snapshot for REF."),
    Flag(("--explain",), "explain", metavar="SYMBOL_ID", help="Explain one symbol."),
    Flag(("--triage",), "triage", metavar="SYMBOL_ID", help="Record a disposition."),
    Flag(("--status",), "status",
         help="Triage status: approved, confirmed, deferred, untriaged."),
    Flag(("--reason",), "reason", default="", help="Why this disposition, in your own words."),
    Flag(
        ("--why", "--wrong"), "why", default="",
        help="Why it was wrong, as a fixed word - the only part `seamcheck share` can "
             "pass on. See `help triage`.",
    ),
    Flag(("--undo",), "undo", "flag",
         help="Take the mark off --triage's symbol; it is raised again."),
    Flag(("--repo-root",), "repo_root", default=".",
         help="Repo to read snapshots/triage from."),
    Flag(
        ("--backfill",), "backfill", "int", metavar="N",
        help="Scan the last N commits into snapshots, so the map's commit picker has "
             "history to show. Each commit is scanned in its own temporary worktree; "
             "roughly 30s per commit.",
        # Needs `django.setup()` twice over: once to read SEAMCHECK_CONFIG here, and once
        # per commit inside the worktree it scans (seamcheck/history.py's `_DRIVER`
        # hardcodes `django.setup()`). There is no plain-project equivalent to fall back
        # to, so the plain door refuses this by name instead of running a Django-shaped
        # scan against a project that has no Django settings to give it.
        plain=False,
    ),
    Flag(
        ("--backfill-ref",), "backfill_ref", default="HEAD", metavar="REF",
        help="Which branch --backfill walks. Defaults to HEAD.",
        plain=False,  # only meaningful alongside --backfill, above.
    ),
    Flag(
        ("--tunnel",), "tunnel", "flag",
        help="With --serve, also open a temporary public HTTPS link via cloudflared, "
             "for a device that is not on this network. Anyone with the link can read "
             "the report; it dies with the command.",
    ),
    Flag(
        ("--set-tunnel",), "set_tunnel", choices=("always", "never"), metavar="WHEN",
        help="Remember, for this machine and every project on it, whether `map` and "
             "`serve` open the public link: `always` or `never`. Written to "
             "~/.config/seamcheck/settings.json. `seamcheck config` shows it.",
    ),
    Flag(
        ("--serve",), "serve", "flag",
        help="Serve the report from this machine so a browser (and a phone on the "
             "same network) can open it. Nothing is uploaded; the server stops when "
             "you do.",
    ),
    Flag(
        ("--no-serve",), "no_serve", "flag",
        help="With --format map: write the file and stop, instead of serving it. "
             "For CI and scripts, which want the artifact and not a running server.",
    ),
    Flag(
        ("--local-only",), "local_only", "flag",
        help="With --serve: bind loopback only, so nothing on the network can reach "
             "it. You lose the phone link.",
    ),
    Flag(
        ("--format",), "format",
        # No choices=: Django's CommandParser.error() raises CommandError (not
        # SystemExit) for call_command() invocations, so argparse-level validation
        # can't produce the SystemExit callers of an invalid --format expect.
        # _format_report() validates instead, via api.report()'s ValueError.
        help=f"Output format: {', '.join(FORMATS)}. json emits the whole graph, as "
             "--json does.",
    ),
    Flag(("--out",), "out", help="Write to PATH instead of stdout ('-' for stdout)."),
    Flag(
        ("--bundle",), "bundle", "flag",
        help="Write the map as a folder (small index.html + data/ loaded as needed) "
             "rather than one file. Automatic above 50 MB.",
    ),
    Flag(
        ("--open",), "open_it", "flag",
        help="Open the written file in your browser when it is done.",
    ),
    Flag(
        ("--observe",), "observe", nargs="*", metavar="URL",
        help="Drive the running app in a browser and record what it actually queried "
             "and fetched. With no URLs, visits the pages the graph knows about at "
             "--base-url.",
        # `_page_urls()` (auto-discovery with no explicit URLs) reads `url` symbols off
        # the scanned graph, and the browser probe itself needs a running app - neither
        # is something this door can promise on a project type it has not modelled the
        # routes of. Explicit URLs would be a reasonable partial feature, but that is a
        # new capability, not a parity fix; refusing is the honest answer for now.
        plain=False,
    ),
    Flag(
        ("--base-url",), "base_url", default="http://127.0.0.1:8080",
        help="Where the application is running, for --observe.",
        plain=False,  # only meaningful alongside --observe, above.
    ),
    Flag(
        ("--shots",), "shots", metavar="DIR",
        help="With --observe, also screenshot each page into DIR.",
        plain=False,  # only meaningful alongside --observe, above.
    ),
    Flag(
        ("--show-config",), "show_config", "flag",
        help="Print the config a scan would use, and where each value came from.",
    ),
    Flag(
        ("--symbols",), "symbols", "flag",
        help="Find symbols by name; --search and --kind narrow it.",
    ),
    Flag(("--search",), "search", default="", help="Substring to look for."),
    Flag(("--kind",), "kind", default="", help="Restrict to one kind."),
    Flag(("--limit",), "limit", "int", default=25, help="How many rows at most."),
    Flag(("--cursor",), "cursor", default="", help="Continue a previous page."),
    Flag(
        ("--findings",), "findings", "flag",
        help="List findings; --file, --kind, --status and --owner narrow it.",
    ),
    Flag(("--file",), "file", default="", help="Only findings in this file."),
    Flag(("--owner",), "owner", default="", help="Only findings owned by this function."),
    Flag(
        ("--include-triaged",), "include_triaged", "flag",
        help="With --findings: also list findings carrying a triage mark (approved, "
             "confirmed or deferred). Left out by default - 'what is wrong' means the "
             "same thing here as in --check and --format sarif/github.",
    ),
    Flag(
        ("--diff",), "diff", "flag",
        help="What appeared, vanished or changed status since --since (default "
             "HEAD~1). Unfiltered by triage - the raw graph difference, not check's "
             "pass/fail opinion.",
    ),
    Flag(
        ("--refresh",), "refresh", "flag",
        help="Skip the scan cache in both directions, for --symbols/--findings/--diff - "
             "the escape for a tree the cache cannot judge on its own (a fresh checkout, "
             "a restored backup, a clock that just got corrected).",
    ),
    Flag(
        ("--no-progress",), "no_progress", "flag",
        help="Never draw the progress bar (it is off already when output is redirected).",
        # The plain door never draws a progress bar at all today (unlike the Django
        # door's `self._progress()`), so this flag has nothing to suppress there yet -
        # but that makes it a no-op, not a wrong answer, so it is safe to accept rather
        # than refuse (unlike --backfill/--observe above, whose absence changes what the
        # command actually does).
    ),
    Flag(
        ("--full",), "full", "flag",
        help="With --json, print the whole graph even past the size warning. Needs "
             "--yes too - one flag alone still refuses.",
    ),
    Flag(
        ("--yes",), "yes", "flag",
        help="Confirms --full. Two separate flags, not one, so printing 18 million "
             "tokens to an agent's context takes a deliberate second keystroke.",
    ),
)


def add_django_arguments(parser) -> None:
    """`Command.add_arguments`, generated from `FLAGS` instead of by-hand `add_argument`
    calls. Every flag's shape lives in `FLAGS` and nowhere else - this only turns each
    entry into the call argparse needs.
    """
    for flag in FLAGS:
        kwargs: dict = {"dest": flag.dest, "help": flag.help}
        if flag.kind == "flag":
            kwargs["action"] = "store_true"
        else:
            kwargs["default"] = flag.default
            if flag.kind == "int":
                kwargs["type"] = int
        if flag.metavar:
            kwargs["metavar"] = flag.metavar
        if flag.choices:
            kwargs["choices"] = list(flag.choices)
        if flag.nargs:
            kwargs["nargs"] = flag.nargs
        parser.add_argument(*flag.names, **kwargs)
