"""The visual map: one self-contained file, no network, pan/zoom/click.

Laid out left to right by kind, so the axis you read along IS the frontend-to-backend
seam: page -> module -> call -> endpoint -> URL -> view -> what the view returns. A
force-directed layout would need a library this file is not allowed to fetch, and would
hide the one relationship the map exists to show.
"""

from __future__ import annotations

import html as html_lib
import json

from seamcheck.mapdata import ConnectivityMap

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
:root {
  /* A developer tool should look like the thing it reads. Cool neutrals rather than warm
     paper, one saturated accent, and four status hues that stay distinguishable when a
     node is 4px tall at full-page zoom - which is where most of this map is read. */
  --bg:#f7f8fa; --panel:#ffffff; --sunk:#eef0f4;
  --ink:#0f1319; --muted:#5a6473; --line:#dfe3ea;
  --sig:#0b6bcb;
  --ok:#1a7f4b; --crit:#c0362c; --warn:#9a6410; --dim:#8b95a5;
  --ok-fill:#e6f4ec; --crit-fill:#fbeae8; --warn-fill:#fdf1de; --dim-fill:#eef0f4;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg:#0d1117; --panel:#151b24; --sunk:#0a0e14;
    --ink:#dde4ee; --muted:#8b97a8; --line:#252d38;
    --sig:#4aa3ff;
    --ok:#3fb27f; --crit:#f0736a; --warn:#d69a3c; --dim:#6f7b8c;
    --ok-fill:#10281e; --crit-fill:#2c1618; --warn-fill:#2a2011; --dim-fill:#161c24;
  }
}
* { box-sizing:border-box; }
/* An author `display` beats the UA rule that [hidden] relies on, so el.hidden = true read
   back as true while the zoom buttons and the breadcrumb stayed on screen over the panel. */
[hidden] { display:none !important; }
/* The canvas is the point. Everything else is a strip above it, and the whole document
   is exactly one screen tall so nothing scrolls the map out of view. */
body { margin:0; background:var(--bg); color:var(--ink); font-size:13.5px; overflow:hidden;
       height:100dvh; -webkit-font-smoothing:antialiased;
       font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif; }
/* Every value the scan read is shown in the font it was written in. */
.mono, #crumb, .meta, .hf, .hl, .row .t, .row .w, .tree, .fl, .covn,
.filters select, #q, .badge, .pill { font-family:ui-monospace,SFMono-Regular,Menlo,
       "Cascadia Mono","Roboto Mono",monospace; }
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
.filters select { width:100%; padding:8px; font-size:12.5px; border-radius:7px;
                  border:1px solid var(--line); background:var(--panel); color:var(--ink); }
.crumbrow { display:flex; align-items:center; gap:7px; padding:0 12px 8px; }
.crumbrow button { flex:none; padding:7px 10px; font-size:12px; border-radius:8px;
                   border:1px solid var(--line); background:var(--bg); color:var(--ink);
                   cursor:pointer; }
#crumb { flex:1 1 auto; min-width:0; font-size:12px; color:var(--muted);
         white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
#q { flex:0 1 150px; min-width:78px; padding:6px 9px; font-size:12.5px; border-radius:7px;
     border:1px solid var(--line); background:var(--panel); color:var(--ink); }
/* Always on screen. A reader should never have to hunt for what a colour claims, and the
   four statuses are the whole contract this tool makes. */
.legendbar { display:flex; flex-wrap:wrap; gap:4px 14px; padding:0 12px 9px; font-size:11px; }
.legendbar .k { display:flex; align-items:baseline; gap:5px; color:var(--ink); }
.legendbar .k i { width:9px; height:9px; border-radius:2px; border:1.5px solid; flex:none;
            transform:translateY(1px); }
.legendbar .k em { font-style:normal; color:var(--muted); }
.legendbar .connected i { border-color:var(--ok); }
.legendbar .unresolved i { border-color:var(--crit); }
.legendbar .unused i { border-color:var(--warn); }
.legendbar .uncertain i { border-color:var(--dim); }
.legendbar .filled i { border-color:var(--crit); background:var(--crit-fill); }
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

