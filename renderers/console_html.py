"""The browsable console: the spec's eight sections in one self-contained file.

Works on a phone, works offline, works with no server. Long lists page in rather than
rendering 18,000 rows at once - this project's largest section would otherwise put every
DOM attribute into the document and freeze the device the file exists to be read on.
"""

from __future__ import annotations

import html as html_lib
import json

from signal_map.console import Console

_CSS = """
:root { --bg:#faf9f6; --panel:#fff; --ink:#14171c; --muted:#5d6673; --line:#e1e0db;
        --sig:#1f7a8c; --ok:#2e7d5b; --crit:#a93b4b; --warn:#a8681b; --dim:#7a8496; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#101317; --panel:#191d23; --ink:#e8ebef; --muted:#98a1ae; --line:#2a2f37;
          --sig:#4fb3c4; --ok:#56b98c; --crit:#e0788a; --warn:#d69b4c; --dim:#8b94a3; }
}
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--ink); font-size:14px; line-height:1.5;
       font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif; }
.shell { display:flex; min-height:100vh; }
.side { width:250px; flex:none; border-right:1px solid var(--line); background:var(--panel);
        display:flex; flex-direction:column; }
.side h1 { font-size:16px; margin:0; padding:14px 14px 2px; }
.meta { padding:0 14px 12px; color:var(--muted); font-size:11.5px;
        font-family:ui-monospace,Menlo,monospace; word-break:break-all; }
.nav { border-top:1px solid var(--line); }
.nv { padding:10px 14px; cursor:pointer; border-bottom:1px solid var(--line);
      display:flex; justify-content:space-between; gap:8px; align-items:baseline; }
.nv:hover { background:var(--bg); }
.nv[aria-current="true"] { background:var(--bg); border-left:3px solid var(--sig); font-weight:600; }
.nv .c { color:var(--muted); font-size:11px; font-family:ui-monospace,Menlo,monospace; }
.main { flex:1; min-width:0; padding:18px 20px 60px; }
h2 { font-size:19px; margin:0 0 4px; }
.blurb { color:var(--muted); margin:0 0 16px; max-width:62ch; }
.cards { display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:10px;
         margin-bottom:18px; }
.card { background:var(--panel); border:1px solid var(--line); border-radius:9px; padding:11px 13px; }
.card .k { color:var(--muted); font-size:11px; text-transform:uppercase; letter-spacing:.07em; }
.card .v { font-size:22px; font-weight:700; font-variant-numeric:tabular-nums; }
.bars { display:flex; gap:5px; flex-wrap:wrap; margin-top:7px; }
.pill { font-size:11px; padding:1px 7px; border-radius:11px; border:1px solid var(--line);
        font-family:ui-monospace,Menlo,monospace; }
.pill.connected { color:var(--ok); } .pill.unresolved { color:var(--crit); }
.pill.unused { color:var(--warn); } .pill.uncertain { color:var(--dim); }
.tools { display:flex; gap:8px; margin-bottom:12px; flex-wrap:wrap; }
.tools input, .tools select { padding:7px 9px; font-size:13px; border:1px solid var(--line);
    border-radius:7px; background:var(--panel); color:var(--ink); }
.tools input { flex:1; min-width:160px; }
.row { background:var(--panel); border:1px solid var(--line); border-radius:9px;
       padding:9px 12px; margin-bottom:7px; cursor:pointer; }
.row:hover { border-color:var(--sig); }
.row .t { font-weight:600; word-break:break-all; font-family:ui-monospace,Menlo,monospace;
          font-size:13px; }
.row .w { color:var(--muted); font-size:11.5px; font-family:ui-monospace,Menlo,monospace;
          word-break:break-all; margin-top:2px; }
.row .n { color:var(--muted); font-size:12.5px; margin-top:5px; }
.row .badge { float:right; font-size:10.5px; padding:1px 7px; border-radius:10px;
              font-family:ui-monospace,Menlo,monospace; margin-left:8px; }
.badge.connected { color:var(--ok); background:color-mix(in srgb,var(--ok) 13%,transparent); }
.badge.unresolved { color:var(--crit); background:color-mix(in srgb,var(--crit) 13%,transparent); }
.badge.unused { color:var(--warn); background:color-mix(in srgb,var(--warn) 15%,transparent); }
.badge.uncertain { color:var(--dim); background:color-mix(in srgb,var(--dim) 15%,transparent); }
.more { text-align:center; padding:10px; color:var(--muted); font-size:12.5px; cursor:pointer;
        border:1px dashed var(--line); border-radius:9px; }
.gap { border-left:3px solid var(--warn); border-radius:0 9px 9px 0; padding:13px 15px;
       background:color-mix(in srgb,var(--warn) 8%,var(--panel)); max-width:64ch; }
.gloss { color:var(--muted); font-size:12.5px; margin-top:20px; max-width:64ch; }
.menu { display:none; }
@media (max-width:760px) {
  .shell { display:block; }
  .side { width:auto; border-right:0; border-bottom:1px solid var(--line); }
  .nav { display:none; }
  .nav.open { display:block; }
  .menu { display:block; margin:0 14px 12px; padding:8px 10px; font-size:13px;
          border:1px solid var(--line); border-radius:7px; background:var(--bg);
          color:var(--ink); width:calc(100% - 28px); text-align:left; cursor:pointer; }
  .main { padding:14px 14px 60px; }
  .card .v { font-size:19px; }
}
"""

