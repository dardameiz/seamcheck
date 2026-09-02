"""The one implementation the CLI, the MCP server, and any future UI all call."""

from __future__ import annotations

import datetime as dt
import glob
import os
import pathlib

from seamcheck.diff import DiffResult, diff_graphs
from seamcheck.graph import Graph, Status, relativise
from seamcheck.nodetools import report as _notify
from seamcheck.pipeline import SCAN_PHASES, run_scan
from seamcheck.progress import Progress, null
from seamcheck.roots import discover_css_files, discover_js_roots, tailwind_classes
from seamcheck.snapshot import current_git_sha, load_snapshot, save_snapshot
from seamcheck.triage import (
    TriageEntry,
    TriageStatus,
    apply_triage,
    fingerprint_for_symbol,
    has_blocking_findings,
    load_triage,
    save_triage,
)


def _config() -> dict:
    """What was configured, over what was detected from the project.

    Seamcheck used to read SEAMCHECK_CONFIG and nothing else, so `pip install seamcheck`
    followed by a scan did nothing until the reader had reverse-engineered eight paths out
    of their own settings. Django already knows most of them; see seamcheck.autoconfig.

    Explicit config wins key by key, so this cannot change the answer for a project that
    had already configured itself.
    """
    from seamcheck.autoconfig import effective

    merged, _ = effective(_CONFIG_ROOT[0])
    return merged


# Detection is relative to the repo, and every public entry point already takes a
# repo_root. Rather than thread it through _config()'s dozen call sites, the scan records
# which root it is working on. Single-process CLI and MCP server; no concurrent scans.
_CONFIG_ROOT = ["."]


def _static_root(config: dict, repo_root: str) -> str:
    """Where a template's `{% static_js 'a/b.js' %}` reference resolves from.

    Was this project's own "pointless/static", written out twice - which meant every
    other project silently resolved nothing and reported every endpoint those scripts
    call as uncalled. Configurable, with Django's own convention as the fallback.
    """
    return os.path.join(repo_root, config.get("static_root", "static"))


def _template_files(config: dict, repo_root: str) -> list[str]:
    """Every template the project has, not just the ones under one chosen directory.

    Detection picks the DENSEST template directory and everything else was invisible. A
    Django project routinely has several - a project-level `templates/` and one per app -
    and on the reference project the app's `pointless/templates/` holds the pages people
    actually visit while the chosen root was the admin overrides. Classes used only in the
    unscanned half were then reported as CSS nothing applies.

    The configured root still comes first; the rest are the conventional locations, which
    is also where Django's own app-directories loader looks.
    """
    roots: list[str] = []
    configured = config.get("templates_root")
    if configured:
        roots.append(os.path.join(repo_root, configured))
    base = pathlib.Path(repo_root)
    for pattern in ("templates", "*/templates", "*/*/templates", "views", "*/views"):
        roots += [str(d) for d in sorted(base.glob(pattern)) if d.is_dir()]

    found: list[str] = []
    seen: set[str] = set()
    for root in dict.fromkeys(roots):
        if not os.path.isdir(root):
            continue
        if any(part in _SKIP_TREES for part in pathlib.PurePath(root).parts):
            continue
        for path in sorted(pathlib.Path(root).rglob("*.html")):
            resolved = str(path.resolve())
            if resolved not in seen:
                seen.add(resolved)
                found.append(str(path))
    return found


def _static_candidates(config: dict, repo_root: str) -> list[str]:
    """Every directory a `{% static 'x' %}` reference might resolve under, best first.

    One root is a guess; when Django cannot be imported the guess is a directory NAME, and
    on the reference project it picked `src` while the stylesheets live under
    `pointless/static`. A template reference is evidence that the file exists somewhere,
    so resolution tries the configured root first and then every `static/` directory in
    the first two levels of the tree - which is also exactly where Django's own
    app-directories finder looks. Whichever root the file is actually under wins.
    """
    roots = [_static_root(config, repo_root)]
    base = pathlib.Path(repo_root)
    for pattern in ("static", "*/static", "*/*/static", "assets", "*/assets", "public"):
        for candidate in sorted(base.glob(pattern)):
            if candidate.is_dir() and not any(
                part in ("node_modules", "venv", ".venv", "dist", "build", "staticfiles")
                for part in candidate.parts
            ):
                roots.append(str(candidate))
    return list(dict.fromkeys(roots))


