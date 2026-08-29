"""CLI surface. All logic lives in signal_map.api, which the MCP server also calls."""

from __future__ import annotations

import json

from django.core.management.base import BaseCommand

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

    def handle(self, *args, **options):
        if options["triage"]:
            return self._triage(options)
        if options["explain"]:
            return self.stdout.write(api.explain(api.scan(options["repo_root"]), options["explain"]))
        if options["json"]:
            from signal_map.graph import graph_to_dict

            return self.stdout.write(json.dumps(graph_to_dict(api.scan(options["repo_root"])), indent=2))
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
            result, message = api.diff_against(graph, options["since"], repo_root)
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

    def _summary(self, options):
        graph = api.scan(options["repo_root"])
        path = api.write_map(graph, options["repo_root"])
        counts = {}
        for symbol in graph.symbols:
            counts[symbol.status.value] = counts.get(symbol.status.value, 0) + 1
        self.stdout.write(f"{len(graph.symbols)} symbols, {len(graph.edges)} edges -> {path}")
        for status, count in sorted(counts.items()):
            self.stdout.write(f"  {status:<12} {count}")
