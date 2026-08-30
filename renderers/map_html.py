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
/* An author `display` beats the UA rule that [hidden] relies on, so el.hidden = true read
   back as true while the zoom buttons and the breadcrumb stayed on screen over the panel. */
[hidden] { display:none !important; }
/* The canvas is the point. Everything else is a strip above it, and the whole document
   is exactly one screen tall so nothing scrolls the map out of view. */
body { margin:0; background:var(--bg); color:var(--ink); font-size:14px; overflow:hidden;
       height:100dvh;
       font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif; }
.shell { display:flex; height:100%; }
.content { flex:1 1 auto; min-width:0; display:flex; flex-direction:column; }
/* The rail is the desktop's navigation. A phone has no room for it and uses the VIEW
   select instead; both drive the same switch, from one list of items. */
.rail { display:none; }
.top { flex:none; background:var(--panel); border-bottom:1px solid var(--line); }
.brand { display:flex; align-items:baseline; gap:9px; padding:8px 12px 6px; }
.brand b { font-size:14px; }
.brand .meta { color:var(--muted); font-size:11px; overflow:hidden; white-space:nowrap;
               text-overflow:ellipsis; font-family:ui-monospace,Menlo,monospace; }

/* Two filters, side by side: what changed on the left, where to look on the right. */
.filters { display:flex; gap:8px; padding:0 12px 8px; }
.filters label { flex:1 1 0; min-width:0; display:block; }
.filters span { display:block; font-size:9.5px; text-transform:uppercase;
                letter-spacing:.09em; color:var(--muted); margin-bottom:3px; }
.filters select { width:100%; padding:9px 8px; font-size:13px; border-radius:8px;
                  border:1px solid var(--line); background:var(--bg); color:var(--ink); }
.crumbrow { display:flex; align-items:center; gap:7px; padding:0 12px 8px; }
.crumbrow button { flex:none; padding:7px 10px; font-size:12px; border-radius:8px;
                   border:1px solid var(--line); background:var(--bg); color:var(--ink);
                   cursor:pointer; }
#crumb { flex:1 1 auto; min-width:0; font-size:12px; color:var(--muted);
         white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
#q { flex:0 1 130px; min-width:78px; padding:7px 9px; font-size:13px; border-radius:8px;
     border:1px solid var(--line); background:var(--bg); color:var(--ink); }
.note { padding:0 12px 8px; font-size:11.5px; color:var(--muted); }
.note:empty { display:none; }
/* A deleted symbol is in no current page, so no canvas can show it. Naming it here is
   the difference between "this commit removed one selector" and an empty screen. */
.gone { padding:0 12px 9px; font-size:11.5px; }
.gone:empty { display:none; }
.gone b { display:block; font-size:9.5px; text-transform:uppercase; letter-spacing:.09em;
          color:var(--muted); margin-bottom:3px; font-weight:500; }
.gone .ch { font-family:ui-monospace,Menlo,monospace; word-break:break-all;
            margin-bottom:3px; color:var(--ink); }
.gone .ch i { display:inline-block; min-width:62px; font-style:normal; font-size:10px;
              text-transform:uppercase; letter-spacing:.05em; }
.gone .ch.added i { color:var(--ok); }
.gone .ch.removed i { color:var(--crit); }
.gone .ch.status i { color:var(--warn); }
.gone .ch span { color:var(--muted); }
.gone { max-height:26dvh; overflow-y:auto; }

.main { flex:1 1 auto; position:relative; min-height:0; }
svg { position:absolute; inset:0; width:100%; height:100%; display:block;
      cursor:grab; touch-action:none; }
svg.drag { cursor:grabbing; }
.nd rect { stroke-width:1.5; }
.nd text { font-size:11px; fill:var(--ink); pointer-events:none;
           font-family:ui-monospace,Menlo,monospace; }
.nd { cursor:pointer; }
.nd.faded { opacity:.12; }
.ed { fill:none; stroke-width:1.2; opacity:.45; }
.ed.faded { opacity:.05; }
.col { font-size:10px; fill:var(--muted); text-transform:uppercase; letter-spacing:.08em; }

.zoom { position:absolute; left:10px; bottom:10px; display:flex; gap:6px; z-index:2; }
.zoom button, .key { width:40px; height:40px; font-size:15px; line-height:1;
                     border-radius:10px; border:1px solid var(--line);
                     background:var(--panel); color:var(--ink); cursor:pointer; }
