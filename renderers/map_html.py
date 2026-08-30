"""The visual map: one self-contained file, no network, pan/zoom/click.

Laid out left to right by kind, so the axis you read along IS the frontend-to-backend
seam: page -> module -> call -> endpoint -> URL -> view -> what the view returns. A
force-directed layout would need a library this file is not allowed to fetch, and would
hide the one relationship the map exists to show.
"""

from __future__ import annotations

import html as html_lib
import json

from signal_map.mapdata import ConnectivityMap

# Column order is the story: browser on the left, database on the right.
_COLUMNS = [
    ("page", "Page"),
    ("module", "Module"),
    ("js_call", "JS call"),
    ("dom_selector", "DOM"),
    ("multi_writer_element", "Multi-writer"),
    ("fetch_target", "Endpoint"),
    ("url", "URL"),
    ("view", "View"),
    ("json_field", "Field"),
    ("dom_attr", "Template"),
    ("css_selector", "CSS"),
    ("css_token_def", "Token"),
    ("css_token_use", "var()"),
]

_CSS = """
:root { --bg:#faf9f6; --panel:#fff; --ink:#14171c; --muted:#5d6673; --line:#e1e0db;
        --sig:#1f7a8c; --ok:#2e7d5b; --crit:#a93b4b; --warn:#a8681b; --dim:#98a1ae; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#101317; --panel:#191d23; --ink:#e8ebef; --muted:#98a1ae; --line:#2a2f37;
          --sig:#4fb3c4; --ok:#56b98c; --crit:#e0788a; --warn:#d69b4c; --dim:#6e7885; }
}
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--ink); font-size:14px;
       font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif; }
.shell { display:flex; height:100vh; }
.side { width:290px; flex:none; border-right:1px solid var(--line); background:var(--panel);
        display:flex; flex-direction:column; overflow:hidden; }
.side h1 { font-size:16px; margin:0; padding:14px 14px 4px; }
.meta { padding:0 14px 10px; color:var(--muted); font-size:12px;
        font-family:ui-monospace,SFMono-Regular,Menlo,monospace; word-break:break-all; }
.pages { overflow-y:auto; flex:1; border-top:1px solid var(--line); }
.pg { padding:9px 14px; cursor:pointer; border-bottom:1px solid var(--line); font-size:13px; }
.pg:hover { background:var(--bg); }
.pg[aria-selected="true"] { background:var(--bg); border-left:3px solid var(--sig); font-weight:600; }
.pg .n { color:var(--muted); font-size:11px; }
.grp { padding:12px 14px 5px; border-bottom:1px solid var(--line); background:var(--bg); }
.grp .t { font-size:13px; font-weight:700; }
.grp .w { font-size:11px; color:var(--muted); margin-top:2px; word-break:break-all;
          font-family:ui-monospace,Menlo,monospace; }
.pg { padding-left:22px; }
.detail { border-top:1px solid var(--line); padding:12px 14px; max-height:42%; overflow-y:auto; }
.detail h2 { font-size:13px; margin:0 0 6px; word-break:break-all; }
.detail .row { color:var(--muted); font-size:12px; margin-bottom:4px;
               font-family:ui-monospace,Menlo,monospace; word-break:break-all; }
.detail pre { background:var(--bg); border:1px solid var(--line); border-radius:6px;
              padding:8px; font-size:11.5px; overflow-x:auto; margin:8px 0 0; }
.detail .note { font-size:12.5px; color:var(--muted); margin-top:8px; font-family:inherit; }
.main { flex:1; position:relative; overflow:hidden; }
.bar { position:absolute; top:0; left:0; right:0; padding:8px 12px; display:flex; gap:8px;
       align-items:center; background:var(--panel); border-bottom:1px solid var(--line); z-index:2; }
.crumb { font-size:12.5px; color:var(--muted); white-space:nowrap; overflow:hidden;
         text-overflow:ellipsis; max-width:40%; }
.bar input { flex:1; padding:6px 9px; font-size:13px; border:1px solid var(--line);
             border-radius:6px; background:var(--bg); color:var(--ink); }
.bar button { padding:6px 10px; font-size:12px; border:1px solid var(--line); border-radius:6px;
              background:var(--bg); color:var(--ink); cursor:pointer; }
/* An <svg> without an explicit height falls back to the replaced-element default of
   150px, silently clipping everything below it. */
svg { position:absolute; inset:41px 0 0 0; width:100%; height:calc(100% - 41px);
      cursor:grab; display:block; }
svg.drag { cursor:grabbing; }
.nd rect { stroke-width:1.5; }
.nd text { font-size:11px; fill:var(--ink); pointer-events:none;
           font-family:ui-monospace,Menlo,monospace; }
.nd { cursor:pointer; }
.nd.faded { opacity:.12; }
.ed { fill:none; stroke-width:1.2; opacity:.45; }
.ed.faded { opacity:.05; }
.col { font-size:10px; fill:var(--muted); text-transform:uppercase; letter-spacing:.08em; }
.legend { pointer-events:none; position:absolute; bottom:10px; right:12px; background:var(--panel);
          border:1px solid var(--line); border-radius:7px; padding:8px 10px; font-size:11px;
          color:var(--muted); z-index:2; }
.legend span { display:inline-block; width:9px; height:9px; border-radius:2px; margin-right:5px; }
.empty { position:absolute; inset:41px 0 0 0; display:flex; align-items:center;
         justify-content:center; color:var(--muted); }

/* A phone has no room for a 290px rail beside the canvas: side by side leaves 100px of
   map. Stack instead, and cap the list so the canvas keeps most of the screen. */
@media (max-width: 760px) {
  .shell { flex-direction:column; height:100dvh; }
  .side { width:auto; border-right:0; border-bottom:1px solid var(--line); max-height:45dvh; }
  .pages { max-height:26dvh; }
  .detail { max-height:none; }
  .main { flex:1; min-height:45dvh; }
  .bar { flex-wrap:wrap; }
  .crumb { max-width:100%; flex-basis:100%; }
  svg { inset:70px 0 0 0; height:calc(100% - 70px); }
  .legend { bottom:6px; right:6px; padding:5px 7px; font-size:10px; }
}
"""

