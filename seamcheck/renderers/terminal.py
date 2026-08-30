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

    for item in report.triage_invalidated:
        lines.append(f"  triage invalidated: {item['symbol_id']} — {item['note']}")
    if report.triage_invalidated:
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
    lines += [counts, _UNCERTAIN_GLOSS]
    return "\n".join(lines)
