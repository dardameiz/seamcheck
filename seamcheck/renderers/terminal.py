"""Plain-text report. No ANSI escapes: this output lands in CI logs and pipes."""

from __future__ import annotations

from seamcheck.renderers._shared import where
from seamcheck.report import Report, ReportGroup

CAP = 5

_UNCERTAIN_GLOSS = "uncertain = no evidence either way, not a claim that it is dead"


def _finding_lines(symbols, cap: int) -> list[str]:
    lines = [f"    {symbol.label:<34} {where(symbol)}".rstrip() for symbol in symbols[:cap]]
    if len(symbols) > cap:
        lines.append(f"    ... +{len(symbols) - cap} more")
    return lines


def returned_line(item: dict) -> str:
    """One returned finding, in words: what it is, what was said about it, and when the
    code moved. Shared with the markdown renderer so the two never phrase it apart."""
    said = item["marked"] + (f" ({item['why']})" if item["why"] else "")
    moved = f", changed {item['expired']}" if item["expired"] else ""
    return (f"{item['symbol_id']} — marked {said} by {item['who']} on {item['when']}"
            f"{moved}; now {item['status']} again. "
            f"Look once more, then re-mark it or `seamcheck triage '{item['symbol_id']}' --undo`.")


def _group_block(group: ReportGroup) -> list[str]:
    triaged = f", {group.triaged} triaged" if group.triaged else ""
    lines = [f"  {group.title} ({len(group.symbols)}{triaged})"]
    if group.caveat:
        lines.append(f"    {group.caveat}")
    lines += _finding_lines(group.symbols, CAP)
    return lines


def render(report: Report) -> str:
    lines: list[str] = [f"Seamcheck — {report.git_sha[:12]} — {report.generated_at}", ""]

    if report.baseline_sha is None:
        lines += [report.baseline_message or "No baseline to compare against.", ""]
    elif report.new_findings:
        lines.append(f"NEW SINCE {report.baseline_sha[:12]} ({len(report.new_findings)})")
        lines += _finding_lines(report.new_findings, len(report.new_findings))
        lines.append("")
    else:
        lines += ["Nothing new since the baseline.", ""]

    if report.returned:
        lines.append(f"RETURNED ({len(report.returned)}) — marked fine once; the evidence has changed")
        lines += [f"    {returned_line(item)}" for item in report.returned]
        lines.append("")

    # A mark whose finding went away is not a return - nothing to raise - but the mark
    # is now dead weight in triage.json, and `--undo` is one line.
    outlived = [item for item in report.triage_invalidated
                if item["symbol_id"] not in {r["symbol_id"] for r in report.returned}]
    for item in outlived:
        lines.append(f"  mark outlived its finding: {item['symbol_id']} — {item['note']}")
    if outlived:
        lines.append("")

    if report.resolved:
        lines += [f"Resolved since the baseline ({len(report.resolved)})", ""]

    if report.groups:
        lines.append("BACKLOG")
        for group in report.groups:
            lines += _group_block(group)
        lines.append("")

    # report.py builds counts as {status.value: 0 for status in Status}, so dict order is
    # already Status declaration order (connected, unused, unresolved, uncertain). That
    # ordering choice belongs to the model - re-sorting it here would just be a different
    # renderer bug from the one this file exists to avoid.
    counts = "  ".join(f"{name} {value}" for name, value in report.counts.items())
    if report.returned:
        counts += f"  returned {len(report.returned)}"
    lines += [counts, _UNCERTAIN_GLOSS]
    return "\n".join(lines)
