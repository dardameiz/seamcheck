"""CLI surface. All logic lives in signal_map.api, which the MCP server also calls."""

from __future__ import annotations

import json
import pathlib

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from signal_map import api


class Command(BaseCommand):
    help = "Scan the project's connectivity graph and report on it."

    def add_arguments(self, parser):
        parser.add_argument("--json", action="store_true", help="Print the graph as JSON.")
        parser.add_argument("--check", action="store_true", help="Diff against HEAD; exit 1 on findings.")
        parser.add_argument("--since", metavar="REF", help="Diff against the snapshot for REF.")
        parser.add_argument("--explain", metavar="SYMBOL_ID", help="Explain one symbol.")
        parser.add_argument("--triage", metavar="SYMBOL_ID", help="Record a disposition.")
        parser.add_argument("--status", help="Triage status: approved, confirmed, deferred, untriaged.")
        parser.add_argument("--reason", default="", help="Why this disposition.")
        parser.add_argument("--repo-root", default=".", help="Repo to read snapshots/triage from.")
        parser.add_argument(
            "--format", default=None,
            # No choices=: Django's CommandParser.error() raises CommandError (not
            # SystemExit) for call_command() invocations, so argparse-level validation
            # can't produce the SystemExit callers of an invalid --format expect.
            # _format_report() validates instead, via api.report()'s ValueError.
            help="Output format: terminal, markdown, html, json. json emits the whole graph, as --json does.",
        )
        parser.add_argument("--out", default=None, help="Write to PATH instead of stdout ('-' for stdout).")

    def handle(self, *args, **options):
        # --json is "--format json" under another name (kept for existing callers); make
        # it walk the exact same path instead of a second, drifting copy of the dump.
        if options["json"] and options["format"] is None:
            options["format"] = "json"
        if options["out"] and not options["format"]:
            # --out is only ever read inside _format_report(), a few lines below. Any
            # other path (bare --check, --since, the no-flags summary) silently ignores
            # it - "wrote the report" that never happened is worse than an error.
            raise CommandError("--out only applies together with --format (or --json).")
        if options["triage"]:
            return self._triage(options)
        if options["explain"]:
            return self.stdout.write(api.explain(api.scan(options["repo_root"]), options["explain"]))
        if options["format"] is not None:
            if not options["format"]:
                # `--format ""` is falsy, so a bare `if options["format"]:` falls through
                # to _summary(), which writes the whole (18 MB on a real project) map -
                # silently, with no hint the flag was even seen. Empty is not "unset".
                raise CommandError("--format was given an empty value; use terminal, markdown, html, or json.")
            # Scan once and share it: without this, --check --format pays for a full
            # scan twice (once for the digest, once for the exit code) to do the same
            # work. When --check isn't set, graph stays None and _format_report scans
            # for itself, unchanged from the --format-alone path.
            graph = api.scan(options["repo_root"]) if options["check"] else None
            self._format_report(options, graph)
            # --check composes with --format: the CI use case is "post this digest as a
            # comment, fail the build" - so the digest must land before the exit, or a
            # failing build ships with nothing to read.
            if options["check"]:
                self._exit_on_check(options["repo_root"], graph)
            return
        if options["check"] or options["since"]:
            return self._check(options)
        return self._summary(options)

    def _triage(self, options):
        if not options["status"]:
            self.stderr.write("--triage requires --status.")
            raise SystemExit(2)
        result = api.triage(
            options["triage"], options["status"], options["repo_root"], options["reason"]
        )
        self.stdout.write(result["message"])
        if not result["ok"]:
            raise SystemExit(2)

    def _check(self, options):
        repo_root = options["repo_root"]
        graph = api.scan(repo_root)
        if options["since"]:
            result, _, message = api.diff_against(graph, options["since"], repo_root)
            if message:
                self.stdout.write(message)
                return
            self._report(result)
            return

        outcome = api.check(repo_root)
        if outcome["message"]:
            self.stdout.write(outcome["message"])
        for key in ("new_unresolved", "new_unused"):
            for item in outcome[key]:
                self.stdout.write(f"{key}: {item['id']}")
        for item in outcome["triage_invalidated"]:
            self.stdout.write(f"triage invalidated: {item['symbol_id']} - {item['note']}")
        self.stdout.write(f"counts: {outcome['counts']}")
        if not outcome["passed"]:
            raise SystemExit(1)

    def _report(self, result):
        for symbol in result.new_unresolved:
            self.stdout.write(f"new_unresolved: {symbol.id}")
        for symbol in result.new_unused:
            self.stdout.write(f"new_unused: {symbol.id}")
        for item in result.triage_invalidated:
            self.stdout.write(f"triage invalidated: {item['symbol_id']} - {item['note']}")

    def _format_report(self, options, graph=None):
        fmt = options["format"]
        repo_root = options["repo_root"]

        if fmt == "json":
            from signal_map.graph import graph_to_dict

            text = json.dumps(graph_to_dict(graph if graph is not None else api.scan(repo_root)), indent=2)
        else:
            try:
                text = api.report(repo_root, fmt, ref=options["since"] or "HEAD", graph=graph)
            except ValueError as error:
                self.stderr.write(str(error))
                raise SystemExit(2) from error

        destination = options["out"]
        if destination == "-":
            # Explicit stdout always wins, even for html: a flag whose help text
            # promises the terminal must not silently redirect to a file.
            return self.stdout.write(text)
        if destination is None:
            if fmt != "html":
                return self.stdout.write(text)
            # An HTML report with no destination goes to the configured path, because
            # dumping a whole document into a terminal helps nobody. Read config from
            # settings, not api._config(): a command reaching into another module's
            # private helper is how a refactor there silently breaks this one.
            config = getattr(settings, "SIGNAL_MAP_CONFIG", {})
            configured = config.get("report_output")
            if not configured:
                # Falling through to stdout here would be the exact ~1MB-dump-to-a-
                # terminal outcome this branch exists to prevent - fail cleanly instead.
                raise CommandError(
                    "--format html has no destination: pass --out PATH, or --out - to "
                    "print the document to stdout."
                )
            destination = configured

        path = pathlib.Path(repo_root) / destination
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        self.stdout.write(f"wrote {path}")

    def _exit_on_check(self, repo_root, graph=None):
        if not api.check(repo_root, graph=graph)["passed"]:
            raise SystemExit(1)

    def _summary(self, options):
        graph = api.scan(options["repo_root"])
        path = api.write_map(graph, options["repo_root"])
        counts = {}
        for symbol in graph.symbols:
            counts[symbol.status.value] = counts.get(symbol.status.value, 0) + 1
        self.stdout.write(f"{len(graph.symbols)} symbols, {len(graph.edges)} edges -> {path}")
        for status, count in sorted(counts.items()):
            self.stdout.write(f"  {status:<12} {count}")