def _discover_roots(config: dict, repo_root: str) -> list[str]:
    return discover_js_roots(
        vite_config=os.path.join(repo_root, config.get("vite_config", "vite.config.js")),
        # .get, like everywhere else that reads it: a project with no templates root
        # discovers no template-loaded scripts, which is an empty answer, not a crash.
        templates_root=os.path.join(repo_root, config.get("templates_root", "")),
        static_root=_static_root(config, repo_root),
    )


# How many first-party JavaScript files to read when there are no entry points to walk
# from. A monorepo can hold tens of thousands; parsing all of them turns a scan into a
# coffee break for a diminishing return, and the cap is high enough that no repository in
# the 32-project corpus reaches it except the very largest.
_MAX_JS_FALLBACK = 4000
# Trees that hold copies or somebody else's code, for the directory sweeps below.
_SKIP_TREES = frozenset({"node_modules", "venv", ".venv", "dist", "build", "staticfiles",
                         "site-packages", "__pycache__", ".git", "corpus",
                         "recall_fixtures"})


def _js_roots(config: dict, repo_root: str) -> tuple[list[str], str, list[str]]:
    """The JavaScript to read: the declared entry points UNION the first-party tree.

    It used to be entry points alone, walked through their imports. That is right for the
    files an entry can reach and blind to everything else, and "everything else" turns out
    to be most of a modern application:

      * A Next.js app-router page is routed to BY THE FILESYSTEM. Nothing imports it, so
        no import walk can ever arrive at it - and those pages are where the fetches are.
      * Entry points themselves are found from a Vite config or a Django template's
        {% static_js %} tag, both Django-shaped, so a plain React or Express repository
        often declared none at all and the entire JavaScript reader ran over an empty list.

    Every axios call, every fetch and every DOM write in such a file was invisible, which
    is a large part of why those projects scanned as almost entirely `uncertain`. A project
    whose entries DO reach everything is unaffected: the extra list is then empty.

    Returned as (entries, project root, the rest) rather than one merged list, because the
    two are read differently - see extract_js.
    """
    from seamcheck.extractors.url_reference_extractor import find_js_files

    if "js_entry_files" in config:
        entries = list(config["js_entry_files"])
        project_root = config.get("js_project_root", repo_root)
    else:
        entries, project_root = _discover_roots(config, repo_root), repo_root

    # ABSOLUTE, because discover_js_files() joins each entry onto the project root, and a
    # root-prefixed relative path joins to itself twice - a directory that cannot exist,
    # so every added file silently dropped out again.
    seen = {os.path.abspath(os.path.join(project_root, entry)) for entry in entries}
    extra = sorted(
        path for path in (os.path.abspath(p) for p in find_js_files(repo_root))
        if path not in seen
    )
    return entries, project_root, extra[:_MAX_JS_FALLBACK]


def _entry_point_files(config: dict, repo_root: str) -> set[str]:
    if "entry_point_files" in config:
        return set(config["entry_point_files"])
    return {
        os.path.abspath(path)
        for prefix in config.get("first_party_prefixes", [])
        for path in glob.glob(os.path.join(repo_root, prefix, "**", "*.py"), recursive=True)
        if "migrations" not in path
    }


# What `scan()` walks before run_scan() does: finding the JS entry points, listing the
# templates, listing the stylesheets, reading the Tailwind build output. Named here so a
# caller can size a bar for the whole job rather than for the second half of it.
PREFLIGHT_PHASES = (
    "JavaScript entry points", "listing templates", "listing stylesheets", "Tailwind output",
)
# The total a `scan()` progress bar should be built with.
SCAN_STEPS = len(PREFLIGHT_PHASES) + len(SCAN_PHASES)
# `map` re-reads the JS import graph for page attribution, walks the snapshots for the
# commit picker, builds the review sections and renders the document.
MAP_PHASES = ("building the report", "page attribution", "commit history", "rendering")
MAP_STEPS = SCAN_STEPS + len(MAP_PHASES)