_SCRIPT = r"""
const D = CONSOLE;
const PAGE = 60;
let section = "overview", query = "", status = "", shown = PAGE;

const esc = v => String(v == null ? "" : v).replace(/[&<>"']/g,
  c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));

const nav = document.getElementById("nav");
const main = document.getElementById("main");

const ITEMS = [{key:"overview", title:"Overview", count:null}].concat(
  D.sections.map(s => ({key:s.key, title:s.title, count:s.unavailable ? null : s.rows.length})));

ITEMS.forEach(it => {
  const el = document.createElement("div");
  el.className = "nv"; el.tabIndex = 0; el.setAttribute("role", "link");
  el.innerHTML = `<span>${esc(it.title)}</span>` +
    (it.count === null ? `<span class="c">—</span>` : `<span class="c">${it.count}</span>`);
  el.onclick = () => { section = it.key; query = ""; status = ""; shown = PAGE;
                       nav.classList.remove("open"); render(); };
  el.onkeydown = e => { if (e.key === "Enter") el.click(); };
  nav.appendChild(el);
});

document.getElementById("menu").onclick = () => nav.classList.toggle("open");

function pills(counts) {
  return ["connected","unresolved","unused","uncertain"]
    .map(k => `<span class="pill ${k}">${k} ${counts[k] || 0}</span>`).join("");
}

function overview() {
  const b = D.backend, f = D.frontend;
  const total = c => Object.values(c).reduce((a, n) => a + n, 0);
  return `<h2>Overview</h2>
    <p class="blurb">Two totals, each broken into the four statuses. Every number is what
    the scan is willing to claim about this commit — nothing here is an estimate.</p>
    <div class="cards">
      <div class="card"><div class="k">Backend symbols</div><div class="v">${total(b)}</div>
        <div class="bars">${pills(b)}</div></div>
      <div class="card"><div class="k">Frontend symbols</div><div class="v">${total(f)}</div>
        <div class="bars">${pills(f)}</div></div>
    </div>
    <h3 style="font-size:15px;margin:18px 0 8px">Backlog by kind</h3>
    ${D.groups.length ? D.groups.map(g =>
      `<div class="row"><span class="badge uncertain">${g[1]}</span>
       <div class="t">${esc(g[0])}</div></div>`).join("")
      : `<div class="gap">Nothing the scan is willing to claim. That is not the same as
         nothing being wrong — see the gaps under Integrations.</div>`}
    <p class="gloss">uncertain means the scan found no evidence either way. It is not a
    claim that anything is dead.</p>`;
}

function rowsHtml(rows) {
  return rows.map(r => `<div class="row">
    <span class="badge ${r.status}">${r.status}</span>
    <div class="t">${esc(r.label)}</div>
    <div class="w">${esc(r.kind)}${r.file ? " · " + esc(r.file) + (r.line ? ":" + r.line : "") : ""}</div>
    ${r.note ? `<div class="n">${esc(r.note)}</div>` : ""}</div>`).join("");
}

function render() {
  [...nav.children].forEach((el, i) =>
    el.setAttribute("aria-current", ITEMS[i].key === section));
  if (section === "overview") { main.innerHTML = overview(); return; }

  const s = D.sections.find(x => x.key === section);
  if (!s) return;
  if (s.unavailable) {
    main.innerHTML = `<h2>${esc(s.title)}</h2><p class="blurb">${esc(s.blurb)}</p>
      <div class="gap">${esc(s.unavailable)}</div>`;
    return;
  }
  const q = query.toLowerCase();
  const filtered = s.rows.filter(r =>
    (!status || r.status === status) &&
    (!q || (r.label + " " + r.file + " " + r.kind).toLowerCase().includes(q)));
  const page = filtered.slice(0, shown);
  main.innerHTML = `<h2>${esc(s.title)}</h2><p class="blurb">${esc(s.blurb)}</p>
    <div class="tools">
      <input id="q" type="search" placeholder="Filter ${filtered.length} rows" value="${esc(query)}">
      <select id="st">
        <option value="">any status</option>
        ${["unresolved","unused","uncertain","connected"].map(v =>
          `<option value="${v}"${v === status ? " selected" : ""}>${v}</option>`).join("")}
      </select>
    </div>
    ${page.length ? rowsHtml(page) : `<div class="gap">No rows match.</div>`}
    ${filtered.length > page.length
      ? `<div class="more" id="more">Show more — ${page.length} of ${filtered.length}</div>` : ""}`;

  const qi = document.getElementById("q");
  qi.oninput = e => { query = e.target.value; shown = PAGE; render();
                      const n = document.getElementById("q"); n.focus();
                      n.setSelectionRange(n.value.length, n.value.length); };
  document.getElementById("st").onchange = e => { status = e.target.value; shown = PAGE; render(); };
  const more = document.getElementById("more");
  if (more) more.onclick = () => { shown += PAGE * 4; render(); };
}

render();
"""


