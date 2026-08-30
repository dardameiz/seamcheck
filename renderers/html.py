"""One self-contained file: inline CSS, a system font stack, no network requests at all.

This surface exists for a phone that cannot reach the machine that produced it, so a
single external reference would break it precisely where nobody can see the failure.
"""

from __future__ import annotations

import html as html_lib

from signal_map.renderers._shared import where
from signal_map.report import Report, ReportGroup

_UNCERTAIN_GLOSS = (
    "uncertain means the scan found no evidence either way. It is not a claim that "
    "anything is dead."
)

_CSS = """
:root { --bg:#faf9f6; --card:#fff; --ink:#14171c; --muted:#5d6673; --line:#e1e0db;
        --crit:#a93b4b; --warn:#a8681b; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#101317; --card:#191d23; --ink:#e8ebef; --muted:#98a1ae; --line:#2a2f37;
          --crit:#e0788a; --warn:#d69b4c; }
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
.where, .note { color:var(--muted); font-size:13px;
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


# Inline, tiny, and the only script on the page. Hides items whose text does not match.
#
# Item text and each item's owning <details> are read ONCE, at load - a real render can
# carry thousands of .item nodes, and re-reading textContent from every one of them on
# every keystroke (the previous version) forces layout+paint of the whole page each
# time. A group with zero matches is hidden outright rather than left open and empty,
# and clearing the box restores every group to collapsed - the previous version only
# ever opened groups and never closed them back up, so the page stayed in its heaviest
# state (all six panels open) for the rest of the session after the first character.
_FILTER_SCRIPT = """
<script>
(function () {
  const box = document.getElementById("filter");
  const items = Array.from(document.querySelectorAll(".item")).map((el) => ({
    el,
    text: el.textContent.toLowerCase(),
    details: el.closest("details"),
  }));
  const groups = Array.from(document.querySelectorAll("details")).map((el) => ({
    el,
    count: el.querySelector(".cnt"),
  }));

  function reset() {
    items.forEach((item) => { item.el.hidden = false; });
    groups.forEach((group) => {
      group.el.hidden = false;
      group.el.open = false;
      if (group.count) { group.count.textContent = group.count.dataset.total; }
    });
  }

  box.addEventListener("input", () => {
    const q = box.value.trim().toLowerCase();
    if (q === "") { reset(); return; }

    const matches = new Map();
    items.forEach((item) => {
      const hit = item.text.includes(q);
      item.el.hidden = !hit;
      if (hit && item.details) {
        matches.set(item.details, (matches.get(item.details) || 0) + 1);
      }
    });
    groups.forEach((group) => {
      const n = matches.get(group.el) || 0;
      group.el.hidden = n === 0;
      group.el.open = n > 0;
      if (group.count) { group.count.textContent = String(n); }
    });
  });
})();
</script>
"""


def _esc(value) -> str:
    return html_lib.escape(str(value if value is not None else ""))


def _item(symbol) -> str:
    note = f'<div class="note">{_esc(symbol.note)}</div>' if symbol.note else ""
    return (
        '<div class="item">'
        f'<div class="label">{_esc(symbol.label)}</div>'
        f'<div class="where">{_esc(where(symbol))}</div>{note}</div>'
    )


def _group(group: ReportGroup) -> str:
    triaged = f" · {group.triaged} triaged" if group.triaged else ""
    caveat = f"<div class='gloss caveat'>{_esc(group.caveat)}</div>" if group.caveat else ""
    items = "".join(_item(symbol) for symbol in group.symbols)
    total = len(group.symbols)
    # .cnt carries the live match count while filtering (see _FILTER_SCRIPT), and
    # data-total is how the script restores the real count once the query clears.
    return (
        f"<details><summary>{_esc(group.title)} "
        f"(<span class='cnt' data-total='{total}'>{total}</span>{_esc(triaged)})"
        f"</summary>{caveat}{items}</details>"
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