.main { flex:1 1 auto; position:relative; min-height:0; background:var(--sunk); }
svg { position:absolute; inset:0; width:100%; height:100%; display:block;
      cursor:grab; touch-action:none; }
svg.drag { cursor:grabbing; }
.nd rect { stroke-width:1.5; }
.nd text { font-size:10.5px; fill:var(--ink); pointer-events:none; letter-spacing:-.1px;
           font-family:ui-monospace,SFMono-Regular,Menlo,monospace; }
.nd { cursor:pointer; }
.nd.faded { opacity:.10; }
.nd.lit rect { stroke-width:3.5; }
.ed { fill:none; stroke-width:1.1; opacity:.38; }
.ed.faded { opacity:.05; }
.col { font-size:9.5px; fill:var(--muted); text-transform:uppercase; letter-spacing:.1em;
       font-family:ui-monospace,Menlo,monospace; }

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
.acts { margin:8px 0 4px; }
.acts button { padding:7px 11px; font-size:12.5px; border-radius:8px; cursor:pointer;
               border:1px solid var(--line); background:var(--bg); color:var(--sig); }
.sheet .lbl { font-size:9.5px; text-transform:uppercase; letter-spacing:.09em;
              color:var(--muted); margin:14px 0 6px; }
/* One row per hop, joined by a rule down the left, so the walk reads as a route. */
.hop { border-left:2px solid var(--line); padding:0 0 9px 10px; position:relative; }
.hop.at { border-left-color:var(--sig); }
.hop .hk { font-size:9.5px; text-transform:uppercase; letter-spacing:.07em; color:var(--muted); }
.hop .hl { font-size:12.5px; font-family:ui-monospace,Menlo,monospace; word-break:break-all; }
.hop.at { background:var(--sunk);
          border-radius:0 7px 7px 0; }
.hop.at .hl { font-weight:700; color:var(--sig); }
.hop .hf { font-size:11px; color:var(--muted); word-break:break-all;
           font-family:ui-monospace,Menlo,monospace; }
.hop button.code { margin-top:5px; padding:3px 9px; font-size:11px; border-radius:6px;
                   border:1px solid var(--line); background:var(--bg); color:var(--sig);
                   cursor:pointer; }
.codebox { position:fixed; inset:0; z-index:20; background:rgba(0,0,0,.45);
           display:flex; align-items:center; justify-content:center; padding:16px; }
.codecard { background:var(--panel); border:1px solid var(--line); border-radius:12px;
            max-width:900px; width:100%; max-height:82vh; display:flex;
            flex-direction:column; overflow:hidden; }
.codehead { display:flex; align-items:center; gap:10px; padding:11px 14px;
            border-bottom:1px solid var(--line); font-size:12px; color:var(--muted);
            font-family:ui-monospace,Menlo,monospace; word-break:break-all; }
.codehead button { margin-left:auto; flex:none; width:30px; height:30px; border-radius:8px;
                   border:1px solid var(--line); background:var(--bg); color:var(--ink);
                   cursor:pointer; }
#codebody { margin:0; padding:14px 0; overflow:auto; font-size:12px; line-height:1.7;
            background:var(--sunk); color:var(--ink); white-space:pre;
            font-family:ui-monospace,SFMono-Regular,Menlo,monospace; }
#codebody { tab-size:4; }

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
.badge, .pill { display:inline-block; font-size:9.5px; text-transform:uppercase;
                letter-spacing:.06em; border:1px solid transparent; border-radius:5px;
                padding:1px 6px; margin-right:6px; font-weight:600; }
.badge.connected, .pill.connected { color:var(--ok); background:var(--ok-fill);
                                    border-color:var(--ok); }
.badge.unresolved, .pill.unresolved { color:var(--crit); background:var(--crit-fill);
                                      border-color:var(--crit); }
.badge.unused, .pill.unused { color:var(--warn); background:var(--warn-fill);
                              border-color:var(--warn); }