def scan(
    repo_root: str = ".", progress: Progress | None = None, static_urls: bool | None = None
) -> Graph:
    progress = progress or null()
    _CONFIG_ROOT[0] = repo_root
    progress.step("JavaScript entry points")
    config = _config()
    js_entry_files, js_project_root, js_extra_files = _js_roots(config, repo_root)
    asgi_module = config.get("asgi_module")
    asgi_file = (
        os.path.join(repo_root, asgi_module.replace(".", os.sep) + ".py") if asgi_module else None
    )
    progress.step("listing templates")
    templates_root = config.get("templates_root")
    template_files = _template_files(config, repo_root)
    progress.step("listing stylesheets")
    css_root = config.get("css_source_root")
    _css_root_dir = (
        os.path.join(repo_root, css_root)
        if css_root and os.path.isdir(os.path.join(repo_root, css_root)) else None
    )
    progress.step("Tailwind output")
    build_output = config.get("tailwind_build_output")
    tailwind_path = os.path.join(repo_root, build_output) if build_output else None

    # The configured CSS root, if it exists, and every static candidate too. A wrong
    # guess at the CSS directory - `src` on a project whose stylesheets live under
    # `pointless/static` - found 4 stylesheets in 511k lines and reported every class in
    # every admin template as unstyled. The static candidates are where Django's own
    # finders look, so a stylesheet there is one the project serves.
    static_candidates = _static_candidates(config, repo_root)
    css_files = discover_css_files(
        _css_root_dir or "", tailwind_path,
        templates_root=os.path.join(repo_root, templates_root) if templates_root else None,
        static_roots=static_candidates,
        extra_roots=[c for c in static_candidates if c != _css_root_dir],
    )

    scanned = relativise(run_scan(
        # Empty for a project that is not Django. The Django adapter is the only reader
        # that wants it, and it is not the one running in that case.
        urlconf_module=config.get("urlconf_module", ""),
        js_entry_files=js_entry_files,
        js_extra_files=js_extra_files,
        js_project_root=js_project_root,
        entry_point_files=_entry_point_files(config, repo_root),
        asgi_file=asgi_file if asgi_file and os.path.isfile(asgi_file) else None,
        first_party_prefixes=config.get("first_party_prefixes"),
        app_labels=[label.split(".")[0] for label in config.get("app_configs", [])] or None,
        template_files=template_files,
        css_files=css_files,
        tailwind_build_classes=(
            tailwind_classes(os.path.join(repo_root, build_output)) if build_output else set()
        ),
        progress=progress,
        repo_root=repo_root,
        static_urls=(
            config.get("static_urls", False) if static_urls is None else static_urls
        ),
    ), repo_root)
    return _with_observations(scanned, repo_root)


def _observed_payload(repo_root: str, sha: str) -> dict:
    rows, at = _observations(repo_root, sha)
    return {
        "at": at,
        "current": at == sha,
        # A page with 3,000 boxes is not more informative than one with 500, and it is six
        # times the payload. The biggest elements come first, so the cap keeps structure.
        "pages": [
            {"page": o.page, "screenshot": o.screenshot,
             "boxes": sorted(o.boxes, key=lambda b: -(b.get("w", 0) * b.get("h", 0)))[:500]}
            for o in rows if o.boxes
        ],
    }


def _observations(repo_root: str, sha: str) -> tuple[list, str]:
    """What a browser saw, and which commit it saw it at.

    Observations are keyed by commit on purpose - a page exercised against a different
    version of the code is a different page. But refusing to show anything but today's
    commit means the view is empty for everyone who has not run `observe` in the last five
    minutes, which is everyone. So the newest recording is used and the commit it came from
    is returned with it, to be SAID rather than hidden.
    """
    from seamcheck import observe

    try:
        current = observe.load(repo_root, sha)
        if current:
            return current, sha
        folder = pathlib.Path(repo_root, observe._STORE_DIR)
        files = sorted(folder.glob("*.json"), key=lambda f: f.stat().st_mtime, reverse=True)
        for path in files:
            rows = observe.load(repo_root, path.stem)
            if rows:
                return rows, path.stem
    except (OSError, ValueError, AttributeError):
        pass
    return [], sha


def _with_observations(graph: Graph, repo_root: str) -> Graph:
    """Fold in browser evidence recorded for THIS commit, if any exists.

    `seamcheck observe` wrote the evidence and `apply_observations` knew how to merge it,
    and nothing ever called the second with the first: the probe saved a file that no scan
    read. A third of a real graph is runtime-built, so that gap was the whole reason those
    symbols stayed `uncertain` no matter how much was observed.

    Keyed by commit, and silent when there is nothing to apply - a cloned repository can
    never be observed, and must scan exactly as it does today.
    """
    try:
        from seamcheck.observe import load, merge
        from seamcheck.provenance import apply_observations

        observations = load(repo_root, current_git_sha(repo_root))
    except Exception:  # noqa: BLE001 - evidence is enrichment; never fail a scan for it
        return graph
    if not observations:
        return graph
    _notify(
        "observations",
        "applied browser evidence from %s page(s) recorded for this commit. Pages the run "
        "did not visit are unaffected - silence about a path is not evidence about it.",
        len(observations),
    )
    return apply_observations(graph, merge(observations))