_SCRIPT = r"""
const S = {connected:"var(--ok)", unresolved:"var(--crit)", unused:"var(--warn)", uncertain:"var(--dim)"};
const CH = {added:"var(--ok)", removed:"var(--crit)", status:"var(--warn)"};
const COLS = MAPDATA.columns, PAGES = MAPDATA.pages, CHANGED = MAPDATA.changed;
const ORDER = new Map(COLS.map((c, i) => [c[0], i]));
// Labels are raw project source: 163 of this project's URL patterns contain
// <path:object_id> and friends, which the parser eats as bogus elements, leaving a
// blank box. Everything interpolated into innerHTML goes through this.
const esc = v => String(v == null ? "" : v).replace(/[&<>"']/g,
  c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
let current = 0, focus = null, view = {x:0, y:0, k:1}, query = "";

const svg = document.getElementById("cv");
const detail = document.getElementById("detail");
const list = document.getElementById("pages");
const crumb = document.getElementById("crumb");
const byId = new Map();
PAGES.forEach(p => p.nodes.forEach(n => byId.set(n.id, n)));

// One heading per page a person recognises; the bundles that page loads sit under it.
// Several bundles share a page here (Push Arena loads nine), and nine identical rows
// tell a reader nothing about where they are.
let heading = null;
PAGES.forEach((p, i) => {
  const key = p.title + "\u0000" + p.where;
  if (key !== heading) {
    heading = key;
    const h = document.createElement("div");
    h.className = "grp";
    h.innerHTML = `<div class="t">${esc(p.title)}</div>` +
      (p.where ? `<div class="w">${esc(p.where)}</div>` : "");
    list.appendChild(h);
  }
  const el = document.createElement("div");
  el.className = "pg"; el.setAttribute("role", "option"); el.tabIndex = 0;
  el.dataset.i = i;
  el.innerHTML = `<div>${esc(p.page)}</div><div class="n">${p.nodes.length} nodes</div>`;
  el.onclick = () => { current = i; focus = null; view = {x:0,y:0,k:1}; draw(); };
  el.onkeydown = e => { if (e.key === "Enter") el.click(); };
  list.appendChild(el);
});

// Which nodes to draw. Without a focus a page shows only its modules - one page here
// has 839 symbols of a single kind, which stacked into a column 28,000px tall and was
// not explorable by any amount of scrolling. Clicking a module opens just its subgraph.
function visible(p) {
  const adj = new Map();
  p.edges.forEach(e => {
    if (!adj.has(e.source)) adj.set(e.source, []);
    if (!adj.has(e.target)) adj.set(e.target, []);
    adj.get(e.source).push(e.target);
    adj.get(e.target).push(e.source);
  });
  if (!focus) {
    const keep = new Set(p.nodes.filter(n => n.kind === "page" || n.kind === "module").map(n => n.id));
    return keep;
  }
  const keep = new Set([focus]);
  let front = [focus];
  for (let hop = 0; hop < 4 && front.length; hop++) {
    const next = [];
    front.forEach(id => (adj.get(id) || []).forEach(nb => {
      const node = byId.get(nb);
      if (!node || keep.has(nb) || node.kind === "module" || node.kind === "page") return;
      keep.add(nb); next.push(nb);
    }));
    front = next;
  }
  return keep;
}

function layout(p, keep) {
  const buckets = new Map();
  p.nodes.filter(n => keep.has(n.id)).forEach(n => {
    const c = ORDER.has(n.kind) ? ORDER.get(n.kind) : COLS.length;
    if (!buckets.has(c)) buckets.set(c, []);
    buckets.get(c).push(n);
  });
  const used = [...buckets.keys()].sort((a, b) => a - b);
  const pos = new Map();
  used.forEach((c, ci) => buckets.get(c).forEach((n, ri) =>
    pos.set(n.id, {x: 40 + ci * 210, y: 62 + ri * 30})));
  return {pos, columns: used.map((c, ci) => ({x: 40 + ci * 210, label: COLS[c] ? COLS[c][1] : "Other"}))};
}

const hit = n => !query || (n.label + " " + n.file).toLowerCase().includes(query);

function draw() {
  const p = PAGES[current];
  // Group headings share the list with the rows, so the row's own index travels on it.
  list.querySelectorAll(".pg").forEach(el =>
    el.setAttribute("aria-selected", Number(el.dataset.i) === current));
  if (!p) return;
  const here = p.where ? `${p.title} · ${p.where}` : p.title;
  crumb.textContent = focus
    ? `${here} › ${(byId.get(focus) || {}).label || ""}`
    : `${here} — pick a module`;
  document.getElementById("up").hidden = !focus;
  const keep = visible(p);
  const {pos, columns} = layout(p, keep);
  // A phone is narrower than two columns of this map, so the untouched view opens
  // zoomed out far enough to see the whole chain. Only the first draw of a view fits:
  // once someone pans or zooms, their view is theirs.
  if (view.k === 1 && view.x === 0 && view.y === 0) {
    const need = 40 + columns.length * 210 + 10, have = svg.clientWidth || 800;
    if (need > have) view.k = Math.max(0.4, have / need);
  }
  const out = [`<g transform="translate(${view.x},${view.y}) scale(${view.k})">`];
  columns.forEach(c => out.push(`<text class="col" x="${c.x}" y="44">${esc(c.label)}</text>`));
  p.edges.forEach(e => {
    const a = pos.get(e.source), b = pos.get(e.target);
    if (!a || !b) return;
    const mx = (a.x + 150 + b.x) / 2;
    out.push(`<path class="ed" stroke="${S[e.status] || "var(--dim)"}"
      d="M${a.x + 150},${a.y + 10} C${mx},${a.y + 10} ${mx},${b.y + 10} ${b.x},${b.y + 10}"/>`);
  });
  p.nodes.filter(n => keep.has(n.id)).forEach(n => {
    const q = pos.get(n.id); if (!q) return;
    const ch = CHANGED[n.id];
    const label = n.label.length > 20 ? n.label.slice(0, 19) + "…" : n.label;
    out.push(`<g class="nd${hit(n) ? "" : " faded"}" data-id="${n.id}">
      <rect x="${q.x}" y="${q.y}" width="150" height="20" rx="5" fill="var(--panel)"
            stroke="${ch ? CH[ch] : (S[n.status] || "var(--dim)")}" stroke-width="${ch ? 3 : 1.5}"/>
      <text x="${q.x + 7}" y="${q.y + 14}">${esc(label)}</text></g>`);
  });
  out.push("</g>");
  svg.innerHTML = out.join("");
}

// Delegated, not per-node: draw() replaces svg.innerHTML, and panning redraws on every
// mousemove, so a handler bound to a node is destroyed between mousedown and mouseup and
// the click never lands.
svg.addEventListener("click", e => {
  if (moved) return;
  const g = e.target.closest(".nd");
  if (!g) return;
  const n = byId.get(g.dataset.id);
  show(g.dataset.id);
  if (n && n.kind === "module") { focus = g.dataset.id; view = {x:0,y:0,k:1}; draw(); }
});

function show(id) {
  const n = byId.get(id); if (!n) return;
  const ch = CHANGED[id];
  detail.innerHTML = `<h2>${esc(n.label)}</h2>
    <div class="row">${esc(n.kind)} · ${esc(n.status)}${ch ? " · " + esc(ch) : ""}</div>
    ${n.file ? `<div class="row">${esc(n.file)}${n.line ? ":" + n.line : ""}</div>` : ""}
    ${n.note ? `<div class="note">${esc(n.note)}</div>` : ""}`;
}

let drag = null, moved = false;
svg.addEventListener("mousedown", e => {
  drag = {x: e.clientX - view.x, y: e.clientY - view.y, sx: e.clientX, sy: e.clientY};
  moved = false;
});
window.addEventListener("mouseup", () => { drag = null; svg.classList.remove("drag"); });
window.addEventListener("mousemove", e => {
  if (!drag) return;
  // A few pixels of hand-shake between press and release is a click, not a pan.
  if (!moved && Math.abs(e.clientX - drag.sx) + Math.abs(e.clientY - drag.sy) < 4) return;
  moved = true;
  svg.classList.add("drag");
  view.x = e.clientX - drag.x; view.y = e.clientY - drag.y; draw();
});
svg.addEventListener("wheel", e => {
  e.preventDefault();
  view.k = Math.min(3, Math.max(0.2, view.k * (e.deltaY < 0 ? 1.1 : 0.9)));
  draw();
}, {passive: false});
document.getElementById("q").addEventListener("input", e => {
  query = e.target.value.trim().toLowerCase(); draw();
});
document.getElementById("up").onclick = () => { focus = null; view = {x:0,y:0,k:1}; draw(); };
draw();
"""


