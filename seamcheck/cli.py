"""The `seamcheck` command.

A Django management command needs a `manage.py`, a settings module on the environment and
the right virtualenv active. That is fine inside a project you already know; it is a poor
first thirty seconds for someone who has just run `pip install seamcheck`.

This finds the project itself - the manage.py beside you, or the settings module the
environment already names - then hands off to the same management command. It is a front
door, not a second implementation: every flag is parsed and executed in exactly one place.
"""

from __future__ import annotations

import argparse
import os
import pathlib
import re
import sys

# name -> (arguments passed through to the management command, one-line description)
COMMANDS: dict[str, tuple[list[str], str]] = {
    "scan":     ([], "Scan and print a summary. Writes a snapshot for later diffs."),
    "check":    (["--check"], "The CI gate. Exit 1 on new findings, 2 if no baseline, 0 clean."),
    "report":   (["--format", "markdown"], "A digest for a chat or a pull request."),
    "map":      (["--format", "map"], "The UI: one self-contained HTML file."),
    "serve":    (["--format", "map", "--serve"], "Open the UI from another device on this network."),
    "json":     (["--json"], "The whole graph, as JSON."),
    "explain":  (["--explain"], "Everything known about one symbol, with its source."),
    "triage":   (["--triage"], "Record a disposition for a finding."),
    "backfill": (["--backfill"], "Scan the last N commits so the map's commit picker has history."),
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


def build_parser() -> argparse.ArgumentParser:
    width = max(len(name) for name in COMMANDS)
    listing = "\n".join(f"  {name:<{width}}  {help_text}" for name, (_, help_text) in COMMANDS.items())
    parser = argparse.ArgumentParser(
        prog="seamcheck",
        description="Find the code your project no longer connects to - and the "
                    "connections it only thinks it has.",
        epilog=f"commands:\n{listing}\n\n"
               "Any flag the management command accepts also works here, e.g.\n"
               "  seamcheck map --since main --out map.html\n"
               "  seamcheck serve --tunnel\n"
               "  seamcheck check --since $BASE_SHA\n",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        add_help=False,
    )
    parser.add_argument("command", nargs="?", default="scan", help=argparse.SUPPRESS)
    parser.add_argument("rest", nargs=argparse.REMAINDER, help=argparse.SUPPRESS)
    return parser


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()

    if not argv or argv[0] in ("help", "-h", "--help"):
        parser.print_help()
        return 0

    known = parser.parse_args(argv)
    if known.command not in COMMANDS:
        # An unknown word is far more likely a typo than a flag for the default command,
        # and guessing wrong here runs a scan the user did not ask for.
        if not known.command.startswith("-"):
            print(f"seamcheck: no command named {known.command!r}\n", file=sys.stderr)
            parser.print_help(sys.stderr)
            return 2
        known.rest = [known.command, *known.rest]
        known.command = "scan"

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

    import django
    from django.core.management import call_command
    from django.core.management.base import CommandError

    django.setup()
    arguments, _ = COMMANDS[known.command]
    try:
        call_command("seamcheck", *arguments, *known.rest)
    except CommandError as error:
        print(f"seamcheck: {error}", file=sys.stderr)
        return 2
    except SystemExit as exit_code:  # --check and friends signal through the exit code
        return int(exit_code.code or 0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