def explain(graph: Graph, symbol_id: str) -> str:
    symbol = next((s for s in graph.symbols if s.id == symbol_id), None)
    if symbol is None:
        return f"No symbol with id `{symbol_id}` in the current scan."
    lines = [
        f"## {symbol.label}  ({symbol.kind})",
        "",
        f"- **status:** {symbol.status.value}",
        f"- **where:** {symbol.file}:{symbol.line}" if symbol.file else "- **where:** (not a file)",
        f"- **chain:** {' -> '.join(symbol.chain)}" if symbol.chain else "",
        "",
        "```",
        symbol.snippet,
        "```",
    ]
    if symbol.note:
        lines += ["", f"> {symbol.note}"]
    return "\n".join(line for line in lines if line != "")


def diff_against(graph: Graph, ref: str, repo_root: str = ".") -> tuple[DiffResult | None, str, str]:
    """Diff `graph` against the snapshot for `ref`, or say plainly that there isn't one.

    Returns `(result, sha, message)`. `sha` is the commit `ref` actually resolved to -
    the baseline the diff describes, which is not necessarily HEAD - so a caller that
    needs to name that commit (the report header, "NEW SINCE ...") never has to
    re-resolve `ref` itself and risk naming the wrong commit. `sha` is `""` only when
    `ref` itself could not be resolved at all.
    """
    try:
        sha = current_git_sha(repo_root) if ref == "HEAD" else _rev_parse(ref, repo_root)
    except Exception as error:  # noqa: BLE001 - surfaced to the user, never swallowed
        return None, "", f"No baseline: could not resolve `{ref}` ({error})."

    baseline = load_snapshot(sha, repo_root)
    if baseline is None:
        return None, sha, f"No baseline snapshot stored for {sha[:12]} yet - nothing to diff against."
    return diff_graphs(baseline, graph, load_triage(repo_root)), sha, ""


def _rev_parse(ref: str, repo_root: str) -> str:
    import subprocess

    return subprocess.run(
        ["git", "-C", repo_root, "rev-parse", ref], capture_output=True, text=True, check=True
    ).stdout.strip()


def check(repo_root: str = ".", graph: Graph | None = None) -> dict:
    if graph is None:
        graph = scan(repo_root)
    entries = load_triage(repo_root)
    graph = apply_triage(graph, entries)
    result, _, message = diff_against(graph, "HEAD", repo_root)

    def _ids(symbols):
        return [{"id": s.id, "label": s.label, "kind": s.kind, "note": s.note} for s in symbols]

    return {
        "passed": not has_blocking_findings(graph, entries),
        "message": message,
        "new_unresolved": _ids(result.new_unresolved) if result else [],
        "new_unused": _ids(result.new_unused) if result else [],
        "triage_invalidated": result.triage_invalidated if result else [],
        "counts": {
            status.value: sum(1 for s in graph.symbols if s.status is status) for status in Status
        },
    }


def report(
    repo_root: str = ".", fmt: str = "terminal", ref: str = "HEAD", graph: Graph | None = None,
    progress: Progress | None = None,
) -> str:
    """Render the report. One model, chosen serializer - ordering lives in report.py."""
    from seamcheck.renderers import html as html_renderer
    from seamcheck.renderers import markdown as markdown_renderer
    from seamcheck.renderers import terminal as terminal_renderer
    from seamcheck.report import build_report

    renderers = {
        "terminal": terminal_renderer.render,
        "markdown": markdown_renderer.render,
        "html": html_renderer.render,
    }
    if fmt == "map":
        return _render_map(repo_root, ref, progress)
    if fmt == "console":
        # The review sections live inside the map now: one document, one link, one render
        # of the same scan. Kept as an alias so an existing caller does not break.
        return _render_map(repo_root, ref, progress)
    if fmt not in renderers and fmt not in ("map", "console", "json"):
        raise ValueError(f"Unknown format {fmt!r}. Use one of: {', '.join(sorted(renderers))}.")

    if graph is None:
        graph = scan(repo_root, progress)
    if fmt == "json":
        # The whole graph, for a script or an agent. Used to live only in the Django
        # management command, so `seamcheck json` on any other backend printed the
        # terminal report instead - and the agent-facing format was the one that broke.
        import json as _json

        from seamcheck.graph import graph_to_dict

        return _json.dumps(graph_to_dict(graph), indent=2)
    diff, baseline_sha, message = diff_against(graph, ref, repo_root)
    try:
        sha = current_git_sha(repo_root)
    except Exception:  # noqa: BLE001 - a report is still useful outside a git checkout
        sha = "unknown"

    built = build_report(
        graph=graph,
        diff=diff,
        entries=load_triage(repo_root),
        git_sha=sha,
        # baseline_sha names the commit the diff was actually taken against - the
        # resolved `ref`, not `sha` (the commit just scanned). They coincide only when
        # `ref == "HEAD"` and nothing changed since; conflating them is what made the
        # header and the "NEW SINCE" heading print the same commit for a real --since.
        baseline_sha=None if diff is None else baseline_sha,
        baseline_message=message,
    )
    return renderers[fmt](built)