def _esc(value) -> str:
    return html_lib.escape(str(value if value is not None else ""))


def _payload(connectivity_map: ConnectivityMap) -> str:
    data = {
        "columns": _COLUMNS,
        "changed": connectivity_map.changed,
        "pages": [
            {
                "page": page.page,
                "title": page.title or page.page,
                "where": page.where,
                "nodes": [
                    {
                        "id": node.id, "label": node.label, "kind": node.kind,
                        "status": node.status, "file": node.file, "line": node.line,
                        "note": node.note, "snippet": "",
                    }
                    for node in page.nodes
                ],
                "edges": [
                    {"source": e.source, "target": e.target, "status": e.status}
                    for e in page.edges
                ],
            }
            for page in connectivity_map.pages
        ],
    }
    # </script> inside JSON would close the tag early; escaping the slash is the
    # standard defence and stays valid JSON.
    return json.dumps(data).replace("</", "<\\/")


def render(connectivity_map: ConnectivityMap) -> str:
    mode = (
        f"diff vs {_esc(connectivity_map.baseline_sha[:12])}"
        if connectivity_map.baseline_sha
        else "current"
    )
    legend = "".join(
        f'<div><span style="background:{colour}"></span>{name}</div>'
        for name, colour in (
            ("connected", "var(--ok)"), ("unresolved", "var(--crit)"),
            ("unused", "var(--warn)"), ("uncertain", "var(--dim)"),
        )
    )
    return "\n".join([
        "<!doctype html>",
        '<html lang="en"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>Signal Map — {_esc(connectivity_map.git_sha[:12])}</title>",
        f"<style>{_CSS}</style></head><body>",
        '<div class="shell"><aside class="side">',
        "<h1>Signal Map</h1>",
        f'<div class="meta">{_esc(connectivity_map.git_sha[:12])} · {_esc(mode)}<br>'
        f"{_esc(connectivity_map.generated_at)}</div>",
        '<div class="pages" id="pages" role="listbox" aria-label="Pages"></div>',
        '<div class="detail" id="detail">Select a node to see its evidence.</div>',
        "</aside><main class=\"main\">",
        '<div class="bar"><button id="up" type="button" hidden>\u2190 Back</button>'
        '<span id="crumb" class="crumb"></span>'
        '<input id="q" type="search" placeholder="Filter this view"></div>',
        '<svg id="cv"></svg>',
        f'<div class="legend">{legend}</div>',
        "</main></div>",
        f"<script>const MAPDATA={_payload(connectivity_map)};</script>",
        f"<script>{_SCRIPT}</script>",
        "</body></html>",
    ])
