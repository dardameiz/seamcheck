"""CLI surface. All logic lives in seamcheck.api, which the MCP server also calls."""

from __future__ import annotations

import json
import os
import pathlib
import sys

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from seamcheck import api
from seamcheck.progress import Progress

# Formats whose output is a whole document rather than a few lines. Printing one to a
# terminal is a wall of markup and a lost scrollback, so each has a default destination
# on disk and says where it went.
_DOCUMENTS = {
    "html": ("report_output", "docs/maps/connectivity-report.html"),
    "map": ("map_output", "docs/maps/connectivity-map.html"),
    "console": ("map_output", "docs/maps/connectivity-map.html"),
}


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
            "--backfill", type=int, metavar="N", default=None,
            help="Scan the last N commits into snapshots, so the map's commit picker has "
                 "history to show. Each commit is scanned in its own temporary worktree; "
                 "roughly 30s per commit.",
        )
        parser.add_argument(
            "--backfill-ref", default="HEAD", metavar="REF",
            help="Which branch --backfill walks. Defaults to HEAD.",
        )
        parser.add_argument(
            "--tunnel", action="store_true",
            help="With --serve, also open a temporary public HTTPS link via cloudflared, "
                 "for a device that is not on this network. Anyone with the link can read "
                 "the report; it dies with the command.",
        )
        parser.add_argument(
            "--serve", action="store_true",
            help="Serve the report from this machine so a browser (and a phone on the "
                 "same network) can open it. Nothing is uploaded; the server stops when "
                 "you do.",
        )
        parser.add_argument(
            "--no-serve", action="store_true",
            help="With --format map: write the file and stop, instead of serving it. "
                 "For CI and scripts, which want the artifact and not a running server.",
        )
        parser.add_argument(
            "--local-only", action="store_true",
            help="With --serve: bind loopback only, so nothing on the network can reach "
                 "it. You lose the phone link.",
        )
        parser.add_argument(
            "--format", default=None,
            # No choices=: Django's CommandParser.error() raises CommandError (not
            # SystemExit) for call_command() invocations, so argparse-level validation
            # can't produce the SystemExit callers of an invalid --format expect.
            # _format_report() validates instead, via api.report()'s ValueError.
            help="Output format: terminal, markdown, html, json, map, console. "
                 "json emits the whole graph, as --json does.",
        )
        parser.add_argument("--out", default=None, help="Write to PATH instead of stdout ('-' for stdout).")
        parser.add_argument(
            "--open", action="store_true", dest="open_it",
            help="Open the written file in your browser when it is done.",
        )
        parser.add_argument(
            "--observe", nargs="*", metavar="URL",
            help="Drive the running app in a browser and record what it actually queried "
                 "and fetched. With no URLs, visits the pages the graph knows about at "
                 "--base-url.",
        )
        parser.add_argument(
            "--base-url", default="http://127.0.0.1:8080",
            help="Where the application is running, for --observe.",
        )
        parser.add_argument(
            "--shots", default=None, metavar="DIR",
            help="With --observe, also screenshot each page into DIR.",
        )
        parser.add_argument(
            "--show-config", action="store_true",
            help="Print the config a scan would use, and where each value came from.",
        )
        parser.add_argument(
            "--no-progress", action="store_true",
            help="Never draw the progress bar (it is off already when output is redirected).",
        )

    def _progress(self, options, total: int) -> Progress:
        """One bar for the whole run, or a silent one.

        Written to stderr so `seamcheck json > graph.json` still yields clean JSON, and
        drawn only on a terminal - a progress bar in a CI log is 400 carriage returns.
        """
        off = options.get("no_progress") or os.environ.get("SEAMCHECK_NO_PROGRESS")
        return Progress(total, stream=sys.stderr, enabled=False if off else None)

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
        if options["observe"] is not None:
            return self._observe(options)
        if options["show_config"]:
            return self._show_config(options["repo_root"])
        if options["triage"]:
            return self._triage(options)
        if options["explain"]:
            bar = self._progress(options, api.SCAN_STEPS)
            graph = api.scan(options["repo_root"], bar)
            bar.finish()
            return self.stdout.write(api.explain(graph, options["explain"]))

        if options["backfill"] is not None:
            return self._backfill(
                options["repo_root"], options["backfill"], options["backfill_ref"]
            )
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
            bar = self._progress(
                options, api.MAP_STEPS if options["format"] in ("map", "console") else api.SCAN_STEPS
            )
            graph = api.scan(options["repo_root"], bar) if options["check"] else None
            self._format_report(options, graph, bar)
            # --check composes with --format: the CI use case is "post this digest as a
            # comment, fail the build" - so the digest must land before the exit, or a
            # failing build ships with nothing to read.
            if options["check"]:
                self._exit_on_check(options["repo_root"], graph)
            return
        if options["check"] or options["since"]:
            return self._check(options)
        return self._summary(options)

    def _observe(self, options):
        """Record what a browser actually did, and keep it beside the scan.

        Separate from the scan on purpose. Everything else here reads files and works on a
        machine that will never open a browser; this needs the application to RUN, so it is
        enrichment and never a prerequisite.
        """
        from seamcheck.browser import BrowserUnavailable, observe_pages
        from seamcheck.observe import merge, save
        from seamcheck.snapshot import current_git_sha

        repo_root = options["repo_root"]
        urls = list(options["observe"])
        if not urls:
            bar = self._progress(options, api.SCAN_STEPS)
            graph = api.scan(repo_root, bar)
            bar.finish()
            urls = self._page_urls(graph, options["base_url"])
            if not urls:
                raise CommandError(
                    "No page URLs found in the graph to visit. Pass them explicitly: "
                    "seamcheck observe http://127.0.0.1:8080/ http://127.0.0.1:8080/store/"
                )
        self.stdout.write(f"Visiting {len(urls)} page(s) with the probe installed.")
        try:
            observations = observe_pages(urls, shots_dir=options["shots"])
        except BrowserUnavailable as error:
            raise CommandError(str(error)) from error

        try:
            sha = current_git_sha(repo_root)
        except Exception:  # noqa: BLE001 - observations are still worth keeping
            sha = "unknown"
        path = save(observations, repo_root, sha)
        folded = merge(observations)
        failed = [o for o in observations if o.screenshot.startswith("error:")]

        self.stdout.write("")
        for bucket in ("selectors", "fetches", "classes"):
            self.stdout.write(f"  {len(folded[bucket]):>6,}  distinct {bucket} observed")
        blind = sum(1 for row in folded["selectors"].values() if not row.get("hits"))
        self.stdout.write(
            f"  {blind:>6,}  selectors that ran and found NOTHING - live nulls, not guesses"
        )
        if failed:
            self.stdout.write(f"\n  {len(failed)} page(s) failed to load:")
            for o in failed[:5]:
                self.stdout.write(f"    {o.page}  {o.screenshot}")
        self.stdout.write(f"\n  wrote  {path}")
        self.stdout.write(
            "  Evidence is keyed to this commit, and says nothing about pages the run did "
            "not visit."
        )

    def _page_urls(self, graph, base_url: str) -> list[str]:
        """Routes a person can open: served by a view, not an API path, no parameters."""
        base = base_url.rstrip("/")
        urls = []
        for symbol in graph.symbols:
            if symbol.kind != "url" or "<" in symbol.label or symbol.label.startswith("^"):
                continue
            path = symbol.label.strip("/")
            if path.startswith(("api/", "admin/")) or path.endswith((".txt", ".xml", ".js")):
                continue
            urls.append(f"{base}/{path}" if path else f"{base}/")
        return sorted(set(urls))

    def _show_config(self, repo_root):
        """What the scan will use, and why - because detection must not be a black box.

        A wrong path is the difference between a real report and an invented one: the one
        config bug found while validating this against a real project had a CSS root set
        narrow enough to exclude stylesheets whose templates were still being read, and it
        manufactured 185 findings. If a value is wrong, a reader has to be able to SEE that
        it is wrong.
        """
        from seamcheck.autoconfig import effective

        config, why = effective(repo_root)
        if not config:
            self.stdout.write(
                "No config, and nothing detected. Seamcheck reads a Django project's "
                "URLconf, templates and static files, so it needs at least "
                "settings.ROOT_URLCONF to be set."
            )
            return
        width = max(len(key) for key in config)
        self.stdout.write("The config this scan will use:\n")
        for key in sorted(config):
            value = config[key]
            if isinstance(value, list) and len(value) > 3:
                value = f"[{len(value)} items] {value[:3]} ..."
            self.stdout.write(f"  {key:<{width}}  {value}")
            self.stdout.write(f"  {'':<{width}}  \u2514\u2500 {why.get(key, 'default')}")
        declared = sum(1 for key in config if why.get(key) == "SEAMCHECK_CONFIG")
        self.stdout.write(
            f"\n  {declared} from SEAMCHECK_CONFIG, {len(config) - declared} detected from "
            "the project.\n  Anything you set in SEAMCHECK_CONFIG wins over detection."
        )

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
        bar = self._progress(options, api.SCAN_STEPS)
        graph = api.scan(repo_root, bar)
        bar.finish()
        if options["since"]:
            result, _, message = api.diff_against(graph, options["since"], repo_root)
            if message:
                self.stdout.write(message)
                # A gate asked to compare against a baseline that is not there has not
                # passed - it has not run. Exit 2, so CI can tell "nothing new" (0) from
                # "no findings, because nothing was checked".
                if options["check"]:
                    raise SystemExit(2)
                return
            self._report(result)
            # `--since` alone answers "what changed"; with `--check` it is a gate, and a
            # gate that prints findings and exits 0 tells CI the build is clean. This
            # branch returned before ever reaching an exit code.
            if options["check"] and (
                result.new_unresolved or result.new_unused or result.triage_invalidated
            ):
                raise SystemExit(1)
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

    def _format_report(self, options, graph=None, bar=None):
        fmt = options["format"]
        repo_root = options["repo_root"]
        bar = bar or self._progress(options, 0)

        if fmt == "json":
            from seamcheck.graph import graph_to_dict

            if graph is None:
                graph = api.scan(repo_root, bar)
            text = json.dumps(graph_to_dict(graph), indent=2)
        else:
            try:
                text = api.report(
                    repo_root, fmt, ref=options["since"] or "HEAD", graph=graph, progress=bar
                )
            except ValueError as error:
                bar.finish()
                self.stderr.write(str(error))
                raise SystemExit(2) from error
        bar.finish()

        serving = options["serve"] and not options["no_serve"]

        destination = options["out"]
        if destination == "-":
            # Explicit stdout always wins, even for a document: a flag whose help text
            # promises the terminal must not silently redirect to a file.
            return self.stdout.write(text)
        if destination is None:
            if fmt not in _DOCUMENTS:
                return self.stdout.write(text)
            # A whole document with no destination goes to a file, because `seamcheck map`
            # used to answer with 3.8 MB of markup down the terminal - which reads as the
            # command being broken. Read config from settings, not api._config(): a command
            # reaching into another module's private helper is how a refactor there
            # silently breaks this one.
            config = getattr(settings, "SEAMCHECK_CONFIG", {})
            key, fallback = _DOCUMENTS[fmt]
            destination = config.get(key) or fallback

        # Written before it is served, not instead of. Serving used to return early, so
        # the one command that renders the UI left nothing behind when you pressed Ctrl-C
        # - and the artifact is the thing you commit, diff and open again tomorrow.
        path = pathlib.Path(repo_root) / destination
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        self._wrote(path, text, open_it=options.get("open_it") and not serving)

        if serving:
            self._serve(
                text, fmt, tunnel=options["tunnel"], local_only=options["local_only"],
                open_it=options.get("open_it"),
            )

    def _wrote(self, path, text: str, open_it: bool = False):
        """Say where the file went.

        No file:// link unless there is nothing better to offer: VS Code's terminal opens
        a file:// URL inside VS Code rather than handing it to a browser, so the one
        "clickable" thing in the output went somewhere the reader did not ask for. An
        http:// link, which the serving path prints, it hands straight to the browser.
        """
        size = len(text.encode("utf-8")) / 1_000_000
        self.stdout.write(f"  wrote  {path}  ({size:.1f} MB)")
        if not open_it:
            return
        self._open(path.resolve().as_uri())

    def _open(self, url: str):
        import webbrowser

        # A browser that will not open must not read as the render having failed - the
        # link is printed either way.
        if not webbrowser.open(url):
            self.stderr.write("could not open a browser; the link above still works.")

    def _exit_on_check(self, repo_root, graph=None):
        if not api.check(repo_root, graph=graph)["passed"]:
            raise SystemExit(1)

    def _summary(self, options):
        """The default command's answer: the totals, in words, and what to type next.

        Four bare numbers under four bare words is a report only to someone who already
        knows what the words claim - and the whole reason this tool exists is that they
        usually do not. Each line says what the number means; the last two lines say where
        to look. Order is worst-first, not alphabetical: `connected` sorted to the top and
        put the one status nobody needs to act on where the eye lands.
        """
        from seamcheck.meaning import BLIND_SPOTS, meaning

        # +1: serialising 37,000 symbols and saving the snapshot is several seconds of
        # its own, and a bar that sits at 100% while work continues is a bar that lies.
        bar = self._progress(options, api.SCAN_STEPS + 1)
        graph = api.scan(options["repo_root"], bar)
        bar.step("writing the snapshot")
        path = api.write_map(graph, options["repo_root"])
        bar.finish()

        counts = {}
        for symbol in graph.symbols:
            counts[symbol.status.value] = counts.get(symbol.status.value, 0) + 1

        self.stdout.write(f"{len(graph.symbols):,} symbols, {len(graph.edges):,} edges")
        for status in ("unresolved", "unused", "uncertain", "connected"):
            count = counts.get(status, 0)
            means, _ = meaning("", status)
            self.stdout.write(f"  {status:<11} {count:>7,}   {means}")
        self.stdout.write("")
        if counts.get("unused"):
            self.stdout.write(f"  Note: {BLIND_SPOTS}")
        self.stdout.write(f"  graph  {path}")
        self.stdout.write("  next   seamcheck map      the same scan, as a page you can click through")
        self.stdout.write("         seamcheck report   the findings as markdown, worst first")

    def _backfill(self, repo_root, count, ref="HEAD"):
        """Fill in the commit history the map's picker reads."""
        from seamcheck.history import backfill

        self.stdout.write(
            f"Scanning up to {count} commits from {ref}, each in its own worktree. "
            "Roughly 30s each; already-scanned commits are skipped."
        )
        scanned = backfill(repo_root, count, ref=ref)
        if not scanned:
            return self.stdout.write(
                f"Nothing to do: the last {count} commits on {ref} already have snapshots."
            )
        for sha in scanned:
            self.stdout.write(f"  scanned {sha[:12]}")
        self.stdout.write(
            f"\n{len(scanned)} commit(s) added. The map's COMMIT picker can now show what "
            "each of them changed - re-run `seamcheck map` to pick it up."
        )

    def _serve(self, text, fmt, tunnel=False, local_only=False, open_it=False):
        """Hold the report open until interrupted, and name every way in."""
        from seamcheck.serve import public_tunnel, serve_addresses

        if fmt == "json":
            raise CommandError("--serve renders a page; use --format map or html.")

        server, addresses = serve_addresses(text, host="127.0.0.1" if local_only else "0.0.0.0")
        self.stdout.write("")
        self.stdout.write(f"  open   {addresses['local']}")
        if "lan" in addresses:
            self.stdout.write(f"  phone  {addresses['lan']}")
        proxy = None
        if tunnel:
            try:
                proxy, public = public_tunnel(server.server_port)
            except RuntimeError as error:
                # A tunnel that will not open must not take the local server down with it.
                self.stderr.write(str(error))
            else:
                path = addresses["local"][addresses["local"].index("/", 8):]
                self.stdout.write(f"  public {public}{path}")
        self.stdout.write("")
        self.stdout.write(
            "  Served from this machine only, for as long as this command runs."
            if local_only else
            "  Served from this machine, reachable by anyone on this network holding"
            "\n  the link. Nothing is uploaded. --local-only drops the phone link and"
            "\n  binds loopback instead."
        )
        self.stdout.write("  Ctrl-C to stop.")
        if open_it:
            self._open(addresses["local"])
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            self.stdout.write("\n  stopped")
        finally:
            server.server_close()
            if proxy is not None:
                proxy.terminate()