def triage(symbol_id: str, status: str, repo_root: str = ".", reason: str = "",
           why: str = "") -> dict:
    """Record a disposition. `why` is the shareable half - a fixed word, see WhyWrong."""
    from seamcheck.triage import WhyWrong

    try:
        triage_status = TriageStatus(status)
    except ValueError:
        allowed = ", ".join(s.value for s in TriageStatus)
        return {"ok": False, "message": f"Unknown status `{status}`. Use one of: {allowed}."}
    if why:
        try:
            why = WhyWrong(why).value
        except ValueError:
            allowed = ", ".join(w.value for w in WhyWrong)
            return {"ok": False, "message": f"Unknown reason `{why}`. Use one of: {allowed}."}

    graph = scan(repo_root)
    symbol = next((s for s in graph.symbols if s.id == symbol_id), None)
    if symbol is None:
        return {"ok": False, "message": f"No symbol with id `{symbol_id}` in the current scan."}

    entries = [e for e in load_triage(repo_root) if e.symbol_id != symbol_id]
    entries.append(
        TriageEntry(
            symbol_id=symbol_id,
            fingerprint=fingerprint_for_symbol(symbol),
            status=triage_status,
            who=os.environ.get("USER", "unknown"),
            when=dt.date.today().isoformat(),
            reason=reason,
            why=why,
        )
    )
    save_triage(entries, repo_root)
    return {"ok": True, "message": f"{symbol_id} marked {triage_status.value}."}


def write_map(graph: Graph, repo_root: str = ".") -> str:
    import json

    from seamcheck.graph import graph_to_dict

    path = pathlib.Path(repo_root) / "docs" / "maps" / "connectivity-map.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(graph_to_dict(graph), indent=2), encoding="utf-8")
    save_snapshot(graph, current_git_sha(repo_root), repo_root)
    return str(path)


def _page_files(repo_root: str) -> dict[str, set[str]]:
    """Which JS files each page entry reaches. Computed only for the map: the import
    walk costs ~13s and the CI path has no use for page attribution.

    Goes through `_js_roots`, which is the one function that knows how a project's entry
    points are resolved. Re-deriving them here meant a config that names `js_entry_files`
    explicitly - the documented way to skip discovery - still had `templates_root` read
    out of it, and `map` died on a KeyError while `scan` was fine.
    """
    from seamcheck.extractors.js_extractor import discover_js_files

    roots, _, _extra = _js_roots(_config(), repo_root)
    return {
        os.path.splitext(os.path.basename(root))[0]: {
            _norm(path, repo_root) for path in discover_js_files([root], repo_root)
        }
        for root in roots
    }


def _norm(path: str, repo_root: str) -> str:
    """One spelling of a path, so page membership can be compared against symbol.file.

    A symbol's file is ALWAYS repo-relative (see relativise). The page's file set was
    neither reliably: `discover_js_files` echoes the entry it was handed and resolves
    imports itself, so the same file arrived as "./public/js/cart.js" when the root was
    "." and as an absolute path when it was not, while the scan recorded
    "public/js/cart.js" both times.

    Nothing matched, so every page's seeds missed, and build_map dropped each page as
    empty - the map showed nothing but the "not reached from any page" buckets. Only the
    one page that happened to be reached through an import survived, which is what made it
    look like a layout problem rather than a path problem.
    """
    try:
        return os.path.relpath(os.path.abspath(path), os.path.abspath(repo_root)).replace(
            os.sep, "/"
        )
    except ValueError:  # different drive on Windows
        return os.path.normpath(path).replace(os.sep, "/")


