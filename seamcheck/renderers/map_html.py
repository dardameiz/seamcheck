"""The visual map: one self-contained file, no network, pan/zoom/click.

Laid out left to right by kind, so the axis you read along IS the frontend-to-backend
seam: page -> module -> call -> endpoint -> URL -> view -> what the view returns. A
force-directed layout would need a library this file is not allowed to fetch, and would
hide the one relationship the map exists to show.
"""

from __future__ import annotations

import html as html_lib
import json

from seamcheck import editors, meaning
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
  --sig:#0b6bcb; --sig-fill:#e7f0fb; --sig-fill-hi:#d5e6f8;
  /* Four statuses, four hues that survive being 4px tall. `unused` was #9a6410 - a dark
     amber that reads as another red next to #c0362c, so the two categories a reader is
     meant to triage differently looked like one. It is violet now: no red in it at all,
     distinct from the green and the blue-grey, and still legible on white. */
  --ok:#1a7f4b; --crit:#d1332a; --warn:#7c3aed; --dim:#8b95a5;
  --ok-fill:#e6f4ec; --crit-fill:#fdeae8; --warn-fill:#f1ebfe; --dim-fill:#eef0f4;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg:#0d1117; --panel:#151b24; --sunk:#0a0e14;
    --ink:#dde4ee; --muted:#8b97a8; --line:#252d38;
    --sig:#4aa3ff; --sig-fill:#12243a; --sig-fill-hi:#183353;
    --ok:#3fb27f; --crit:#f0736a; --warn:#a78bfa; --dim:#6f7b8c;
    --ok-fill:#10281e; --crit-fill:#2c1618; --warn-fill:#211a35; --dim-fill:#161c24;
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
/* The one control that changes what kind of thing you are looking at, and it read as a
   disabled-looking grey chip beside the breadcrumb. It is the accent colour, it says
   which way it will switch you, and it carries the icon of the destination. */
#aslist { font-weight:600; color:var(--sig); border-color:var(--sig);
          background:var(--sig-fill); letter-spacing:.01em; }
#aslist:hover { background:var(--sig-fill-hi); }
#crumb { flex:1 1 auto; min-width:0; font-size:12px; color:var(--muted);
         white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
#q { flex:0 1 150px; min-width:78px; padding:6px 9px; font-size:12.5px; border-radius:7px;
     border:1px solid var(--line); background:var(--panel); color:var(--ink); }
/* A filter that matched nothing used to grey all 1,450 nodes and say nothing at all, so
   the map looked broken rather than empty. It says how many matched. */
.qn { flex:none; font-size:11px; color:var(--muted); white-space:nowrap;
      font-family:ui-monospace,Menlo,monospace; }
.qn.none { color:var(--crit); }
/* Always on screen. A reader should never have to hunt for what a colour claims, and the
   four statuses are the whole contract this tool makes. */
.legendbar { display:flex; flex-wrap:wrap; gap:4px 14px; padding:0 12px 9px; font-size:11px; }
.legendbar .k { display:flex; align-items:baseline; gap:5px; color:var(--ink); }
.legendbar .k i { width:9px; height:9px; border-radius:2px; border:1.5px solid; flex:none;
            transform:translateY(1px); }
.legendbar .k em { font-style:normal; color:var(--muted); }
/* The key was already the vocabulary of the map; making it the filter means the control
   and its explanation are the same object, instead of a second control that repeats it. */
button.k { background:none; border:0; padding:2px 6px; margin:-2px -6px; border-radius:5px;
  font:inherit; cursor:pointer; }
button.k:hover { background:var(--chip); }
button.k[aria-pressed="true"] { background:var(--chip); outline:1px solid var(--line); }
button.k[aria-pressed="true"] em { color:var(--ink); }
.legendbar .hint { color:var(--muted); font-style:italic; }
.meta .read { color:var(--ink); }

/* The trend. Deliberately a plain SVG polyline rather than a charting library: the page
   already carries a megabyte of graph and the shape of eight numbers does not need one. */
.trend { margin:14px 0 22px; }
.trend svg { width:100%; height:180px; display:block; background:var(--panel);
  border:1px solid var(--line); border-radius:8px; }
.trend .band { fill:var(--crit); opacity:.10; }
.trend .line { fill:none; stroke:var(--crit); stroke-width:2; }
.trend .dot { fill:var(--crit); }
.trend .grid { stroke:var(--line); stroke-width:1; }
.trend .lbl { fill:var(--muted); font-size:10px; }
.headline { font-size:15px; margin:0 0 4px; color:var(--ink); }
.headline b { font-size:19px; }
.headline .down { color:var(--ok); }
.headline .up { color:var(--crit); }
.movers { display:grid; gap:2px; margin:10px 0 0; }
.movers .m { display:grid; grid-template-columns:1fr auto auto; gap:12px; padding:5px 8px;
  border-bottom:1px solid var(--line); font-size:12px; align-items:baseline; }
.movers .m:last-child { border-bottom:0; }
.movers .num { font-variant-numeric:tabular-nums; color:var(--muted); }
.movers .chg { font-variant-numeric:tabular-nums; font-weight:600; }
.movers .chg.down { color:var(--ok); }
.movers .chg.up { color:var(--crit); }
.legendbar.filtering .hint { display:none; }
.legendbar .connected i { border-color:var(--ok); }
.legendbar .unresolved i { border-color:var(--crit); }
.legendbar .unused i { border-color:var(--warn); }
.legendbar .uncertain i { border-color:var(--dim); }
/* Not a fifth status: a swatch showing what "solid" means, so it borrows the two hues
   it describes rather than adding a third red to the row. */
.legendbar .filled i { border-color:var(--crit);
        background:linear-gradient(135deg,var(--crit-fill) 50%,var(--warn-fill) 50%); }
.note { padding:0 12px 8px; font-size:11.5px; color:var(--muted); }
.note:empty { display:none; }
/* Not a warning - a statement of what is on screen versus what exists. */
.capnote { color:var(--warn); }
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
svg.nolabels .nd text { display:none; }
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
.hop .hk { font-size:9.5px; text-transform:uppercase; letter-spacing:.07em;
           color:var(--muted); display:flex; align-items:center; gap:6px; }
/* The step number sits in the rule the hops hang off, so the column of numbers reads as
   an order rather than as part of each label. */
.hop .hn { flex:none; width:16px; height:16px; margin-left:-19px; border-radius:50%;
           background:var(--line); color:var(--ink); font-size:9px; font-weight:700;
           display:inline-flex; align-items:center; justify-content:center;
           letter-spacing:0; }