.badge.uncertain, .pill.uncertain { color:var(--dim); background:var(--dim-fill);
                                    border-color:var(--dim); }
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
.tree { font-family:ui-monospace,Menlo,monospace; font-size:12.5px; }
.tree summary { cursor:pointer; padding:3px 0; color:var(--muted); }
.fl { display:flex; align-items:center; gap:8px; padding:3px 0; cursor:pointer;
      border-radius:6px; }
.fl:hover { background:var(--panel); }
.fl .fn { flex:0 1 auto; min-width:0; overflow:hidden; text-overflow:ellipsis;
          white-space:nowrap; }
.cov { flex:none; width:52px; height:6px; border-radius:3px; background:var(--line);
       overflow:hidden; }
.cov i { display:block; height:100%; background:var(--sig); }
.cov.none { width:auto; height:auto; background:none; color:var(--muted); font-size:11px; }
.covn { flex:none; color:var(--muted); font-size:11px; font-variant-numeric:tabular-nums; }
.blank { position:absolute; inset:0; display:flex; align-items:center; padding:0 22px;
         color:var(--muted); font-size:13px; }

@media (min-width: 761px) {
  .brand, .filters, .crumbrow { padding-left:16px; padding-right:16px; }
  .filters { max-width:820px; }
  .onlymob { display:none; }
  .rail { display:flex; flex-direction:column; width:230px; flex:none; overflow-y:auto;
          background:var(--panel); border-right:1px solid var(--line); }
  .railhead { font-size:10px; font-weight:600; padding:14px 14px 9px; color:var(--muted);
              text-transform:uppercase; letter-spacing:.11em;
              font-family:ui-monospace,Menlo,monospace; }
  .nv { display:flex; justify-content:space-between; align-items:center; gap:8px;
        padding:7px 14px; cursor:pointer; font-size:12.5px; }
  .nv:hover { background:var(--sunk); }
  .nv[aria-current="true"] { background:var(--sunk); box-shadow:inset 2px 0 0 var(--sig);
                             color:var(--sig); font-weight:600; }
  .nv .c { color:var(--muted); font-size:11px; font-variant-numeric:tabular-nums;
           font-family:ui-monospace,Menlo,monospace; }
  .sheet { left:auto; right:0; width:380px; top:0; bottom:auto; max-height:100%;
           border-top:0; border-left:1px solid var(--line); }
}
"""

_SCRIPT = r"""
const S = {connected:"var(--ok)", unresolved:"var(--crit)", unused:"var(--warn)", uncertain:"var(--dim)"};
// A tuned fill per status, not a computed tint: color-mix against the panel produced a
// grey mush in dark mode, where the panel is nearly black.
const F = {connected:"var(--ok-fill)", unresolved:"var(--crit-fill)",
           unused:"var(--warn-fill)", uncertain:"var(--dim-fill)"};
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
// The node a reader clicked, and whether the canvas should show only its chain. A page
// draws 1,366 symbols; the one question a click asks is "what is this joined to", and
// answering it by colour beats answering it by making the reader trace a line by eye.
let lit = null, isolate = false, fileFilter = null;

// A section is a lens on the same canvas, not a different page. The list of rows is the
// CLI's answer; on screen the answer is the shape.
const SECTION_KINDS = {
  map: null,
  boundary: new Set(["module", "js_call", "fetch_target", "url", "view", "json_field"]),
  dom: new Set(["dom_attr", "dom_selector", "multi_writer_element"]),
  django: new Set(["url", "view", "model", "admin_action", "signal_receiver",
                   "template_tag", "management_command"]),
  css: new Set(["css_selector", "css_token_def", "css_token_use"]),
};

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
// The line through a node: what leads to it, and what it leads to. Followed with the
// arrows, not across them - an undirected walk reaches the page node, and from there
// every module and everything under them, so clicking one endpoint lit all 327 symbols
// on the page and told a reader nothing.
function chainOf(p, id) {
  const back = new Map(), fwd = new Map();
  p.edges.forEach(e => {
    if (!back.has(e.target)) back.set(e.target, []);
    if (!fwd.has(e.source)) fwd.set(e.source, []);
    back.get(e.target).push(e.source);
    fwd.get(e.source).push(e.target);
  });
  const seen = new Set([id]);
  const walk = (adj, from) => {
    let front = [from];
    while (front.length) {
      const next = [];
      front.forEach(n => (adj.get(n) || []).forEach(m => {
        if (seen.has(m)) return;
        seen.add(m); next.push(m);
      }));
      front = next;
    }
  };
  walk(back, id);
  walk(fwd, id);
  return seen;
}