def _esc(value) -> str:
    return html_lib.escape(str(value if value is not None else ""))


def _payload(console: Console) -> str:
    data = {
        "backend": console.backend,
        "frontend": console.frontend,
        "counts": console.counts,
        "groups": console.groups,
        "sections": [
            {
                "key": s.key, "title": s.title, "blurb": s.blurb, "unavailable": s.unavailable,
                "rows": [
                    {
                        "id": r.id, "label": r.label, "kind": r.kind, "status": r.status,
                        "file": r.file, "line": r.line, "note": r.note,
                    }
                    for r in s.rows
                ],
            }
            for s in console.sections
        ],
    }
    return json.dumps(data).replace("</", "<\\/")


def render(console: Console) -> str:
    # A baseline equal to the scanned commit is not a diff - saying "diff vs <same sha>"
    # reads as a bug to anyone who looks at the two strings.
    is_diff = bool(console.baseline_sha) and console.baseline_sha != console.git_sha
    mode = f"diff vs {_esc(console.baseline_sha[:12])}" if is_diff else "current"
    return "\n".join([
        "<!doctype html>",
        '<html lang="en"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>Signal Map — {_esc(console.git_sha[:12])}</title>",
        f"<style>{_CSS}</style></head><body>",
        '<div class="shell"><aside class="side">',
        "<h1>Signal Map</h1>",
        f'<div class="meta">{_esc(console.git_sha[:12])} · {_esc(mode)}<br>'
        f"{_esc(console.generated_at)}</div>",
        '<button class="menu" id="menu" type="button">Sections</button>',
        '<nav class="nav" id="nav"></nav>',
        '</aside><main class="main" id="main"></main></div>',
        f"<script>const CONSOLE={_payload(console)};</script>",
        f"<script>{_SCRIPT}</script>",
        "</body></html>",
    ])
