"""GitHub-flavoured digest. Renders in a chat client, a PR comment, or a model's context."""

from __future__ import annotations

from signal_map.report import Report, ReportGroup

CAP = 10

_UNCERTAIN_GLOSS = (
    "`uncertain` means the scan found no evidence either way — it is not a claim that "
    "something is dead."
)


def _where(symbol) -> str:
    if not symbol.file:
        return ""
    return f"`{symbol.file}:{symbol.line}`" if symbol.line else f"`{symbol.file}`"


def _items(symbols, cap: int) -> list[str]:
    lines = [f"- **{symbol.label}** — {_where(symbol)}".rstrip(" —") for symbol in symbols[:cap]]
    if len(symbols) > cap:
        lines.append(f"- _+{len(symbols) - cap} more_")
    return lines


def _group_block(group: ReportGroup) -> list[str]:
    triaged = f" · {group.triaged} triaged" if group.triaged else ""
    return [f"**{group.title}** ({len(group.symbols)}{triaged})", "", *_items(group.symbols, CAP), ""]


def render(report: Report) -> str:
    lines = [f"## Signal Map — `{report.git_sha[:12]}`", ""]

    if report.baseline_sha is None:
        lines += [report.baseline_message or "No baseline to compare against.", ""]
    elif report.new_findings:
        lines += [f"### New since `{report.baseline_sha[:12]}` ({len(report.new_findings)})", ""]
        lines += _items(report.new_findings, len(report.new_findings))
        lines.append("")
    else:
        lines += ["Nothing new since the baseline.", ""]

    if report.triage_invalidated:
        lines += ["### Triage marks that no longer apply", ""]
        lines += [f"- `{item['symbol_id']}` — {item['note']}" for item in report.triage_invalidated]
        lines.append("")

    if report.resolved:
        lines += [f"### Resolved since the baseline ({len(report.resolved)})", ""]

    if report.groups:
        lines += ["### Backlog", ""]
        for group in report.groups:
            lines += _group_block(group)

    # No sort - build_report() owns the order; see the terminal renderer.
    counts = " · ".join(f"{name} **{value}**" for name, value in report.counts.items())
    lines += [counts, "", _UNCERTAIN_GLOSS]
    return "\n".join(lines)