.key { position:absolute; right:10px; bottom:10px; z-index:2; }
/* Pinned over the canvas, the legend sat on top of the nodes it explains. It opens now
   only when asked, and closes by tapping anywhere. */
.legend { position:absolute; right:10px; bottom:58px; background:var(--panel);
          border:1px solid var(--line); border-radius:9px; padding:9px 11px;
          font-size:11.5px; color:var(--muted); z-index:3; pointer-events:none; }
.legend span { display:inline-block; width:9px; height:9px; border-radius:2px; margin-right:6px; }

/* Evidence arrives as a sheet over the canvas, not as a permanent rail stealing height. */
.sheet { position:absolute; left:0; right:0; bottom:0; z-index:4; background:var(--panel);
         border-top:1px solid var(--line); padding:12px 14px 14px; max-height:52%;
         overflow-y:auto; box-shadow:0 -6px 24px -18px rgba(0,0,0,.6); }
.sheet h2 { font-size:13px; margin:0 26px 6px 0; word-break:break-all; }
.sheet .row { color:var(--muted); font-size:12px; margin-bottom:4px;
              font-family:ui-monospace,Menlo,monospace; word-break:break-all; }
.sheet .note { padding:0; margin-top:8px; font-family:inherit; }
.sheet .lbl { font-size:9.5px; text-transform:uppercase; letter-spacing:.09em;
              color:var(--muted); margin:14px 0 6px; }
/* One row per hop, joined by a rule down the left, so the walk reads as a route. */
.hop { border-left:2px solid var(--line); padding:0 0 9px 10px; position:relative; }
.hop.at { border-left-color:var(--sig); }
.hop .hk { font-size:9.5px; text-transform:uppercase; letter-spacing:.07em; color:var(--muted); }
.hop .hl { font-size:12.5px; font-family:ui-monospace,Menlo,monospace; word-break:break-all; }
.hop.at .hl { font-weight:700; color:var(--sig); }
.hop .hf { font-size:11px; color:var(--muted); word-break:break-all;
           font-family:ui-monospace,Menlo,monospace; }
.hop pre { margin:5px 0 0; padding:6px 8px; background:var(--bg); border:1px solid var(--line);
           border-radius:6px; font-size:11px; overflow-x:auto; white-space:pre-wrap;
           word-break:break-all; }
.sheet .x { position:absolute; top:8px; right:10px; width:30px; height:30px;
            border-radius:8px; border:1px solid var(--line); background:var(--bg);
            color:var(--ink); cursor:pointer; }
.filters label.wide { flex:1 1 100%; }
.panel { position:absolute; inset:0; overflow-y:auto; padding:14px 12px 40px;
         background:var(--bg); }
.panel h2 { font-size:17px; margin:0 0 4px; }
.panel .blurb { color:var(--muted); font-size:12.5px; margin:0 0 12px; max-width:62ch; }
.panel .tools { display:flex; gap:8px; margin-bottom:12px; }
.panel .tools input { flex:1 1 auto; min-width:0; }
.panel input, .panel select { padding:8px 9px; font-size:13px; border-radius:8px;
                              border:1px solid var(--line); background:var(--panel);
                              color:var(--ink); }
.row { background:var(--panel); border:1px solid var(--line); border-radius:9px;
       padding:9px 11px; margin-bottom:7px; }
.row .t { font-size:13px; font-family:ui-monospace,Menlo,monospace; word-break:break-all; }
.row .w { color:var(--muted); font-size:11.5px; word-break:break-all;
          font-family:ui-monospace,Menlo,monospace; }
.row .n { color:var(--muted); font-size:12px; margin-top:4px; }
.badge, .pill { display:inline-block; font-size:10px; text-transform:uppercase;
                letter-spacing:.05em; border:1px solid var(--line); border-radius:20px;
                padding:1px 8px; margin-right:6px; }
.badge.connected, .pill.connected { color:var(--ok); }
.badge.unresolved, .pill.unresolved { color:var(--crit); }
.badge.unused, .pill.unused { color:var(--warn); }
.badge.uncertain, .pill.uncertain { color:var(--dim); }
.cards { display:flex; gap:10px; flex-wrap:wrap; }
.card { flex:1 1 210px; background:var(--panel); border:1px solid var(--line);
        border-radius:11px; padding:12px 13px; }
.card .k { font-size:10px; text-transform:uppercase; letter-spacing:.08em; color:var(--muted); }
.card .v { font-size:24px; font-weight:700; margin:2px 0 7px; }
.gap { color:var(--muted); font-size:12.5px; border:1px dashed var(--line);
       border-radius:9px; padding:11px 12px; }
