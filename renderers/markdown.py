"""GitHub-flavoured digest. Renders in a chat client, a PR comment, or a model's context."""

from __future__ import annotations

from signal_map.renderers._shared import where
from signal_map.report import Report, ReportGroup

CAP = 10

_UNCERTAIN_GLOSS = (
    "`uncertain` means the scan found no evidence either way — it is not a claim that "
    "something is dead."
)


def _code_span(symbol) -> str:
    # Markdown-specific presentation of the shared location string; empty stays empty
    # rather than rendering a pair of bare backticks around nothing.
    location = where(symbol)
    return f"`{location}`" if location else ""


def _items(symbols, cap: int) -> list[str]:
    # The label is source-controlled project text (a CSS class, a URL name, ...), not
    # ours - `[`/`]` show up routinely (Tailwind arbitrary values like `text-[9px]`), and
    # unescaped they are markdown link/reference syntax. A code span, matching how
    # where() is already rendered, neutralises that without an HTML-escaping pass.
    lines = [
        f"- **`{symbol.label}`** — {_code_span(symbol)}".rstrip(" —") for symbol in symbols[:cap]
    ]
    if len(symbols) > cap:
        lines.append(f"- _+{len(symbols) - cap} more_")
    return lines


def _group_block(group: ReportGroup) -> list[str]:
    triaged = f" · {group.triaged} triaged" if group.triaged else ""
    lines = [f"**{group.title}** ({len(group.symbols)}{triaged})", ""]
    if group.caveat:
        lines += [f"_{group.caveat}_", ""]
    lines += [*_items(group.symbols, CAP), ""]
    return lines


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
