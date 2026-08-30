"""The one implementation the CLI, the MCP server, and any future UI all call."""

from __future__ import annotations

import datetime as dt
import glob
import os
import pathlib

from django.conf import settings

from signal_map.diff import DiffResult, diff_graphs
from signal_map.graph import Graph, Status, relativise
from signal_map.pipeline import run_scan
from signal_map.roots import discover_css_files, discover_js_roots, tailwind_classes
from signal_map.snapshot import current_git_sha, load_snapshot, save_snapshot
from signal_map.triage import (
    TriageEntry,
    TriageStatus,
    apply_triage,
    fingerprint_for_symbol,
    has_blocking_findings,
    load_triage,
    save_triage,
)


def _config() -> dict:
    return getattr(settings, "SIGNAL_MAP_CONFIG", {})


def _js_roots(config: dict, repo_root: str) -> tuple[list[str], str]:
    if "js_entry_files" in config:
        return list(config["js_entry_files"]), config.get("js_project_root", repo_root)
    roots = discover_js_roots(
        vite_config=os.path.join(repo_root, "vite.config.js"),
        templates_root=os.path.join(repo_root, config["templates_root"]),
        static_root=os.path.join(repo_root, "pointless", "static"),
    )
    return roots, repo_root


def _entry_point_files(config: dict, repo_root: str) -> set[str]:
    if "entry_point_files" in config:
        return set(config["entry_point_files"])
    return {
        os.path.abspath(path)
        for prefix in config.get("first_party_prefixes", [])
        for path in glob.glob(os.path.join(repo_root, prefix, "**", "*.py"), recursive=True)
        if "migrations" not in path
    }


def scan(repo_root: str = ".") -> Graph:
    config = _config()
    js_entry_files, js_project_root = _js_roots(config, repo_root)
    asgi_module = config.get("asgi_module")
    asgi_file = (
        os.path.join(repo_root, asgi_module.replace(".", os.sep) + ".py") if asgi_module else None
    )
    templates_root = config.get("templates_root")
    template_files = (
        [str(p) for p in pathlib.Path(repo_root, templates_root).rglob("*.html")]
        if templates_root and os.path.isdir(os.path.join(repo_root, templates_root))
        else []
    )
    css_root = config.get("css_source_root")
    css_files = (
        discover_css_files(os.path.join(repo_root, css_root))
        if css_root and os.path.isdir(os.path.join(repo_root, css_root))
        else []
    )
    build_output = config.get("tailwind_build_output")

    return relativise(run_scan(
        urlconf_module=config["urlconf_module"],
        js_entry_files=js_entry_files,
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
    ), repo_root)


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
    repo_root: str = ".", fmt: str = "terminal", ref: str = "HEAD", graph: Graph | None = None
) -> str:
    """Render the report. One model, chosen serializer - ordering lives in report.py."""
    from signal_map.renderers import html as html_renderer
    from signal_map.renderers import markdown as markdown_renderer
    from signal_map.renderers import terminal as terminal_renderer
    from signal_map.report import build_report

    renderers = {
        "terminal": terminal_renderer.render,
        "markdown": markdown_renderer.render,
        "html": html_renderer.render,
    }
    if fmt == "map":
        return _render_map(repo_root, ref)
    if fmt == "console":
        return _render_console(repo_root, ref)
    if fmt not in renderers and fmt not in ("map", "console"):
        raise ValueError(f"Unknown format {fmt!r}. Use one of: {', '.join(sorted(renderers))}.")

    if graph is None:
        graph = scan(repo_root)
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


def triage(symbol_id: str, status: str, repo_root: str = ".", reason: str = "") -> dict:
    try:
        triage_status = TriageStatus(status)
    except ValueError:
        allowed = ", ".join(s.value for s in TriageStatus)
        return {"ok": False, "message": f"Unknown status `{status}`. Use one of: {allowed}."}

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
        )
    )
    save_triage(entries, repo_root)
    return {"ok": True, "message": f"{symbol_id} marked {triage_status.value}."}


def write_map(graph: Graph, repo_root: str = ".") -> str:
    import json

    from signal_map.graph import graph_to_dict

    path = pathlib.Path(repo_root) / "docs" / "maps" / "connectivity-map.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(graph_to_dict(graph), indent=2), encoding="utf-8")
    save_snapshot(graph, current_git_sha(repo_root), repo_root)
    return str(path)


def _page_files(repo_root: str) -> dict[str, set[str]]:
    """Which JS files each page entry reaches. Computed only for the map: the import
    walk costs ~13s and the CI path has no use for page attribution."""
    import os as _os

    from signal_map.extractors.js_extractor import discover_js_files
    from signal_map.roots import discover_js_roots

    config = _config()
    roots = discover_js_roots(
        vite_config=_os.path.join(repo_root, "vite.config.js"),
        templates_root=_os.path.join(repo_root, config["templates_root"]),
        static_root=_os.path.join(repo_root, "pointless", "static"),
    )
    return {
        _os.path.splitext(_os.path.basename(root))[0]: set(discover_js_files([root], repo_root))
        for root in roots
    }


def _render_map(repo_root: str, ref: str) -> str:
    from signal_map.history import commit_series
    from signal_map.mapdata import build_map
    from signal_map.pagenames import page_names
    from signal_map.renderers import map_html

    graph = scan(repo_root)
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
    return map_html.render(
        build_map(graph, _page_files(repo_root), git_sha=sha,
                  baseline=baseline, baseline_sha=baseline_sha if baseline else None,
                  names=page_names(repo_root, _config(), graph),
                  commits=[
                      {"sha": entry.sha, "subject": entry.subject, "date": entry.date,
                       "symbols": entry.symbols, "changed": entry.changed,
                       "baseline": entry.baseline_sha}
                      for entry in commit_series(repo_root)
                  ])
    )


def _render_console(repo_root: str, ref: str) -> str:
    from signal_map.console import build_console
    from signal_map.renderers import console_html
    from signal_map.report import build_report

    graph = scan(repo_root)
    diff, baseline_sha, message = diff_against(graph, ref, repo_root)
    try:
        sha = current_git_sha(repo_root)
    except Exception:  # noqa: BLE001
        sha = "unknown"
    report = build_report(
        graph=graph, diff=diff, entries=load_triage(repo_root), git_sha=sha,
        baseline_sha=baseline_sha, baseline_message=message,
    )
    return console_html.render(build_console(graph, report))