function changedIn(p) {
  return new Set(p.nodes.filter(n => CHANGED[n.id] || n.kind === "page").map(n => n.id));
}

function visible(p) {
  if (only) return changedIn(p);
  if (isolate && lit) return chainOf(p, lit);
  if (fileFilter) return new Set(p.nodes.filter(n => n.file === fileFilter).map(n => n.id));
  // Everything this page touches, in one canvas. Showing only modules until a reader
  // drilled in hid the whole point: which symbols connect and which stand alone.
  if (!focus) {
    const kinds = SECTION_KINDS[mode];
    return new Set(p.nodes.filter(n => !kinds || n.kind === "page" || kinds.has(n.kind))
                          .map(n => n.id));
  }
  const adj = new Map();
  p.edges.forEach(e => {
    if (!adj.has(e.source)) adj.set(e.source, []);
    if (!adj.has(e.target)) adj.set(e.target, []);
    adj.get(e.source).push(e.target);
    adj.get(e.target).push(e.source);
  });
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

const NODE_W = 150, LANE = 160, ROW = 30;

const ROW_CHOICES = [16, 22, 30, 42, 58, 80, 110, 150, 210];

// How tall a column runs before wrapping into another lane. Fixed at 42, the widest page
// laid out 6,600 x 1,300 and fitting that to a landscape canvas shrank it to 18% with two
// thirds of the screen empty; a formula off the tallest column alone then made the SMALL
// pages worse, because what drives the width is every column's lanes, not one column's
// depth. So the layout is simply computed at each candidate and the one that fits largest
// wins - a handful of arithmetic passes over a few thousand nodes, once per draw.
function place(buckets, used, rows) {
  const pos = new Map();
  const columns = [];
  let x = 40, deepest = 1;
  used.forEach(c => {
    const items = buckets.get(c);
    const lanes = Math.max(1, Math.ceil(items.length / rows));
    items.forEach((n, i) => pos.set(n.id, {
      x: x + Math.floor(i / rows) * LANE,
      y: 62 + (i % rows) * ROW,
    }));
    deepest = Math.max(deepest, Math.min(items.length, rows));
    columns.push({x, label: COLS[c] ? COLS[c][1] : "Other", count: items.length});
    x += lanes * LANE + 50;
  });
  return {pos, columns, width: x, height: 62 + deepest * ROW};
}

// A column wraps into lanes instead of running off the bottom of the world. One page here
// holds 839 selectors: stacked in a single file that column stood 25,000px tall and no
// amount of scrolling made the page legible, which is why the map used to show only
// modules until you drilled in. Wrapped, the same page is about 1,300px tall and every
// symbol it has is on screen at once.
function layout(p, keep) {
  const buckets = new Map();
  p.nodes.filter(n => keep.has(n.id)).forEach(n => {
    const c = ORDER.has(n.kind) ? ORDER.get(n.kind) : COLS.length;
    if (!buckets.has(c)) buckets.set(c, []);
    buckets.get(c).push(n);
  });
  const used = [...buckets.keys()].sort((a, b) => a - b);
  const w = svg.clientWidth || 1200, h = svg.clientHeight || 700;
  let best = null, bestFit = -1;
  ROW_CHOICES.forEach(rows => {
    const candidate = place(buckets, used, rows);
    const fit = Math.min(w / (candidate.width + 40), h / (candidate.height + 40));
    if (fit > bestFit) { bestFit = fit; best = candidate; }
  });
  return best;
}

const hit = n => !query || (n.label + " " + n.file).toLowerCase().includes(query);

function draw() {
  const p = PAGES[current];
  if (!p) return;
  pages.value = String(current);
  const here = p.where ? `${p.title} · ${p.where}` : p.title;
  crumb.textContent = fileFilter ? `${here} › ${fileFilter}`
    : focus ? `${here} › ${(byId.get(focus) || {}).label || ""}`
    : `${here} — pick a module`;
  document.getElementById("up").hidden = !focus;
  // A commit that touched only files the scan does not read has an empty changed set.
  // Drawing that as a bare page node reads as a broken map rather than as an answer.
  if (only && !Object.keys(CHANGED).length) {
    svg.innerHTML = `<text x="20" y="40" class="col">nothing the scan reads changed in this commit</text>`;
    return;
  }
  const keep = visible(p);
  const {pos, columns, width, height} = layout(p, keep);
  // A phone is narrower than two columns of this map, so an untouched view opens showing
  // the whole chain, nudged clear of the left edge. Only the first draw of a view fits:
  // once someone pans or zooms, the view is theirs.
  if (view.k === 1 && view.x === 0 && view.y === 0) {
    const haveW = svg.clientWidth || 800, haveH = svg.clientHeight || 600;
    // Fit the whole page, however wide it got. The floor is low on purpose: at that zoom
    // nobody is reading labels, they are seeing which blocks connect and which stand
    // alone - the question the canvas exists to answer.
    view.k = Math.min(1, Math.max(0.06, Math.min(haveW / (width + 40), haveH / (height + 40))));
    if (height * view.k < haveH) view.y = Math.min(48, (haveH - height * view.k) / 2);
  }
  // What the click lit up. Everything else stays on the canvas but recedes, so the chain
  // reads as a line through the page instead of the reader tracing edges by eye. Declared
  // before the edges that read it: the node loop is further down, but the edge loop is not.
  const chain = lit && !isolate ? chainOf(p, lit) : null;
  const out = [`<g transform="translate(${view.x},${view.y}) scale(${view.k})">`];
  columns.forEach(c => out.push(
    `<text class="col" x="${c.x}" y="44">${esc(c.label)} ${c.count}</text>`));
  p.edges.forEach(e => {
    const a = pos.get(e.source), b = pos.get(e.target);
    if (!a || !b) return;
    const dim = chain && !(chain.has(e.source) && chain.has(e.target));
    const mx = (a.x + 150 + b.x) / 2;
    out.push(`<path class="ed${dim ? " faded" : ""}" stroke="${S[e.status] || "var(--dim)"}"
      d="M${a.x + 150},${a.y + 10} C${mx},${a.y + 10} ${mx},${b.y + 10} ${b.x},${b.y + 10}"/>`);
  });
  // "Alone" is the STATUS, not the edge count. A symbol reaches a page by an edge, so
  // nothing here can have no edges, and the map's edge set carries no self-loops either -
  // a marking driven off degree fired on precisely nothing. What alone actually means is
  // unresolved (something reached for it and it was not there) or unused (both ends were
  // observable and nothing used it), so those are filled rather than outlined: at the zoom
  // where a whole page fits, labels are gone and a solid block is the only thing that
  // carries across a wall of outlines.
  const withLabels = view.k >= 0.34;
  p.nodes.filter(n => keep.has(n.id)).forEach(n => {
    const q = pos.get(n.id); if (!q) return;
    const ch = CHANGED[n.id];
    const stroke = ch ? CH[ch] : (S[n.status] || "var(--dim)");
    const alone = n.status === "unresolved" || n.status === "unused";
    const label = n.label.length > 20 ? n.label.slice(0, 19) + "…" : n.label;
    // The drawn box is 20px tall and the view opens scaled down, so on a phone the
    // visible target can be 8px. The transparent rect below it fills the row pitch.
    const shown = hit(n) && (!chain || chain.has(n.id));
    out.push(`<g class="nd${shown ? "" : " faded"}${n.id === lit ? " lit" : ""}" data-id="${n.id}">
      <rect x="${q.x - 5}" y="${q.y - 5}" width="160" height="30" fill="transparent"
            pointer-events="all"/>
      <rect x="${q.x}" y="${q.y}" width="150" height="20" rx="5"
            fill="${alone ? (F[n.status] || "var(--panel)") : "var(--panel)"}"
            stroke="${stroke}" stroke-width="${ch ? 3 : 1.5}"/>
      ${withLabels ? `<text x="${q.x + 7}" y="${q.y + 14}">${esc(label)}</text>` : ""}</g>`);
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
  if (!g) { if (lit) { lit = null; isolate = false; closeSheet(); draw(); } return; }
  lit = g.dataset.id;
  show(lit);
  draw();
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
  const code = n.context || n.snippet;
  return `<div class="hop${id === here ? " at" : ""}">
    <div class="hk">${esc(n.kind)}</div>
    <div class="hl">${esc(n.label)}</div>
    ${n.file ? `<div class="hf">${esc(n.file)}${n.line ? ":" + n.line : ""}</div>` : ""}
    ${code ? `<button class="code" data-code="${esc(id)}">code</button>` : ""}</div>`;
}

// Code opens on request, over the page. Inline, one chain filled the panel with six
// listings a reader had not asked for and had to scroll past to see the shape of the path.
const codebox = document.getElementById("codebox");
function showCode(id) {
  const n = byId.get(id); if (!n) return;
  document.getElementById("codetitle").textContent =
    n.label + (n.file ? "  —  " + n.file + (n.line ? ":" + n.line : "") : "");
  document.getElementById("codebody").textContent = n.context || n.snippet || "";
  codebox.hidden = false;
}
document.getElementById("codeclose").onclick = () => { codebox.hidden = true; };
codebox.onclick = e => { if (e.target === codebox) codebox.hidden = true; };
dbody.addEventListener("click", e => {
  const b = e.target.closest(".code");
  if (b) showCode(b.dataset.code);
});

// A fetch and the call that makes it are the same line of source. Deduped across the
// whole sheet, not per list: one seen-set per list still printed that line under Path and
// again under Reaches.
function deduper() {
  const seen = new Set();
  return ids => ids.filter(x => {
    const n = byId.get(x); if (!n) return false;
    const key = n.file && n.line ? n.file + ":" + n.line : x;
    if (seen.has(key)) return false;
    seen.add(key); return true;
  });
}

function show(id) {
  const n = byId.get(id); if (!n) return;
  const ch = CHANGED[id];
  const {inbound, outbound} = routes(id);
  const take = deduper();
  const path = take(inbound), reaches = take(outbound);
  dbody.innerHTML = `<h2>${esc(n.label)}</h2>
    <div class="acts">
      <button id="iso" type="button">${isolate ? "Show the whole page" : "Show only this chain"}</button>
    </div>
    <div class="row"><span class="badge ${esc(n.status)}">${esc(n.status)}</span>
      ${esc(n.kind)}${ch ? " · " + esc(ch) : ""}</div>
    ${n.file ? `<div class="row">${esc(n.file)}${n.line ? ":" + n.line : ""}</div>` : ""}
    ${n.note ? `<div class="note">${esc(n.note)}</div>` : ""}
    <div class="lbl">Path — browser to backend</div>
    ${path.map(step => hop(step, id)).join("")}
    ${reaches.length ? `<div class="lbl">Reaches</div>` +
      reaches.map(step => hop(step, id)).join("") : ""}`;
  sheet.hidden = false;
  document.getElementById("iso").onclick = () => {
    isolate = !isolate; view = {x:0, y:0, k:1}; draw(); show(id);
  };
}
document.getElementById("dx").onclick = () => { lit = null; isolate = false; closeSheet(); draw(); };

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
  // A Mac trackpad fires a stream of high-resolution wheel events, so a fixed 1.1x per
  // event flew from one end of the zoom range to the other on a single flick. macOS marks
  // a pinch as ctrlKey, which is the gesture that should zoom; a plain two-finger scroll
  // pans, as it does in every map on this platform.
  if (e.ctrlKey || e.metaKey) {
    zoomTo(view.k * Math.exp(-e.deltaY * 0.01));
    return;
  }
  view.x -= e.deltaX; view.y -= e.deltaY;
  draw();
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
let mode = "map", cq = "", cstatus = "", shown = ROWS_PER_PAGE, asList = false;
const listToggle = document.getElementById("aslist");
listToggle.onclick = () => { asList = !asList; switchTo(mode); };
// The map is what this page is for, so it opens on the map with the whole scan drawn.
// The commit filter therefore starts at "everything": defaulting it to the newest commit
// left the canvas holding one node, because a commit that only deletes something has
// nothing left to draw - which reads as a page with no nodes in it.
const OPENS_ON = "map";

const VIEWS = [{key: "map", title: "Map — what reaches what", count: null},
               {key: "files", title: "Files", count: FILES.length},
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

// A folder tree, the shape the repository actually has. The map is rooted at pages,
// which is how a browser reaches code and not how anyone edits it: from the map you
// cannot tell whether a file's other twenty functions were ever considered.
let fileQuery = "";

function treeHtml() {
  const root = {dirs: new Map(), files: []};
  const needle = fileQuery.toLowerCase();
  const shown = FILES.filter(f => !needle || f.path.toLowerCase().includes(needle));
  shown.forEach(f => {
    const parts = f.path.split("/");
    let node = root;
    parts.slice(0, -1).forEach(part => {
      if (!node.dirs.has(part)) node.dirs.set(part, {dirs: new Map(), files: []});
      node = node.dirs.get(part);
    });
    node.files.push(f);
  });
  const bar = f => {
    if (!f.declarations) return `<span class="cov none">no declarations</span>`;
    const pct = Math.round(f.known / f.declarations * 100);
    return `<span class="cov"><i style="width:${pct}%"></i></span>` +
           `<span class="covn">${f.known}/${f.declarations}</span>`;
  };
  const flags = f => ["unresolved", "unused", "uncertain", "connected"]
    .filter(k => f.counts[k])
    .map(k => `<span class="badge ${k}">${k} ${f.counts[k]}</span>`).join("");
  const walk = (node, name, depth) => {
    const kids = [...node.dirs.entries()].sort((a, b) => a[0].localeCompare(b[0]));
    const inner = kids.map(([n, d]) => walk(d, n, depth + 1)).join("") +
      node.files.sort((a, b) => a.path.localeCompare(b.path)).map(f =>
        `<div class="fl" data-path="${esc(f.path)}" style="padding-left:${depth * 13 + 20}px">
           <span class="fn">${esc(f.path.split("/").pop())}</span>
           ${bar(f)}${flags(f)}</div>`).join("");
    if (name === null) return inner;
    return `<details${depth < 2 || needle ? " open" : ""}>
      <summary style="padding-left:${depth * 13}px">${esc(name)}</summary>${inner}</details>`;
  };
  return `<h2>Files</h2><p class="blurb">Every file the scan read, in the shape the
    repository has. The bar is how many of a file's own declarations appear in the graph
    at all — that is coverage, not a finding: a helper that makes no request and touches
    no element has nothing to model. Click a file to draw only its symbols.</p>
    <div class="tools"><input id="fq" type="search"
      placeholder="Filter ${shown.length} of ${FILES.length} files" value="${esc(fileQuery)}"></div>
    <div class="tree">${walk(root, null, 0)}</div>`;
}

function renderPanel() {
  if (mode === "files") {
    panel.innerHTML = treeHtml();
    panel.querySelectorAll(".fl").forEach(el => {
      el.onclick = () => { fileFilter = el.dataset.path; viewer.value = "map"; switchTo("map"); };
    });
    const box = document.getElementById("fq");
    box.oninput = e => {
      fileQuery = e.target.value; renderPanel();
      const again = document.getElementById("fq");
      again.focus(); again.setSelectionRange(again.value.length, again.value.length);
    };
    return;
  }
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
  // A section with a lens draws on the canvas; one without (Overview, and the sections no
  // extractor feeds yet) has nothing to draw and falls back to its rows.
  if (mode !== "map") { fileFilter = null; fileQuery = mode === "files" ? fileQuery : ""; }
  const drawable = SECTION_KINDS[mode] !== undefined && !asList;
  panel.hidden = drawable; svg.hidden = !drawable;
  document.getElementById("colourkey").hidden = !drawable;
  document.querySelector(".zoom").hidden = !drawable;
  document.getElementById("lg").hidden = !drawable;
  const hasLens = SECTION_KINDS[mode] !== undefined;
  // The row stays while a lens exists, or the button that switches back to the map goes
  // with it and the list becomes a dead end.
  document.querySelector(".crumbrow").hidden = !hasLens;
  document.getElementById("crumb").hidden = !drawable;
  document.getElementById("q").hidden = !drawable;
  pgwrap.hidden = !drawable;
  listToggle.hidden = !hasLens;
  listToggle.textContent = asList ? "Show as map" : "Show as list";
  closeSheet();
  cq = ""; cstatus = ""; shown = ROWS_PER_PAGE;
  focus = null; view = {x:0, y:0, k:1};
  if (drawable) draw(); else renderPanel();
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
                        "context": node.context,
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


def render(connectivity_map: ConnectivityMap, console=None, files=None) -> str:
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
        f"<title>Seamcheck — {_esc(connectivity_map.git_sha[:12])}</title>",
        f"<style>{_CSS}</style></head><body>",
        '<div class="shell"><aside class="rail">'
        '<div class="railhead">Seamcheck</div><div class="nav" id="nav"></div></aside>'
        '<div class="content">'
        '<header class="top">',
        f'<div class="brand"><b>Seamcheck</b>'
        f'<span class="meta">HEAD {_esc(connectivity_map.git_sha[:12])} · {_esc(mode)}</span>'
        f"</div>",
        '<div class="filters onlymob"><label class="wide"><span>View</span>'
        '<select id="vw"></select></label></div>',
        '<div class="filters">'
        '<label><span>Commit</span><select id="cm"></select></label>'
        '<label id="pgwrap"><span>Page</span><select id="pg"></select></label></div>',
        '<div class="crumbrow"><button id="up" type="button" hidden>\u2190</button>'
        '<button id="aslist" type="button" hidden>Show as list</button>'
        '<span id="crumb"></span>'
        '<input id="q" type="search" placeholder="Filter"></div>',
        '<div class="legendbar" id="colourkey">'
        '<span class="k connected"><i></i>connected<em>something reaches it, evidence attached</em></span>'
        '<span class="k unresolved"><i></i>unresolved<em>something reaches for it and it is not there</em></span>'
        '<span class="k unused"><i></i>unused<em>both ends observable, nothing uses it</em></span>'
        '<span class="k uncertain"><i></i>uncertain<em>no evidence either way \u2014 not a claim it is dead</em></span>'
        '<span class="k filled"><i></i>filled<em>unresolved or unused: the ones to look at</em></span>'
        "</div>",
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
        '<div class="codebox" id="codebox" hidden><div class="codecard">'
        '<div class="codehead"><span id="codetitle"></span>'
        '<button id="codeclose" type="button" aria-label="Close">\u00d7</button></div>'
        '<pre id="codebody"></pre></div></div>',
        '<aside class="sheet" id="detail" hidden>'
        '<button class="x" id="dx" type="button" aria-label="Close">\u00d7</button>'
        '<div id="dbody"></div></aside>',
        "</main></div></div>",
        f"<script>const MAPDATA={_payload(connectivity_map)};</script>",
        f"<script>const CONSOLE={_console_payload(console)};</script>",
        f"<script>const FILES={json.dumps(files or []).replace('</', '<\\/')};</script>",
        f"<script>{_SCRIPT}</script>",
        "</body></html>",
    ])