# The files the last rendered map named. The viewer fetches source over the same server
# the map is served from, and an ALLOWLIST is what makes that safe: the set of files the
# scan actually saw, rather than a document root somebody can walk out of.
LAST_MAP_FILES: set[str] = set()


def _share_payload(repo_root: str, graph: Graph) -> dict:
    """The share report for a graph already scanned. Never raises: a map is worth more
    than a report section, so a failure here costs the section and nothing else."""
    try:
        from seamcheck import share
        from seamcheck.pipeline import LAST_ADAPTERS
        from seamcheck.services import detect_services

        return share._payload(
            graph, list(LAST_ADAPTERS), detect_services(repo_root),
            share._triage_shapes(repo_root, graph),
        )
    except Exception:  # noqa: BLE001
        return {}


def _render_map(repo_root: str, ref: str, progress: Progress | None = None) -> str:
    progress = progress or null()
    js_entry_files, _, _extra = _js_roots(_config(), repo_root)
    from seamcheck.console import build_console
    from seamcheck.filetree import build_file_tree
    from seamcheck.history import commit_series
    from seamcheck.mapdata import build_map
    from seamcheck.pagenames import page_names
    from seamcheck.renderers import map_html
    from seamcheck.report import build_report

    graph = scan(repo_root, progress)
    baseline = None
    baseline_sha = None
    if ref and ref != "HEAD":
        try:
            baseline_sha = _rev_parse(ref, repo_root)
            baseline = load_snapshot(baseline_sha, repo_root)
        except Exception:  # noqa: BLE001 - a missing baseline degrades to current mode
            baseline, baseline_sha = None, None
    try:
        sha = current_git_sha(repo_root)
    except Exception:  # noqa: BLE001
        sha = "unknown"
    # One document: the map and the review sections describe the same scan, and a second
    # render of the same graph bought a second link and nothing else.
    diff, baseline_message_sha, message = diff_against(graph, ref, repo_root)
    progress.step("building the report")
    console = build_console(graph, build_report(
        graph=graph, diff=diff, entries=load_triage(repo_root), git_sha=sha,
        baseline_sha=baseline_message_sha, baseline_message=message,
    ))
    progress.step("page attribution")
    page_files = _page_files(repo_root)
    progress.step("commit history")
    commits = commit_series(repo_root)
    # One row per scan, appended. This is the only place a series can be built from - a
    # scan that is not recorded is a data point nobody can get back - and it is cheap
    # enough (a few hundred bytes) that not recording it would be the odd choice.
    from seamcheck.trend import load as load_trend
    from seamcheck.trend import record as record_trend
    from seamcheck.trend import trend as summarise_trend
    try:
        record_trend(graph, sha, repo_root)
        series = summarise_trend(load_trend(repo_root))
    except OSError:  # a read-only checkout still gets a map
        series = summarise_trend([])
    progress.step("rendering")
    from seamcheck.pipeline import LAST_ADAPTERS

    LAST_MAP_FILES.clear()
    LAST_MAP_FILES.update(
        symbol.file for symbol in graph.symbols if symbol.file
    )
    return map_html.render(
        build_map(graph, page_files, git_sha=sha,
                  baseline=baseline, baseline_sha=baseline_sha if baseline else None,
                  names=page_names(repo_root, _config(), graph),
                  commits=[
                      {"sha": entry.sha, "subject": entry.subject, "date": entry.date,
                       "symbols": entry.symbols, "changed": entry.changed,
                       "baseline": entry.baseline_sha, "head": entry.sha == sha,
                       # Capped: a large refactor can change thousands, and a browser
                       # needs the first screenful, not the whole set.
                       "changes": entry.changes[:300], "change_total": len(entry.changes)}
                      for entry in commits
                  ]),
        console=console,
        files=[
            {"path": record.path, "counts": record.counts,
             "declarations": record.declarations, "known": record.known}
            for record in build_file_tree(graph, js_entry_files)
        ],
        # What a browser actually saw, if `seamcheck observe` has been run: one entry per
        # page, with the position and size of every element big enough to point at. This is
        # the other half of the picture - the scan says what the code declares, and this
        # says what the page put on screen.
        observed=_observed_payload(repo_root, sha),
        repo_root=os.path.abspath(repo_root),
        editor=_config().get("editor"),
        series=series,
        adapters=list(LAST_ADAPTERS),
        # The code-free report, built from the graph already in hand. Embedded so the
        # Report view can show a reader exactly what would be sent without another scan,
        # and so the page and `seamcheck share` can never disagree about it.
        share_payload=_share_payload(repo_root, graph),
    )
