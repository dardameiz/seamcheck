"""One self-contained file: inline CSS, a system font stack, no network requests at all.

This surface exists for a phone that cannot reach the machine that produced it, so a
single external reference would break it precisely where nobody can see the failure.
"""

from __future__ import annotations

import html as html_lib

from signal_map.report import Report, ReportGroup

_UNCERTAIN_GLOSS = (
    "uncertain means the scan found no evidence either way. It is not a claim that "
    "anything is dead."
)

_CSS = """
:root { --bg:#faf9f6; --card:#fff; --ink:#14171c; --muted:#5d6673; --line:#e1e0db;
        --sig:#1f7a8c; --crit:#a93b4b; --warn:#a8681b; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#101317; --card:#191d23; --ink:#e8ebef; --muted:#98a1ae; --line:#2a2f37;
          --sig:#4fb3c4; --crit:#e0788a; --warn:#d69b4c; }
}
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--ink); font-size:15px; line-height:1.55;
       font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif; }
.wrap { max-width:820px; margin:0 auto; padding:20px 16px 60px; }
h1 { font-size:20px; margin:0 0 2px; }
.meta { color:var(--muted); font-size:13px; margin-bottom:20px; }
h2 { font-size:16px; margin:26px 0 10px; }
.item { background:var(--card); border:1px solid var(--line); border-radius:8px;
        padding:10px 12px; margin-bottom:8px; }
.label { font-weight:600; word-break:break-word; }
.where, .note { color:var(--muted); font-size:12.5px;
                font-family:ui-monospace,SFMono-Regular,Menlo,monospace; }
.note { font-family:inherit; margin-top:4px; }
details { background:var(--card); border:1px solid var(--line); border-radius:8px;
          margin-bottom:8px; }
summary { padding:10px 12px; cursor:pointer; font-weight:600; }
details > .item { margin:0 10px 8px; }
.counts { display:flex; flex-wrap:wrap; gap:8px; margin:18px 0 6px; }
.count { border:1px solid var(--line); border-radius:20px; padding:2px 10px; font-size:13px; }
.gloss { color:var(--muted); font-size:13px; }
.filter { width:100%; padding:9px 11px; margin-bottom:14px; font-size:15px;
          border:1px solid var(--line); border-radius:8px;
          background:var(--card); color:var(--ink); }
[hidden] { display:none !important; }
.crit { color:var(--crit); } .warn { color:var(--warn); }
"""


# Inline, tiny, and the only script on the page. Hides items whose text does not match,
# and opens every group while a query is active so matches are not buried in a
# collapsed <details>.
_FILTER_SCRIPT = """
<script>
const box = document.getElementById("filter");
box.addEventListener("input", () => {
  const q = box.value.trim().toLowerCase();
  document.querySelectorAll(".item").forEach(el => {
    el.hidden = q !== "" && !el.textContent.toLowerCase().includes(q);
  });
  document.querySelectorAll("details").forEach(d => {
    if (q !== "") { d.open = true; }
  });
});
</script>
"""


def _esc(value) -> str:
    return html_lib.escape(str(value if value is not None else ""))


def _where(symbol) -> str:
    if not symbol.file:
        return ""
    return f"{symbol.file}:{symbol.line}" if symbol.line else symbol.file


def _item(symbol) -> str:
    note = f'<div class="note">{_esc(symbol.note)}</div>' if symbol.note else ""
    return (
        '<div class="item">'
        f'<div class="label">{_esc(symbol.label)}</div>'
        f'<div class="where">{_esc(_where(symbol))}</div>{note}</div>'
    )


def _group(group: ReportGroup) -> str:
    triaged = f" · {group.triaged} triaged" if group.triaged else ""
    items = "".join(_item(symbol) for symbol in group.symbols)
    return (
        f"<details><summary>{_esc(group.title)} "
        f"({len(group.symbols)}{_esc(triaged)})</summary>{items}</details>"
    )


def render(report: Report) -> str:
    parts = [
        "<!doctype html>",
        '<html lang="en"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>Signal Map — {_esc(report.git_sha[:12])}</title>",
        f"<style>{_CSS}</style></head><body><div class='wrap'>",
        f"<h1>Signal Map</h1><div class='meta'>{_esc(report.git_sha[:12])} · "
        f"{_esc(report.generated_at)}</div>",
        '<input id="filter" class="filter" type="search" placeholder="Filter findings">',
    ]

    if report.baseline_sha is None:
        parts.append(f"<p>{_esc(report.baseline_message or 'No baseline to compare against.')}</p>")
    elif report.new_findings:
        parts.append(f"<h2 class='crit'>New since {_esc(report.baseline_sha[:12])} "
                     f"({len(report.new_findings)})</h2>")
        parts += [_item(symbol) for symbol in report.new_findings]
    else:
        parts.append("<p>Nothing new since the baseline.</p>")

    if report.triage_invalidated:
        parts.append("<h2 class='warn'>Triage marks that no longer apply</h2>")
        parts += [
            f"<div class='item'><div class='label'>{_esc(item['symbol_id'])}</div>"
            f"<div class='note'>{_esc(item['note'])}</div></div>"
            for item in report.triage_invalidated
        ]

    if report.resolved:
        parts.append(f"<h2>Resolved since the baseline ({len(report.resolved)})</h2>")

    if report.groups:
        parts.append("<h2>Backlog</h2>")
        parts += [_group(group) for group in report.groups]

    # No sort - build_report() owns the order; see the terminal renderer.
    counts = "".join(
        f"<span class='count'>{_esc(name)} {value}</span>"
        for name, value in report.counts.items()
    )
    parts.append(f"<div class='counts'>{counts}</div>")
    parts.append(f"<p class='gloss'>{_esc(_UNCERTAIN_GLOSS)}</p>")
    parts.append(_FILTER_SCRIPT)
    parts.append("</div></body></html>")
    return "\n".join(parts)