.hop.at .hn { background:var(--sig); color:#fff; }
.hop .hs { color:var(--muted); font-size:9px; letter-spacing:.04em; }
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
/* The listing. A grid rather than a <pre> so the gutter can carry a status stripe without
   the line numbers being selectable along with the code a reader wants to copy. */
.listing { font-variant-ligatures:none; }
.listing .ln { display:grid; grid-template-columns:auto 1fr; gap:14px; padding:0 14px;
  border-left:3px solid transparent; white-space:pre; }
.listing .num { color:var(--muted); user-select:none; text-align:right; opacity:.7; }
.listing .here { background:var(--chip); border-left-color:var(--accent); }
.listing .here .num { color:var(--ink); opacity:1; }
.listing .mark.unresolved { border-left-color:var(--crit); }
.listing .mark.unused { border-left-color:var(--warn); }
.listing .mark.uncertain { border-left-color:var(--dim); }
.listing .s { color:#9ecbff; font-style:normal; }
.listing .c { color:var(--muted); font-style:italic; }
.listing .n { color:#f8c555; font-style:normal; }
.listing .k { color:#c792ea; font-style:normal; }
#codenote { padding:6px 14px 10px; color:var(--muted); font-size:11px; }

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
.card.wide { flex:1 1 100%; }
.card .k { font-size:10px; text-transform:uppercase; letter-spacing:.08em; color:var(--muted); }
.card .v { font-size:24px; font-weight:700; margin:2px 0 7px; }
.card .vs { font-size:13px; font-weight:600; color:var(--muted); }
.stack { display:flex; height:9px; border-radius:5px; overflow:hidden; background:var(--sunk);
         margin:0 0 10px; }
.stack i { display:block; height:100%; }
.stack i.connected { background:var(--ok); }
.stack i.unresolved { background:var(--crit); }
.stack i.unused { background:var(--warn); }
.stack i.uncertain { background:var(--dim); }
.panel h3.sec { font-size:13px; margin:24px 0 10px; }

/* The one number worth opening the page for, at the size that says so. */
.hero { background:var(--panel); border:1px solid var(--line); border-radius:12px;
        padding:15px 17px; }
.hero .hk { font-size:10px; text-transform:uppercase; letter-spacing:.09em; color:var(--muted); }
.hero .hv { font-size:34px; font-weight:700; letter-spacing:-.5px; margin:1px 0 12px;
            display:flex; align-items:baseline; gap:11px; font-variant-numeric:tabular-nums; }
.hero .hp { font-size:13px; font-weight:500; color:var(--muted); letter-spacing:0; }
/* Findings against findings. Drawn against the whole codebase this bar was 96% the two
   statuses nobody acts on, and the part you came for was a sliver at the far edge. */
.split { display:flex; height:14px; border-radius:7px; overflow:hidden; background:var(--sunk); }
.split i { display:block; height:100%; }
.split i.unresolved { background:var(--crit); }
.split i.unused { background:var(--warn); }
.splitkey { display:flex; flex-wrap:wrap; gap:5px 22px; margin-top:11px; }
.kk { display:flex; align-items:baseline; gap:7px; font-size:12.5px; max-width:60ch; }
.kk i { width:9px; height:9px; border-radius:2px; flex:none; transform:translateY(1px); }
.kk i.unresolved { background:var(--crit); }
.kk i.unused { background:var(--warn); }
.kk b { font-variant-numeric:tabular-nums; }
.kk em { font-style:normal; color:var(--muted); }
.rest { margin:13px 0 0; padding-top:12px; border-top:1px solid var(--line);
        color:var(--muted); font-size:12.5px; line-height:1.6; max-width:78ch; }
.rest b { color:var(--ink); font-variant-numeric:tabular-nums; }

/* One scale for both sides, or the comparison the layout invites is a lie. */
.wtable { background:var(--panel); border:1px solid var(--line); border-radius:12px;
          padding:6px 15px 12px; }
.wrow { display:grid; grid-template-columns:88px 1fr 78px 78px 58px; gap:12px;
        align-items:center; padding:9px 0; border-bottom:1px solid var(--line); }
.wrow:last-child { border-bottom:0; }
.whead { padding:9px 0 6px; border-bottom:1px solid var(--line); }
/* The header's bar cell is a spacer, not a bar. With the track background on it, it read
   as an empty third row above the data. */
.whead .wbar { background:none; }
.whead .wnum { font-size:9.5px; text-transform:uppercase; letter-spacing:.08em;
               color:var(--muted); font-weight:500; }
.wname { font-size:13px; font-weight:600; }
.wbar { height:16px; background:var(--sunk); border-radius:5px; overflow:hidden; }
.wbar i { display:block; height:100%; background:var(--ok); border-radius:5px 0 0 5px;
          position:relative; min-width:2px; }
.wbar em { position:absolute; right:0; top:0; height:100%; background:var(--crit);
           display:block; }
.wnum { text-align:right; font-size:13px; font-variant-numeric:tabular-nums;
        font-family:ui-monospace,Menlo,monospace; }
.wnum.hot { color:var(--crit); font-weight:700; }
.wnum.cool { color:var(--muted); }

@media (max-width: 760px) {
  .wrow { grid-template-columns:74px 1fr 62px 46px; }
  .wrow > :nth-child(4) { display:none; }
}
.gap { color:var(--muted); font-size:12.5px; border:1px dashed var(--line);
       border-radius:9px; padding:11px 12px; }
.more { text-align:center; padding:10px; border:1px solid var(--line); border-radius:9px;
        cursor:pointer; font-size:13px; color:var(--sig); }
.gloss { color:var(--muted); font-size:12px; margin-top:14px; }
/* One line, once, at the bottom of the one panel a person reads end to end. A tool that
   asks on every screen is an advert; a tool that never asks does not get maintained. */
.colophon { margin:26px 0 0; padding-top:14px; border-top:1px solid var(--line);
            color:var(--muted); font-size:12px; line-height:1.6; max-width:70ch; }
.colophon a { color:var(--sig); }
.colophon p { margin:0 0 11px; }
/* GitHub's own sponsor card is an embedded frame, which would make this file phone
   github.com every time it is opened - from a document listing a private repo's paths,
   often over a LAN or a tunnel. "One file, no network" is the promise; a link keeps it. */
.sponsor { display:inline-block; text-decoration:none; font-size:12.5px; font-weight:600;
           padding:7px 13px; border-radius:8px; border:1px solid #ea4aaa;
           color:#ea4aaa !important; }
.sponsor:hover { background:var(--sig-fill); }
.tree { font-family:ui-monospace,Menlo,monospace; font-size:12.5px; }
.tree summary { cursor:pointer; padding:3px 0; color:var(--muted); }
.fl { display:flex; align-items:center; gap:8px; padding:3px 0; cursor:pointer;
      border-radius:6px; }
.fl:hover { background:var(--panel); }
/* The row's own action is "show me this on the map", and it was invisible next to an
   `open` link that left for VS Code - so the one thing the view exists for read as the
   secondary one. Named, and on hover it is the loud half of the row. */
.fl .go { flex:none; margin-left:auto; color:var(--sig); font-size:11px; opacity:0;
          font-family:ui-monospace,Menlo,monospace; }
.fl:hover .go { opacity:1; }
.fl .edit { flex:none; font-size:11px; opacity:.55; }
.fl:hover .edit { opacity:1; }
.fl .fn { flex:0 1 auto; min-width:0; overflow:hidden; text-overflow:ellipsis;
          white-space:nowrap; }
.cov { flex:none; width:52px; height:6px; border-radius:3px; background:var(--line);
       overflow:hidden; }
.cov i { display:block; height:100%; background:var(--sig); }
.cov.none { width:auto; height:auto; background:none; color:var(--muted); font-size:11px; }
.covn { flex:none; color:var(--muted); font-size:11px; font-variant-numeric:tabular-nums; }
.blank { position:absolute; inset:0; display:flex; align-items:center; padding:0 22px;
         color:var(--muted); font-size:13px; }


/* A location is the whole point of a finding, so it is a control, not a caption: click
   opens the file at the line in the reader's editor, shift-click copies the path. */
.loc { color:var(--sig); text-decoration:none; border-bottom:1px dotted currentColor;
       cursor:pointer; }
.loc:hover { border-bottom-style:solid; }
.loc.copied { color:var(--ok); border-bottom-color:var(--ok); }
/* Precise and unreadable is not informative. Every finding carries what the scan
   observed, and the handful of things that are usually true when you see it. */
.why { margin-top:8px; padding:8px 10px; background:var(--sunk); border-radius:7px;
       font-size:12.5px; line-height:1.5; }
.why p { margin:0 0 5px; color:var(--ink); }
.why p:last-child { margin-bottom:0; color:var(--muted); }
.why b { font-size:9.5px; text-transform:uppercase; letter-spacing:.08em;
         color:var(--muted); font-weight:600; margin-right:7px; }
/* Prose, for the one panel that is prose. */
.doc { max-width:72ch; }
.doc p, .doc li { color:var(--ink); font-size:13.5px; line-height:1.65; }
.doc p { margin:0 0 12px; }
.doc h3 { font-size:13px; margin:22px 0 9px; }
.doc ol, .doc ul { margin:0 0 12px; padding-inline-start:20px; }
.doc li { margin-bottom:6px; }
.doc code { background:var(--sunk); border-radius:4px; padding:1px 5px; font-size:12px;
            font-family:ui-monospace,Menlo,monospace; }
.caveat { border-inline-start:3px solid var(--warn); background:var(--warn-fill);
          padding:10px 12px; border-radius:0 7px 7px 0; font-size:12.5px;
          line-height:1.55; margin:0 0 14px; color:var(--ink); }
.skey { display:grid; gap:8px; margin:0 0 6px; }
.skey .s { border:1px solid var(--line); border-radius:9px; padding:10px 12px;
           background:var(--panel); }
.skey h4 { margin:0 0 4px; font-size:12.5px; display:flex; align-items:baseline; gap:8px; }
.skey p { margin:0 0 4px; font-size:12.5px; line-height:1.55; color:var(--ink); }
.skey p.c { color:var(--muted); margin-bottom:0; }

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
// Nodes arrive as arrays against three string tables (see _payload: it saved ~7 MB on a
// real scan). Expanded once, here, into exactly the objects the rest of this script has
// always read - so the wire format is a detail of loading and of nothing else.
const COLS = MAPDATA.columns, COMMITS = MAPDATA.commits || [];
const PAGES = (() => {
  const F = MAPDATA.fields, K = MAPDATA.kinds, S_ = MAPDATA.statuses, FI = MAPDATA.files;
  const inflate = row => {
    const n = {};
    F.forEach((field, i) => {
      const v = row[i];
      n[field] = field === "kind" ? K[v]
        : field === "status" ? S_[v]
        : field === "file" ? (FI[v] || "")
        : (v == null ? (field === "line" ? null : "") : v);
    });
    return n;
  };
  return MAPDATA.pages.map(p => ({
    ...p,
    nodes: p.nodes.map(inflate),
    edges: p.edges.map(e => ({source: e[0], target: e[1], status: S_[e[2]]})),
  }));
})();
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
  backend: new Set(["url", "view", "model", "admin_action", "signal_receiver",
                    "template_tag", "management_command"]),
  css: new Set(["css_selector", "css_token_def", "css_token_use"]),
};

// Empty means "every status". A Set rather than a single value because "show me the
// unresolved AND the unused" - the two that are actionable - is the common ask.
const statusFilter = new Set();

const ROWS_PER_PAGE = 60;
// Panel state lives here rather than beside the panel code: the canvas reads `mode` to
// decide its lens, and fillPages() runs before the panel section of this file is reached.
let mode = "map", cq = "", cstatus = "", shown = ROWS_PER_PAGE, asList = false;

const svg = document.getElementById("cv");
const sheet = document.getElementById("detail");
const dbody = document.getElementById("dbody");
const pages = document.getElementById("pg");
const crumb = document.getElementById("crumb");
const byId = new Map();
PAGES.forEach(p => p.nodes.forEach(n => byId.set(n.id, n)));

// How many symbols each file has in the whole scan, so the breadcrumb can say "210 of
// 674" rather than showing 210 boxes and letting a reader conclude that is all of it.
//
// Counted from FILES - the same numbers the Files view puts on its badges - and NOT from
// the drawn pages. The canvas can only show what a page entry reaches, so counting what
// it drew would always report "210 of 210" and quietly hide the 464 symbols in that file
// that no page reaches at all. Those are exactly the ones worth knowing about.
const FILE_TOTALS = new Map(
  FILES.map(f => [f.path, Object.values(f.counts || {}).reduce((a, n) => a + n, 0)])
);

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
    const drawn = lensed(p).length;
    const tail = counts ? `${counts[i]} changed` : `${drawn} node${drawn === 1 ? "" : "s"}`;
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

// Clicking a colour in the key filters the canvas to that status. Toggling, so several
// can be on at once, and clicking the last one off restores everything rather than
// leaving an empty canvas that reads as "nothing here".
const colourkey = document.getElementById("colourkey");
colourkey.addEventListener("click", event => {
  const button = event.target.closest("button.k[data-status]");
  if (!button) return;
  const status = button.dataset.status;
  if (statusFilter.has(status)) statusFilter.delete(status);
  else statusFilter.add(status);
  syncStatusKey();
  focus = null;
  fillPages();
  draw();
});

function syncStatusKey() {
  colourkey.classList.toggle("filtering", statusFilter.size > 0);
  colourkey.querySelectorAll("button.k[data-status]").forEach(button => {
    button.setAttribute("aria-pressed", statusFilter.has(button.dataset.status) ? "true" : "false");
  });
}
syncStatusKey();

// The nodes a page contributes under the current lens AND status filter. One function,
// because the page selector's count and the canvas must agree: a dropdown that promises
// "16 nodes" over a canvas showing two is the tool lying about its own view.
function lensed(p) {
  const kinds = SECTION_KINDS[mode];
  return p.nodes.filter(n =>
    (!kinds || n.kind === "page" || kinds.has(n.kind)) &&
    (!statusFilter.size || n.kind === "page" || statusFilter.has(n.status)));
}

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

// A page's whole node set, drawn at once, is the point - but the not-reached buckets are
// 17,967 template elements, and no layout makes that readable. Past this it draws a
// screenful and says what it left out, rather than either hanging or silently lying about
// how much is there. Narrowing (a file, a filter, a commit) always wins over the cap.
const MAX_DRAW = 2000;
let capped = 0;

function capping(ids) {
  capped = Math.max(ids.size - MAX_DRAW, 0);
  if (!capped) return ids;
  return new Set([...ids].slice(0, MAX_DRAW));
}

function visible(p) {
  capped = 0;
  if (only) return changedIn(p);
  if (isolate && lit) return chainOf(p, lit);
  if (fileFilter) return new Set(p.nodes.filter(n => n.file === fileFilter).map(n => n.id));
  // Everything this page touches, in one canvas. Showing only modules until a reader
  // drilled in hid the whole point: which symbols connect and which stand alone.
  if (!focus) {
    return capping(new Set(lensed(p).map(n => n.id)));
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

// A label longer than the box loses its end, and the end is the half that identifies it:
// `api/announcements/m…` and `api/announcements/p…` are the same string on screen, and
// `mark_announcement_v…` could be viewed, visible or void. Both ends survive instead, so
// the middle - which is almost always a shared prefix - is what gets dropped. The full
// value is on the node's <title>, and in the panel, either way.
function fit(text, max) {
  const value = String(text == null ? "" : text);
  if (value.length <= max) return value;
  // Two thirds to the front: a path's tail is short and decisive ("/pbits-notif" ->
  // "-notify/"), its head is the part that repeats.
  const tail = Math.max(4, Math.floor((max - 1) / 3));
  return value.slice(0, max - 1 - tail) + "…" + value.slice(-tail);
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

// How many nodes the filter box matched, said out loud. Fading is only meaningful when
// something survives it: with no matches every node dimmed at once, which is a canvas of
// ghosts and edge spaghetti that reads as a bug, not as "nothing here is called that".
function reportMatches(drawn, matched) {
  const box = document.getElementById("qn");
  if (!box) return;
  box.classList.toggle("none", Boolean(query) && matched === 0);
  box.textContent = !query ? ""
    : matched === 0 ? `no match in ${drawn}`
    : `${matched} of ${drawn}`;
}

// The layout is nine trial placements over every node on the page - the single most
// expensive thing here - and it was recomputed on every draw, including the draws that
// only moved the viewport. Nothing about a pan changes where a node sits relative to its
// neighbours, so the answer is cached against the things that DO change it.
let _layout = {key: null, keep: null, value: null};

function layoutKey() {
  return [current, mode, focus, fileFilter, only, isolate ? lit : "", asList].join("\u0000");
}

function layoutFor(p) {
  const key = layoutKey();
  if (_layout.key !== key) {
    const keep = visible(p);
    // `capped` is set as a side effect of visible(); a cached draw must not forget it.
    _layout = {key, keep, capped, value: layout(p, keep)};
  }
  capped = _layout.capped;
  return _layout;
}

function draw() {
  const p = PAGES[current];
  if (!p) return;
  pages.value = String(current);
  const here = p.where ? `${p.title} · ${p.where}` : p.title;
  const drawnHere = fileFilter ? p.nodes.filter(n => n.file === fileFilter).length : 0;
  const inFile = FILE_TOTALS.get(fileFilter) || drawnHere;
  crumb.textContent = fileFilter
      ? `${here} › ${fileFilter} — ${drawnHere} of ${inFile} symbols`
        + (drawnHere < inFile ? "; the rest are not reached from any page" : "")
    : focus ? `${here} › ${(byId.get(focus) || {}).label || ""}`
    : `${here} — pick a module`;
  document.getElementById("up").hidden = !focus;
  const cap = document.getElementById("capnote");
  if (cap) {
    cap.textContent = capped
      ? `Showing ${MAX_DRAW.toLocaleString()} of ${(MAX_DRAW + capped).toLocaleString()}`
        + " on this canvas. Pick a file in Files, or use the filter, to see the rest."
      : "";
  }
  // A commit that touched only files the scan does not read has an empty changed set.
  // Drawing that as a bare page node reads as a broken map rather than as an answer.
  if (only && !Object.keys(CHANGED).length) {
    svg.innerHTML = `<text x="20" y="40" class="col">nothing the scan reads changed in this commit</text>`;
    return;
  }
  const {keep, value: {pos, columns, width, height}} = layoutFor(p);
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
  // Counted before the loop that fades, so "nothing matched" can be treated as its own
  // answer rather than as 1,450 individually-dimmed nodes.
  const drawnNodes = p.nodes.filter(n => keep.has(n.id));
  const matched = query ? drawnNodes.filter(hit).length : drawnNodes.length;
  const fading = !query || matched > 0;
  reportMatches(drawnNodes.length, matched);
  const chain = lit && !isolate ? chainOf(p, lit) : null;
  const out = ['<g id="vp">'];
  columns.forEach(c => out.push(
    `<text class="col" x="${c.x}" y="44">${esc(c.label)} ${c.count}</text>`));
  p.edges.forEach(e => {
    const a = pos.get(e.source), b = pos.get(e.target);
    if (!a || !b) return;
    const ends = [byId.get(e.source), byId.get(e.target)];
    const dim = (chain && !(chain.has(e.source) && chain.has(e.target)))
      || (fading && query && !ends.every(n => n && hit(n)));
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
  // Labels used to be left out of the markup below a zoom threshold, which made every
  // wheel tick a full rebuild. They are always emitted now and hidden by a class on the
  // svg (see applyView), so the threshold costs one attribute instead of re-parsing
  // thousands of nodes.
  drawnNodes.forEach(n => {
    const q = pos.get(n.id); if (!q) return;
    const ch = CHANGED[n.id];
    const stroke = ch ? CH[ch] : (S[n.status] || "var(--dim)");
    const alone = n.status === "unresolved" || n.status === "unused";
    const label = fit(n.label, 20);
    // The drawn box is 20px tall and the view opens scaled down, so on a phone the
    // visible target can be 8px. The transparent rect below it fills the row pitch.
    const shown = (!fading || hit(n)) && (!chain || chain.has(n.id));
    out.push(`<g class="nd${shown ? "" : " faded"}${n.id === lit ? " lit" : ""}" data-id="${n.id}">
      <rect x="${q.x - 5}" y="${q.y - 5}" width="160" height="30" fill="transparent"
            pointer-events="all"/>
      <rect x="${q.x}" y="${q.y}" width="150" height="20" rx="5"
            fill="${alone ? (F[n.status] || "var(--panel)") : "var(--panel)"}"
            stroke="${stroke}" stroke-width="${ch ? 3 : 1.5}"/>
      <title>${esc(n.label)}${n.file ? "\n" + esc(n.file) + (n.line ? ":" + n.line : "") : ""}</title>
      <text x="${q.x + 7}" y="${q.y + 14}">${esc(label)}</text></g>`);
  });
  out.push("</g>");
  svg.innerHTML = out.join("");
  applyView();
}

// The whole cost of a pan. The viewport is one transform on one group, so moving it is a
// single attribute write no matter how many thousand nodes are under it - where rebuilding
// the markup was O(nodes) per pointermove frame, which is what made a 1,450-node page
// unusable and would have made a 15,000-node one impossible.
function applyView() {
  const g = document.getElementById("vp");
  if (!g) return;
  g.setAttribute("transform", `translate(${view.x},${view.y}) scale(${view.k})`);
  // Below this zoom the text is sub-pixel anyway; hiding it by class keeps the DOM stable
  // and is what makes zooming out of a large page cheap rather than catastrophic.
  svg.classList.toggle("nolabels", view.k < 0.34);
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

// Five unlabelled boxes down a rule do not say which end is the browser and which is the
// database, and a reader tracing a bug needs to know which way round it is. Each hop is
// numbered, "1 of 5", and the last one says so.
function hop(id, here, step, total) {
  const n = byId.get(id); if (!n) return "";
  const code = n.context || n.snippet;
  // The count lives in the section heading ("2 hops"), so a per-row "of 2" only repeats
  // it. The number and the word `last` are what a row needs: where am I, and is this the
  // end of the line.
  const mark = step ? `<span class="hn">${step}</span>` : "";
  const end = step && step === total ? `<span class="hs">last</span>` : "";
  return `<div class="hop${id === here ? " at" : ""}">
    <div class="hk">${mark}${esc(n.kind)}${end}</div>
    <div class="hl">${esc(n.label)}</div>
    ${n.file ? `<div class="hf">${loc(n.file, n.line)}</div>` : ""}
    ${code ? `<button class="code" data-code="${esc(id)}">code</button>` : ""}</div>`;
}

// Code opens on request, over the page. Inline, one chain filled the panel with six
// listings a reader had not asked for and had to scroll past to see the shape of the path.
const codebox = document.getElementById("codebox");
// A finding is one line, and one line is never enough to judge it. The viewer fetches the
// WHOLE file from the server this page is being served by - the same origin, the same
// token, an allowlist of exactly the files the scan named - numbers every line, highlights
// the one in question, and marks every OTHER finding in the same file in the gutter, so a
// reader sees at once whether they are looking at one problem or a pattern.
//
// Opened as a bare file:// document there is no server to ask, so it falls back to the
// snippet and says why. That degradation is the reason the file is still self-contained.
const SOURCE_CACHE = new Map();

// Enough highlighting to read by, in about thirty lines: strings, comments, numbers and
// keywords. A real highlighter is a megabyte and this page already carries one.
const KEYWORDS = new Set([
  "async","await","break","case","catch","class","const","continue","def","del","elif",
  "else","except","export","extends","finally","for","from","function","global","if",
  "import","in","is","lambda","let","new","not","or","and","pass","raise","return","self",
  "static","super","switch","this","throw","try","typeof","var","while","with","yield",
  "True","False","None","true","false","null","undefined","interface","type","enum",
  "public","private","protected","implements","namespace","declare","readonly",
]);

// ONE pass over the raw line, not a stack of replaces over the escaped one. Chaining
// regexes was the obvious version and it corrupted itself: escaping turned an apostrophe
// into `&#x27;`, the string rule wrapped that, and then the NUMBER rule matched the 39
// inside the entity and the keyword rule matched the `class` in the tag it had just
// written. The output was HTML source printed as code. A scanner cannot do that, because
// each token's text is escaped exactly once, after it is known what the token is.
function highlight(line) {
  let out = "";
  let i = 0;
  const emit = (cls, text) => { out += cls ? `<i class="${cls}">${esc(text)}</i>` : esc(text); };
  while (i < line.length) {
    const ch = line[i];
    const two = line.slice(i, i + 2);
    if (two === "//" || ch === "#") { emit("c", line.slice(i)); break; }
    if (two === "/*") {
      const end = line.indexOf("*/", i + 2);
      emit("c", end === -1 ? line.slice(i) : line.slice(i, end + 2));
      i = end === -1 ? line.length : end + 2;
      continue;
    }
    if (ch === '"' || ch === "'" || ch === "`") {
      let j = i + 1;
      while (j < line.length && line[j] !== ch) j += line[j] === "\\" ? 2 : 1;
      emit("s", line.slice(i, Math.min(j + 1, line.length)));
      i = j + 1;
      continue;
    }
    if (ch >= "0" && ch <= "9") {
      let j = i;
      while (j < line.length && /[\w.]/.test(line[j])) j++;
      emit("n", line.slice(i, j));
      i = j;
      continue;
    }
    if (/[A-Za-z_$]/.test(ch)) {
      let j = i;
      while (j < line.length && /[\w$]/.test(line[j])) j++;
      const word = line.slice(i, j);
      emit(KEYWORDS.has(word) ? "k" : "", word);
      i = j;
      continue;
    }
    emit("", ch);
    i += 1;
  }
  return out;
}

function findingsIn(file) {
  // Every other thing the scan claims about this file, so the gutter can mark them.
  const marks = new Map();
  PAGES.forEach(p => p.nodes.forEach(node => {
    if (node.file === file && node.line && node.status !== "connected") {
      marks.set(node.line, node.status);
    }
  }));
  return marks;
}

function renderSource(file, text, line, title) {
  const marks = findingsIn(file);
  const lines = text.split("\n");
  const width = String(lines.length).length;
  const body = lines.map((raw, i) => {
    const number = i + 1;
    const mark = marks.get(number);
    const cls = ["ln", number === line ? "here" : "", mark ? "mark " + mark : ""].join(" ");
    return `<div class="${cls}" id="L${number}"><span class="num">${
      String(number).padStart(width, " ")}</span><span class="src">${highlight(raw)}</span></div>`;
  }).join("");
  document.getElementById("codetitle").textContent = title;
  const holder = document.getElementById("codebody");
  holder.innerHTML = `<div class="listing">${body}</div>`;
  const target = document.getElementById("L" + line);
  if (target) target.scrollIntoView({block: "center"});
  const other = marks.size - (marks.has(line) ? 1 : 0);
  document.getElementById("codenote").textContent = other
    ? `${lines.length} lines · ${other} other finding${other === 1 ? "" : "s"} in this file, marked in the gutter`
    : `${lines.length} lines`;
}

async function showCode(id) {
  const n = byId.get(id); if (!n) return;
  const title = n.label + (n.file ? "  —  " + n.file + (n.line ? ":" + n.line : "") : "");
  codebox.hidden = false;
  document.getElementById("codetitle").textContent = title;
  document.getElementById("codenote").textContent = "";
  const fallback = () => {
    document.getElementById("codebody").textContent = n.context || n.snippet || "";
    document.getElementById("codenote").textContent =
      "Showing the snippet: the whole file is available when this map is served " +
      "(seamcheck map), not when it is opened as a file.";
  };
  if (!n.file || location.protocol === "file:") { fallback(); return; }
  document.getElementById("codebody").textContent = "Loading " + n.file + " …";
  try {
    let text = SOURCE_CACHE.get(n.file);
    if (text === undefined) {
      const response = await fetch(location.pathname.replace(/\/$/, "") +
        "/source?path=" + encodeURIComponent(n.file));
      if (!response.ok) throw new Error(String(response.status));
      const data = await response.json();
      if (data.error) throw new Error(data.error);
      text = data.text;
      SOURCE_CACHE.set(n.file, text);
    }
    renderSource(n.file, text, n.line, title);
  } catch {
    fallback();
  }
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
    ${n.file ? `<div class="row">${loc(n.file, n.line)}</div>` : ""}
    ${n.note ? `<div class="note">${esc(n.note)}</div>` : ""}
    ${why(n.kind, n.status)}
    ${path.length ? `<div class="lbl">Path — browser to backend · ${path.length} hop${
      path.length === 1 ? "" : "s"}</div>` : ""}
    ${path.map((step, i) => hop(step, id, i + 1, path.length)).join("")}
    ${reaches.length ? `<div class="lbl">Reaches — ${reaches.length} from here</div>` +
      reaches.map((step, i) => hop(step, id, i + 1, reaches.length)).join("") : ""}`;
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

const zoomTo = k => { view.k = Math.min(3, Math.max(0.2, k)); applyView(); };

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
  view.x = e.clientX - drag.x; view.y = e.clientY - drag.y; applyView();
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
  applyView();
}, {passive: false});

// A pinch needs two fingers and some dexterity; these need one thumb.
document.getElementById("zi").onclick = () => zoomTo(view.k * 1.25);
document.getElementById("zo").onclick = () => zoomTo(view.k / 1.25);
document.getElementById("zf").onclick = () => {
  // Back to "unset", which is the signal draw() reads to re-fit the page to the screen.
  view = {x:0, y:0, k:1}; draw();
};
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
          ` <span>${esc(d.kind)}${d.file ? " · " + loc(d.file, d.line) : ""}</span></div>`
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

// --- a location you can click ------------------------------------------------------
// Every finding is a place in a file, and the distance between reading a path and having
// the cursor on that line was a copy, a paste and a lost train of thought. The scan
// stores paths relative to the repo; an editor URL needs an absolute one, so the root
// arrives once and the join happens here.
const ROOT = (OPEN.root || "").replace(/\/+$/, "");
function absolute(file) { return ROOT ? ROOT + "/" + file : file; }

function loc(file, line) {
  if (!file) return "";
  const text = esc(file) + (line ? ":" + line : "");
  const full = absolute(file) + (line ? ":" + line : "");
  if (!OPEN.href) {
    return `<span class="loc" data-copy="${esc(full)}" title="Click to copy">${text}</span>`;
  }
  // split/join, not replace: replace(string) substitutes the first match only, and a
  // scheme that names {path} twice would have shipped half a URL.
  const href = OPEN.href.split("{path}").join(absolute(file)).split("{line}").join(line || 1);
  return `<a class="loc" href="${esc(href)}" data-copy="${esc(full)}"
    title="Open in your editor · shift-click to copy the path">${text}</a>`;
}

// Clipboard, with a fallback: navigator.clipboard needs a secure context and this file
// is routinely opened over file://, where it is absent and reading it throws.
function copy(text, el) {
  const mark = () => {
    if (!el) return;
    el.classList.add("copied");
    setTimeout(() => el.classList.remove("copied"), 900);
  };
  try {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(mark, () => {});
      return;
    }
  } catch (_) { /* fall through to the textarea */ }
  const box = document.createElement("textarea");
  box.value = text;
  box.style.cssText = "position:fixed;top:-1000px;opacity:0";
  document.body.appendChild(box);
  box.select();
  try { document.execCommand("copy"); mark(); } catch (_) { /* nothing else to try */ }
  box.remove();
}

document.addEventListener("click", e => {
  const el = e.target.closest(".loc");
  if (!el) return;
  // A span has no href to follow, so a plain click is the copy. On a link, only
  // shift-click is - otherwise the editor never opens.
  if (el.tagName === "A" && !e.shiftKey) return;
  e.preventDefault();
  copy(el.dataset.copy || "", el);
});

// --- what a finding means -----------------------------------------------------------
// Two sentences per (kind, status), shipped once as a lookup rather than repeated on
// each of a thousand rows: what the scan observed, and what is usually actually true.
function why(kind, status) {
  const m = MEANING[kind + "|" + status] || MEANING["*|" + status];
  if (!m) return "";
  return `<div class="why"><p><b>means</b>${esc(m.means)}</p>
    <p><b>check</b>${esc(m.check)}</p></div>`;
}

// Rows arrive sorted worst-first then by location, so identical kinds land in runs. The
// explanation belongs on the first row of each run: printed on all 1,455 it is a wall of
// the same two sentences, and printed nowhere it is back to being a status word nobody
// can act on. Runs are recomputed after every filter, so whatever is on screen at the
// top of a group always carries its own explanation.
function whyOncePerRun(rows) {
  let previous = null;
  return rows.map(r => {
    const key = r.kind + "|" + r.status;
    const first = key !== previous;
    previous = key;
    return first && r.status !== "connected" ? why(r.kind, r.status) : "";
  });
}

// --- the review views, in the same shell -------------------------------------------
// One surface, not two: a second document meant a second render, a second link and a
// second mental model for the same scan. The map answers "what reaches what"; these
// answer "how many, of what kind, where" - a switch, not a separate page.
const D = CONSOLE, panel = document.getElementById("panel");
const pgwrap = document.getElementById("pgwrap"), viewer = document.getElementById("vw");
const listToggle = document.getElementById("aslist");
listToggle.onclick = () => { asList = !asList; switchTo(mode); };
// Overview opens, because "how is this project doing" is the question someone has when
// they open the file, and it is one screen rather than 30,000 nodes. The commit filter
// starts at "everything": defaulting it to the newest commit left the canvas holding one
// node, because a commit that only deletes something has nothing left to draw.
const OPENS_ON = "overview";

// Overview first: it is the answer to "how is this project doing", which is the question
// someone opening the file has. The map is the instrument you reach for second, once a
// number has told you where to point it. "Start here" was a third thing explaining the
// other two; its content lives in Overview now, where the numbers it explains are.
const VIEWS = [{key: "overview", title: "Overview", count: null},
               {key: "map", title: "Map", count: null},
               {key: "files", title: "Files", count: FILES.length}].concat(
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

function statusKey() {
  const of = k => MEANING["*|" + k] || {means: "", check: ""};
  return ["connected", "unresolved", "unused", "uncertain"].map(k =>
    `<div class="s"><h4><span class="badge ${k}">${k}</span></h4>
       <p>${esc(of(k).means)}</p><p class="c">${esc(of(k).check)}</p></div>`).join("");
}

// A count with no denominator is not a result. "1,319 unresolved" reads as a catastrophe
// or as nothing at all depending on whether the project has two thousand symbols or forty
// thousand; the share is the part that means something.
function pct(n, total) {
  if (!total) return "0%";
  const share = (n / total) * 100;
  return share > 0 && share < 0.1 ? "<0.1%" : `${share.toFixed(share < 10 ? 1 : 0)}%`;
}

function bar(counts, total) {
  return `<div class="stack">` + ["connected", "uncertain", "unused", "unresolved"]
    .filter(k => counts[k])
    .map(k => `<i class="${k}" style="width:${(counts[k] / total) * 100}%"
      title="${k} — ${counts[k].toLocaleString()} (${pct(counts[k], total)})"></i>`)
    .join("") + `</div>`;
}

const n = v => (v || 0).toLocaleString();

// The four statuses split two ways that matter, and only one of them is a to-do list.
function totals() {
  const all = {};
  ["connected", "unresolved", "unused", "uncertain"].forEach(k => {
    all[k] = (D.backend[k] || 0) + (D.frontend[k] || 0);
  });
  const sum = c => Object.values(c).reduce((a, v) => a + v, 0);
  return {all, total: sum(all), looking: (all.unresolved || 0) + (all.unused || 0)};
}

// Findings drawn against FINDINGS, not against the codebase. A stacked bar of all four
// statuses spends 96% of its width on connected and uncertain - the two nobody acts on -
// and leaves the 4% you came for as a sliver at the far right. Inside the actionable set
// the split is legible, which is the only place a bar earns its ink here.
function heroHtml() {
  const {all, total, looking} = totals();
  const rest = total - looking;
  const seg = k => !all[k] ? "" :
    `<i class="${k}" style="width:${(all[k] / looking) * 100}%"
        title="${k} — ${n(all[k])}"></i>`;
  const key = k => !all[k] ? "" : `<span class="kk"><i class="${k}"></i>
    <b>${n(all[k])}</b> ${k} <em>${esc((MEANING["*|" + k] || {}).means || "")}</em></span>`;

  return `<div class="hero">
    <div class="hk">To look at</div>
    <div class="hv">${n(looking)}<span class="hp">${pct(looking, total)} of ${n(total)} symbols</span></div>
    ${looking ? `<div class="split">${seg("unresolved")}${seg("unused")}</div>
    <div class="splitkey">${key("unresolved")}${key("unused")}</div>`
      : `<div class="gap">Nothing unresolved and nothing unused. That is the whole to-do list.</div>`}
    <p class="rest">The other ${n(rest)} are not findings —
      <b>${n(all.connected)}</b> connected (${pct(all.connected, total)}), evidence attached, and
      <b>${n(all.uncertain)}</b> uncertain (${pct(all.uncertain, total)}), which is the scan
      declining to guess rather than a claim that anything is dead.</p>
  </div>`;
}

// Backend against frontend, on ONE scale. Two cards each drawn to their own width made 854
// and 35,890 look like comparable quantities sitting side by side, and reported each as a
// share of the total - which says only "the frontend is bigger", a fact about the project
// and not about its health. The bar is size; the darker inset is that side's own findings;
// the rate is findings over that side's own symbols. On this project it says the thing that
// matters in one line: the backend is clean and every finding is in the frontend.
function sidesHtml() {
  const sum = c => Object.values(c).reduce((a, v) => a + v, 0);
  const rows = [["Frontend", D.frontend], ["Backend", D.backend]]
    .map(([name, c]) => ({
      name, total: sum(c), finds: (c.unresolved || 0) + (c.unused || 0),
    }))
    .sort((a, b) => b.total - a.total);
  const widest = Math.max(...rows.map(r => r.total), 1);

  return `<h3 class="sec">Where the work is</h3>
    <div class="wtable">
      <div class="wrow whead"><div class="wname"></div><div class="wbar"></div>
        <div class="wnum">symbols</div><div class="wnum">to look at</div><div class="wnum">rate</div></div>
      ${rows.map(r => `<div class="wrow">
        <div class="wname">${esc(r.name)}</div>
        <div class="wbar"><i style="width:${(r.total / widest) * 100}%">
          <em style="width:${r.total ? (r.finds / r.total) * 100 : 0}%"></em></i></div>
        <div class="wnum">${n(r.total)}</div>
        <div class="wnum ${r.finds ? "hot" : "cool"}">${n(r.finds)}</div>
        <div class="wnum ${r.finds ? "hot" : "cool"}">${pct(r.finds, r.total)}</div>
      </div>`).join("")}
    </div>
    <p class="gloss">Both bars share one scale, so the sizes are comparable; the darker inset
      is that side's own findings. The rate is findings over that side's own symbols — a share
      of the whole project would only tell you which half is bigger.</p>`;
}

function overviewHtml() {
  return `<h2>Overview</h2><p class="blurb">What the scan is willing to claim about this
    commit, worst first. A count with no denominator is not a result.</p>

    ${heroHtml()}
    ${sidesHtml()}

    <h3 class="sec">What the four words claim</h3>
    <div class="skey">${statusKey()}</div>
    <div class="caveat"><b>Two things to know before you read a number.</b>
      Seamcheck reads source; it never runs your code, so every row is evidence rather
      than a verdict. And ${esc(BLIND_SPOTS)}</div>

    <h3 class="sec">Backlog by kind</h3>
    ${D.groups.length ? D.groups.map(g =>
      `<div class="row"><span class="badge uncertain">${g[1]}</span>
       <div class="t">${esc(g[0])}</div></div>`).join("")
      : `<div class="gap">Nothing the scan is willing to claim.</div>`}

    <h3 class="sec">Where to look next</h3>
    <div class="doc"><ol>
      <li><b>Findings</b>, filtered to <code>unresolved</code> — things the code reaches
        for that are not there. The shortest path to a real bug.</li>
      <li><b>DOM Wiring</b> — elements more than one file writes. Two writers on one
        element is the usual cause of a value that flickers or reverts.</li>
      <li><b>Map</b> — click any node to see the chain that reaches it, hop by hop. The
        <b>PAGE</b> picker also has the buckets for everything no page reaches.</li>
      <li><b>Files</b> — click a file to draw its symbols; <code>edit</code> opens it.
        Any <code>file:line</code> anywhere here opens in your editor.</li>
    </ol></div>

    <div class="colophon">
      <p>Built by
        <a href="https://github.com/dardameiz/seamcheck" target="_blank" rel="noreferrer">Seamcheck</a>
        — free, MIT, no company behind it. Got a finding wrong?
        <a href="https://github.com/dardameiz/seamcheck/issues" target="_blank" rel="noreferrer">Say so</a>
        — that is worth more than money.</p>
      <a href="https://github.com/sponsors/dardameiz" class="sponsor"
         target="_blank" rel="noreferrer">\u2665 Sponsor Seamcheck</a>
    </div>`;
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
        `<div class="fl" data-path="${esc(f.path)}" style="padding-left:${depth * 13 + 20}px"
              title="Draw this file's symbols on the map">
           <span class="fn">${esc(f.path.split("/").pop())}</span>
           ${bar(f)}${flags(f)}
           <span class="go">on map \u2192</span>
           ${OPEN.href
             ? `<a class="loc edit" href="${esc(OPEN.href.split("{path}").join(absolute(f.path))
                 .split("{line}").join(1))}" data-copy="${esc(absolute(f.path))}"
                 title="Open the file in your editor">edit</a>` : ""}</div>`).join("");
    if (name === null) return inner;
    return `<details${depth < 2 || needle ? " open" : ""}>
      <summary style="padding-left:${depth * 13}px">${esc(name)}</summary>${inner}</details>`;
  };
  return `<h2>Files</h2><p class="blurb"><b>Click a file to draw its symbols on the
    map</b> — that is what this view is for: it answers "what of this file is actually
    wired to anything", which the folder tree alone cannot. <code>edit</code> is the
    other thing, and opens the file in your editor.<br><br>
    Every file the scan read, in the shape the repository has. The bar is how many of a
    file's own declarations appear in the graph at all — that is coverage, not a finding:
    a helper that makes no request and touches no element has nothing to model.</p>
    <div class="tools"><input id="fq" type="search"
      placeholder="Filter ${shown.length} of ${FILES.length} files" value="${esc(fileQuery)}"></div>
    <div class="tree">${walk(root, null, 0)}</div>`;
}

// Which page shows the most of one file. A file's symbols are spread across the pages
// that load it, and only one page can be drawn at a time.
function bestPageFor(path) {
  let best = current, most = -1;
  PAGES.forEach((p, i) => {
    const n = p.nodes.reduce((count, node) => count + (node.file === path ? 1 : 0), 0);
    if (n > most) { most = n; best = i; }
  });
  return best;
}

function renderPanel() {
  if (mode === "files") {
    panel.innerHTML = treeHtml();
    panel.querySelectorAll(".fl").forEach(el => {
      el.onclick = e => {
        // The row filters the canvas; the `edit` link inside it opens an editor. Without
        // this the link did both, and the map jumped out from under the reader.
        if (e.target.closest(".loc")) return;
        fileFilter = el.dataset.path;
        // ...and go to the page that actually holds this file. Keeping whatever page was
        // selected answered "what of this file is on the page you happened to be looking
        // at", which for push_arena.js was 3 symbols out of 674 - a blank-looking canvas
        // that reads as the file being unwired.
        current = bestPageFor(fileFilter);
        viewer.value = "map"; switchTo("map");
      };
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
  if (mode === "changes") { panel.innerHTML = changesHtml(); return; }
  const sec = D.sections.find(x => x.key === mode);
  if (!sec) return;
  if (sec.unavailable) {
    panel.innerHTML = `<h2>${esc(sec.title)}</h2><p class="blurb">${esc(sec.blurb)}</p>
      <div class="gap">${esc(sec.unavailable)}</div>`;
    return;
  }
  // Offer only statuses this section actually contains. A findings list holds nothing
  // connected by definition, so offering "connected" gave a filter that could only ever
  // answer "No rows match" - the control implying data that cannot exist.
  const counts = {};
  sec.rows.forEach(r => { counts[r.status] = (counts[r.status] || 0) + 1; });
  // Counts come from the WHOLE section where the scan sent them, not from the sample the
  // page holds - a filter that says "unused (12)" when the section has 2,380 of them is
  // describing the payload rather than the codebase.
  const totals = sec.status_totals || counts;
  const present = ["unresolved", "unused", "uncertain", "connected"].filter(v => totals[v]);
  if (cstatus && !counts[cstatus]) cstatus = "";

  const needle = cq.toLowerCase();
  const rows = sec.rows.filter(r => (!cstatus || r.status === cstatus) &&
    (!needle || (r.label + " " + r.file + " " + r.kind).toLowerCase().includes(needle)));
  const page = rows.slice(0, shown);
  const notes = whyOncePerRun(page);
  panel.innerHTML = `<h2>${esc(sec.title)}</h2><p class="blurb">${esc(sec.blurb)}</p>
    <div class="tools">
      <input id="cq" type="search" placeholder="Filter ${sec.rows.length} rows" value="${esc(cq)}">
      <select id="cst"><option value="">any status</option>
        ${present.map(v =>
          `<option value="${v}"${v === cstatus ? " selected" : ""}>${v} (${totals[v].toLocaleString()})</option>`
        ).join("")}
      </select></div>
    ${page.map((r, i) => `<div class="row"><span class="badge ${esc(r.status)}">${esc(r.status)}</span>
       <div class="t">${esc(r.label)}</div>
       <div class="w">${esc(r.kind)}${r.file ? " · " + loc(r.file, r.line) : ""}</div>
       ${r.note ? `<div class="n">${esc(r.note)}</div>` : ""}
       ${notes[i]}</div>`).join("")
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

// The trend. Answers the question a single before-and-after cannot: which way is this
// going. A codebase gets one row per scan, so the chart is real history rather than two
// points and a hopeful line between them.
function trendChart(entries) {
  const W = 720, H = 180, PAD = 30;
  const values = entries.map(e => e.findings);
  const top = Math.max(...values, 1);
  const x = i => PAD + (entries.length < 2 ? 0 : i * (W - PAD * 2) / (entries.length - 1));
  const y = v => H - PAD - (v / top) * (H - PAD * 2);
  const points = entries.map((e, i) => `${x(i)},${y(e.findings)}`).join(" ");
  const area = `${PAD},${H - PAD} ${points} ${x(entries.length - 1)},${H - PAD}`;
  const ticks = [0, Math.round(top / 2), top];
  return `<svg viewBox="0 0 ${W} ${H}" role="img"
    aria-label="findings over ${entries.length} scans, ${values[0]} to ${values[values.length - 1]}">
    ${ticks.map(t => `<line class="grid" x1="${PAD}" x2="${W - PAD}" y1="${y(t)}" y2="${y(t)}"/>
      <text class="lbl" x="4" y="${y(t) + 3}">${n(t)}</text>`).join("")}
    <polygon class="band" points="${esc(area)}"/>
    <polyline class="line" points="${esc(points)}"/>
    ${entries.map((e, i) => `<circle class="dot" cx="${x(i)}" cy="${y(e.findings)}" r="3">
      <title>${esc(e.sha.slice(0, 12))} · ${esc(e.at.slice(0, 10))} · ${n(e.findings)} findings</title>
    </circle>`).join("")}
    <text class="lbl" x="${PAD}" y="${H - 8}">${esc(entries[0].at.slice(0, 10))}</text>
    <text class="lbl" x="${W - PAD}" y="${H - 8}" text-anchor="end">${
      esc(entries[entries.length - 1].at.slice(0, 10))}</text>
  </svg>`;
}

function changesHtml() {
  const sec = D.sections.find(x => x.key === "changes");
  const entries = (SERIES && SERIES.entries) || [];
  const head = `<h2>Changes</h2>`;
  if (entries.length < 2) {
    // One scan is not a trend, and saying so beats drawing a line through a single point.
    return head + `<p class="blurb">Every scan is recorded. Once there are two, this
      becomes a trend line and the list of what moved most.</p>
      <div class="gap">${entries.length ? "1 scan recorded so far." : "No scans recorded yet."}
      Run a scan on another commit and come back.</div>`;
  }
  const delta = SERIES.delta;
  const dir = delta < 0 ? "down" : "up";
  const word = delta < 0 ? "fewer" : "more";
  const first = SERIES.first, last = SERIES.last;
  return head + `<p class="blurb">Findings across every scan recorded, oldest first.</p>
    <p class="headline"><b class="${dir}">${n(Math.abs(delta))} ${word}</b> findings than the
      first recorded scan — ${n(first.findings)} on ${esc(first.at.slice(0, 10))}
      to ${n(last.findings)} on ${esc(last.at.slice(0, 10))}, across ${n(SERIES.span)} scans.</p>
    <div class="trend">${trendChart(entries)}</div>
    ${SERIES.movers.length ? `<h3 class="sub">What moved</h3><div class="movers">
      ${SERIES.movers.map(m => `<div class="m">
        <span>${esc(m.kind)}</span>
        <span class="num">${n(m.from)} → ${n(m.to)}</span>
        <span class="chg ${m.change < 0 ? "down" : "up"}">${m.change < 0 ? "" : "+"}${n(m.change)}</span>
      </div>`).join("")}</div>` : ""}
    ${sec && sec.rows && sec.rows.length ? `<h3 class="sub">Against the baseline</h3>` +
      sec.rows.slice(0, 40).map(r => `<div class="row">
        <span class="badge ${esc(r.status)}">${esc(r.status)}</span>
        <div class="t">${esc(r.label)}</div>
        <div class="w">${esc(r.kind)}${r.file ? " · " + loc(r.file, r.line) : ""}</div></div>`).join("")
      : ""}`;
}

function switchTo(next) {
  mode = next;
  rail.querySelectorAll(".nv").forEach(el =>
    el.setAttribute("aria-current", el.dataset.key === next));
  // The lens changes which nodes are drawn, so the page selector's counts change with it.
  // Leaving them alone promised "16 nodes" over a canvas showing two - the view disagreeing
  // with the control that chose it.
  fillPages();
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
  listToggle.textContent = asList ? "\u25f1  Show as map" : "\u2630  Show as list";
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


def _adapter_label(adapters) -> str:
    """What the scan read, named. "Backend Internals" is a noun; this says whose.

    Five frameworks across three languages can fill that section now, and a monorepo can
    hold two at once - cal.com serves Next.js and NestJS from one repository. A reader
    looking at a route deserves to know which backend put it there without inferring it
    from the shape of the paths.
    """
    if not adapters:
        return ""
    parts = []
    for adapter in adapters:
        name = adapter.get("name", "")
        language = adapter.get("language", "")
        parts.append(f"{name} · {language}" if language else name)
    return ' · <span class="read">' + html_lib.escape(" + ".join(parts)) + "</span>"


def _esc(value) -> str:
    return html_lib.escape(str(value if value is not None else ""))


# One node used to ship as an object with nine spelled-out keys. At 37,505 nodes that is
# ~3.6 MB of the words "snippet" and "context", another ~1.7 MB of the same file paths
# written out over and over, and ~0.9 MB of keys whose value is "". The map came to
# 15.9 MB, which is a lot to hand a browser to say the same thing.
#
# So nodes travel as arrays in a fixed order against three string tables, and the page
# expands them back into exactly the objects the rest of the script already reads. The
# decode is one pass at load; nothing downstream knows the difference.
_NODE_FIELDS = ("id", "label", "kind", "status", "file", "line", "note", "snippet", "context")


class _Table:
    """Repeated strings, sent once and referred to by index."""

    def __init__(self):
        self.values: list[str] = []
        self._index: dict[str, int] = {}

    def __call__(self, value: str) -> int:
        if value not in self._index:
            self._index[value] = len(self.values)
            self.values.append(value)
        return self._index[value]


def _payload(connectivity_map: ConnectivityMap) -> str:
    kinds, statuses, files = _Table(), _Table(), _Table()

    def _row(node):
        # Trailing empties are dropped, not sent as "": most nodes carry no note and the
        # buckets carry no source context at all.
        row = [
            node.id, node.label, kinds(node.kind), statuses(node.status),
            files(node.file or ""), node.line, node.note, node.snippet, node.context,
        ]
        while len(row) > 4 and not row[-1]:
            row.pop()
        return row

    pages = [
        {
            "page": page.page,
            "title": page.title or page.page,
            "where": page.where,
            "nodes": [_row(node) for node in page.nodes],
            "edges": [[e.source, e.target, statuses(e.status)] for e in page.edges],
        }
        for page in connectivity_map.pages
    ]
    data = {
        "columns": _COLUMNS,
        "changed": connectivity_map.changed,
        "commits": connectivity_map.commits,
        "fields": _NODE_FIELDS,
        "kinds": kinds.values,
        "statuses": statuses.values,
        "files": files.values,
        "pages": pages,
    }
    # </script> inside JSON would close the tag early; escaping the slash is the
    # standard defence and stays valid JSON.
    return json.dumps(data, separators=(",", ":")).replace("</", "<\\/")


def _console_payload(console) -> str:
    """The review sections, or an empty shell so the page still renders without them."""
    from dataclasses import asdict

    if console is None:
        return json.dumps({"backend": {}, "frontend": {}, "groups": [], "sections": []})
    # Rows carry a snippet the panel never draws, and a section can hold well over a
    # thousand of them - together a megabyte and a half on a page meant to open on a
    # phone. Send a screenful, and say so when a section is longer than what was sent.
    #
    # A share PER STATUS, not the first N overall. Rows arrive worst-first, so a flat cut
    # sent 400 unresolved rows and nothing else - and the status filter then offered a
    # choice between "unresolved" and "unresolved", with `unused` and `uncertain`
    # unreachable in the UI even though the section counted thousands of them.
    limit = 400
    per_status = 150

    def _section(section):
        out = asdict(section)
        rows = out.pop("rows")
        kept, seen = [], {}
        for row in rows:
            status = row.get("status")
            if seen.get(status, 0) >= per_status and len(kept) >= limit:
                continue
            seen[status] = seen.get(status, 0) + 1
            if len(kept) >= limit and seen[status] > per_status:
                continue
            kept.append(row)
        out["rows"] = [{k: v for k, v in row.items() if k != "snippet"} for row in kept]
        out["total"] = len(rows)
        # The true shape of the whole section, so the filter can show real counts and say
        # when what it holds is a sample rather than the lot.
        totals: dict[str, int] = {}
        for row in rows:
            totals[row.get("status")] = totals.get(row.get("status"), 0) + 1
        out["status_totals"] = totals
        return out

    data = {
        "backend": console.backend, "frontend": console.frontend,
        "groups": [[title, count, gloss] for title, count, gloss in console.groups],
        "sections": [_section(section) for section in console.sections],
    }
    return json.dumps(data).replace("</", "<\\/")


def render(connectivity_map: ConnectivityMap, console=None, files=None,
           repo_root: str = "", editor: str | None = None, series=None,
           adapters=None) -> str:
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
        f'<span class="meta">HEAD {_esc(connectivity_map.git_sha[:12])} · {_esc(mode)}'
        f'{_adapter_label(adapters)}</span>'
        f"</div>",
        '<div class="filters onlymob"><label class="wide"><span>View</span>'
        '<select id="vw"></select></label></div>',
        '<div class="filters">'
        '<label><span>Commit</span><select id="cm"></select></label>'
        '<label id="pgwrap"><span>Page</span><select id="pg"></select></label></div>',
        '<div class="crumbrow"><button id="up" type="button" hidden>\u2190</button>'
        '<button id="aslist" type="button" hidden>Show as list</button>'
        '<span id="crumb"></span>'
        '<input id="q" type="search" placeholder="Filter">'
        '<span id="qn" class="qn"></span></div>',
        '<div class="legendbar" id="colourkey">'
        '<button type="button" class="k connected" data-status="connected"><i></i>connected'
        '<em>something reaches it, evidence attached</em></button>'
        '<button type="button" class="k unresolved" data-status="unresolved"><i></i>unresolved'
        '<em>something reaches for it and it is not there</em></button>'
        '<button type="button" class="k unused" data-status="unused"><i></i>unused'
        '<em>both ends observable, nothing uses it</em></button>'
        '<button type="button" class="k uncertain" data-status="uncertain"><i></i>uncertain'
        '<em>no evidence either way \u2014 not a claim it is dead</em></button>'
        '<span class="k filled"><i></i>filled in<em>= unresolved or unused. The two to look at '
        'are drawn solid, the rest as outlines.</em></span>'
        '<span class="k hint" id="statushint">Click a colour to show only those.</span>'
        "</div>",
        '<div class="note" id="cmnote"></div>'
        '<div class="note capnote" id="capnote"></div>'
        '<div class="gone" id="gone"></div>',
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
        '<div id="codenote"></div>'
        '<pre id="codebody"></pre></div></div>',
        '<aside class="sheet" id="detail" hidden>'
        '<button class="x" id="dx" type="button" aria-label="Close">\u00d7</button>'
        '<div id="dbody"></div></aside>',
        "</main></div></div>",
        f"<script>const MAPDATA={_payload(connectivity_map)};</script>",
        f"<script>const CONSOLE={_console_payload(console)};</script>",
        f"<script>const SERIES={json.dumps(series or {'entries': []}).replace('</', '<\\/')};</script>",
        f"<script>const FILES={json.dumps(files or []).replace('</', '<\\/')};</script>",
        # Locations are stored relative to the repo; an editor URL needs an absolute
        # path. Ship the root once and let the page join, rather than absolutising
        # every one of tens of thousands of rows.
        f"<script>const OPEN={json.dumps({'root': repo_root, 'href': editors.scheme(editor)})};</script>",
        f"<script>const MEANING={json.dumps(meaning.table()).replace('</', '<\\/')};</script>",
        f"<script>const BLIND_SPOTS={json.dumps(meaning.BLIND_SPOTS)};</script>",
        f"<script>{_SCRIPT}</script>",
        "</body></html>",
    ])