.more { text-align:center; padding:10px; border:1px solid var(--line); border-radius:9px;
        cursor:pointer; font-size:13px; color:var(--sig); }
.gloss { color:var(--muted); font-size:12px; margin-top:14px; }
.blank { position:absolute; inset:0; display:flex; align-items:center; padding:0 22px;
         color:var(--muted); font-size:13px; }

@media (min-width: 761px) {
  .brand, .filters, .crumbrow { padding-left:16px; padding-right:16px; }
  .filters { max-width:820px; }
  .onlymob { display:none; }
  .rail { display:flex; flex-direction:column; width:236px; flex:none; overflow-y:auto;
          background:var(--panel); border-right:1px solid var(--line); }
  .railhead { font-size:14px; font-weight:700; padding:14px 16px 10px; }
  .nv { display:flex; justify-content:space-between; align-items:center; gap:8px;
        padding:9px 16px; cursor:pointer; border-top:1px solid var(--line);
        font-size:13px; }
  .nv:hover { background:var(--bg); }
  .nv[aria-current="true"] { background:var(--bg); box-shadow:inset 3px 0 0 var(--sig);
                             font-weight:600; }
  .nv .c { color:var(--muted); font-size:11px; font-variant-numeric:tabular-nums; }
  .sheet { left:auto; right:0; width:380px; top:0; bottom:auto; max-height:100%;
           border-top:0; border-left:1px solid var(--line); }
}
"""

_SCRIPT = r"""
const S = {connected:"var(--ok)", unresolved:"var(--crit)", unused:"var(--warn)", uncertain:"var(--dim)"};
const CH = {added:"var(--ok)", removed:"var(--crit)", status:"var(--warn)"};
const COLS = MAPDATA.columns, PAGES = MAPDATA.pages, COMMITS = MAPDATA.commits || [];
// Which changed-set is in force. Starts as the whole-run diff, and a commit selection
// replaces it - so "what changed" always means the thing the reader picked, never a
// blend of a commit and a branch.
let CHANGED = MAPDATA.changed, only = false;
const ORDER = new Map(COLS.map((c, i) => [c[0], i]));
// Labels are raw project source: 163 of this project's URL patterns contain
// <path:object_id> and friends, which the parser eats as bogus elements, leaving a
// blank box. Everything interpolated into innerHTML goes through this.
const esc = v => String(v == null ? "" : v).replace(/[&<>"']/g,
  c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
let current = 0, focus = null, view = {x:0, y:0, k:1}, query = "";

const svg = document.getElementById("cv");
const sheet = document.getElementById("detail");
const dbody = document.getElementById("dbody");
const pages = document.getElementById("pg");
const crumb = document.getElementById("crumb");
const byId = new Map();
PAGES.forEach(p => p.nodes.forEach(n => byId.set(n.id, n)));

// The pages a reader recognises, each holding the bundles it loads. A scrolling rail of
// 34 rows cost more than half a phone screen; an optgroup says the same thing in one
// control and gives every pixel of it back to the canvas.
function fillPages(counts) {
  let group = null, heading = null, out = [];
  PAGES.forEach((p, i) => {
    const key = p.title + "\u0000" + p.where;
    if (key !== heading) {
      if (group) out.push(group + "</optgroup>");
      heading = key;
      group = `<optgroup label="${esc(p.title)}${p.where ? " · " + esc(p.where) : ""}">`;
    }
    const tail = counts ? `${counts[i]} changed` : `${p.nodes.length} nodes`;
    group += `<option value="${i}">${esc(p.page)} — ${tail}</option>`;
  });
  if (group) out.push(group + "</optgroup>");
  pages.innerHTML = out.join("");
  pages.value = String(current);
}

pages.onchange = e => {
  current = Number(e.target.value); focus = null; view = {x:0, y:0, k:1};
  closeSheet(); draw();
};

// Which nodes to draw. Without a focus a page shows only its modules - one page here
// has 839 symbols of a single kind, which stacked into a column 28,000px tall and was
// not explorable by any amount of scrolling. Clicking a module opens just its subgraph.
// A commit selection narrows every page to the nodes that commit touched, plus the
// page node itself so the column still has a root to hang from.
function changedIn(p) {
  return new Set(p.nodes.filter(n => CHANGED[n.id] || n.kind === "page").map(n => n.id));
}

function visible(p) {
  if (only) return changedIn(p);
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

// How many rows the deepest column holds, for the fit.
function rowsDeep(keep, pos) {
  const perColumn = new Map();
  keep.forEach(id => {
    const q = pos.get(id); if (!q) return;
    perColumn.set(q.x, (perColumn.get(q.x) || 0) + 1);
  });
  return Math.max(1, ...perColumn.values());
}

function draw() {
  const p = PAGES[current];
  if (!p) return;
  pages.value = String(current);
  const here = p.where ? `${p.title} · ${p.where}` : p.title;
  crumb.textContent = focus
    ? `${here} › ${(byId.get(focus) || {}).label || ""}`
    : `${here} — pick a module`;
  document.getElementById("up").hidden = !focus;
  // A commit that touched only files the scan does not read has an empty changed set.
  // Drawing that as a bare page node reads as a broken map rather than as an answer.
  if (only && !Object.keys(CHANGED).length) {
    svg.innerHTML = `<text x="20" y="40" class="col">nothing the scan reads changed in this commit</text>`;
    return;
  }
  const keep = visible(p);
  const {pos, columns} = layout(p, keep);
  // A phone is narrower than two columns of this map, so an untouched view opens showing
  // the whole chain, nudged clear of the left edge. Only the first draw of a view fits:
  // once someone pans or zooms, the view is theirs.
  if (view.k === 1 && view.x === 0 && view.y === 0) {
    const need = 40 + columns.length * 210 + 10;
    const haveW = svg.clientWidth || 800, haveH = svg.clientHeight || 600;
    if (need > haveW) view.k = Math.max(0.35, haveW / need);
    // Tall columns stay pannable rather than being shrunk to nothing; short ones get
    // centred, because a handful of nodes pinned to the top corner reads as a bug.
    // Short pages get a small top margin, not half a screen of nothing: centring 16
    // rows in an 800px canvas buried the map in dead space.
    const tall = 62 + rowsDeep(keep, pos) * 30;
    if (tall * view.k < haveH) view.y = Math.min(48, (haveH - tall * view.k) / 2);
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
    // The drawn box is 20px tall and the view opens scaled down, so on a phone the
    // visible target can be 8px. The transparent rect below it fills the row pitch.
    out.push(`<g class="nd${hit(n) ? "" : " faded"}" data-id="${n.id}">
      <rect x="${q.x - 5}" y="${q.y - 5}" width="160" height="30" fill="transparent"
            pointer-events="all"/>
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

function closeSheet() { sheet.hidden = true; }

// The route that arrives at a node, and the ones leaving it. A node on its own says
// "this exists"; the question a reader actually has is where the browser started and
// where it ended up, so the sheet reconstructs that walk from the edges.
function routes(id) {
  const p = PAGES[current];
  const back = new Map(), fwd = new Map();
  p.edges.forEach(e => {
    if (!back.has(e.target)) back.set(e.target, []);
    if (!fwd.has(e.source)) fwd.set(e.source, []);
    back.get(e.target).push(e.source);
    fwd.get(e.source).push(e.target);
  });
  // Shortest walk back to the page node: the frontend end of the chain.
  const seen = new Set([id]), queue = [[id]];
  let inbound = [id];
  while (queue.length) {
    const path = queue.shift(), head = path[path.length - 1];
    if ((byId.get(head) || {}).kind === "page") { inbound = path; break; }
    (back.get(head) || []).forEach(prev => {
      if (seen.has(prev)) return;
      seen.add(prev); queue.push([...path, prev]);
    });
  }
  // Edges are walkable both ways, so a node that reaches this one comes back as
  // something it reaches. Listing it twice makes the route look like a loop.
  const path = inbound.slice().reverse();
  const seenOnPath = new Set(path);
  return {inbound: path,
          outbound: (fwd.get(id) || []).filter(x => !seenOnPath.has(x)).slice(0, 12)};
}

function hop(id, here) {
  const n = byId.get(id); if (!n) return "";
  return `<div class="hop${id === here ? " at" : ""}">
    <div class="hk">${esc(n.kind)}</div>
    <div class="hl">${esc(n.label)}</div>
    ${n.file ? `<div class="hf">${esc(n.file)}${n.line ? ":" + n.line : ""}</div>` : ""}
    ${n.snippet ? `<pre>${esc(n.snippet)}</pre>` : ""}</div>`;
}

function show(id) {
  const n = byId.get(id); if (!n) return;
  const ch = CHANGED[id];
  const {inbound, outbound} = routes(id);
  dbody.innerHTML = `<h2>${esc(n.label)}</h2>
    <div class="row">${esc(n.kind)} · ${esc(n.status)}${ch ? " · " + esc(ch) : ""}</div>
    ${n.file ? `<div class="row">${esc(n.file)}${n.line ? ":" + n.line : ""}</div>` : ""}
    ${n.note ? `<div class="note">${esc(n.note)}</div>` : ""}
    <div class="lbl">Path — browser to backend</div>
    ${inbound.map(step => hop(step, id)).join("")}
    ${outbound.length ? `<div class="lbl">Reaches</div>${outbound.map(step => hop(step, id)).join("")}` : ""}`;
  sheet.hidden = false;
}
document.getElementById("dx").onclick = closeSheet;

// Pointer events, not mouse events: one code path covers a mouse, a finger and a pen.
// Listening for `mousedown` alone left a phone with no pan and no zoom at all, and the
// pointer is never captured - capture would retarget the click away from the node.
const ptrs = new Map();
let drag = null, moved = false, pinch = null;

const zoomTo = k => { view.k = Math.min(3, Math.max(0.2, k)); draw(); };

let lastTap = 0;
svg.addEventListener("pointerdown", e => {
  // Double-tap zooms. A phone has no wheel, and reaching the corner buttons mid-read
  // costs a thumb-shift; this is the gesture people already try.
  const now = Date.now();
  if (ptrs.size === 0 && now - lastTap < 320) { zoomTo(view.k * 1.6); lastTap = 0; }
  else lastTap = now;
  ptrs.set(e.pointerId, {x: e.clientX, y: e.clientY});
  if (ptrs.size === 2) {
    const [a, b] = [...ptrs.values()];
    pinch = {d: Math.hypot(a.x - b.x, a.y - b.y), k: view.k};
    drag = null; moved = true;  // two fingers are a gesture, never a tap
    return;
  }
  drag = {x: e.clientX - view.x, y: e.clientY - view.y, sx: e.clientX, sy: e.clientY};
  moved = false;
});
window.addEventListener("pointermove", e => {
  if (!ptrs.has(e.pointerId)) return;
  ptrs.set(e.pointerId, {x: e.clientX, y: e.clientY});
  if (pinch && ptrs.size === 2) {
    const [a, b] = [...ptrs.values()];
    const d = Math.hypot(a.x - b.x, a.y - b.y);
    if (pinch.d > 0) zoomTo(pinch.k * d / pinch.d);
    return;
  }
  if (!drag) return;
  // A few pixels between press and release is a tap, not a pan. A finger wobbles more
  // than a mouse, so the threshold is wider than a mouse alone would need.
  if (!moved && Math.abs(e.clientX - drag.sx) + Math.abs(e.clientY - drag.sy) < 6) return;
  moved = true;
  svg.classList.add("drag");
  view.x = e.clientX - drag.x; view.y = e.clientY - drag.y; draw();
});
const release = e => {
  ptrs.delete(e.pointerId);
  if (ptrs.size < 2) pinch = null;
  if (ptrs.size === 0) { drag = null; svg.classList.remove("drag"); }
};
window.addEventListener("pointerup", release);
window.addEventListener("pointercancel", release);

svg.addEventListener("wheel", e => {
  e.preventDefault();
  zoomTo(view.k * (e.deltaY < 0 ? 1.1 : 0.9));
}, {passive: false});

// A pinch needs two fingers and some dexterity; these need one thumb.
document.getElementById("zi").onclick = () => zoomTo(view.k * 1.25);
document.getElementById("zo").onclick = () => zoomTo(view.k / 1.25);
document.getElementById("zf").onclick = () => { view = {x:0, y:0, k:1}; draw(); };
document.getElementById("q").addEventListener("input", e => {
  query = e.target.value.trim().toLowerCase(); draw();
});
// --- the commit picker -------------------------------------------------------------
const picker = document.getElementById("cm"), note = document.getElementById("cmnote");
const gone = document.getElementById("gone");

// "2026-08-30T14:18:06+02:00" -> "2026-08-30 14:18". The date alone cannot separate two
// commits made the same afternoon, which is most of them.
const when = iso => String(iso || "").replace("T", " ").slice(0, 16);

function fillPicker() {
  const opts = [`<option value="">Everything in this scan</option>`];
  COMMITS.forEach((c, i) => opts.push(
    `<option value="${i}">${c.head ? "HEAD · " : ""}${esc(c.sha.slice(0, 8))} · ` +
    `${esc(when(c.date))} · ${esc(c.subject)}</option>`));
  picker.innerHTML = opts.join("");
  if (!COMMITS.length) {
    picker.disabled = true;
    note.textContent = "Only this commit has been scanned. Run --backfill to build history.";
  }
}

function countsFor(changed) {
  const n = {added: 0, removed: 0, status: 0};
  Object.values(changed).forEach(kind => { n[kind] = (n[kind] || 0) + 1; });
  return n;
}

function selectCommit(index) {
  const c = COMMITS[index];
  if (!c) {
    CHANGED = MAPDATA.changed; only = false;
    note.textContent = ""; gone.innerHTML = "";
  } else {
    CHANGED = c.changed; only = true;
    const n = countsFor(c.changed);
    const total = n.added + n.removed + n.status;
    note.textContent = !c.baseline
      ? `${when(c.date)} — earliest scanned commit, nothing before it to compare against.`
      : total === 0
      ? `${when(c.date)} — no scanned symbol changed since ${c.baseline.slice(0, 8)}.`
      : `${when(c.date)} · ${n.added} added · ${n.removed} removed · ` +
        `${n.status} changed status, vs ${c.baseline.slice(0, 8)}`;
    // Every change, named. The canvas draws today's code, so it shows only the ones
    // that survived to today; this list is the whole commit either way.
    const list = c.changes || [], more = (c.change_total || list.length) - list.length;
    gone.innerHTML = list.length
      ? `<b>What this commit changed</b>` + list.map(d =>
          `<div class="ch ${esc(d.change)}"><i>${esc(d.change)}</i> ${esc(d.label)}` +
          ` <span>${esc(d.kind)}${d.file ? " · " + esc(d.file) +
          (d.line ? ":" + d.line : "") : ""}</span></div>`
        ).join("") + (more > 0 ? `<div class="ch"><i></i>+${more} more</div>` : "")
      : "";
  }
  // Every page says how much of this commit is in it, so the picker itself shows where
  // to look instead of making a reader open each page to find out.
  fillPages(only ? PAGES.map(p => p.nodes.filter(n => CHANGED[n.id]).length) : null);
  focus = null; view = {x:0, y:0, k:1};
  closeSheet();
  draw();
}

picker.onchange = e => selectCommit(e.target.value === "" ? -1 : Number(e.target.value));
fillPicker();
fillPages(null);
// The newest commit sits first in the list and is marked HEAD, so "what did the last
// commit do" is one tap away - but it is not the opening view. A commit whose only change
// was a deletion has nothing left to draw, and opening there showed an empty canvas.

// --- the colour key, out of the canvas's way until asked for -------------------------
const legendBox = document.getElementById("legend");
document.getElementById("lg").onclick = e => {
  e.stopPropagation(); legendBox.hidden = !legendBox.hidden;
};
document.addEventListener("pointerdown", () => { legendBox.hidden = true; });

// --- the review views, in the same shell -------------------------------------------
// One surface, not two: a second document meant a second render, a second link and a
// second mental model for the same scan. The map answers "what reaches what"; these
// answer "how many, of what kind, where" - a switch, not a separate page.
const D = CONSOLE, panel = document.getElementById("panel");
const pgwrap = document.getElementById("pgwrap"), viewer = document.getElementById("vw");
const ROWS_PER_PAGE = 60;
let mode = "map", cq = "", cstatus = "", shown = ROWS_PER_PAGE;
// The map is what this page is for, so it opens on the map with the whole scan drawn.
// The commit filter therefore starts at "everything": defaulting it to the newest commit
// left the canvas holding one node, because a commit that only deletes something has
// nothing left to draw - which reads as a page with no nodes in it.
const OPENS_ON = "map";

const VIEWS = [{key: "map", title: "Map — what reaches what", count: null},
               {key: "overview", title: "Overview", count: null}].concat(
  D.sections.map(sec => ({key: sec.key, title: sec.title,
                          count: sec.unavailable ? null : (sec.total ?? sec.rows.length)})));

viewer.innerHTML = VIEWS.map(v =>
  `<option value="${esc(v.key)}">${esc(v.title)}` +
  `${v.count === null ? "" : ` — ${v.count}`}</option>`).join("");

// The same items as a rail, for a screen with room for one. Built from VIEWS, not from a
// second copy of the list: two navigations that can disagree is how a menu goes stale.
const rail = document.getElementById("nav");
rail.innerHTML = VIEWS.map(v =>
  `<div class="nv" role="link" tabindex="0" data-key="${esc(v.key)}">
     <span>${esc(v.title)}</span>
     <span class="c">${v.count === null ? "—" : v.count}</span></div>`).join("");
rail.querySelectorAll(".nv").forEach(el => {
  el.onclick = () => { viewer.value = el.dataset.key; switchTo(el.dataset.key); };
  el.onkeydown = e => { if (e.key === "Enter") el.click(); };
});

function pills(counts) {
  return ["connected","unresolved","unused","uncertain"]
    .map(k => `<span class="pill ${k}">${k} ${counts[k] || 0}</span>`).join("");
}

function overviewHtml() {
  const total = c => Object.values(c).reduce((a, n) => a + n, 0);
  return `<h2>Overview</h2><p class="blurb">Two totals, each broken into the four
    statuses. Every number is what the scan is willing to claim about this commit.</p>
    <div class="cards">
      <div class="card"><div class="k">Backend symbols</div><div class="v">${total(D.backend)}</div>
        ${pills(D.backend)}</div>
      <div class="card"><div class="k">Frontend symbols</div><div class="v">${total(D.frontend)}</div>
        ${pills(D.frontend)}</div></div>
    <h2 style="font-size:14px;margin:18px 0 8px">Backlog by kind</h2>
    ${D.groups.length ? D.groups.map(g =>
      `<div class="row"><span class="badge uncertain">${g[1]}</span>
       <div class="t">${esc(g[0])}</div></div>`).join("")
      : `<div class="gap">Nothing the scan is willing to claim.</div>`}
    <p class="gloss">uncertain means the scan found no evidence either way. It is not a
    claim that anything is dead.</p>`;
}

function renderPanel() {
  if (mode === "overview") { panel.innerHTML = overviewHtml(); return; }
  const sec = D.sections.find(x => x.key === mode);
  if (!sec) return;
  if (sec.unavailable) {
    panel.innerHTML = `<h2>${esc(sec.title)}</h2><p class="blurb">${esc(sec.blurb)}</p>
      <div class="gap">${esc(sec.unavailable)}</div>`;
    return;
  }
  const needle = cq.toLowerCase();
  const rows = sec.rows.filter(r => (!cstatus || r.status === cstatus) &&
    (!needle || (r.label + " " + r.file + " " + r.kind).toLowerCase().includes(needle)));
  const page = rows.slice(0, shown);
  panel.innerHTML = `<h2>${esc(sec.title)}</h2><p class="blurb">${esc(sec.blurb)}</p>
    <div class="tools">
      <input id="cq" type="search" placeholder="Filter ${rows.length} rows" value="${esc(cq)}">
      <select id="cst"><option value="">any status</option>
        ${["unresolved","unused","uncertain","connected"].map(v =>
          `<option value="${v}"${v === cstatus ? " selected" : ""}>${v}</option>`).join("")}
      </select></div>
    ${page.map(r => `<div class="row"><span class="badge ${esc(r.status)}">${esc(r.status)}</span>
       <div class="t">${esc(r.label)}</div>
       <div class="w">${esc(r.kind)}${r.file ? " · " + esc(r.file) +
         (r.line ? ":" + r.line : "") : ""}</div>
       ${r.note ? `<div class="n">${esc(r.note)}</div>` : ""}</div>`).join("")
      || `<div class="gap">No rows match.</div>`}
    ${rows.length > page.length
      ? `<div class="more" id="cmore">Show more — ${page.length} of ${rows.length}</div>` : ""}
    ${sec.total > sec.rows.length ? `<div class="gloss">Showing the first
      ${sec.rows.length} of ${sec.total}. The rest are in the CLI:
      <code>--check --format markdown</code>.</div>` : ""}`;

  const box = document.getElementById("cq");
  box.oninput = e => {
    cq = e.target.value; shown = ROWS_PER_PAGE; renderPanel();
    const again = document.getElementById("cq");
    again.focus(); again.setSelectionRange(again.value.length, again.value.length);
  };
  document.getElementById("cst").onchange = e => {
    cstatus = e.target.value; shown = ROWS_PER_PAGE; renderPanel();
  };
  const more = document.getElementById("cmore");
  if (more) more.onclick = () => { shown += ROWS_PER_PAGE * 4; renderPanel(); };
}

function switchTo(next) {
  mode = next;
  rail.querySelectorAll(".nv").forEach(el =>
    el.setAttribute("aria-current", el.dataset.key === next));
  const isMap = mode === "map";
  panel.hidden = isMap; svg.style.display = isMap ? "" : "none";
  document.querySelector(".zoom").hidden = !isMap;
  document.getElementById("lg").hidden = !isMap;
  document.querySelector(".crumbrow").hidden = !isMap;
  pgwrap.hidden = !isMap;
  closeSheet();
  cq = ""; cstatus = ""; shown = ROWS_PER_PAGE;
  if (isMap) draw(); else renderPanel();
}

viewer.onchange = e => switchTo(e.target.value);

document.getElementById("up").onclick = () => { focus = null; view = {x:0,y:0,k:1}; draw(); };
draw();
viewer.value = OPENS_ON;
switchTo(OPENS_ON);
"""


def _esc(value) -> str:
    return html_lib.escape(str(value if value is not None else ""))


def _payload(connectivity_map: ConnectivityMap) -> str:
    data = {
        "columns": _COLUMNS,
        "changed": connectivity_map.changed,
        "commits": connectivity_map.commits,
        "pages": [
            {
                "page": page.page,
                "title": page.title or page.page,
                "where": page.where,
                "nodes": [
                    {
                        "id": node.id, "label": node.label, "kind": node.kind,
                        "status": node.status, "file": node.file, "line": node.line,
                        "note": node.note, "snippet": node.snippet,
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


def _console_payload(console) -> str:
    """The review sections, or an empty shell so the page still renders without them."""
    from dataclasses import asdict

    if console is None:
        return json.dumps({"backend": {}, "frontend": {}, "groups": [], "sections": []})
    # Rows carry a snippet the panel never draws, and a section can hold 1,500 of them.
    # Shipping all of it cost 1.6 MB on a page a phone opens over a tunnel; the reader
    # filters to find a row, and is told when a section is longer than what was sent.
    limit = 400

    def _section(section):
        out = asdict(section)
        rows = out.pop("rows")
        out["rows"] = [{k: v for k, v in row.items() if k != "snippet"} for row in rows[:limit]]
        out["total"] = len(rows)
        return out

    data = {
        "backend": console.backend, "frontend": console.frontend,
        "groups": [[title, count, gloss] for title, count, gloss in console.groups],
        "sections": [_section(section) for section in console.sections],
    }
    return json.dumps(data).replace("</", "<\\/")


def render(connectivity_map: ConnectivityMap, console=None) -> str:
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
        '<meta name="viewport" content="width=device-width, initial-scale=1, '
        'viewport-fit=cover">',
        f"<title>Signal Map — {_esc(connectivity_map.git_sha[:12])}</title>",
        f"<style>{_CSS}</style></head><body>",
        '<div class="shell"><aside class="rail">'
        '<div class="railhead">Signal Map</div><div class="nav" id="nav"></div></aside>'
        '<div class="content">'
        '<header class="top">',
        f'<div class="brand"><b>Signal Map</b>'
        f'<span class="meta">HEAD {_esc(connectivity_map.git_sha[:12])} · {_esc(mode)}</span>'
        f"</div>",
        '<div class="filters onlymob"><label class="wide"><span>View</span>'
        '<select id="vw"></select></label></div>',
        '<div class="filters">'
        '<label><span>Commit</span><select id="cm"></select></label>'
        '<label id="pgwrap"><span>Page</span><select id="pg"></select></label></div>',
        '<div class="crumbrow"><button id="up" type="button" hidden>\u2190</button>'
        '<span id="crumb"></span>'
        '<input id="q" type="search" placeholder="Filter"></div>',
        '<div class="note" id="cmnote"></div><div class="gone" id="gone"></div>',
        "</header>",
        '<main class="main">',
        '<svg id="cv"></svg>',
        '<div class="panel" id="panel" hidden></div>',
        '<div class="zoom"><button id="zo" type="button" aria-label="Zoom out">\u2212</button>'
        '<button id="zi" type="button" aria-label="Zoom in">+</button>'
        '<button id="zf" type="button" aria-label="Fit to screen">\u2316</button></div>',
        '<button class="key" id="lg" type="button" aria-label="Colour key">?</button>',
        f'<div class="legend" id="legend" hidden>{legend}</div>',
        '<aside class="sheet" id="detail" hidden>'
        '<button class="x" id="dx" type="button" aria-label="Close">\u00d7</button>'
        '<div id="dbody"></div></aside>',
        "</main></div></div>",
        f"<script>const MAPDATA={_payload(connectivity_map)};</script>",
        f"<script>const CONSOLE={_console_payload(console)};</script>",
        f"<script>{_SCRIPT}</script>",
        "</body></html>",
    ])
