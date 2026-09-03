"""The visual map: one self-contained file, no network, pan/zoom/click.

Laid out left to right by kind, so the axis you read along IS the frontend-to-backend
seam: page -> module -> call -> endpoint -> URL -> view -> what the view returns. A
force-directed layout would need a library this file is not allowed to fetch, and would
hide the one relationship the map exists to show.
"""

from __future__ import annotations

import base64
import gzip
import html as html_lib
import json

from seamcheck import editors, meaning
from seamcheck.mapdata import ConnectivityMap

# Column order is the story: browser on the left, database on the right.
_COLUMNS = [
    # ── the browser ─────────────────────────────────────────────────────────
    ("page", "Page"),
    ("module", "Module"),
    ("js_call", "JS call"),
    ("dom_selector", "Selector"),
    ("multi_writer_element", "Multi-writer"),
    ("dom_attr", "Element"),
    ("css_selector", "CSS"),
    ("css_token_def", "Token"),
    ("css_token_use", "var()"),
    # ── the seam ────────────────────────────────────────────────────────────
    ("fetch_target", "Request"),
    ("json_field", "Response"),
    # ── the server ──────────────────────────────────────────────────────────
    ("url", "Route"),
    ("view", "Handler"),
    # A kind reaches its BAND through its column - place() reads COLS[c][0] and looks that
    # up in bandOfKind - so a kind with no entry here becomes "other" and falls into the
    # unnamed overflow group, whatever band it is listed in. Seventeen kinds were in that
    # state: every Celery task, every Stripe event, every GraphQL field, every background
    # job and every model was being filed under "everything else the scan found" while
    # BANDS said otherwise. Adding the store band is what made it visible.
    ("model", "Model"),
    ("url_reference", "Link"),
    ("signal_receiver", "Signal"),
    ("admin_action", "Admin action"),
    ("template_tag", "Template tag"),
    ("management_command", "Command"),
    # ── the store ───────────────────────────────────────────────────────────
    # Named for what a reader is looking at. Without these every data-layer card fell to
    # the "Other" heading in a region called "everything else the scan found", which is
    # where a mistyped column - the quiet bug this tool exists for - was being filed.
    ("db_table", "Table"),
    ("db_column", "Column"),
    ("db_function", "SQL function"),
    ("db_policy", "Row security"),
    ("db_table_use", "Reads table"),
    ("db_column_use", "Selects column"),
    ("db_function_use", "Calls rpc()"),
    ("redis_key", "Redis key"),
    ("redis_key_use", "Touches key"),
    ("redis_ttl", "No expiry"),
    ("firestore_rule", "Rule"),
    ("firestore_collection", "Collection"),
    ("cloud_function", "Cloud function"),
    ("cloud_function_use", "Calls function"),
    ("storage_bucket", "Bucket"),
    ("edge_function", "Edge function"),
    ("edge_function_use", "Invokes"),
    # ── reached without a browser ───────────────────────────────────────────
    ("celery_task", "Task"),
    ("celery_schedule", "Beat schedule"),
    ("job", "Background job"),
    ("job_enqueue", "Queues work"),
    ("job_schedule", "Cron"),
    ("stripe_webhook", "Stripe webhook"),
    ("stripe_event", "Stripe event"),
    ("graphql_field", "GraphQL field"),
    ("graphql_selection", "GraphQL query"),
    ("env_var", "Config key"),
    ("env_read", "Reads config"),
]

_CSS = """
/* ═══════════════════════════════════════════════════════════════════════
   FIVE PACKS. A pack is a complete world - palette, faces, corner radius,
   easing, wire weight - declared as custom properties on [data-pack]. Nothing
   below this block hard-codes a colour or a face, so switching packs re-skins
   the canvas, the panels and the terminal-ish surfaces in one repaint.

   AURORA is the default: dark grounds read better for a graph of hairlines,
   and a dark theme with no chroma in it reads as nothing at all. The other
   four are one keypress away.
   ═══════════════════════════════════════════════════════════════════════ */

/* ── AURORA (default) — deep indigo with real chroma. Dark, but alive. ── */
:root, :root[data-pack="aurora"] {
  --bg:#080a1a; --panel:#101430; --sunk:#0b0e22; --line:#232a55; --line-2:#39417a;
  --ink:#eef1ff; --muted:#7b83b8;
  --sig:#7c6cff; --sig-fill:#1c1a44; --sig-fill-hi:#282456;
  --ok:#2ee6a8; --crit:#ff6b8a; --warn:#c07cff; --dim:#7b83b8;
  --ok-fill:#0d2b23; --crit-fill:#301826; --warn-fill:#261a3a; --dim-fill:#171a34;
  --font:"DM Sans",system-ui,-apple-system,"Segoe UI",sans-serif;
  --mono:"IBM Plex Mono",ui-monospace,SFMono-Regular,Menlo,monospace;
  --display:"DM Sans",system-ui,sans-serif; --display-w:700; --display-ls:-.028em;
  --r-pill:999px; --r-card:18px; --r-node:12px;
  --ease:cubic-bezier(.34,1.4,.5,1); --dur:520ms;
  --wire:2.2; --glow:0 0 22px; --grid:rgba(124,108,255,.07);
  --wire-style:curve;
  --shadow:0 2px 8px rgba(0,0,0,.5),0 18px 50px rgba(50,30,140,.28);
  color-scheme:dark;
}
/* ── BLUEPRINT — paper, ink, cobalt. A drawing office. ────────────────── */
:root[data-pack="blueprint"] {
  --bg:#fbfaf7; --panel:#ffffff; --sunk:#f2f0ea; --line:#ddd8cc; --line-2:#c8c2b2;
  --ink:#141d2e; --muted:#7a8496;
  --sig:#2b5ce6; --sig-fill:#e8eefc; --sig-fill-hi:#d7e2fa;
  --ok:#0d8f68; --crit:#d94436; --warn:#7a4fd4; --dim:#8b93a3;
  --ok-fill:#e4f4ef; --crit-fill:#fceceb; --warn-fill:#f0ebfb; --dim-fill:#eef0f4;
  --font:"Archivo",system-ui,-apple-system,"Segoe UI",sans-serif;
  --mono:"IBM Plex Mono",ui-monospace,SFMono-Regular,Menlo,monospace;
  --display:"Archivo",system-ui,sans-serif; --display-w:700; --display-ls:-.032em;
  --r-pill:999px; --r-card:12px; --r-node:5px;
  --ease:cubic-bezier(.2,.9,.25,1); --dur:320ms;
  --wire:2; --glow:0 0 0; --grid:rgba(43,92,230,.055);
  --wire-style:ortho;
  --shadow:0 1px 2px rgba(20,29,46,.07),0 10px 28px rgba(20,29,46,.06);
  color-scheme:light;
}
:root[data-pack="blueprint"][data-mode="dark"] {
  --bg:#0d1424; --panel:#141d31; --sunk:#101728; --line:#22304c; --line-2:#33456a;
  --ink:#eaf0fb; --muted:#7f8da6;
  --sig:#6d9bff; --sig-fill:#16233d; --sig-fill-hi:#1e3050;
  --ok:#33c795; --crit:#f2705f; --warn:#a184f0; --dim:#8b93a3;
  --ok-fill:#0e2a22; --crit-fill:#2d1a1a; --warn-fill:#221c38; --dim-fill:#161c26;
  --grid:rgba(110,150,255,.07);
  --shadow:0 1px 2px rgba(0,0,0,.45),0 10px 28px rgba(0,0,0,.3);
  color-scheme:dark;
}
/* ── PHOSPHOR — the terminal, on the wall. One face doing every job. ──── */
:root[data-pack="phosphor"] {
  --bg:#080b09; --panel:#0e120f; --sunk:#0b0e0c; --line:#1e2a22; --line-2:#2f4438;
  --ink:#d6f5e3; --muted:#5c8a70;
  --sig:#4ee88f; --sig-fill:#0e2318; --sig-fill-hi:#143121;
  --ok:#4ee88f; --crit:#ff5f56; --warn:#ffb833; --dim:#5c8a70;
  --ok-fill:#0e2318; --crit-fill:#2a1210; --warn-fill:#2a2010; --dim-fill:#131d17;
  --font:"JetBrains Mono",ui-monospace,SFMono-Regular,Menlo,monospace;
  --mono:"JetBrains Mono",ui-monospace,SFMono-Regular,Menlo,monospace;
  --display:"JetBrains Mono",monospace; --display-w:700; --display-ls:-.02em;
  --r-pill:4px; --r-card:4px; --r-node:2px;
  --ease:steps(5,end); --dur:180ms;
  --wire:1.6; --glow:0 0 14px; --grid:rgba(78,232,143,.05);
  --wire-style:ortho;
  --shadow:0 0 0 1px rgba(78,232,143,.07);
  color-scheme:dark;
}
/* ── SIGNAL — warm paper, editorial serif, one loud orange. ───────────── */
:root[data-pack="signal"] {
  --bg:#f8f5ef; --panel:#fffdf9; --sunk:#efeae0; --line:#ded6c8; --line-2:#c3b8a4;
  --ink:#241d18; --muted:#867a6c;
  --sig:#e8532a; --sig-fill:#fceee9; --sig-fill-hi:#fae0d7;
  --ok:#177f6f; --crit:#c62f26; --warn:#7a3fa8; --dim:#8a7f72;
  --ok-fill:#e3f1ee; --crit-fill:#fbeae8; --warn-fill:#f1eaf8; --dim-fill:#eeeae3;
  --font:"DM Sans",system-ui,-apple-system,"Segoe UI",sans-serif;
  --mono:"Space Mono",ui-monospace,Menlo,monospace;
  --display:"Instrument Serif",Georgia,"Times New Roman",serif;
  --display-w:400; --display-ls:-.012em;
  --r-pill:999px; --r-card:16px; --r-node:16px;
  --ease:cubic-bezier(.16,1,.3,1); --dur:640ms;
  --wire:2.4; --glow:0 0 0; --grid:rgba(36,29,24,.045);
  --wire-style:organic;
  --shadow:0 1px 2px rgba(36,29,24,.06),0 14px 34px rgba(36,29,24,.07);
  color-scheme:light;
}
:root[data-pack="signal"][data-mode="dark"] {
  --bg:#181310; --panel:#221b16; --sunk:#141009; --line:#3a2f26; --line-2:#584839;
  --ink:#f6efe6; --muted:#9a8b7c;
  --sig:#ff7a4d; --sig-fill:#33201a; --sig-fill-hi:#442a20;
  --ok:#3fbfa8; --crit:#f2705f; --warn:#b98ae0; --dim:#9a8b7c;
  --ok-fill:#122b27; --crit-fill:#301a17; --warn-fill:#291f36; --dim-fill:#1e1812;
  --grid:rgba(246,239,230,.04);
  --shadow:0 1px 2px rgba(0,0,0,.5),0 14px 34px rgba(0,0,0,.35);
  color-scheme:dark;
}
/* ── SLATE — the one you take to a procurement meeting. ───────────────── */
:root[data-pack="slate"] {
  --bg:#f7f9fb; --panel:#ffffff; --sunk:#eef2f6; --line:#dce3ea; --line-2:#bcc7d2;
  --ink:#0f1b2a; --muted:#6b7a8c;
  --sig:#3b5bdb; --sig-fill:#eaeefc; --sig-fill-hi:#dae1fa;
  --ok:#0d8050; --crit:#c0362c; --warn:#6741d9; --dim:#7a8899;
  --ok-fill:#e4f2eb; --crit-fill:#fbeae8; --warn-fill:#eeeafb; --dim-fill:#eef1f5;
  --font:"Inter Tight",system-ui,-apple-system,"Segoe UI",sans-serif;
  --mono:"IBM Plex Mono",ui-monospace,SFMono-Regular,Menlo,monospace;
  --display:"Inter Tight",system-ui,sans-serif; --display-w:600; --display-ls:-.024em;
  --r-pill:8px; --r-card:10px; --r-node:4px;
  --ease:cubic-bezier(.4,0,.2,1); --dur:240ms;
  --wire:1.8; --glow:0 0 0; --grid:rgba(15,27,42,.038);
  --wire-style:ortho;
  --shadow:0 1px 2px rgba(15,27,42,.06),0 6px 18px rgba(15,27,42,.05);
  color-scheme:light;
}
:root[data-pack="slate"][data-mode="dark"] {
  --bg:#0d1420; --panel:#151e2c; --sunk:#101825; --line:#243244; --line-2:#38495e;
  --ink:#e8eef5; --muted:#7f8ea1;
  --sig:#7b93ff; --sig-fill:#1a2340; --sig-fill-hi:#233054;
  --ok:#3ecb92; --crit:#f2705f; --warn:#a68cf5; --dim:#7f8ea1;
  --ok-fill:#102b22; --crit-fill:#2d1a17; --warn-fill:#231d3a; --dim-fill:#17202c;
  --grid:rgba(232,238,245,.04);
  --shadow:0 1px 2px rgba(0,0,0,.4),0 8px 24px rgba(0,0,0,.32);
  color-scheme:dark;
}
/* Aurora and Phosphor are single-world designs on purpose - a light Aurora is not Aurora.
   The light/dark control hides itself for them rather than offering a worse version. */
* { box-sizing:border-box; }
/* An author `display` beats the UA rule that [hidden] relies on, so el.hidden = true read
   back as true while the zoom buttons and the breadcrumb stayed on screen over the panel. */
[hidden] { display:none !important; }
/* The canvas is the point. Everything else is a strip above it, and the whole document
   is exactly one screen tall so nothing scrolls the map out of view. */
/* overscroll-behavior:none stops the page rubber-banding when a drag reaches the edge of
   the canvas: on iOS that bounce steals the gesture mid-pan, which reads as the map
   fighting back rather than as the page doing something. */
body { margin:0; background:var(--bg); color:var(--ink); font-size:13.5px; overflow:hidden;
       overscroll-behavior:none;
       height:100dvh; -webkit-font-smoothing:antialiased;
       font-family:var(--font); }
/* Every value the scan read is shown in the font it was written in. */
.mono, #crumb, .meta, .hf, .hl, .row .t, .row .w, .tree, .fl, .covn,
.filters select, #q, .badge, .pill { font-family:var(--mono); }
/* ═══ THE FLOATING CHROME ═══════════════════════════════════════════════
   Nothing sits above the map. One dropdown, four pills, the zoom, and a readout
   that appears only when it has something to say. Everything else the script
   still writes to lives offstage.
   ═══════════════════════════════════════════════════════════════════════════ */
.offstage { position:absolute; width:1px; height:1px; overflow:hidden; clip-path:inset(50%);
            pointer-events:none; }
.hud { position:absolute; z-index:6; display:flex; gap:8px; align-items:center; }
/* The left corner holds the menu AND the page picker; the right holds Back, List and the
   appearance button. Both were unbounded, so on anything narrower than a desk monitor the
   List button sat on top of the page picker. The left is capped to what the right leaves,
   and the picker truncates rather than pushing. */
.hud.tl { top:14px; left:14px; max-width:calc(100% - 250px); flex-wrap:wrap; }
.hud.tl > * { min-width:0; }
.hud.tr { top:14px; right:14px; }
.hud.bl { bottom:14px; left:14px; right:14px; flex-wrap:wrap; }
.hud.br { bottom:14px; right:14px; flex-direction:column; }

.menuwrap { position:relative; }
/* The page picker, on the glass beside the menu, so moving from one page to the next is
   one tap instead of three. Same shape as the menu button: the two read as one strip. */
/* Capped so it cannot grow into the readout sitting centred behind it. */
.pagepick { flex:none; max-width:min(34vw, 340px); min-width:0; }
.pagepick select {
  max-width:100%; height:36px; padding:0 30px 0 13px; cursor:pointer;
  border:1.2px solid var(--line-2); background:var(--panel); color:var(--ink);
  border-radius:var(--r-pill); font-family:var(--mono); font-size:12.5px;
  box-shadow:var(--shadow); appearance:none; text-overflow:ellipsis;
  background-image:linear-gradient(45deg,transparent 50%,var(--muted) 50%),
                   linear-gradient(135deg,var(--muted) 50%,transparent 50%);
  background-position:calc(100% - 15px) 16px, calc(100% - 11px) 16px;
  background-size:5px 5px, 5px 5px; background-repeat:no-repeat;
}
.pagepick select:hover, .pagepick select:focus { border-color:var(--sig); outline:none; }
.menubtn {
  display:flex; align-items:center; gap:9px; cursor:pointer; padding:8px 15px 8px 12px;
  border:1.2px solid var(--line-2); background:var(--panel); color:var(--ink);
  border-radius:var(--r-pill); font-family:var(--font); font-size:13.5px; font-weight:600;
  box-shadow:var(--shadow); transition:border-color var(--dur) var(--ease); max-width:62vw;
}
.menubtn:hover, .menubtn:focus-visible { border-color:var(--sig); outline:none; }
.menubtn > span:last-child { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.menubtn .bars { display:flex; flex-direction:column; gap:3px; flex:none; }
.menubtn .bars i { width:14px; height:1.6px; background:currentColor; border-radius:2px;
                   transition:transform var(--dur) var(--ease), opacity var(--dur) var(--ease); }
.menubtn[aria-expanded="true"] .bars i:nth-child(1) { transform:translateY(4.6px) rotate(45deg); }
.menubtn[aria-expanded="true"] .bars i:nth-child(2) { opacity:0; }
.menubtn[aria-expanded="true"] .bars i:nth-child(3) { transform:translateY(-4.6px) rotate(-45deg); }

.mapsheet {
  position:absolute; top:calc(100% + 9px); left:0; width:min(360px, calc(100vw - 34px));
  background:var(--panel); border:1.2px solid var(--line); border-radius:var(--r-card);
  box-shadow:var(--shadow); padding:8px; z-index:9;
  opacity:0; transform:translateY(-8px) scale(.98); pointer-events:none;
  transition:opacity var(--dur) var(--ease), transform var(--dur) var(--ease);
  max-height:min(72dvh, 620px); overflow-y:auto; overscroll-behavior:contain;
}
.mapsheet.open { opacity:1; transform:none; pointer-events:auto; }
.mapsheet .mlab { font-family:var(--mono); font-size:10px; letter-spacing:.15em;
                  text-transform:uppercase; color:var(--muted); padding:10px 10px 6px; }
.mapsheet .msep { height:1px; background:var(--line); margin:6px 4px; }
.mapsheet .mfilters { display:grid; gap:7px; padding:0 8px 4px; }
.mapsheet .mfilters label { display:grid; gap:3px; }
.mapsheet .mfilters label > span { font-family:var(--mono); font-size:10px;
  letter-spacing:.13em; text-transform:uppercase; color:var(--muted); }
.mapsheet .mfilters select {
  width:100%; padding:8px 10px; border-radius:calc(var(--r-card) - 4px);
  border:1px solid var(--line); background:var(--bg); color:var(--ink);
  font-family:var(--mono); font-size:12.5px;
}
.mapsheet .msearch { padding:0 8px 8px; display:grid; gap:4px; }
.mapsheet .msearch input {
  width:100%; padding:9px 11px; border-radius:calc(var(--r-card) - 4px);
  border:1px solid var(--line); background:var(--bg); color:var(--ink);
  font-family:var(--mono); font-size:12.5px;
}
.mapsheet .msearch input:focus { outline:none; border-color:var(--sig); }
.mapsheet .qn { font-family:var(--mono); font-size:11px; color:var(--muted); padding:0 2px; }
/* The nav is the view control now, so it reads as a list of places rather than a rail. */
.mapsheet .nav { display:grid; gap:2px; padding:0 4px; }

/* Back / show-as-list share the appearance button's shape. */
.iconbtn {
  height:30px; min-width:30px; padding:0 9px; display:grid; place-items:center; cursor:pointer;
  border:1.2px solid var(--line-2); background:var(--panel); color:var(--muted);
  border-radius:var(--r-pill); font-family:var(--mono); font-size:13px;
  box-shadow:var(--shadow); transition:color var(--dur) var(--ease),
             border-color var(--dur) var(--ease);
}
.iconbtn:hover { color:var(--sig); border-color:var(--sig); }
.iconbtn.wide { font-size:12px; padding:0 13px; gap:6px; display:inline-flex;
                align-items:center; }
.iconbtn .swap { opacity:.75; font-size:13px; }

/* What you are looking at. Empty and invisible the rest of the time. */
.readout {
  position:absolute; top:14px; left:50%; transform:translateX(-50%); z-index:5;
  font-family:var(--mono); font-size:11.5px; color:var(--muted); pointer-events:none;
  background:var(--panel); border:1px solid var(--line); padding:6px 14px;
  border-radius:var(--r-pill); box-shadow:var(--shadow); white-space:nowrap;
  max-width:min(46vw, 640px); overflow:hidden; text-overflow:ellipsis;
  opacity:0; transition:opacity var(--dur) var(--ease);
}
.readout.show { opacity:1; }
/* The canvas is full-bleed, so the first row of the drawing has to clear the chrome. The
   layout leaves this much headroom above the first column heading. */
:root { --chrome-top:56px; --chrome-bottom:62px; }

/* ── the four words, as pills on the glass ─────────────────────────────── */
#colourkey .seg { display:flex; gap:8px; flex-wrap:wrap; border:0; background:none;
                  padding:0; border-radius:0; }
#colourkey .seg button {
  display:inline-flex; align-items:center; gap:7px; cursor:pointer; flex:none;
  font-family:var(--mono); font-size:12.5px; font-weight:500;
  border-radius:var(--r-pill); padding:7px 14px; white-space:nowrap;
  border:1.2px solid var(--line-2); background:var(--panel); color:var(--muted);
  box-shadow:var(--shadow);
  transition:color var(--dur) var(--ease), border-color var(--dur) var(--ease),
             background var(--dur) var(--ease), transform var(--dur) var(--ease);
}
#colourkey .seg button:hover { transform:translateY(-1px); }
#colourkey .seg button i { width:7px; height:7px; border-radius:50%; flex:none;
                           background:currentColor; }
#colourkey .seg button b { font-weight:500; opacity:.7; font-variant-numeric:tabular-nums; }
#colourkey .seg button b:empty { display:none; }
#colourkey .seg .s-connected i { background:var(--ok); }
#colourkey .seg .s-unresolved i { background:var(--crit); }
#colourkey .seg .s-unused i { background:var(--warn); }
#colourkey .seg .s-uncertain i { background:var(--dim); }
#colourkey .seg .s-connected[aria-pressed="true"] { color:var(--ok); border-color:var(--ok);
  background:var(--ok-fill); box-shadow:var(--glow) var(--ok-fill), var(--shadow); }
#colourkey .seg .s-unresolved[aria-pressed="true"] { color:var(--crit); border-color:var(--crit);
  background:var(--crit-fill); box-shadow:var(--glow) var(--crit-fill), var(--shadow); }
#colourkey .seg .s-unused[aria-pressed="true"] { color:var(--warn); border-color:var(--warn);
  background:var(--warn-fill); box-shadow:var(--glow) var(--warn-fill), var(--shadow); }
#colourkey .seg .s-uncertain[aria-pressed="true"] { color:var(--dim); border-color:var(--dim);
  background:var(--dim-fill); }

.shell { display:flex; height:100%; }

.content { flex:1 1 auto; min-width:0; display:flex; flex-direction:column; }
/* The rail is the desktop's navigation. A phone has no room for it and uses the VIEW
   select instead; both drive the same switch, from one list of items. */
.rail { display:none; }
.top { flex:none; background:var(--panel); border-bottom:1px solid var(--line); }
.brand { display:flex; align-items:center; gap:9px; padding:8px 12px 6px; }
.brand b { font-size:14px; }
/* The appearance control ends the header row, so it is present on every view - it used to
   sit inside the status row, which four views out of five hide. */
.brand .meta { color:var(--muted); font-size:11px; overflow:hidden; white-space:nowrap;
               text-overflow:ellipsis; font-family:var(--mono); }

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
      font-family:var(--mono); }
.qn.none { color:var(--crit); }
/* Always on screen. A reader should never have to hunt for what a colour claims, and the
   four statuses are the whole contract this tool makes. */
.legendbar { display:flex; flex-wrap:wrap; gap:4px 14px; padding:0 12px 9px; font-size:11px; }
.legendbar .k { display:flex; align-items:baseline; gap:5px; color:var(--ink); }
.legendbar .k i { width:9px; height:9px; border-radius:2px; border:1.5px solid; flex:none;
            transform:translateY(1px); }
.legendbar .k em { font-style:normal; color:var(--muted); }
.legendbar { align-items:center; gap:8px; padding:0 12px 8px; }
.flabel { font-size:11px; letter-spacing:.09em; text-transform:uppercase; color:var(--muted); }
.seg { display:flex; flex:1 1 auto; min-width:0; border:1px solid var(--line);
  border-radius:8px; overflow:hidden; background:var(--panel); }
.seg button { flex:1 1 0; min-width:0; background:none; border:0; border-right:1px solid var(--line);
  color:var(--muted); font:inherit; font-size:11px; padding:7px 2px; cursor:pointer;
  white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.seg button:last-child { border-right:0; }
.seg button[aria-pressed="true"] { color:var(--bg); background:var(--ink); }
/* "all" pressed is the resting state and should look like nothing; anything else is a
   filter the reader set and has to be findable when they come back to the screen a minute
   later, which on a phone is the common case. The pill itself carries the signal, so it
   reads from the corner of the eye without parsing five small words. */
.pill.filtering, .legendbar.filtering .seg { border-color:var(--sig); }
.pill.filtering { box-shadow:0 0 0 1px var(--sig), 0 10px 30px -12px rgba(0,0,0,.6); }
/* On the glass with the status pills, because that is where a reader is looking when a
   filter empties the map. */
.fnote { display:flex; align-items:center; gap:9px; flex:none;
         font-family:var(--mono); font-size:12px; color:var(--sig);
         background:var(--sig-fill); border:1.2px solid var(--sig);
         border-radius:var(--r-pill); padding:6px 6px 6px 14px; box-shadow:var(--shadow);
         max-width:100%; min-width:0; }
.fnote > span { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.fnote button { flex:none; background:var(--sig); color:var(--bg); border:0; cursor:pointer;
                border-radius:var(--r-pill); padding:5px 12px; font-family:var(--mono);
                font-size:11.5px; font-weight:600; }
.seg button.s-connected[aria-pressed="true"] { background:var(--ok); }
.seg button.s-unresolved[aria-pressed="true"] { background:var(--crit); }
.seg button.s-unused[aria-pressed="true"] { background:var(--warn); }
.seg button.s-uncertain[aria-pressed="true"] { background:var(--dim); }
.seg button:focus-visible, .tmode:focus-visible { outline:2px solid var(--sig); outline-offset:-2px; }
/* Explanations, folded. A disclosure rather than a "?" button because the summary line
   still says what is inside - a bare question mark makes the reader guess. */
.explain { border-top:1px solid var(--line); margin-top:14px; padding-top:10px; }
.explain > summary { cursor:pointer; font-size:12.5px; color:var(--muted); list-style:none;
  display:flex; align-items:center; gap:7px; padding:3px 0; }
.explain > summary::-webkit-details-marker { display:none; }
.explain > summary::before { content:"?"; flex:none; width:17px; height:17px; border-radius:50%;
  border:1px solid var(--line); display:grid; place-items:center; font-size:10.5px;
  color:var(--muted); }
.explain[open] > summary { color:var(--ink); margin-bottom:8px; }
.explain[open] > summary::before { content:"\u00d7"; }
.explain > summary:focus-visible { outline:2px solid var(--sig); outline-offset:2px; }
/* The chain, as a line. The arrow is dimmed and unselectable so copying a row gives the
   names and not a string of glyphs between them. */
.chainrow .t { white-space:normal; }
.chainrow .arrow { font-style:normal; color:var(--muted); padding:0 6px; user-select:none; }
.chainrow .t b { font-weight:600; }
/* Results sit over the canvas, anchored under the box that produced them. */
.found { position:absolute; left:10px; right:10px; top:96px; z-index:8; max-height:60vh;
  overflow:auto; background:var(--panel); border:1px solid var(--line); border-radius:12px;
  box-shadow:0 18px 40px -18px rgba(0,0,0,.7); padding:4px; }
.found .fr { padding:8px 10px; border-bottom:1px solid var(--line); cursor:pointer;
  display:grid; gap:2px; }
.found .fr:last-of-type { border-bottom:0; }
.found .fr:hover, .found .fr:focus-visible { background:var(--sunk); }
.found .fr .t { font-size:13px; color:var(--ink); }
.found .fr .w { font-size:11px; color:var(--muted); }
.found .gloss { padding:7px 10px; font-size:11px; color:var(--muted); }
.nothing { position:absolute; inset:auto 12px 78px; text-align:center; pointer-events:none;
  color:var(--muted); font-size:13px; display:grid; gap:4px; }
.nothing b { color:var(--ink); font-size:15px; font-weight:600; }
/* A word, not a symbol: the glyph needed a tooltip to explain itself, and a phone has no
   tooltip. It names the pack now, and opens the picker. */
.packwrap { position:relative; flex:none; margin-left:auto; }
.packmenu {
  position:absolute; top:calc(100% + 8px); right:0; width:262px; z-index:40;
  background:var(--panel); border:1px solid var(--line); border-radius:var(--r-card);
  box-shadow:var(--shadow); padding:6px;
  opacity:0; transform:translateY(-6px) scale(.98); pointer-events:none;
  transition:opacity var(--dur) var(--ease), transform var(--dur) var(--ease);
}
.packmenu.open { opacity:1; transform:none; pointer-events:auto; }
.packmenu .pk {
  display:flex; align-items:center; gap:11px; width:100%; padding:8px 9px; cursor:pointer;
  border:0; background:none; border-radius:calc(var(--r-card) - 3px); text-align:left;
  font-family:var(--font); color:var(--ink); transition:background 140ms var(--ease);
}
.packmenu .pk:hover { background:var(--sunk); }
.packmenu .pk[aria-current="true"] { background:var(--sig-fill); }
.packmenu .sw { display:flex; width:44px; height:19px; border-radius:999px; overflow:hidden;
                flex:none; border:1px solid var(--line); }
.packmenu .sw i { flex:1; }
.packmenu .pkn { display:flex; flex-direction:column; line-height:1.25; min-width:0; }
.packmenu .pkn b { font-size:13px; font-weight:600; }
.packmenu .pkn em { font-style:normal; font-size:10.5px; color:var(--muted);
                    font-family:var(--mono); }
.packmenu .modes { display:flex; align-items:center; gap:6px; padding:9px 9px 5px;
                   margin-top:5px; border-top:1px solid var(--line); }
.packmenu .mlbl { font-size:10px; letter-spacing:.13em; text-transform:uppercase;
                  color:var(--muted); font-family:var(--mono); margin-right:auto; }
.packmenu .modes button {
  border:1px solid var(--line); background:var(--bg); color:var(--muted); cursor:pointer;
  border-radius:var(--r-pill); padding:4px 12px; font-family:var(--mono); font-size:11.5px;
}
.packmenu .modes button[aria-current="true"] { border-color:var(--sig); color:var(--sig);
  background:var(--sig-fill); }
.tmode { flex:none; min-width:70px; height:30px; border-radius:8px; border:1px solid var(--line);
  background:var(--panel); color:var(--muted); font-size:12px; cursor:pointer; line-height:1;
  font-family:inherit; padding:0 10px; }
.tmode:hover { color:var(--ink); border-color:var(--sig); }

/* The reading: one number given real size, because it is the number a reader opened the
   page for, with the trend beside it as a sparkline rather than a second screen. */
.reading { display:flex; align-items:flex-end; gap:18px; padding:2px 12px 10px; }
.big { line-height:.9; }
.big span { display:block; font-size:10px; letter-spacing:.14em; text-transform:uppercase;
  color:var(--muted); margin-bottom:6px; }
.big b { font-size:38px; font-weight:600; letter-spacing:-.03em; font-variant-numeric:tabular-nums; }
.spark { flex:1; height:30px; min-width:0; }


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
.gone .ch { font-family:var(--mono); word-break:break-all;
            margin-bottom:3px; color:var(--ink); }
.gone .ch i { display:inline-block; min-width:62px; font-style:normal; font-size:10px;
              text-transform:uppercase; letter-spacing:.05em; }
.gone .ch.added i { color:var(--ok); }
.gone .ch.removed i { color:var(--crit); }
.gone .ch.status i { color:var(--warn); }
.gone .ch span { color:var(--muted); }
.gone { max-height:26dvh; overflow-y:auto; }

.main { flex:1 1 auto; position:relative; min-height:0; background:var(--sunk); }
/* A gesture is a CSS transform on .cvlayer, one composited layer, so the compositor slides
   or scales what is already rasterised and the main thread does nothing per frame. Three
   things make that true, each measured on a 20,000-element page and each costing the whole
   idea when missing:
   - the transform is on a plain div, NOT the <svg>: Chrome treats a transform on an SVG
     root as a change to its contents and re-lays out every element (22ms of Layout/move);
   - contain:paint on the layer: without it every move re-ran compositor layer assignment
     over all 20,000 items (25ms of Layerize/move; 2.7ms with it);
   - the layer is three viewports wide and tall (inset:-100%), with the <svg> box kept at
     the middle third so nothing about coordinates changes: contain:paint clips at the
     layer's edge, and a layer the size of the screen would show blank strips while dragging
     until the commit. Tiles are rasterised near the viewport only, so the size is free.
   .cvclip clips to the screen - not .main, whose menus may hang past its edge. */
.cvclip { position:absolute; inset:0; overflow:hidden; }
.cvlayer { position:absolute; inset:-100%; contain:paint; will-change:transform;
           transform-origin:calc(100% / 3) calc(100% / 3); }
#cv { position:absolute; left:calc(100% / 3); top:calc(100% / 3);
      width:calc(100% / 3); height:calc(100% / 3); display:block;
      cursor:grab; touch-action:none; overflow:visible; }
#cv.drag { cursor:grabbing; }
#cv * { touch-action:none; }
/* Nothing under a gesture needs hit-testing, and hit-testing is what a pointermove over
   twelve thousand elements spends most of its time on. */
#cv.gesture #vp, #cv.drag #vp { pointer-events:none; }
/* One bounding-box test per card instead of one per rect and text inside it. */
.nd { pointer-events:bounding-box; }
.ed, .band, .col, .bandbig, .bandlbl, .bandlang, .lanename, .lanebadge { pointer-events:none; }
.nd rect { stroke-width:1.5; }
.nd text { font-size:10.5px; fill:var(--ink); pointer-events:none; letter-spacing:-.1px;
           font-family:var(--mono); }
.nd { cursor:pointer; }
.nd.faded { opacity:.10; }
#cv.nolabels .nd text { display:none; }
.nd.lit rect { stroke-width:3.5; }
/* stroke-opacity, not opacity: `opacity` makes every wire its own transparency group
   that the compositor has to blend separately; a translucent stroke is just a colour. */
.ed { fill:none; stroke-width:1.1; stroke-opacity:.38; }
/* On the isolated path: opaque and heavier, so the one thing being followed is the one
   thing the eye lands on. */
.ed.lit { stroke-width:2.4; stroke-opacity:1; }
.ed.faded { stroke-opacity:.05; }
.col { font-size:9.5px; fill:var(--muted); text-transform:uppercase; letter-spacing:.1em;
       font-family:var(--mono); }

.zoom { position:absolute; right:14px; bottom:62px; display:flex; flex-direction:column;
        gap:7px; z-index:6; }
.zoom button, .key { width:38px; height:38px; font-size:15px; line-height:1;
                     border-radius:var(--r-pill); border:1.2px solid var(--line-2);
                     background:var(--panel); color:var(--muted); cursor:pointer;
                     box-shadow:var(--shadow); font-family:var(--mono);
                     transition:color var(--dur) var(--ease),
                                border-color var(--dur) var(--ease); }
.zoom button:hover, .key:hover { color:var(--sig); border-color:var(--sig); }
.key { position:absolute; right:14px; bottom:14px; z-index:6; }
/* Pinned over the canvas, the legend sat on top of the nodes it explains. It opens now
   only when asked, and closes by tapping anywhere. */
.legend { position:absolute; right:10px; bottom:58px; background:var(--panel);
          border:1px solid var(--line); border-radius:9px; padding:9px 11px;
          font-size:11.5px; color:var(--muted); z-index:3; pointer-events:none; }
.legend span { display:inline-block; width:9px; height:9px; border-radius:2px; margin-right:6px; }

/* Evidence arrives as a sheet over the canvas, not as a permanent rail stealing height. */
/* Above the filter pill, which is z-index 6 and pinned to the same bottom edge. The
   sheet was 4, so on a phone the filters covered the evidence a tap had just opened -
   the one thing the tap was for. Raised rather than the pill lowered: the sheet is a
   response to a direct action and outranks a standing control. */
.sheet { position:absolute; left:0; right:0; bottom:0; z-index:12; background:var(--panel);
         border-top:1px solid var(--line); padding:12px 14px 14px; max-height:52%;
         overflow-y:auto; box-shadow:0 -6px 24px -18px rgba(0,0,0,.6); }
.sheet h2 { font-size:13px; margin:0 26px 6px 0; word-break:break-all; }
.sheet .row { color:var(--muted); font-size:12px; margin-bottom:4px;
              font-family:var(--mono); word-break:break-all; }
/* The line number is a coordinate someone is about to type into an editor. */
.sheet .row .ln { color:var(--ink); font-weight:600; }
.sheet .note { padding:0; margin-top:8px; font-family:inherit; }
.acts { margin:8px 0 4px; display:flex; gap:8px; flex-wrap:wrap; align-items:center; }
/* Marking a finding wrong is the single most useful thing a reader can do for the tool,
   so it sits beside the isolate button rather than behind a menu. Tinted, not shouted:
   it is an offer, not an instruction. */
.wrongbtn { border-color:var(--crit) !important; color:var(--crit) !important; }
.whybox { margin:6px 0 2px; display:grid; gap:5px; }
.whyopt { text-align:left; padding:8px 10px; border-radius:9px; cursor:pointer;
          border:1px solid var(--line); background:var(--sunk); color:var(--ink);
          display:grid; gap:2px; font-family:inherit; }
.whyopt b { font-family:var(--mono); font-size:11.5px; color:var(--sig); }
.whyopt span { font-size:11.5px; color:var(--muted); line-height:1.35; }
.whyopt:hover { border-color:var(--sig); }
.whyopt.copied { border-color:var(--ok); }
.whyopt.copied b { color:var(--ok); }
/* The report view. A table of the exact values, because "we only send counts" is a claim
   and the counts on screen are the evidence for it. */
.rep { background:var(--sunk); border:1px solid var(--line); border-radius:12px;
       padding:14px 16px; margin:10px 0 4px; }
.reptab { width:100%; border-collapse:collapse; font-family:var(--mono); font-size:12px; }
.reptab td { padding:4px 0; border-bottom:1px solid var(--line); vertical-align:top; }
.reptab tr:last-child td { border-bottom:none; }
.reptab .k { color:var(--muted); word-break:break-all; padding-right:12px; }
.reptab .v { color:var(--ink); text-align:right; white-space:nowrap; font-weight:600; }
.reph { font-size:11px; text-transform:uppercase; letter-spacing:.09em; color:var(--muted);
        margin:16px 0 6px; font-weight:600; }
.repnote { margin:10px 0 0; }
.repacts { margin:10px 0 6px; }
.repbtn { display:inline-block; padding:7px 11px; font-size:12.5px; border-radius:8px;
          border:1px solid var(--sig); background:var(--sig); color:#fff;
          text-decoration:none; }
.repbtn:hover { filter:brightness(1.1); }
.acts button { padding:7px 11px; font-size:12.5px; border-radius:8px; cursor:pointer;
               border:1px solid var(--line); background:var(--bg); color:var(--sig); }
.sheet .lbl { font-size:9.5px; text-transform:uppercase; letter-spacing:.09em;
              color:var(--muted); margin:14px 0 6px; }
/* The NUMBER in a label is the thing being read - "3 hops", "12 from here" - and it was
   the same dim grey as the word beside it. On a dark pack that is the difference between
   a value and a caption. */
.sheet .lbl b, .sheet .n { color:var(--ink); font-weight:700; font-size:11px;
                           letter-spacing:.02em; }
.sheet .row b { color:var(--ink); font-weight:600; }
/* One row per hop, joined by a rule down the left, so the walk reads as a route. */
.hop { border-left:2px solid var(--line); padding:0 0 9px 10px; position:relative; }
.hop.at { border-left-color:var(--sig); }
.hop .hk { font-size:9.5px; text-transform:uppercase; letter-spacing:.07em;
           color:var(--muted); display:flex; align-items:center; gap:6px; }
/* The step number sits in the rule the hops hang off, so the column of numbers reads as
   an order rather than as part of each label. */
.hop .hn { flex:none; width:16px; height:16px; margin-left:-19px; border-radius:50%;
           background:var(--line-2); color:var(--ink); font-size:9.5px; font-weight:700;
           display:inline-flex; align-items:center; justify-content:center;
           letter-spacing:0; }
.hop.at .hn { background:var(--sig); color:#fff; }
.hop .hs { color:var(--muted); font-size:9px; letter-spacing:.04em; }
.hop .hl { font-size:12.5px; font-family:var(--mono); word-break:break-all; }
.hop.at { background:var(--sunk);
          border-radius:0 7px 7px 0; }
.hop.at .hl { font-weight:700; color:var(--sig); }
.hop .hf { font-size:11px; color:var(--muted); word-break:break-all;
           font-family:var(--mono); }
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
            font-family:var(--mono); word-break:break-all; }
.codehead button { margin-left:auto; flex:none; width:30px; height:30px; border-radius:8px;
                   border:1px solid var(--line); background:var(--bg); color:var(--ink);
                   cursor:pointer; }
#codebody { margin:0; padding:14px 0; overflow:auto; font-size:12px; line-height:1.7;
            background:var(--sunk); color:var(--ink); white-space:pre;
            font-family:var(--mono); }
#codebody { tab-size:4; }
/* The listing. A grid rather than a <pre> so the gutter can carry a status stripe without
   the line numbers being selectable along with the code a reader wants to copy. */
/* A filter a reader set from somewhere else, shown where they can take it off again. */
.chip { flex:none; border:1px solid var(--sig); border-radius:999px; background:transparent;
        color:var(--sig); font-size:12px; padding:0 11px; height:32px; cursor:pointer;
        font-family:inherit; white-space:nowrap; }
/* A float dropped the affordance onto its own line under the title. A flex row keeps
   count, name and "Findings ›" on one line, with the name taking the slack. */
.rowgo { display:flex; align-items:center; gap:9px; width:100%; text-align:left;
         font:inherit; cursor:pointer; }
.rowgo .t { flex:1 1 auto; min-width:0; }
.rowgo .go { flex:none; color:var(--sig); font-size:12px; }
.rowgo:hover { border-color:var(--sig); }
/* Two questions in one view, so a tab rather than a second menu entry - and the same
   control does duty for the state filter under it. */
.tabs { display:flex; gap:8px; padding:0 12px 10px; }
.tabs.wrap { flex-wrap:wrap; padding-bottom:12px; }
.tab { border:1px solid var(--line); background:transparent; color:var(--muted);
       border-radius:999px; padding:0 13px; height:32px; cursor:pointer; font:inherit;
       font-size:13px; white-space:nowrap; }
.tab b { color:var(--ink); font-weight:600; font-variant-numeric:tabular-nums; }
.tab.on { border-color:var(--sig); color:var(--sig); }
.tab.on b { color:var(--sig); }
.fl.inv { cursor:default; }
.fl.inv .fn { flex:1 1 auto; }
/* An empty canvas has to say why. Readable size, not the 9.5px column-heading style. */
/* The page as the browser laid it out. Boxes at true proportions, coloured by what the
   scan says - the two views of one page, side by side in one picture. */
.obwrap { border:1px solid var(--line); border-radius:var(--r-card); background:var(--sunk);
          padding:10px; overflow:auto; max-height:64dvh; }
.obshot { width:100%; height:auto; display:block; }
.obshot .ob { fill:transparent; stroke:var(--dim); stroke-width:1.5; }
.obshot .ob-connected { stroke:var(--ok); fill:var(--ok-fill); fill-opacity:.35; }
.obshot .ob-unresolved { stroke:var(--crit); fill:var(--crit-fill); fill-opacity:.5; }
.obshot .ob-unused { stroke:var(--warn); fill:var(--warn-fill); fill-opacity:.45; }
.obshot .ob-uncertain { stroke:var(--dim); stroke-dasharray:3 3; }
.obshot .ob-unknown { stroke:var(--line-2); stroke-dasharray:2 4; }
.obshot .ob:hover { stroke-width:3; }
.obkey { display:flex; gap:10px; flex-wrap:wrap; margin-bottom:10px; }
.obk { display:inline-flex; align-items:center; gap:6px; font-family:var(--mono);
       font-size:11.5px; color:var(--muted); }
.obk i { width:9px; height:9px; border-radius:2px; border:1.5px solid currentColor; }
.obk.ob-connected { color:var(--ok); } .obk.ob-unresolved { color:var(--crit); }
.obk.ob-unused { color:var(--warn); } .obk.ob-uncertain,.obk.ob-unknown { color:var(--dim); }
/* Two lines per card: the name, then what it is. */
.nd .sub { font-size:9.5px; fill:var(--muted); }
.nd .big { font-size:19px; font-weight:600; fill:var(--ink);
           font-variant-numeric:tabular-nums; }
.nd.agg { cursor:pointer; }
.nd.agg rect { stroke-width:2; }
/* The bands sit behind everything: a faint fill, and a border a reader can still see with
   the whole repository zoomed out - which is the one view in which "what regions are
   there" is the question. fill-opacity and stroke-opacity are set apart on purpose: a
   single `opacity` dimmed the border with the fill, and the strips read as black boxes. */
#cv .band { fill:var(--panel); fill-opacity:.30; stroke:var(--ink); stroke-width:3;
            stroke-opacity:.85; }
/* Named languages, sitting on the band's own top line. */
#cv .bandlang { fill:var(--ink); font-size:15px; font-weight:600; letter-spacing:.04em;
                text-anchor:end; opacity:.85; font-family:var(--mono); }
#cv .band.seam { fill:var(--sig-fill); fill-opacity:.55; stroke:var(--sig);
                 stroke-dasharray:5 4; }
/* A lane heading inside a band. Bigger than the kind headings under it, because the
   store is the thing a reader is choosing between, and the kind is a detail of it. */
#cv .lanename { font-size:14px; fill:var(--ink); font-weight:700; letter-spacing:-.01em; }
#cv .lanebadge { font-size:9.5px; letter-spacing:.1em; font-family:var(--mono); }
#cv .lanebadge.has { fill:var(--ok); }
#cv .lanebadge.none { fill:var(--muted); }
#cv .bandlbl { font-size:11px; fill:var(--muted); letter-spacing:.17em; font-weight:500;
               pointer-events:none; }
#cv .bandbig { font-size:26px; fill:var(--ink); font-weight:700; letter-spacing:-.02em;
               font-family:var(--display); pointer-events:none; opacity:.92; }
.listing { font-variant-ligatures:none; }
/* The block the line belongs to: the function, the rule, the handler. The line itself
   still gets the strongest mark; its block gets a quieter one so the eye reads the whole
   shape and lands on the exact row. */
.listing .ln.inblock { background:var(--sig-fill); }
.listing .ln.inblock .num { color:var(--sig); opacity:.8; }
/* The run of characters the row was actually about. */
.listing mark.hit { background:var(--sig); color:var(--bg); border-radius:3px;
                    padding:0 2px; font-weight:600; }
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
/* The panel fills the same box the canvas does, and the chrome floats over both - so its
   first heading has to start below the dropdown and its last row above the pills. */
.panel { position:absolute; inset:0; overflow-y:auto; background:var(--bg);
         padding:calc(var(--chrome-top, 56px) + 10px) 12px
                 calc(var(--chrome-bottom, 62px) + 24px); }
.panel h2 { font-size:17px; margin:0 0 4px; }
.panel .blurb { color:var(--muted); font-size:12.5px; margin:0 0 12px; max-width:62ch; }
.panel .tools { display:flex; gap:8px; margin-bottom:12px; }
.panel .tools input { flex:1 1 auto; min-width:0; }
.panel input, .panel select { padding:8px 9px; font-size:13px; border-radius:8px;
                              border:1px solid var(--line); background:var(--panel);
                              color:var(--ink); }
.row { background:var(--panel); border:1px solid var(--line); border-radius:9px;
       padding:9px 11px; margin-bottom:7px; }
.row .t { font-size:13px; font-family:var(--mono); word-break:break-all; }
.row .w { color:var(--muted); font-size:11.5px; word-break:break-all;
          font-family:var(--mono); }
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
        font-family:var(--mono); }
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
.tree { font-family:var(--mono); font-size:12.5px; }
.tree summary { cursor:pointer; padding:3px 0; color:var(--muted); }
.fl { display:flex; align-items:center; gap:8px; padding:3px 0; cursor:pointer;
      border-radius:6px; }
.fl:hover { background:var(--panel); }
/* The row's own action is "show me this on the map", and it was invisible next to an
   `open` link that left for VS Code - so the one thing the view exists for read as the
   secondary one. Named, and on hover it is the loud half of the row. */
.fl .go { flex:none; margin-left:auto; color:var(--sig); font-size:11px; opacity:0;
          font-family:var(--mono); }
.fl:hover .go { opacity:1; }
.fl .edit { flex:none; font-size:11px; opacity:.55; }
.fl:hover .edit { opacity:1; }
.fl .fn { flex:1 1 auto; min-width:0; overflow:hidden; text-overflow:ellipsis;
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
            font-family:var(--mono); }
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
  .nv { display:flex; justify-content:space-between; align-items:center; gap:8px;
        padding:7px 14px; cursor:pointer; font-size:12.5px; }
  .nv:hover { background:var(--sunk); }
  .nv[aria-current="true"] { background:var(--sunk); box-shadow:inset 2px 0 0 var(--sig);
                             color:var(--sig); font-weight:600; }
  .nv .c { color:var(--muted); font-size:11px; font-variant-numeric:tabular-nums;
           font-family:var(--mono); }
  .sheet { left:auto; right:0; width:380px; top:0; bottom:auto; max-height:100%;
           border-top:0; border-left:1px solid var(--line); }
}

/* The phone layout lives LAST, deliberately. These rules override base rules of equal
   specificity, and CSS breaks that tie by source order - declared earlier, `bottom:auto`
   lost to the base `bottom:10px`, so the zoom control kept BOTH and stretched from y=96
   to the foot of the screen. It covered the whole canvas, and every drag landed on it
   instead of on the map. */
/* ── Direction A, on the screen it was drawn for ────────────────────────────
   On a phone the header was six stacked rows - brand, view, commit, page, crumb,
   legend - and the map got what was left, which was about a third. Every control
   belongs in one pill at the bottom instead: over the canvas rather than above it,
   and within reach of the thumb that is already holding the phone. */
@media (max-width: 720px) {
  /* The canvas IS the page. Everything else floats on top of it - the header was a block
     ABOVE the map, and with the brand line, the reading, the view picker and the crumb
     row it took nearly half the screen before a single node was drawn. Overlaying costs
     nothing: the map under a control is still map, and a reader pans it out from under. */
  .content { position:relative; }
  .top {
    position:absolute; inset:0 0 auto; z-index:5;
    background:linear-gradient(var(--bg) 62%, transparent);
    border-bottom:0; padding-bottom:10px; pointer-events:none;
  }
  /* Only things you can actually press take the gesture. `.top > *` handed it to whole
     full-width rows - the crumb row alone made a 40px band of the map undraggable and
     swallowed the zoom control sitting under it. A row is a layout box, not a target. */
  .top button, .top select, .top input, .top a, .top label, .top summary { pointer-events:auto; }
  .main { position:absolute; inset:0; }

  /* One line for identity, and the count kept small enough to sit beside it. */
  .brand { padding:7px 12px 2px; }
  .brand b { font-size:13px; }
  .reading { padding:0 12px 6px; gap:12px; align-items:center; }
  .big { display:flex; align-items:baseline; gap:8px; line-height:1; }
  .big span { margin:0; font-size:9.5px; }
  .big b { font-size:21px; }
  .spark { height:20px; max-width:120px; }
  /* The menu is navigation, and navigation lives at the top on every screen. */
  .filters.onlymob { padding:0 12px 6px; }
  .filters.onlymob label span { display:none; }
  .crumbrow { padding:2px 12px 6px; }
  .crumbrow #crumb { display:none; }
  /* Clear of the floating header, and stacked so they take a thumb's width rather than a
     row of the map. `bottom:auto` matters as much as `top`: with both set the control
     stretches between them, which is how it came to cover the entire canvas. */
  /* A phone is not a small desktop. The top row holds the menu and the appearance
     control and nothing else; the readout drops to its own line under them rather than
     being run over by a button; and the zoom stacks ABOVE the pills instead of through
     them, because the pills wrap to two rows and the stack has to clear both. */
  /* The comment above says this row holds the menu and the appearance control and
     nothing else. Two things were added to it since and it stopped being true: the page
     picker went into the left corner and the List toggle into the right, and at 390px
     they overlapped by 36px - List sitting on top of the picker.
     So the picker takes its OWN row. It is the widest control here and it carries the
     longest text (a template path), which is exactly the thing that should not be
     squeezed into a corner it has to share. */
  .hud.tl { top:12px; left:12px; right:12px; flex-wrap:wrap; }
  .hud.tl > .menuwrap { flex:none; }
  .pagepick { flex-basis:100%; max-width:none; order:2; }
  /* Clear of the right corner's own controls, which stay on row one. */
  .hud.tr { top:12px; right:12px; gap:6px; z-index:7; }
  .menubtn { max-width:44vw; }
  /* The page picker on the glass now says which page this is, so the readout was the
     same sentence twice - and the second copy floated over a band heading. */
  .readout { display:none; }
  .hud.bl { bottom:12px; left:12px; right:12px; gap:7px; }
  #colourkey .seg { gap:7px; }
  #colourkey .seg button { padding:6px 12px; font-size:12px; }
  /* A menu row is a thumb target, not a line of text. */
  .mapsheet { width:min(340px, calc(100vw - 24px)); }
  .mapsheet .nv { padding:13px 12px; font-size:16px; border-radius:12px; }
  .mapsheet .nv .c { font-size:13px; }
  .mapsheet .mlab { font-size:11px; padding:13px 12px 7px; }
  .mapsheet .mfilters select, .mapsheet .msearch input { padding:12px 12px; font-size:15px; }
  .mapsheet .mfilters label > span { font-size:11px; }
  .pagepick select { height:36px; font-size:12px; }
  .zoom { left:auto; right:12px; top:auto;
          bottom:calc(var(--chrome-bottom, 62px) + 108px);
          flex-direction:column; width:44px; height:auto; gap:6px; }
  .zoom button, .key { width:44px; height:40px; }
  /* The colour key joins the zoom stack rather than sitting under it in the pills' way. */
  .key { right:12px; bottom:calc(var(--chrome-bottom, 62px) + 58px); }
  /* "Show as list" is a phrase on a desktop and an icon on a phone. */
  .iconbtn.wide { font-size:11.5px; padding:0 11px; }
  /* A file row does not fit on one phone line: name, coverage bar, ratio, up to four
     status badges and an editor link. Unwrapped, the name lost - which is the one part
     of the row a reader is looking for. It takes its own line; the rest follows under. */
  .tree .fl { flex-wrap:wrap; padding:7px 0; }
  .tree .fl .fn { flex:1 0 100%; white-space:normal; word-break:break-all; }
  /* There is no hover on a phone, so the row's own action - and the editor link - were
     permanently invisible on exactly the devices that cannot reveal them. */
  .tree .fl .go, .tree .fl .edit { opacity:1; }
  .tree .fl .go { margin-left:0; }
  .key { left:auto; right:10px; top:auto; bottom:96px; }
  /* Taller than 30px, because on a phone this is a thumb target rather than a mouse one. */
  .tmode { height:36px; font-size:12.5px; }

  /* The canvas can be panned out from under the floating chrome; a scrolling panel
     cannot. So the panel is given exactly the room the chrome occupies, measured at
     runtime rather than guessed - the header changes height when the reading is hidden,
     and the pill changes when a filter notice appears. Safe-area insets are added on top,
     because the home indicator sits over the last row of any list. */
  .panel {
    padding-top:calc(var(--chrome-top, 110px) + 6px);
    padding-bottom:calc(var(--chrome-bottom, 120px) + env(safe-area-inset-bottom, 0px) + 12px);
  }
  .pill { bottom:calc(8px + env(safe-area-inset-bottom, 0px)); }
  /* The reading is about the map. On a panel it repeats the hero underneath it and costs
     the list a line it needs. */
  .top.panelmode .reading { display:none; }

  .filters, .legendbar {
    position:static; margin:0;
  }
  /* While a sheet is open the pill is behind it anyway; hidden so it cannot peek out
     around the sheet's edges on a short screen. */
  body:has(.sheet:not([hidden])) .pill { display:none; }
  .pill {
    position:absolute; left:8px; right:8px; bottom:8px; z-index:6;
    /* Never more than half the screen, whatever it ends up holding: a control surface
       that grows past the thing it controls has stopped being a control surface. */
    max-height:46vh; overflow:auto;
    /* Solid, not translucent. The palette bans mixing against the panel because a
       near-black ground turns any mix to grey mush - and a control sitting over a graph
       of hairlines needs to be read, not seen through. */
    background:var(--panel);
    border:1px solid var(--line); border-radius:14px; padding:8px;
    display:grid; gap:7px;
    box-shadow:0 10px 30px -12px rgba(0,0,0,.6);
  }
  /* Two by two. Four named controls in one row leaves each about 90px, which truncates
     every value to two words and a comma; two rows of two is barely taller and every
     value stays readable. */
  /* In the sheet there is room to stack, so every value is readable rather than truncated
     to two words and a comma. */
  .pill .filters { display:grid; gap:7px; padding:0; }
  .pill .filters label { min-width:0; display:grid; gap:2px; }
  .pill select { width:100%; }

  .pillbar { display:flex; align-items:center; gap:9px; }
  .pillbar .now { flex:1; min-width:0; overflow:hidden; text-overflow:ellipsis;
    white-space:nowrap; font-size:12px; color:var(--ink); }
  .pillbar .now b { color:var(--muted); font-weight:400; }
  .pillbar .open { flex:none; background:var(--bg); border:1px solid var(--line);
    border-radius:9px; color:var(--ink); font:inherit; font-size:11.5px; padding:8px 13px;
    cursor:pointer; }
  .pillsheet { display:grid; gap:8px; position:relative; padding-top:2px; }
  .sheetclose { position:absolute; right:-2px; top:-4px; background:none; border:0;
    color:var(--muted); font-size:17px; line-height:1; cursor:pointer; padding:2px 6px; }
  .pillbar .open:focus-visible, .sheetclose:focus-visible {
    outline:2px solid var(--sig); outline-offset:2px; }
  /* Named. Three unlabelled dropdowns reading "Overview", "Everything in this scan" and
     "Everything" tell a reader what each is SET to and never what it decides - and the
     answer to "which one is the commit picker" was to open them and find out. */
  .pill .filters label { display:grid; gap:2px; }
  .pill .filters label span {
    display:block; font-size:9px; letter-spacing:.13em; text-transform:uppercase;
    color:var(--muted); padding-left:2px;
  }
  .pill .legendbar { padding:0; }
  .pill .flabel { display:none; }
  .pill .seg button { font-size:10px; padding:8px 1px; }
  /* The crumb row keeps its search, but stops being a row of its own. */
  .crumbrow { padding:4px 12px 8px; }
  .reading { padding-bottom:8px; }

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
// Declared up here because the menu is built long before the view that draws it, and a
// `const` read above its declaration is a dead zone, not an undefined.
const OBS_PAGES = (typeof OBSERVED !== "undefined" && OBSERVED.pages) || [];
// Three regions and a strip, named the way a person would name them rather than by the
// kind of symbol that happens to land there. A reader opening this map for the first time
// does not know what a `dom_attr` is and should not have to.
const BANDS = [
  {id: "browser", label: "THE BROWSER \u2014 WHAT A PERSON TOUCHES", short: "THE BROWSER",
   kinds: ["page", "module", "js_call", "dom_selector", "multi_writer_element",
           "dom_attr", "css_selector", "css_token_def", "css_token_use"]},
  {id: "seam", label: "THE SEAM \u2014 THE NETWORK BOUNDARY", short: "THE SEAM",
   kinds: ["fetch_target", "json_field"]},
  {id: "server", label: "THE SERVER \u2014 WHAT RUNS WHEN THE REQUEST LANDS",
   short: "THE SERVER",
   kinds: ["url", "view", "model", "signal_receiver", "admin_action", "template_tag",
           "url_reference", "management_command"]},
  // The second seam. A request crosses the network and lands on a route; a query crosses
  // another boundary and lands on a table. Same disease, next boundary down - and every
  // one of these kinds used to fall into the unnamed overflow group at the bottom.
  {id: "store", label: "THE STORE \u2014 THE SECOND SEAM, WHERE THE SERVER TALKS TO ITS DATA",
   short: "THE STORE",
   kinds: ["db_table", "db_column", "db_function", "db_policy",
           "db_table_use", "db_column_use", "db_function_use",
           "redis_key", "redis_key_use", "redis_ttl",
           "firestore_rule", "firestore_collection",
           "cloud_function", "cloud_function_use",
           "storage_bucket", "edge_function", "edge_function_use"],
   // Lanes, because "does this project have a database" is a question nobody asks and
   // "show me Redis" is. Each store also fails differently: a mistyped column returns the
   // row without it, a mistyped Redis key returns nothing and the code carries a default
   // nobody chose. `declared` names the kinds that constitute an ORACLE - if a scan found
   // none of them, the lane can only pair, never verify, and says so in its badge.
   lanes: [
     {id: "postgres", name: "Postgres", prefix: ["db_"],
      declared: ["db_table", "db_column", "db_function", "db_policy"]},
     {id: "redis", name: "Redis", prefix: ["redis_"], declared: []},
     {id: "firebase", name: "Firebase", prefix: ["firestore_", "cloud_function"],
      declared: ["firestore_rule", "cloud_function"]},
     {id: "storage", name: "Storage", prefix: ["storage_", "edge_function"],
      declared: ["edge_function"]},
   ]},
  {id: "off", label: "REACHED WITHOUT A BROWSER", short: "NO BROWSER",
   kinds: ["celery_task", "celery_schedule", "stripe_webhook", "stripe_event",
           "graphql_field", "graphql_selection",
           "job", "job_enqueue", "job_schedule", "env_var", "env_read"]},
];
// Nodes arrive as arrays against string tables, and they arrive LATE: each page's rows
// sit in an inert <script type="text/plain"> block until the page is drawn, so a map of
// 700,000 symbols costs the browser one page's worth of objects, not all of them. PAGES
// holds the manifest for every page from the start - name, counts, which layer - and
// `nodes` is null until ensurePage() has decoded that page's chunk. draw() loads its own
// page, so nothing else in this file has to know a page can be absent.
const PAGES = MAPDATA.pages.map(p => ({...p, nodes: null, edges: null, detailed: false}));
const NODE_FIELDS = MAPDATA.fields, DETAIL_FIELDS = MAPDATA.detail || [];
const byId = new Map();
const CHUNKS = new Map();

function inflateNode(row) {
  const K = MAPDATA.kinds, S_ = MAPDATA.statuses, FI = MAPDATA.files;
  const LA = MAPDATA.langs || [], SV = MAPDATA.services || [];
  const n = {note: "", snippet: "", context: ""};
  NODE_FIELDS.forEach((field, i) => {
    const v = row[i];
    n[field] = field === "kind" ? K[v]
      : field === "status" ? S_[v]
      : field === "file" ? (FI[v] || "")
      : field === "lang" ? (LA[v] || "")
      : field === "service" ? (SV[v] || "")
      : (v == null ? (field === "line" ? null : "") : v);
  });
  return n;
}

// One chunk, decoded once. gzip+base64 goes through a data: URL and DecompressionStream,
// which is the one path that works when the map is opened as a file - reading a
// sibling file is refused there, and a module script never runs. The text is dropped
// from the DOM once read: the base64 alone was 20 MB on a real map.
function readChunk(name) {
  if (CHUNKS.has(name)) return CHUNKS.get(name);
  const el = document.querySelector(`script[type="text/plain"][data-chunk="${name}"]`);
  let promise;
  if (!el) promise = Promise.resolve(null);
  else if (el.dataset.enc === "gz") {
    if (typeof DecompressionStream === "undefined") {
      promise = Promise.reject(new Error("This browser cannot unpack the map (no DecompressionStream)."
        + " Chrome 80, Safari 16.4 or Firefox 113 and later can."));
    } else {
      const text = el.textContent;
      promise = fetch("data:application/octet-stream;base64," + text)
        .then(r => new Response(r.body.pipeThrough(new DecompressionStream("gzip"))).text())
        .then(JSON.parse);
    }
  } else promise = Promise.resolve(JSON.parse(el.textContent));
  promise.then(() => { if (el) el.textContent = ""; }, () => {});
  CHUNKS.set(name, promise);
  return promise;
}
// Once a chunk's rows live in PAGES (or the index), the parsed JSON is a second copy -
// 33 MB for a 450k-symbol page. The cache keeps a resolved null so the text, gone from
// the DOM, is never looked for again; every reader checks its own state first.
function dropChunk(name) { CHUNKS.set(name, Promise.resolve(null)); }

function ensurePage(index) {
  const p = PAGES[index];
  if (!p) return Promise.resolve(null);
  if (p.nodes) return Promise.resolve(p);
  return readChunk("p" + index).then(data => {
    if (p.nodes) return p;
    const S_ = MAPDATA.statuses;
    p.nodes = (data ? data.nodes : []).map(inflateNode);
    p.edges = (data ? data.edges : []).map(e => ({source: e[0], target: e[1], status: S_[e[2]]}));
    // A service page shares its nodes with the pages they came from; whichever loaded
    // first owns the object in byId, and the other page points at the same one so a
    // detail loaded through either is seen by both.
    p.nodes = p.nodes.map(n => {
      const had = byId.get(n.id);
      if (had) return had;
      byId.set(n.id, n);
      return n;
    });
    dropChunk("p" + index);
    return p;
  });
}

// The long strings - note, snippet, context - are a third of the bytes and are read
// only when a sheet or the code box opens, so they come in a chunk of their own.
function ensureDetail(index) {
  const p = PAGES[index];
  if (!p) return Promise.resolve(null);
  if (p.detailed) return Promise.resolve(p);
  return ensurePage(index).then(() => readChunk("d" + index)).then(cols => {
    if (p.detailed) return p;
    if (cols) {
      DETAIL_FIELDS.forEach(field => {
        const values = cols[field] || [];
        p.nodes.forEach((n, i) => { if (values[i]) n[field] = values[i]; });
      });
    }
    p.detailed = true;
    dropChunk("d" + index);
    return p;
  });
}

// Which page a node id lives on, among the pages loaded so far - and the current page
// first, because that is where a reader is looking.
function pageIndexOf(id) {
  if (PAGES[current] && PAGES[current].nodes && PAGES[current].nodes.some(n => n.id === id)) return current;
  for (let i = 0; i < PAGES.length; i++) {
    const p = PAGES[i];
    if (p.nodes && p.nodes.some(n => n.id === id)) return i;
  }
  return current;
}
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
// Which aggregate card a reader has opened, if any. One at a time: opening a second while
// the first is out would put 18,000 cards on the canvas. Declared with the other filters
// because the filter notice reads it, and that runs long before the canvas does.
let expandedKind = null;
// Where an opened kind's window starts. A kind with 2,000 members opens 500 at a time -
// the cap used to cut silently at 2,000 and the reader never learned what was missing.
let expandedFrom = 0;
const EXPAND_PAGE = 500;

// A section is a lens on the same canvas, not a different page. The list of rows is the
// CLI's answer; on screen the answer is the shape.
const SECTION_KINDS = {
  map: null,
  boundary: new Set(["module", "js_call", "fetch_target", "url", "view", "json_field"]),
  dom: new Set(["dom_attr", "dom_selector", "multi_writer_element"]),
  backend: new Set(["url", "view", "model", "admin_action", "signal_receiver",
                    "template_tag", "management_command"]),
  css: new Set(["css_selector", "css_token_def", "css_token_use"]),
  integrations: new Set(["stripe_webhook", "stripe_event", "celery_task",
                         "celery_schedule", "graphql_field", "graphql_selection",
                         "job", "job_enqueue", "job_schedule"]),
};

// A service is its own layer. "Integrations" as one bucket answers "does this project
// talk to anything", which nobody asks; the question is "show me Stripe" - one service,
// its entry point and every name it dispatches on, with the rest of the graph out of the
// way. Kept separate from SECTION_KINDS because a layer filters the CANVAS while a
// section switches the whole view.
const LAYERS = [
  ["", "Everything"],
  ["boundary", "Frontend ↔ Backend"],
  ["dom", "DOM wiring"],
  ["css", "CSS & tokens"],
  ["backend", "Backend"],
  ["stripe", "Stripe"],
  ["celery", "Celery"],
  ["jobs", "Background jobs"],
  ["config", "Configuration"],
  ["database", "Database"],
  ["redis", "Redis"],
  ["graphql", "GraphQL"],
];
const LAYER_KINDS = {
  stripe: new Set(["stripe_webhook", "stripe_event"]),
  celery: new Set(["celery_task", "celery_schedule"]),
  jobs: new Set(["job", "job_enqueue", "job_schedule"]),
  config: new Set(["env_var", "env_read"]),
  // The data layer, split by store rather than lumped together. "Does this project have a
  // database" is not a question anyone asks; "show me Redis" is, and the two answer to
  // completely different failure modes - a mistyped column returns a row without it, a
  // mistyped Redis key returns nothing at all and nobody notices.
  database: new Set(["db_table", "db_column", "db_function", "db_policy",
                     "db_table_use", "db_column_use", "db_function_use",
                     "edge_function", "edge_function_use", "storage_bucket",
                     "firestore_collection", "firestore_rule",
                     "cloud_function", "cloud_function_use"]),
  redis: new Set(["redis_key", "redis_key_use", "redis_ttl"]),
  graphql: new Set(["graphql_field", "graphql_selection"]),
};
// A layer only offers itself when the scan actually found that service.
function layersPresent() {
  const kinds = new Set();
  PAGES.forEach(p => p.ks.forEach(([k]) => kinds.add(MAPDATA.kinds[k])));
  return LAYERS.filter(([key]) => {
    if (!key) return true;
    const want = LAYER_KINDS[key] || SECTION_KINDS[key];
    return !want || [...want].some(k => kinds.has(k));
  });
}
let layer = "";

// A structural layer narrows the page you are on. A SERVICE does not live on a page at
// all: a Stripe webhook is reached by Stripe and a Celery task by a worker, so neither
// hangs off any page entry - which is why picking Stripe while "Base · base.html" was
// selected drew an empty canvas and looked broken. A service layer is global, so it gets
// a synthetic page unioned across every page in the map.
const SERVICE_LAYERS = new Set(["stripe", "celery", "graphql"]);
const LAYER_PAGE = new Map(PAGES.map((p, i) => [p.layer, i]).filter(([k]) => k));
// A page with nothing in it, for a service the scan found no trace of.
const EMPTY_PAGE = {page: "", title: "", where: "", layer: "", n: 0, st: {}, ks: [],
                    nodes: [], edges: [], detailed: true};

function currentPageIndex() {
  if (!SERVICE_LAYERS.has(layer)) return current;
  return LAYER_PAGE.has(layer) ? LAYER_PAGE.get(layer) : -1;
}

function currentPage() {
  const i = currentPageIndex();
  if (i >= 0) return PAGES[i];
  const title = (LAYERS.find(([k]) => k === layer) || [, layer])[1];
  return {...EMPTY_PAGE, page: layer, title: title};
}

// Empty means "every status". A Set rather than a single value because "show me the
// unresolved AND the unused" - the two that are actionable - is the common ask.
const statusFilter = new Set();

const ROWS_PER_PAGE = 60;
// Panel state lives here rather than beside the panel code: the canvas reads `mode` to
// decide its lens, and fillPages() runs before the panel section of this file is reached.
let mode = "map", cq = "", cstatus = "", shown = ROWS_PER_PAGE, asList = false;
// Set when a reader opens one of the Overview backlog rows, so the list they land on is
// the kind they pressed rather than all 14 kinds with theirs somewhere inside.
let ckind = "";

const svg = document.getElementById("cv");
const cvlayer = document.getElementById("cvlayer");
const sheet = document.getElementById("detail");
const dbody = document.getElementById("dbody");
const pages = document.getElementById("pg");
const crumb = document.getElementById("crumb");

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
// "Push Arena · /push_arena/" - the name and the address, which is how a reader knows
// where they are. Falls back to the entry filename for a root no template references,
// because inventing a nicer name for it would be inventing evidence.
// Two entries can render the same template and so carry the same title AND the same URL -
// "Push Arena · /push_arena/" listed twice, different node counts, nothing to tell them
// apart. Where a name is not unique the entry that produced it is appended, because two
// identical rows is worse than a long one.
let _labelCounts = null;
function pageLabel(p) {
  const where = (p.where || "").split(" - ")[0].trim();
  const title = p.title || p.page;
  const base = !where || title === where ? title : `${title} · ${where}`;
  if (!_labelCounts) {
    _labelCounts = {};
    PAGES.forEach(other => {
      if (other.layer) return;
      const w = (other.where || "").split(" - ")[0].trim();
      const t = other.title || other.page;
      _labelCounts[!w || t === w ? t : `${t} · ${w}`] = (_labelCounts[!w || t === w ? t : `${t} · ${w}`] || 0) + 1;
    });
  }
  return _labelCounts[base] > 1 ? `${base} · ${p.page}` : base;
}

function fillPages(counts) {
  // Each option says what a PERSON would call the page. The title and the URL were
  // already known and were spent on a grey optgroup heading, while the option itself read
  // "base-main — 392 nodes" - the name of a build artefact and a number. On a phone the
  // heading is the first thing to truncate, so the only legible part named nothing.
  const out = PAGES.map((p, i) => {
    if (p.layer) return "";
    const drawn = lensedCount(p);
    const tail = counts ? `${counts[i]} changed` : `${drawn} node${drawn === 1 ? "" : "s"}`;
    return `<option value="${i}">${esc(pageLabel(p))} — ${tail}</option>`;
  });
  pages.innerHTML = out.join("");
  pages.value = String(current);
}

pages.onchange = e => {
  current = Number(e.target.value); focus = null; view = {x:0, y:0, k:1};
  closeSheet(); draw();
  if (window.syncReadout) syncReadout();
  // The colour key counts the CURRENT page, and changing the page did not refresh it - so
  // it kept the previous page's numbers. On a page holding one red card the key said
  // "connected 14 · uncertain 2" and hid the unresolved pill entirely, which is the
  // sharpest way this tool could contradict itself: a control insisting there is nothing
  // to look at while the thing to look at is on screen in red.
  if (window.syncChrome) syncChrome();
};

// Clicking a colour in the key filters the canvas to that status. Toggling, so several
// can be on at once, and clicking the last one off restores everything rather than
// leaving an empty canvas that reads as "nothing here".
const colourkey = document.getElementById("colourkey");
colourkey.addEventListener("click", event => {
  const button = event.target.closest(".seg button[data-status]");
  if (!button) return;
  const status = button.dataset.status;
  // "all" is the absence of a filter, not a fifth status - so it clears rather than joins.
  if (!status) statusFilter.clear();
  else if (statusFilter.has(status)) statusFilter.delete(status);
  else statusFilter.add(status);
  syncStatusKey();
  focus = null;
  fillPages();
  draw();
  // A list is filtered by the same control, so it has to be rebuilt with it.
  if (panel && !panel.hidden) renderPanel();
  if (window.syncReadout) syncReadout();
});

window.syncStatusKey = syncStatusKey;
function syncStatusKey() {
  const select = document.getElementById("cst");
  if (select) {
    const one = statusFilter.size === 1 ? [...statusFilter][0] : "";
    if (select.value !== one) select.value = one;
    cstatus = one;
  }
  colourkey.querySelectorAll(".seg button[data-status]").forEach(button => {
    const status = button.dataset.status;
    const on = status ? statusFilter.has(status) : statusFilter.size === 0;
    button.setAttribute("aria-pressed", on ? "true" : "false");
  });
  markFiltered();
}

// Anything other than "everything" has to be visible from across the room. A reader sets
// a filter, pans for a minute, and comes back to a map that is not the whole map - with
// no way to tell except reading five small words in a row of buttons.
function markFiltered() {
  colourkey.classList.toggle("filtering", statusFilter.size > 0);

  const note = document.getElementById("fnote");
  if (!note) return;
  const bits = [];
  if (layer) bits.push((LAYERS.find(([k]) => k === layer) || [, layer])[1]);
  if (statusFilter.size) bits.push([...statusFilter].join(" + "));
  if (fileFilter) bits.push(fileFilter.split("/").pop());
  if (expandedKind) bits.push("opened " + expandedKind.replace(/_/g, " "));
  note.hidden = bits.length === 0;
  if (!bits.length) return;
  note.innerHTML = `<span>${esc(bits.join(" \u00b7 "))}</span>` +
    `<button type="button">clear</button>`;
  note.onclick = event => {
    if (!event.target.closest("button")) return;
    // Everything a filter could be, cleared at once. Half-clearing leaves the reader in
    // the same hole, one step shallower.
    statusFilter.clear();
    layer = "";
    fileFilter = null;
    expandedKind = null;
    const picker = document.getElementById("ly");
    if (picker) picker.value = "";
    const wrap = document.getElementById("pgwrap");
    if (wrap) wrap.hidden = false;
    focus = null;
    syncStatusKey();
    if (window.syncChrome) syncChrome();
    fillPages();
    _layout.key = null;
    draw();
  };
}

syncStatusKey();

// Theme: follow the system until a reader says otherwise, then remember it. The map is
// read on a phone in a light OS as often as on a dark desktop, and a graph of hairlines
// is a different document on each.
// Dark first, because that is the design the map was drawn for and the ground the status
// colours were chosen against - and because a reader on a light phone was getting the pale
// variant with no idea a better one existed. The system setting is still reachable, it is
// just no longer the thing that decides.
// Five packs. A pack is a whole world - palette, faces, radii, easing - and switching one
// is a single attribute on the root element. Aurora is the default because a graph of
// hairlines reads better on a dark ground, and because a dark theme with no chroma in it
// (which is what this map had) reads as nothing at all.
const PACKS = [
  ["aurora",    "Aurora",    "indigo · violet · mint",     false],
  ["blueprint", "Blueprint", "paper · ink · cobalt",       true],
  ["phosphor",  "Phosphor",  "terminal · green · amber",   false],
  ["signal",    "Signal",    "sand · espresso · orange",   true],
  ["slate",     "Slate",     "neutral · indigo · dense",   true],
];
const PACK_SWATCH = {
  aurora:["#080a1a","#7c6cff","#2ee6a8","#ff6b8a"],
  blueprint:["#fbfaf7","#2b5ce6","#141d2e","#d94436"],
  phosphor:["#080b09","#4ee88f","#ffb833","#ff5f56"],
  signal:["#f8f5ef","#e8532a","#241d18","#177f6f"],
  slate:["#f7f9fb","#3b5bdb","#0f1b2a","#0d8050"],
};
const tmode = document.getElementById("tmode");
const packmenu = document.getElementById("packmenu");
let packNow = "aurora", modeNow = "light";
try {
  const p = localStorage.getItem("seamcheck-pack");
  if (p && PACKS.some(([k]) => k === p)) packNow = p;
  const m = localStorage.getItem("seamcheck-mode");
  if (m === "dark" || m === "light") modeNow = m;
} catch { /* private window, or storage refused: the default pack is fine */ }

function hasModes(k) { return (PACKS.find(([p]) => p === k) || [])[3]; }

function applyTheme() {
  const root = document.documentElement;
  root.setAttribute("data-pack", packNow);
  // A light Aurora is not Aurora, and a light Phosphor is a text editor. Those two are
  // single-world designs; offering a worse second version of them is not a kindness.
  if (hasModes(packNow)) root.setAttribute("data-mode", modeNow);
  else root.removeAttribute("data-mode");
  const name = (PACKS.find(([k]) => k === packNow) || [, packNow])[1];
  tmode.textContent = name;
  tmode.title = "Appearance: " + name + " — tap to change";
  if (packmenu) {
    packmenu.querySelectorAll("[data-pk]").forEach(b =>
      b.setAttribute("aria-current", b.dataset.pk === packNow));
    const row = packmenu.querySelector(".modes");
    if (row) {
      row.hidden = !hasModes(packNow);
      row.querySelectorAll("[data-md]").forEach(b =>
        b.setAttribute("aria-current", b.dataset.md === modeNow));
    }
  }
  // The canvas re-reads the pack's wire weight and corner radius on the next draw.
  if (window.draw && typeof current !== "undefined") requestAnimationFrame(() => draw());
}

if (packmenu) {
  packmenu.innerHTML =
    PACKS.map(([k, name, tag]) =>
      `<button type="button" class="pk" data-pk="${k}">
         <span class="sw">${PACK_SWATCH[k].map(c =>
           `<i style="background:${c}"></i>`).join("")}</span>
         <span class="pkn"><b>${name}</b><em>${tag}</em></span>
       </button>`).join("") +
    `<div class="modes"><span class="mlbl">Ground</span>
       <button type="button" data-md="light">Light</button>
       <button type="button" data-md="dark">Dark</button></div>`;
  packmenu.addEventListener("click", e => {
    const pk = e.target.closest("[data-pk]"), md = e.target.closest("[data-md]");
    if (pk) { packNow = pk.dataset.pk;
      try { localStorage.setItem("seamcheck-pack", packNow); } catch { /* fine */ } }
    if (md) { modeNow = md.dataset.md;
      try { localStorage.setItem("seamcheck-mode", modeNow); } catch { /* fine */ } }
    if (pk || md) applyTheme();
    e.stopPropagation();
  });
}
tmode.addEventListener("click", e => {
  e.stopPropagation();
  if (packmenu) packmenu.classList.toggle("open");
});
document.addEventListener("click", () => {
  if (packmenu) packmenu.classList.remove("open");
});
applyTheme();

// The nodes a page contributes under the current lens AND status filter. One function,
// because the page selector's count and the canvas must agree: a dropdown that promises
// "16 nodes" over a canvas showing two is the tool lying about its own view.
function lensAllows(kind, status) {
  const kinds = SECTION_KINDS[mode];
  const only = layer ? (LAYER_KINDS[layer] || SECTION_KINDS[layer]) : null;
  return (!kinds || kind === "page" || kinds.has(kind)) &&
    (!only || only.has(kind)) &&
    (!statusFilter.size || kind === "page" || statusFilter.has(status));
}
function lensed(p) {
  return (p.nodes || []).filter(n => lensAllows(n.kind, n.status));
}
// The same count from the manifest, for a page that is not loaded: the picker lists
// every page and must not decode 60 chunks to number them.
function lensedCount(p) {
  if (p.nodes) return lensed(p).length;
  return p.ks.reduce((sum, [k, st, n]) =>
    sum + (lensAllows(MAPDATA.kinds[k], MAPDATA.statuses[st]) ? n : 0), 0);
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
  // A file filter NARROWS what is drawn; it does not replace every other filter. Returning
  // here meant clicking a file in Files silently discarded the layer and the status filter,
  // and every one set afterwards did nothing - the control moved and the canvas did not.
  if (fileFilter) {
    return new Set(lensed(p).filter(n => n.file === fileFilter).map(n => n.id));
  }
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
// The heading over each hop of an isolated path. Reads as the story, not the schema.
// Roughly the detail sheet's width, so an isolated path is laid out beside it.
const SHEET_ROOM = 420;
const HOP_WORD = {
  page: "the page", module: "the file", js_call: "asks for", fetch_target: "crosses",
  url: "the route", view: "the handler", dom_selector: "looks for",
  dom_attr: "the element", css_selector: "styled by",
  multi_writer_element: "all write", redis_key: "the key", db_table_use: "reads",
  redis_key_use: "touches", 
  db_table: "the table", job_enqueue: "queues", job: "the worker",
};
// The second line of a card, in a reader's words rather than the extractor's.
const KIND_WORD = {
  page: "page", module: "source file", js_call: "fetch call",
  fetch_target: "request", url: "route", view: "handler", model: "model",
  dom_selector: "selector in js", dom_attr: "element in a template",
  multi_writer_element: "written by more than one file",
  css_selector: "css rule", css_token_def: "design token", css_token_use: "var() use",
  json_field: "response field", url_reference: "link or reverse()",
  celery_task: "background task", celery_schedule: "scheduled",
  job: "background job", job_enqueue: "queues work", job_schedule: "schedule",
  env_var: "config key", env_read: "reads config",
  stripe_webhook: "stripe webhook", stripe_event: "stripe event",
  signal_receiver: "signal receiver", admin_action: "admin action",
  template_tag: "template tag", graphql_field: "graphql field",
  graphql_selection: "graphql query",
};

// Read once per draw from the pack, not per edge: getComputedStyle in a loop over four
// thousand edges is the difference between a map that pans and one that stutters.
let NODE_R = 5, WIRE_STYLE = "curve";
function readPack() {
  const cs = getComputedStyle(document.documentElement);
  NODE_R = parseFloat(cs.getPropertyValue("--r-node")) || 5;
  WIRE_STYLE = (cs.getPropertyValue("--wire-style") || "curve").trim() || "curve";
}
const MARKERS = '<defs>' + [
  ["connected", "var(--ok)"], ["unresolved", "var(--crit)"],
  ["unused", "var(--warn)"], ["uncertain", "var(--dim)"], ["dim", "var(--dim)"],
].flatMap(([name, colour]) => [["", 5], ["-lit", 7]].map(([suffix, size]) =>
  `<marker id="ar-${name}${suffix}" viewBox="0 0 10 10" refX="9" refY="5"` +
  ` markerWidth="${size}" markerHeight="${size}" orient="auto-start-reverse">` +
  `<path d="M0,1 L9,5 L0,9 z" fill="${colour}"/></marker>` +
  `<marker id="tl-${name}${suffix}" viewBox="0 0 10 10" refX="1" refY="5"` +
  ` markerWidth="${size}" markerHeight="${size}" orient="auto-start-reverse">` +
  `<path d="M10,1 L1,5 L10,9 z" fill="${colour}"/></marker>`
)).join("") + '</defs>';

function wirePath(ax, ay, bx, by, straight) {
  // Under isolation the reader has asked to follow ONE path, so the line stops being
  // decorative and becomes a schematic: right angles, which are far easier to trace with
  // an eye than a bundle of parallel curves. The turn is offset from the midpoint so two
  // runs between the same rows do not lie on top of each other.
  if (straight) {
    const mx = ax + Math.max(24, (bx - ax) / 2);
    return `M${ax},${ay} L${mx},${ay} L${mx},${by} L${bx},${by}`;
  }
  if (WIRE_STYLE === "ortho") {
    // Right angles: a schematic. The turn happens halfway, so parallel runs stay parallel.
    const mx = ax + (bx - ax) / 2;
    return `M${ax},${ay} L${mx},${ay} L${mx},${by} L${bx},${by}`;
  }
  if (WIRE_STYLE === "organic") {
    // A long, lazy sweep - it leaves its origin sideways and arrives the same way.
    const d = Math.max(70, Math.abs(bx - ax) * 0.7);
    return `M${ax},${ay} C${ax + d},${ay} ${bx - d},${by} ${bx},${by}`;
  }
  const mx = (ax + bx) / 2;
  return `M${ax},${ay} C${mx},${ay} ${mx},${by} ${bx},${by}`;
}

// Cards per row. The layout is tried at each and the one that fits the canvas largest
// wins - a wide screen wants long rows, a phone wants short ones, and neither should be
// a constant somebody guessed.
const ROW_CHOICES = [3, 4, 5, 6, 8, 10, 12, 16, 20];

// How tall a column runs before wrapping into another lane. Fixed at 42, the widest page
// laid out 6,600 x 1,300 and fitting that to a landscape canvas shrank it to 18% with two
// thirds of the screen empty; a formula off the tallest column alone then made the SMALL
// pages worse, because what drives the width is every column's lanes, not one column's
// depth. So the layout is simply computed at each candidate and the one that fits largest
// wins - a handful of arithmetic passes over a few thousand nodes, once per draw.
// Bands stack, they do not sit side by side. The browser is a horizontal strip across the
// top, the seam is the strip under it, the server is under that, and anything no browser
// reaches is last. Reading down the canvas is reading the request: a person touches
// something at the top, it crosses the middle, and the bottom is what runs. Columns still
// run left-to-right INSIDE a band, which is where the kind ordering belongs.
const BAND_TOP = 96, BAND_BOT = 22, BAND_GAP = 26;
// Room for a lane heading, and the air before one that is not the band's first.
const LANE_HEAD = 34, LANE_GAP = 26;
// The band's own heading is two lines tall, so the first lane inside it has to start
// below them - without this the store name sat on top of the band's subtitle.
const LANE_FIRST_DROP = 16;

// Cards, flowing left to right, not slivers stacked in a column. A 150x20 sliver holds one
// truncated line and 18,000 of them is a wall nobody reads. A card is 190x48 with a name on
// one line and what it IS on the next, and a kind with more of them than fit collapses to a
// single big card carrying the count - which is the one that matters at this zoom anyway.
const CARD_W = 190, CARD_H = 48, GAP_X = 12, GAP_Y = 10;
const BIG_W = 268, BIG_H = 62;
const KIND_GAP = 26, KIND_LABEL = 22;
// Above this a kind is a number, not a list. Twenty-eight cards is already two full rows.
const AGGREGATE_OVER = 28;

function place(buckets, used, perRow) {
  const pos = new Map();
  const columns = [];
  const bands = [];
  const aggregates = [];
  const bandOfKind = new Map();
  BANDS.forEach((band, i) => band.kinds.forEach(k => bandOfKind.set(k, i)));

  const lanes = [];
  const groups = new Map();
  used.forEach(c => {
    const kind = COLS[c] ? COLS[c][0] : "other";
    const at = bandOfKind.has(kind) ? bandOfKind.get(kind) : BANDS.length;
    if (!groups.has(at)) groups.set(at, []);
    groups.get(at).push(c);
  });

  const rowWidth = perRow * (CARD_W + GAP_X);
  let y = 12, width = 0;

  [...groups.keys()].sort((m, n) => m - n).forEach(at => {
    let x = 44, rowTop = y + BAND_TOP, tallest = 0;
    const bandLangs = new Set();
    const bandDef = BANDS[at];
    // A band with lanes orders its columns by store and heads each run with the store's
    // name and its oracle badge. Everything else keeps the flat kind order.
    let ordered = groups.get(at);
    let laneOf = null;
    if (bandDef && bandDef.lanes) {
      const which = kind => bandDef.lanes.findIndex(
        L => L.prefix.some(pre => kind.startsWith(pre)));
      laneOf = new Map(ordered.map(c => [c, which(COLS[c] ? COLS[c][0] : "")]));
      ordered = [...ordered].sort((m, n) => (laneOf.get(m) - laneOf.get(n)) || (m - n));
    }
    let lastLane = -2;
    // One column of one kind, laid out where the cursor currently is. Pulled out of the
    // loop so the same code can lay out a whole kind, or one service's slice of it.
    const layoutColumn = (c, items) => {
      if (!items || !items.length) return;
      const kind = COLS[c] ? COLS[c][0] : "other";
      const label = COLS[c] ? COLS[c][1] : "Other";
      // A new kind starts on a fresh row, so the group heading always sits over its own
      // cards rather than over the tail of the previous kind's.
      if (x > 44) { x = 44; rowTop += tallest + GAP_Y + KIND_GAP; tallest = 0; }
      if (laneOf && laneOf.get(c) !== lastLane) {
        lastLane = laneOf.get(c);
        const lane = bandDef.lanes[lastLane];
        if (lane) {
          // Does this scan hold an oracle for this store? Only true if it actually found
          // declarations - a Supabase project with no migrations checked in has the same
          // lane and cannot verify a single name in it.
          const hasOracle = lane.declared.some(k => {
            const idx = ORDER.get(k);
            return idx !== undefined && buckets.has(idx) && buckets.get(idx).length;
          });
          rowTop += rowTop > y + BAND_TOP ? LANE_GAP : LANE_FIRST_DROP;
          lanes.push({x: 44, y: rowTop - 30, name: lane.name, id: lane.id,
                      oracle: hasOracle,
                      badge: hasOracle ? "SCHEMA IN REPO"
                                       : "NO SCHEMA \u00b7 PAIRING ONLY"});
          rowTop += LANE_HEAD;
        }
      }
      columns.push({x, y: rowTop - 9, kind, label, count: items.length});
      // Which languages this band actually contains. A reader zoomed out wants to know
      // that the browser strip is JavaScript AND TypeScript AND CSS before they can read
      // a single card, and that the server strip is a different language again - that
      // crossing is the whole subject and the map never said it.
      items.forEach(n => { if (n.lang) bandLangs.add(n.lang); });

      // `!expandedKind` here once meant that opening ONE kind opened EVERY kind on the
      // page at once - 2,000 cards and every wire between them, the wall this card exists
      // to prevent. A kind is open only when it is the one the reader tapped.
      if (items.length > AGGREGATE_OVER && expandedKind !== kind) {
        // One card for the whole kind, sized up because it stands for more.
        const worst = ["unresolved", "unused", "uncertain", "connected"]
          .find(st => items.some(n => n.status === st)) || "connected";
        const open = items.filter(n => n.status === "unresolved" || n.status === "unused").length;
        aggregates.push({kind, label, x, y: rowTop, w: BIG_W, h: BIG_H,
                         count: items.length, open, status: worst});
        // Every node in the kind is parked on the card, so edges still land somewhere
        // truthful and the chain a reader lights still reaches the right region.
        items.forEach(n => pos.set(n.id, {x, y: rowTop, w: BIG_W, h: BIG_H, agg: true}));
        x += BIG_W + GAP_X;
        tallest = Math.max(tallest, BIG_H);
        return;
      }
      // An opened kind shows one window of EXPAND_PAGE cards; the rest are parked on a
      // "more" card at the end, which is where their wires land and what the reader taps
      // to see the next window. Nothing is cut without a card that says so.
      let shown = items, rest = [];
      if (expandedKind === kind && items.length > EXPAND_PAGE) {
        const from = Math.min(expandedFrom, items.length - 1);
        shown = items.slice(from, from + EXPAND_PAGE);
        rest = items.slice(0, from).concat(items.slice(from + EXPAND_PAGE));
      }
      shown.forEach((n, i) => {
        const col = i % perRow, row = Math.floor(i / perRow);
        pos.set(n.id, {x: x + col * (CARD_W + GAP_X),
                       y: rowTop + row * (CARD_H + GAP_Y), w: CARD_W, h: CARD_H});
      });
      let rows = Math.ceil(shown.length / perRow);
      if (rest.length) {
        const my = rowTop + rows * (CARD_H + GAP_Y);
        aggregates.push({kind, label, x, y: my, w: BIG_W, h: BIG_H, more: true,
                         count: rest.length, open: 0, status: "connected",
                         from: Math.min(expandedFrom, items.length - 1), total: items.length});
        rest.forEach(n => pos.set(n.id, {x, y: my, w: BIG_W, h: BIG_H, agg: true}));
        rows += 1;
        tallest = Math.max(tallest, rows * (CARD_H + GAP_Y) - GAP_Y + (BIG_H - CARD_H));
      } else {
        tallest = Math.max(tallest, rows * (CARD_H + GAP_Y) - GAP_Y);
      }
      x += Math.min(shown.length, perRow) * (CARD_W + GAP_X);
      width = Math.max(width, x);
    };

    // Which deployables have anything in this band. A monorepo renders as one
    // undifferentiated strip otherwise: a Django service and a Node service become an
    // anonymous row of `route` cards, and the fact that a request crosses from one
    // deployable into another - the thing the reader came for - is nowhere on screen.
    const svcOf = n => n.service || "";
    const bandServices = [...new Set(
      ordered.flatMap(c => (buckets.get(c) || []).map(svcOf)))].filter(Boolean).sort();

    if (!laneOf && bandServices.length > 1) {
      // A lane per service, in name order, with anything unattributed last so it is
      // visibly the remainder rather than silently folded into a real service.
      const runs = [...bandServices, ""];
      runs.forEach(svc => {
        const slice = new Map(ordered.map(c =>
          [c, (buckets.get(c) || []).filter(n => svcOf(n) === svc)]));
        if (![...slice.values()].some(v => v.length)) return;
        if (x > 44) { x = 44; rowTop += tallest + GAP_Y + KIND_GAP; tallest = 0; }
        rowTop += rowTop > y + BAND_TOP ? LANE_GAP : LANE_FIRST_DROP;
        const langs = [...new Set([...slice.values()].flat()
          .map(n => n.lang).filter(Boolean))].sort();
        lanes.push({x: 44, y: rowTop - 30, id: svc || "unattributed",
                    name: (svc || "not in any service")
                          + (langs.length ? "  \u00b7  " + langs.join(", ") : ""),
                    oracle: true, badge: svc ? "SERVICE" : ""});
        rowTop += LANE_HEAD;
        ordered.forEach(c => layoutColumn(c, slice.get(c)));
      });
    } else {
      ordered.forEach(c => layoutColumn(c, buckets.get(c)));
    }
    width = Math.max(width, x, rowWidth + 44);
    const h = BAND_TOP + (rowTop - (y + BAND_TOP)) + tallest + BAND_BOT;
    const band = BANDS[at] || {id: "other", label: "EVERYTHING ELSE THE SCAN FOUND",
                               short: "EVERYTHING ELSE"};
    bands.push({id: band.id, label: band.label, short: band.short || band.label,
                langs: [...bandLangs].sort(), y, h, first: bands.length === 0});
    y += h + BAND_GAP;
  });
  bands.forEach(band => { band.w = Math.max(width - 26, 300); });
  return {pos, columns, bands, lanes, aggregates, width: width + 44, height: y};
}

// A column wraps into lanes instead of running off the bottom of the world. One page here
// holds 839 selectors: stacked in a single file that column stood 25,000px tall and no
// amount of scrolling made the page legible, which is why the map used to show only
// modules until you drilled in. Wrapped, the same page is about 1,300px tall and every
// symbol it has is on screen at once.
// Following ONE path is a different picture from surveying a page, and it was being
// drawn with the survey's rules: kind columns stacked downwards, so a four-hop chain came
// out as a single narrow column and the four writers of one element sat on top of each
// other. A path has a direction, and a direction reads left to right.
//
// Hops are BFS depth from the page, so every node lands in the column matching its
// distance along the chain and a fan-in - the case where the shape IS the finding - draws
// as several cards in one column converging on the next.
function layoutPath(p, keep) {
  const nodes = p.nodes.filter(n => keep.has(n.id));
  const byId = new Map(nodes.map(n => [n.id, n]));
  const fwd = new Map(), back = new Map();
  p.edges.forEach(e => {
    if (!byId.has(e.source) || !byId.has(e.target) || e.source === e.target) return;
    (fwd.get(e.source) || fwd.set(e.source, []).get(e.source)).push(e.target);
    (back.get(e.target) || back.set(e.target, []).get(e.target)).push(e.source);
  });
  // Start from the page if it is here, otherwise from whatever nothing points at.
  const roots = nodes.filter(n => n.kind === "page").map(n => n.id);
  const starts = roots.length ? roots
    : nodes.filter(n => !(back.get(n.id) || []).length).map(n => n.id);
  const depth = new Map();
  let front = (starts.length ? starts : [nodes[0] && nodes[0].id]).filter(Boolean);
  front.forEach(id => depth.set(id, 0));
  while (front.length) {
    const next = [];
    front.forEach(id => (fwd.get(id) || []).forEach(m => {
      if (depth.has(m)) return;
      depth.set(m, depth.get(id) + 1);
      next.push(m);
    }));
    front = next;
  }
  // Anything the walk never reached still has to be drawn somewhere truthful.
  let deepest = 0;
  depth.forEach(d => { deepest = Math.max(deepest, d); });
  nodes.forEach(n => { if (!depth.has(n.id)) depth.set(n.id, deepest + 1); });

  const cols = new Map();
  nodes.forEach(n => {
    const d = depth.get(n.id);
    if (!cols.has(d)) cols.set(d, []);
    cols.get(d).push(n);
  });
  const STEP_X = CARD_W + 96, STEP_Y = CARD_H + 26;
  const pos = new Map();
  const columns = [];
  let width = 0, height = 0;
  [...cols.keys()].sort((a, b) => a - b).forEach((d, i) => {
    const items = cols.get(d);
    const x = 60 + i * STEP_X;
    // Centred on a common axis, so a column of four writers brackets the single card it
    // converges on rather than hanging below it.
    const total = items.length * STEP_Y - (STEP_Y - CARD_H);
    const top = 120 + Math.max(0, (260 - total) / 2);
    items.forEach((n, j) => {
      pos.set(n.id, {x, y: top + j * STEP_Y, w: CARD_W, h: CARD_H});
      height = Math.max(height, top + j * STEP_Y + CARD_H);
    });
    columns.push({x, y: top - 18, kind: items[0].kind,
                  label: HOP_WORD[items[0].kind] || (items.length > 1 ? "these" : "then"),
                  count: items.length});
    width = Math.max(width, x + CARD_W);
  });
  // The detail sheet is open whenever this layout is in use - it is the thing the
  // reader clicked to get here - so the path has to be fitted into what is left of the
  // canvas. Without this the last hop, which is the finding itself, sat underneath it.
  return {pos, columns, bands: [], lanes: [], aggregates: [],
          width: width + 60 + SHEET_ROOM, height: height + 80};
}


function layout(p, keep) {
  if (isolate && lit) return layoutPath(p, keep);
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

// Search the WHOLE scan, not the page you happen to be on. The box faded non-matches
// among the nodes already drawn, so "where is cookieConsentBanner?" - the most obvious
// question to ask it - could only be answered by guessing the right page first.
//
// Built once, lazily: 37,000 nodes is a list worth keeping, and worth not building for a
// reader who never types anything.
//
// Columnar, not a row per symbol. Turning every row into an object with its own search
// string cost ~300 bytes of heap each - 633 MB at two million symbols, measured. The
// ids and labels arrive as one newline-joined string apiece, the numeric columns become
// typed arrays, and a row is materialised only when it is a result.
let _index = null, _indexLoading = null;
// Where each row starts in a joined string, plus one past the end, so row i is
// text.slice(at[i], at[i + 1] - 1). One pass, once.
function rowStarts(text, count) {
  const at = new Uint32Array(count + 1);
  let pos = 0;
  for (let i = 0; i < count; i++) { at[i] = pos; pos = text.indexOf("\n", pos) + 1; }
  at[count] = text.length + 1;
  return at;
}
// Synchronous once loaded; before that it returns null and starts the load, and `then`
// runs the caller again when the columns are in. The box shows "Opening the index…"
// for the one pause a reader will ever see from it.
function searchIndex(then) {
  if (_index) return _index;
  if (!_indexLoading) {
    _indexLoading = readChunk("search").then(cols => {
      cols = cols || {n: 0, ids: "", labels: "", kind: [], status: [], file: [], line: [], page: []};
      const n = cols.n | 0;
      const ids = cols.ids, labels = cols.labels;
      const idAt = rowStarts(ids, n), labelAt = rowStarts(labels, n);
      const K = MAPDATA.kinds, S_ = MAPDATA.statuses, FI = MAPDATA.files;
      const kind = Int32Array.from(cols.kind), status = Int32Array.from(cols.status);
      const file = Int32Array.from(cols.file), line = Int32Array.from(cols.line);
      const page = Int32Array.from(cols.page);
      dropChunk("search");
      _index = {
        n, lower: labels.toLowerCase(), labelAt, kind, status, file, line, page,
        label: i => labels.slice(labelAt[i], labelAt[i + 1] - 1),
        row: i => ({id: ids.slice(idAt[i], idAt[i + 1] - 1),
                    label: labels.slice(labelAt[i], labelAt[i + 1] - 1),
                    kind: K[kind[i]], status: S_[status[i]], file: FI[file[i]] || "",
                    line: line[i] || null, page: page[i]}),
      };
      return _index;
    });
  }
  if (then) _indexLoading.then(then, () => {});
  return null;
}

// The row a character offset falls in: the last start at or before it.
function rowOf(at, offset) {
  let lo = 0, hi = at.length - 2;
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1;
    if (at[mid] <= offset) lo = mid; else hi = mid - 1;
  }
  return lo;
}

// Ranked, because a substring match puts "cart" behind "shopping-cart-badge-count" and a
// reader typing four letters means the four-letter thing.
function searchEverywhere(term, limit = 60) {
  const needle = term.trim().toLowerCase();
  const ix = searchIndex();
  if (needle.length < 2 || !ix) return [];
  const found = new Map();  // row -> score
  // Labels: one scan of one string, at memchr speed, one hit per row.
  const lower = ix.lower, at = ix.labelAt;
  let pos = 0;
  while (found.size <= 4000) {
    const hit = lower.indexOf(needle, pos);
    if (hit === -1) break;
    const i = rowOf(at, hit);
    const label = lower.slice(at[i], at[i + 1] - 1);
    found.set(i, label === needle ? 0 : label.startsWith(needle) ? 1 : 2);
    pos = at[i + 1];
  }
  // Files and kinds are interned tables of a few thousand at most: match those, then
  // the rows are a typed-array compare each.
  const fileHit = new Set(), kindHit = new Set();
  MAPDATA.files.forEach((f, k) => { if (f.toLowerCase().includes(needle)) fileHit.add(k); });
  MAPDATA.kinds.forEach((f, k) => { if (f.toLowerCase().includes(needle)) kindHit.add(k); });
  if (fileHit.size || kindHit.size) {
    const file = ix.file, kind = ix.kind;
    for (let i = 0; i < ix.n && found.size <= 4000; i++) {
      if (!found.has(i) && (fileHit.has(file[i]) || kindHit.has(kind[i]))) found.set(i, 3);
    }
  }
  const out = [...found].sort((a, b) => a[1] - b[1]
    || (at[a[0] + 1] - at[a[0]]) - (at[b[0] + 1] - at[b[0]]));
  return out.slice(0, limit).map(([i]) => ix.row(i));
}

// Open a result where it lives: the page that holds it, the node selected, its chain lit.
function jumpTo(id, page) {
  if (typeof page === "number" && page !== current) {
    current = page;
    pages.value = String(current);
    view = {x: 0, y: 0, k: 1};
  }
  layer = "";
  const picker = document.getElementById("ly");
  if (picker) picker.value = "";
  focus = null;
  if (SECTION_KINDS[mode] === undefined) switchTo("map");
  asList = false;
  lit = id;
  return ensurePage(current).then(() => { draw(); show(id); });
}

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

// A filter that matches nothing draws an empty canvas, and an empty canvas is
// indistinguishable from a broken one. Stripe has six symbols and none of them are
// unresolved, so "Stripe + unresolved" is legitimately empty - and has to SAY so, naming
// the filters that emptied it, because the reader set them one at a time and cannot see
// the combination.
function nothingBox() {
  let box = document.getElementById("nothing");
  if (!box) {
    box = document.createElement("div");
    box.id = "nothing";
    box.className = "nothing";
    (document.querySelector(".main") || document.body).appendChild(box);
  }
  return box;
}
function reportOpening(p) {
  const box = nothingBox();
  box.hidden = false;
  box.innerHTML = `<b>Opening ${esc(p.title || p.page)}…</b>
    <span>${p.n.toLocaleString()} symbols</span>`;
}
function reportFailure(err) {
  const box = nothingBox();
  box.hidden = false;
  box.innerHTML = `<b>This page could not be opened.</b><span>${esc(String(err && err.message || err))}</span>`;
  console.error("SC-MAP-CHUNK", err);
}
function reportEmpty(count) {
  const box = nothingBox();
  box.hidden = count > 0;
  if (count > 0) return;
  // A commit filter empties the canvas far more often than the others, because most
  // commits touch documentation, config or tests - none of which the scan reads. The
  // header note said so and the header is 11px tall on a phone; the empty canvas is where
  // a reader is actually looking.
  if (only) {
    const chosen = COMMITS[Number(picker.value)];
    box.innerHTML = chosen
      ? `<b>This commit changed nothing the scan reads.</b>
         <span>${esc(chosen.sha.slice(0, 8))} · ${esc(chosen.subject)}</span>
         <span>Documentation, config and tests are not in the graph. Pick a commit
         marked "changed", or go back to everything.</span>`
      : "<b>Nothing changed here.</b><span>Go back to everything in this scan.</span>";
    return;
  }
  const bits = [];
  if (layer) bits.push((LAYERS.find(([k]) => k === layer) || [, layer])[1]);
  if (statusFilter.size) bits.push([...statusFilter].join(" or "));
  if (fileFilter) bits.push("in " + fileFilter.split("/").pop());
  box.innerHTML = bits.length
    ? `<b>Nothing is both ${bits.map(esc).join(" and ")}.</b>
       <span>The filters are fine; this combination is empty. Take one off, or pick
       another page.</span>`
    : "<b>Nothing to draw here.</b><span>Try another page.</span>";
}

// The layout is nine trial placements over every node on the page - the single most
// expensive thing here - and it was recomputed on every draw, including the draws that
// only moved the viewport. Nothing about a pan changes where a node sits relative to its
// neighbours, so the answer is cached against the things that DO change it.
let _layout = {key: null, keep: null, value: null};

function layoutKey() {
  // Every input to `visible()` has to be in this key. The layer and the status filter were
  // missing, so choosing "Stripe" or "unresolved" recomputed the page COUNT and reused the
  // cached layout - the dropdown said 35 nodes over a canvas still drawing all 392. A
  // cache keyed on less than its function reads is a cache that lies.
  return [current, mode, focus, fileFilter, only, isolate ? lit : "", asList,
          layer, expandedKind || "", expandedFrom, [...statusFilter].sort().join(",")].join("\u0000");
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

let _drawWaiting = -1;
function draw() {
  const p = currentPage();
  if (!p) return;
  if (!p.nodes) {
    // The page's rows are still text in the document. Decode them, then draw for real;
    // every caller of draw() gets this for free and none of them has to wait.
    const want = currentPageIndex();
    if (_drawWaiting !== want) {
      _drawWaiting = want;
      svg.innerHTML = "";
      reportOpening(p);
      ensurePage(want).then(() => {
        _drawWaiting = -1;
        if (currentPageIndex() === want) { draw(); if (window.syncChrome) syncChrome(); }
      }, err => { _drawWaiting = -1; reportFailure(err); });
    }
    return;
  }
  readPack();
  pages.value = String(current);
  const here = p.where ? `${p.title} · ${p.where}` : p.title;
  // Count what is DRAWN, not what the file holds: now that a filter narrows a file
  // selection instead of replacing it, the two differ, and a breadcrumb reading "674 of
  // 674" over a canvas showing nine is the same lie the filter bug was.
  const onPage = fileFilter ? p.nodes.filter(n => n.file === fileFilter).length : 0;
  const drawnHere = fileFilter ? visible(p).size : 0;
  const inFile = FILE_TOTALS.get(fileFilter) || onPage;
  const narrowed = drawnHere < onPage;
  crumb.textContent = fileFilter
      ? `${here} › ${fileFilter} — ${drawnHere} of ${inFile} symbols`
        + (narrowed ? "; filtered" : "")
        + (onPage < inFile ? "; the rest are not reached from any page" : "")
    : focus ? `${here} › ${(byId.get(focus) || {}).label || ""}`
    : `${here} — pick a module`;
  document.getElementById("up").hidden = !focus;
  // draw() is what WRITES the breadcrumb, so the readout has to be told after it, not
  // before - switchTo asked the question while the answer was still the previous view's.
  if (window.syncReadout) syncReadout();
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
    svg.innerHTML = "";
    reportEmpty(0);
    return;
  }
  // Filters that exclude everything draw an empty canvas, which is indistinguishable from
  // a broken one - and "the filter is not working" was exactly the report. ONE thing says
  // so: the notice box, which names the filters and sits where the reader is looking.
  // This used to write its own line of svg text instead, so the box - the one with the
  // "clear" beside it - never appeared for exactly the case it was written for.
  const shownNow = visible(p);
  const onlyPage = [...shownNow].every(id => (byId.get(id) || {}).kind === "page");
  if (!shownNow.size || onlyPage) {
    svg.innerHTML = "";
    reportMatches(0, 0);
    reportEmpty(0);
    return;
  }
  const {keep, value: {pos, columns, bands, lanes, aggregates, width, height}} = layoutFor(p);
  // A phone is narrower than two columns of this map, so an untouched view opens showing
  // the whole chain, nudged clear of the left edge. Only the first draw of a view fits:
  // once someone pans or zooms, the view is theirs.
  if (view.k === 1 && view.x === 0 && view.y === 0) {
    // The controls float ON the canvas, so the drawing has to open INSIDE what is left of
    // it. Fitting to the whole element put the top band under the menu button and the
    // bottom one under the status pills - the first and last thing a reader looks at were
    // the two things covered up.
    // Measured from the controls themselves, not from a custom property they publish: on
    // a phone the status pills wrap to two rows, so the number in the variable is a frame
    // out of date exactly when it matters most and the last band ends up under them.
    const box = svg.getBoundingClientRect();
    const clear = (el, edge) => {
      if (!el || el.hidden) return 56;
      const r = el.getBoundingClientRect();
      if (!r.height) return 56;
      return edge === "top" ? (r.bottom - box.top) + 14 : (box.bottom - r.top) + 14;
    };
    const inTop = clear(document.getElementById("menubtn"), "top");
    const inBot = clear(document.getElementById("colourkey"), "bottom");
    const haveW = (svg.clientWidth || 800) - 24;
    const haveH = (svg.clientHeight || 600) - inTop - inBot;
    // The floor is low on purpose: at that zoom nobody is reading labels, they are seeing
    // which blocks connect and which stand alone.
    view.k = Math.min(1, Math.max(0.06, Math.min(haveW / (width + 40), haveH / (height + 40))));
    view.x = 12;
    view.y = inTop + Math.max(0, (haveH - height * view.k) / 2);
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
  reportEmpty(drawnNodes.length);
  // Under isolation EVERY drawn edge is on the chain, so the chain set is still what
  // decides how a wire is drawn - excluding it here meant the schematic style, the heavier
  // stroke and the lit arrowheads were all switched off in precisely the view whose
  // comment says it exists to turn them on. The reader asked to follow one path and got
  // the decorative curves back.
  const chain = lit ? chainOf(p, lit) : null;
  // Arrowhead markers, emitted with every draw. They cannot live in the static shell:
  // draw() replaces svg.innerHTML wholesale, so defs written once were wiped on the first
  // frame and every marker-end pointed at nothing - the wires had no heads at all, and
  // the markup looked perfectly correct while it happened. One marker per status because
  // a marker cannot inherit the stroke of the path using it, and a grey head on a red
  // wire reads as a different edge.
  const out = [MARKERS, '<g id="vp">'];
  // The bands, drawn first and behind everything: full-width horizontal strips a reader
  // goes DOWN, which is the direction a request actually travels. A person touches
  // something in the top strip, it crosses the middle, and the bottom is what runs.
  (bands || []).forEach(band => {
    out.push(`<rect class="band ${esc(band.id)}" x="26" y="${band.y}"
      width="${band.w}" height="${band.h}" rx="18"/>`);
    // Two lines, and the first of them big. A 10px tracked heading is a caption; the
    // thing a reader needs at a glance is WHICH REGION they are looking at, so the region
    // gets display size and the sentence explaining it sits underneath in small caps.
    const [big, ...rest] = band.label.split(" \u2014 ");
    out.push(`<text class="bandbig" x="44" y="${band.y + 42}">${esc(big)}</text>`);
    if (rest.length) {
      out.push(`<text class="bandlbl" x="44" y="${band.y + 62}">${esc(rest.join(" \u2014 "))}</text>`);
    }
    // The languages in this strip, named on the strip. Right-aligned so it reads as a
    // property of the region rather than part of its title, and large enough to survive
    // being zoomed out - which is the only view in which "what is this repository made
    // of" is the question being asked.
    if (band.langs && band.langs.length) {
      out.push(`<text class="bandlang" x="${26 + band.w - 18}" y="${band.y + 42}">`
        + esc(band.langs.join("  \u00b7  ")) + `</text>`);
    }
  });
  columns.forEach(c => out.push(
    `<text class="col" x="${c.x}" y="${c.y}">${esc(c.label)} ${c.count}</text>`));
  // ── the wires ──────────────────────────────────────────────────────────────
  // Three things this used to get wrong, all of them noise rather than error.
  //
  // SELF-LOOPS. A symbol whose status is carried on an edge to ITSELF - which is how
  // unresolved and unused are recorded - was drawn as a path from its own right edge back
  // to its own left edge: a curve looping backwards through the card, meaning nothing. On
  // one project 78% of every line on the canvas was one of these. The status is already
  // in the card's colour and fill; the line said it a second time, illegibly.
  //
  // DIRECTION. A seam has a direction - the call reaches the route, not the other way -
  // and nothing on the canvas showed it. Every line now ends in an arrowhead at the
  // TARGET, and a reciprocal pair collapses into ONE line with a head at each end instead
  // of two curves lying on top of each other.
  //
  // ISOLATION. When a reader lights one node and asks to see only that, the answer should
  // be as plain as possible: the curve becomes a right-angled run, the stroke thickens,
  // and the arrow grows. A curve is atmosphere for a whole canvas; a schematic is what
  // you want when you are following one path.
  const seen = new Set();
  const reciprocal = new Set();
  p.edges.forEach(e => {
    if (e.source === e.target) return;
    seen.add(e.source + "\u0000" + e.target);
  });
  p.edges.forEach(e => {
    if (e.source !== e.target && seen.has(e.target + "\u0000" + e.source)) {
      reciprocal.add([e.source, e.target].sort().join("\u0000"));
    }
  });
  const drawnEdge = new Set();
  // One wire per pair of CARDS, not per edge. Two aggregate cards with 8,591 edges
  // between them drew 8,591 identical paths, each its own opacity group and marker -
  // that was 85% of the markup on the biggest page, and it was one line. The merged
  // wire is lit if any edge under it is, faded only if all of them are, and thickens
  // with the log of how many it stands for.
  const wires = new Map();
  p.edges.forEach(e => {
    // A status carried on a loop to itself is not a connection. See above.
    if (e.source === e.target) return;
    const a = pos.get(e.source), b = pos.get(e.target);
    if (!a || !b) return;
    // Both ends parked on the same aggregate card would draw a line inside one rectangle.
    if (a.agg && b.agg && a.x === b.x && a.y === b.y) return;
    const pairKey = [e.source, e.target].sort().join("\u0000");
    const twoWay = reciprocal.has(pairKey);
    if (twoWay) {
      if (drawnEdge.has(pairKey)) return;   // one line for the pair, two arrowheads
      drawnEdge.add(pairKey);
    }
    const ends = [byId.get(e.source), byId.get(e.target)];
    const dim = (chain && !(chain.has(e.source) && chain.has(e.target)))
      || (fading && query && !ends.every(n => n && hit(n)));
    // Lit: this edge is part of the path the reader asked to see on its own.
    const onChain = !!chain && chain.has(e.source) && chain.has(e.target);
    const st = e.status || "dim";
    const key = [a.x, a.y, b.x, b.y, st, twoWay ? 1 : 0].join("\u0000");
    const w = wires.get(key);
    if (w) { w.n += 1; w.dim = w.dim && dim; w.onChain = w.onChain || onChain; return; }
    wires.set(key, {a, b, st, twoWay, dim, onChain, n: 1});
  });
  wires.forEach(w => {
    const colour = S[w.st] || "var(--dim)";
    const head = `url(#ar-${w.st}${w.onChain ? "-lit" : ""})`;
    const tail = w.twoWay ? ` marker-start="url(#tl-${w.st}${w.onChain ? "-lit" : ""})"` : "";
    const thick = w.n > 1 && !w.onChain ? ` style="stroke-width:${(1.1 + Math.log10(w.n)).toFixed(1)}"` : "";
    out.push(`<path class="ed${w.dim ? " faded" : ""}${w.onChain ? " lit" : ""}"${thick}
      stroke="${colour}" marker-end="${head}"${tail}${w.n > 1 ? ` data-n="${w.n}"` : ""}
      d="${wirePath(w.a.x + (w.a.w || CARD_W), w.a.y + (w.a.h || CARD_H) / 2,
                    w.b.x, w.b.y + (w.b.h || CARD_H) / 2, w.onChain)}"/>`);
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
  // The aggregate cards first, behind the individual ones: a kind with more members than
  // fit is a single card carrying its count, which is the fact that matters at this zoom.
  // Lane headings inside the store band: the store's name, and whether this scan holds
  // anything to check it against. The badge is the one thing no other band needs - every
  // other band's evidence is in the repository by definition, and a data store's may live
  // in a dashboard. A reader who sees "no schema" knows a grey card means unknowable
  // rather than dead, before looking at a single card.
  (lanes || []).forEach(L => {
    out.push(`<g class="lane">
      <text class="lanename" x="${L.x}" y="${L.y}">${esc(L.name)}</text>
      ${L.badge ? `<text class="lanebadge ${L.oracle ? "has" : "none"}"
            x="${L.x + 12 + L.name.length * 8.6}" y="${L.y}">${esc(L.badge)}</text>` : ""}
    </g>`);
  });

  (aggregates || []).forEach(g => {
    if (g.more) {
      // The window of an opened kind: "501-1,000 of 2,164 - tap for the next 500". The
      // count is every card NOT in this window, before it as well as after, because that is
      // what is parked here; on the last window the tap wraps back to the first.
      const to = Math.min(g.from + EXPAND_PAGE, g.total), last = to >= g.total;
      const tap = last ? "tap for the first" : "tap for the next";
      out.push(`<g class="nd agg more" data-kind="${esc(g.kind)}" data-more="1">
        <rect x="${g.x}" y="${g.y}" width="${g.w}" height="${g.h}" rx="${NODE_R}"
              fill="var(--panel)" stroke="var(--ink)" stroke-width="2" stroke-dasharray="6 4"/>
        <title>${esc(g.count.toLocaleString())} not in this window — ${tap} ${EXPAND_PAGE}</title>
        <text class="big" x="${g.x + 14}" y="${g.y + 27}">+${g.count.toLocaleString()}</text>
        <text class="sub" x="${g.x + 14}" y="${g.y + 45}">${(g.from + 1).toLocaleString()}–${
          to.toLocaleString()} of ${g.total.toLocaleString()} · ${last ? "tap for first" : "tap for next"}</text>
      </g>`);
      return;
    }
    out.push(`<g class="nd agg st-${esc(g.status)}" data-kind="${esc(g.kind)}">
      <rect x="${g.x}" y="${g.y}" width="${g.w}" height="${g.h}" rx="${NODE_R}"
            fill="${F[g.status] || "var(--panel)"}" stroke="${S[g.status] || "var(--dim)"}"
            stroke-width="2"/>
      <title>${esc(g.count.toLocaleString())} ${esc(g.label.toLowerCase())} — tap to open</title>
      <text class="big" x="${g.x + 14}" y="${g.y + 27}">${g.count.toLocaleString()}</text>
      <text class="sub" x="${g.x + 14}" y="${g.y + 45}">${esc(g.label.toLowerCase())}${
        g.open ? " · " + g.open.toLocaleString() + " to look at" : ""}</text>
    </g>`);
  });

  drawnNodes.forEach(n => {
    const q = pos.get(n.id); if (!q) return;
    // A node parked on its kind's aggregate card is represented by that card, not by a
    // sliver hidden underneath it.
    if (q.agg) return;
    const ch = CHANGED[n.id];
    const stroke = ch ? CH[ch] : (S[n.status] || "var(--dim)");
    const alone = n.status === "unresolved" || n.status === "unused";
    const shown = (!fading || hit(n)) && (!chain || chain.has(n.id));
    // Two lines: what it is called, and what it IS. One truncated line was the whole
    // reason nothing on this canvas could be read.
    const label = fit(n.label, 24);
    // A module's second line names its LANGUAGE, because that is the thing a reader is
    // actually scanning for on a polyglot map - "javascript file" under a card called
    // totals.ts was wrong on its face, and wrong in the one way this map exists to fix.
    const sub = fit(
      n.kind === "module" && n.lang ? n.lang.toLowerCase() + " file"
        : (KIND_WORD[n.kind] || n.kind.replace(/_/g, " ")), 26);
    // On an isolated path a FILE is drawn as a pill rather than a card. Reading a chain
    // is reading a sequence of different kinds of thing, and a column of identical
    // rectangles makes every hop look alike - the shape should carry some of the meaning
    // before the label is even read. A true circle cannot hold "live.js / javascript
    // file", so the corner radius goes to half the height and the text stays legible.
    const pill = isolate && n.kind === "module";
    out.push(`<g class="nd${shown ? "" : " faded"}${n.id === lit ? " lit" : ""}" data-id="${n.id}">
      <rect x="${q.x}" y="${q.y}" width="${q.w}" height="${q.h}" rx="${pill ? q.h / 2 : NODE_R}"
            fill="${alone ? (F[n.status] || "var(--panel)") : "var(--panel)"}"
            stroke="${stroke}" stroke-width="${ch ? 3 : 1.5}"/>
      <title>${esc(n.label)}${n.file ? "\n" + esc(n.file) + (n.line ? ":" + n.line : "") : ""}</title>
      <text x="${q.x + (pill ? 22 : 12)}" y="${q.y + 20}">${esc(label)}</text>
      <text class="sub" x="${q.x + (pill ? 22 : 12)}" y="${q.y + 36}">${esc(sub)}</text></g>`);
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
  // Labels are dropped when they would be unreadable, not when they are merely small: at
  // 0.34 a phone opening a 400-node page (which fits at about 0.18) saw a map with no
  // names at all and no obvious way to get them.
  // Written only on change: toggling a class on the <svg> - even to the value it already
  // has - is a style recalc over every element under it.
  const nolabels = view.k < 0.24;
  if (svg.classList.contains("nolabels") !== nolabels) svg.classList.toggle("nolabels", nolabels);
}

// Delegated, not per-node: draw() replaces svg.innerHTML, and panning redraws on every
// mousemove, so a handler bound to a node is destroyed between mousedown and mouseup and
// the click never lands.
// An aggregate card opens its kind out into individual cards, and opening a second one
// closes the first - two expanded kinds on one canvas is the wall this replaced.
svg.addEventListener("click", e => {
  const agg = e.target.closest && e.target.closest(".nd.agg");
  if (agg) {
    e.stopPropagation();
    if (agg.dataset.more) {
      // Next window of the open kind; past the end, wrap to the first.
      expandedFrom += EXPAND_PAGE;
      const total = (currentPage().nodes || []).filter(n => n.kind === agg.dataset.kind).length;
      if (expandedFrom >= total) expandedFrom = 0;
    } else {
      expandedKind = expandedKind === agg.dataset.kind ? null : agg.dataset.kind;
      expandedFrom = 0;
    }
    _layout.key = null;
    draw();
    return;
  }
}, true);
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
  const p = currentPage();
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
  // A module carries no snippet and no context - a file has no single line to quote - so
  // the button never appeared on exactly the hop a reader most wants to open. When the map
  // is served the whole file is one request away, so a file is enough.
  const code = n.context || n.snippet || (n.file && location.protocol !== "file:");
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
  const mark = node => {
    if (node.file === file && node.line && node.status !== "connected") {
      marks.set(node.line, node.status);
    }
  };
  // The search index is one row per symbol in the whole scan; until a reader has typed
  // something it is not loaded, and the loaded pages are what there is.
  if (_index) {
    const fi = MAPDATA.files.indexOf(file);
    const S_ = MAPDATA.statuses, connected = S_.indexOf("connected");
    if (fi >= 0) {
      for (let i = 0; i < _index.n; i++) {
        if (_index.file[i] === fi && _index.line[i] && _index.status[i] !== connected) {
          marks.set(_index.line[i], S_[_index.status[i]]);
        }
      }
    }
  } else PAGES.forEach(p => (p.nodes || []).forEach(mark));
  return marks;
}

// Marking the line was not enough: on a 180-character line of minified-looking template
// the reader still has to hunt for the thing the row was about. The label is marked inside
// the line, so the answer is the highlighted run rather than "somewhere on this row".
function markLabel(raw, label) {
  if (!label) return highlight(raw);
  const at = raw.indexOf(label);
  if (at < 0) return highlight(raw);
  return highlight(raw.slice(0, at)) +
    '<mark class="hit">' + highlight(label) + '</mark>' +
    highlight(raw.slice(at + label.length));
}

// The block a line belongs to, found by indentation: the line itself, everything nested
// under it, and the closing bracket that ends it. Marking one line answers "where", and a
// reader opening code wants "what" - the function, the rule, the handler.
function blockRange(lines, line) {
  const i = line - 1;
  if (i < 0 || i >= lines.length) return null;
  const indent = t => { const at = t.search(/\S/); return at < 0 ? -1 : at; };
  const base = indent(lines[i]);
  if (base < 0) return null;
  let end = i;
  for (let j = i + 1; j < lines.length; j++) {
    if (!lines[j].trim()) continue;
    if (indent(lines[j]) <= base) break;
    end = j;
  }
  // The line that closes it sits back at the opening indent - `}`, `)`, `</div>` - and
  // belongs to the block even though it is not nested inside it.
  for (let j = end + 1; j < lines.length; j++) {
    const t = lines[j].trim();
    if (!t) continue;
    if (indent(lines[j]) <= base && /^[)}\]>;,]/.test(t)) end = j;
    break;
  }
  return end > i ? [i, end] : null;
}

function renderSource(file, text, line, title, label) {
  const marks = findingsIn(file);
  const lines = text.split("\n");
  const width = String(lines.length).length;
  const block = line ? blockRange(lines, line) : null;
  const body = lines.map((raw, i) => {
    const number = i + 1;
    const mark = marks.get(number);
    const inBlock = block && i > block[0] && i <= block[1];
    const cls = ["ln", number === line ? "here" : "", inBlock ? "inblock" : "",
                 mark ? "mark " + mark : ""].join(" ");
    return `<div class="${cls}" id="L${number}"><span class="num">${
      String(number).padStart(width, " ")}</span><span class="src">${
      number === line ? markLabel(raw, label) : highlight(raw)}</span></div>`;
  }).join("");
  document.getElementById("codetitle").textContent = title;
  const holder = document.getElementById("codebody");
  holder.innerHTML = `<div class="listing">${body}</div>`;
  const target = document.getElementById("L" + line);
  if (target) target.scrollIntoView({block: "center"});
  const other = marks.size - (marks.has(line) ? 1 : 0);
  const bits = [`${lines.length} lines`];
  if (block) bits.push(`block ${block[0] + 1}\u2013${block[1] + 1} highlighted`);
  if (other) bits.push(`${other} other finding${other === 1 ? "" : "s"} in this file, marked in the gutter`);
  document.getElementById("codenote").textContent = bits.join(" \u00b7 ");
}

async function showCode(id) {
  const n = byId.get(id); if (!n) return;
  await ensureDetail(pageIndexOf(id));
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
    renderSource(n.file, text, n.line, title, n.label);
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
  const at = pageIndexOf(id);
  if (!PAGES[at].detailed) {
    // The note and the code behind each hop are in the detail chunk; open it, then show.
    ensureDetail(at).then(() => show(id), reportFailure);
    return;
  }
  const ch = CHANGED[id];
  const {inbound, outbound} = routes(id);
  const take = deduper();
  const path = take(inbound), reaches = take(outbound);
  dbody.innerHTML = `<h2>${esc(n.label)}</h2>
    <div class="acts">
      <button id="iso" type="button">${isolate ? "Show the whole page" : "Show only this chain"}</button>
      ${n.status === "unresolved" || n.status === "unused"
        ? `<button id="wrong" type="button" class="wrongbtn">This is wrong</button>` : ""}
    </div>
    <div class="whybox" id="whybox" hidden>
      <div class="lbl">Why is it wrong? One tap copies the command that records it.</div>
      ${Object.entries(WHY).map(([key, help]) =>
        `<button type="button" class="whyopt" data-why="${esc(key)}"
                 data-id="${esc(id)}"><b>${esc(key)}</b><span>${esc(help)}</span></button>`
      ).join("")}
    </div>
    <div class="row"><span class="badge ${esc(n.status)}">${esc(n.status)}</span>
      ${esc(n.kind)}${ch ? " · " + esc(ch) : ""}</div>
    ${n.file ? `<div class="row">${loc(n.file, n.line)}</div>` : ""}
    ${n.note ? `<div class="note">${esc(n.note)}</div>` : ""}
    ${why(n.kind, n.status)}
    ${path.length ? `<div class="lbl">Path — browser to backend · <b>${path.length}</b> hop${
      path.length === 1 ? "" : "s"}</div>` : ""}
    ${path.map((step, i) => hop(step, id, i + 1, path.length)).join("")}
    ${reaches.length ? `<div class="lbl">Reaches — <b>${reaches.length}</b> from here</div>` +
      reaches.map((step, i) => hop(step, id, i + 1, reaches.length)).join("") : ""}`;
  sheet.hidden = false;
  document.getElementById("iso").onclick = () => {
    isolate = !isolate; view = {x:0, y:0, k:1}; draw(); show(id);
  };
  // A static file cannot write to disk, so it does the honest thing: it hands over the
  // exact command that does. One tap, no typing, and the reader can see what it will do
  // before running it - which is the same contract as the pre-filled issue link.
  const wrongBtn = document.getElementById("wrong");
  if (wrongBtn) {
    const box = document.getElementById("whybox");
    wrongBtn.onclick = () => { box.hidden = !box.hidden; };
    box.querySelectorAll(".whyopt").forEach(b => {
      b.onclick = () => {
        const cmd = `seamcheck triage '${b.dataset.id}' --wrong ${b.dataset.why}`;
        navigator.clipboard?.writeText(cmd);
        b.classList.add("copied");
        b.querySelector("span").textContent = "copied - run it in the project";
      };
    });
  }
}
document.getElementById("dx").onclick = () => { lit = null; isolate = false; closeSheet(); draw(); };

// Pointer events, not mouse events: one code path covers a mouse, a finger and a pen.
// Listening for `mousedown` alone left a phone with no pan and no zoom at all, and the
// pointer is never captured - capture would retarget the click away from the node.
//
// HOW A GESTURE STAYS CHEAP. The committed view lives in the `transform` attribute of
// #vp, and writing that attribute makes the browser recompute style, re-hit-test, repaint
// and re-rasterise every one of the ~12,000 elements on a big page - about 50ms a frame
// on a Retina screen, which is the lag a reader feels as "the whole map redraws when I
// move it". So a gesture in progress never touches #vp. It writes a CSS transform on the
// <svg> element itself, which the compositor applies to the already-rasterised layer
// without waking the main thread: a pan is a texture move, a zoom scales the texture
// (slightly soft until the fingers lift). The gesture is folded into #vp exactly once,
// when it ends - one repaint per gesture instead of one per frame.
const ptrs = new Map();
let drag = null, moved = false, pinch = null;

// The gesture in progress, as a transform layered ON TOP of the committed view:
// screen = translate(dx, dy) · scale(s) · committed. Identity when nothing is happening.
const gest = {dx: 0, dy: 0, s: 1, active: false, timer: null};
let box = null;   // the svg's client box, read once per gesture - not once per move

function gestureStart() {
  if (!gest.active) { gest.active = true; box = svg.getBoundingClientRect(); svg.classList.add("gesture"); }
}
function gestureApply() {
  cvlayer.style.transform = `translate(${gest.dx}px, ${gest.dy}px) scale(${gest.s})`;
}
// Fold the gesture into the committed view and let the page repaint once, crisp.
function gestureEnd() {
  if (!gest.active) return;
  clearTimeout(gest.timer); gest.timer = null;
  view.k = view.k * gest.s;
  view.x = view.x * gest.s + gest.dx;
  view.y = view.y * gest.s + gest.dy;
  gest.dx = gest.dy = 0; gest.s = 1; gest.active = false;
  cvlayer.style.transform = "";
  svg.classList.remove("gesture");
  applyView();
}
function panBy(dx, dy) {
  gestureStart();
  gest.dx += dx; gest.dy += dy;
  gestureApply();
}
// Zoom by `f` about a screen point, so the thing under the pointer stays under the
// pointer. Zooming about the canvas origin, which is what a bare change of scale does,
// sent the corner a reader was looking at off the screen on every wheel tick.
function zoomBy(f, cx, cy) {
  gestureStart();
  const want = view.k * gest.s * f;
  const k = Math.min(3, Math.max(ZOOM_FLOOR, want));
  f = k / (view.k * gest.s);
  if (f === 1) return;
  const px = cx - box.left, py = cy - box.top;
  gest.dx = px - (px - gest.dx) * f;
  gest.dy = py - (py - gest.dy) * f;
  gest.s *= f;
  gestureApply();
}
// A wheel has no "up" event; the gesture ends when the ticks stop.
function settleSoon() {
  clearTimeout(gest.timer);
  gest.timer = setTimeout(gestureEnd, 140);
}
// draw() may fit a big page at 0.06 to get it all on screen, and the old floor of 0.2
// meant the first zoom-OUT from there jumped the map larger. The floor is the smaller
// of the two, so from a fitted page the wheel does what it says.
const ZOOM_FLOOR = 0.05;
// The corner buttons zoom about the middle of the screen, which is where the eye is.
const zoomStep = f => {
  const r = svg.getBoundingClientRect();
  zoomBy(f, r.left + r.width / 2, r.top + r.height / 2);
  settleSoon();
};

let lastTap = 0;
svg.addEventListener("pointerdown", e => {
  // Double-tap zooms. A phone has no wheel, and reaching the corner buttons mid-read
  // costs a thumb-shift; this is the gesture people already try.
  clearTimeout(gest.timer); gest.timer = null;
  const now = Date.now();
  if (ptrs.size === 0 && now - lastTap < 320) { zoomBy(1.6, e.clientX, e.clientY); gestureEnd(); lastTap = 0; }
  else lastTap = now;
  ptrs.set(e.pointerId, {x: e.clientX, y: e.clientY});
  if (ptrs.size === 2) {
    const [a, b] = [...ptrs.values()];
    pinch = {d: Math.hypot(a.x - b.x, a.y - b.y), cx: (a.x + b.x) / 2, cy: (a.y + b.y) / 2};
    drag = null; moved = true;  // two fingers are a gesture, never a tap
    return;
  }
  drag = {x: e.clientX, y: e.clientY, sx: e.clientX, sy: e.clientY};
  moved = false;
});
window.addEventListener("pointermove", e => {
  if (!ptrs.has(e.pointerId)) return;
  ptrs.set(e.pointerId, {x: e.clientX, y: e.clientY});
  if (pinch && ptrs.size === 2) {
    const [a, b] = [...ptrs.values()];
    const d = Math.hypot(a.x - b.x, a.y - b.y);
    const cx = (a.x + b.x) / 2, cy = (a.y + b.y) / 2;
    // The fingers' midpoint moves as well as their spread: that movement is a pan.
    panBy(cx - pinch.cx, cy - pinch.cy);
    if (pinch.d > 0) zoomBy(d / pinch.d, cx, cy);
    pinch = {d, cx, cy};
    return;
  }
  if (!drag) return;
  // A few pixels between press and release is a tap, not a pan. A finger wobbles more
  // than a mouse, so the threshold is wider than a mouse alone would need.
  if (!moved && Math.abs(e.clientX - drag.sx) + Math.abs(e.clientY - drag.sy) < 6) return;
  if (!moved) { moved = true; svg.classList.add("drag"); }
  panBy(e.clientX - drag.x, e.clientY - drag.y);
  drag.x = e.clientX; drag.y = e.clientY;
});
const release = e => {
  ptrs.delete(e.pointerId);
  if (ptrs.size < 2) pinch = null;
  if (ptrs.size === 0) { drag = null; svg.classList.remove("drag"); gestureEnd(); }
};
window.addEventListener("pointerup", release);
window.addEventListener("pointercancel", release);

// WebKit's own pinch. `touch-action:none` stops Chrome and Firefox from zooming the
// document, and iOS Safari ignores both that and `user-scalable=no` - it fires these
// non-standard gesture events instead, and the only way to keep a two-finger pinch on the
// map from zooming the whole PAGE is to refuse them here. Scoped to the canvas on purpose:
// pinching the panel to read a listing is a reasonable thing to want, and still works.
["gesturestart", "gesturechange", "gestureend"].forEach(name => {
  svg.addEventListener(name, e => e.preventDefault());
});
// A double-tap on iOS zooms the document as well, and the canvas has its own meaning for
// it. Belt and braces with the pointerdown handler above, which cannot preventDefault a
// gesture the browser synthesises after the fact.
svg.addEventListener("dblclick", e => e.preventDefault());

svg.addEventListener("wheel", e => {
  e.preventDefault();
  // A Mac trackpad fires a stream of high-resolution wheel events, so a fixed 1.1x per
  // event flew from one end of the zoom range to the other on a single flick. macOS marks
  // a pinch as ctrlKey, which is the gesture that should zoom; a plain two-finger scroll
  // pans, as it does in every map on this platform.
  if (e.ctrlKey || e.metaKey) zoomBy(Math.exp(-e.deltaY * 0.01), e.clientX, e.clientY);
  else panBy(-e.deltaX, -e.deltaY);
  settleSoon();
}, {passive: false});

// A pinch needs two fingers and some dexterity; these need one thumb.
document.getElementById("zi").onclick = () => zoomStep(1.25);
document.getElementById("zo").onclick = () => zoomStep(1 / 1.25);
document.getElementById("zf").onclick = () => {
  // Back to "unset", which is the signal draw() reads to re-fit the page to the screen.
  view = {x:0, y:0, k:1}; draw();
};
// One redraw per pause in typing. draw() on every keystroke rebuilt the canvas six
// times for a six-letter word; the results list is cheap and still follows each key.
let qTimer = null;
document.getElementById("q").addEventListener("input", e => {
  query = e.target.value.trim().toLowerCase();
  showResults(query);
  clearTimeout(qTimer);
  qTimer = setTimeout(draw, 120);
});
document.getElementById("q").addEventListener("focus", () => showResults(query));
document.addEventListener("click", e => {
  if (!e.target.closest("#found") && e.target.id !== "q") hideResults();
});

// Results as a list under the box, because a fade tells you a match exists somewhere on
// this page and nothing about the other eighteen pages.
function resultsBox() {
  let box = document.getElementById("found");
  if (!box) {
    box = document.createElement("div");
    box.id = "found";
    box.className = "found";
    document.querySelector(".content").appendChild(box);
    box.addEventListener("click", event => {
      const row = event.target.closest("[data-jump]");
      if (!row) return;
      hideResults();
      jumpTo(row.dataset.jump, Number(row.dataset.page));
    });
  }
  return box;
}
function hideResults() { const box = document.getElementById("found"); if (box) box.hidden = true; }

function showResults(term) {
  if (!term || term.length < 2) { hideResults(); return; }
  const box = resultsBox();
  box.hidden = false;
  if (!_index) {
    searchIndex(() => { if (query === term) showResults(term); });
    box.innerHTML = `<div class="gap">Opening the index…</div>`;
    return;
  }
  const found = searchEverywhere(term);
  if (!found.length) {
    box.innerHTML = `<div class="gap">Nothing in this scan is called “${esc(term)}”.</div>`;
    return;
  }
  box.innerHTML = found.map(r => `<div class="fr" data-jump="${esc(r.id)}" data-page="${r.page}">
      <span class="badge ${esc(r.status)}">${esc(r.status)}</span>
      <div class="t">${esc(r.label)}</div>
      <div class="w">${esc(r.kind)}${r.file ? " · " + esc(r.file) : ""}${
        PAGES[r.page] ? " · " + esc(PAGES[r.page].title || PAGES[r.page].page) : ""}</div>
    </div>`).join("") +
    `<div class="gloss">${found.length === 60 ? "First 60 matches" : n(found.length) +
      " match" + (found.length === 1 ? "" : "es")} across the whole scan. Tap one to open it.</div>`;
}
// --- the commit picker -------------------------------------------------------------
const picker = document.getElementById("cm"), note = document.getElementById("cmnote");
const gone = document.getElementById("gone");

// "2026-08-30T14:18:06+02:00" -> "2026-08-30 14:18". The date alone cannot separate two
// commits made the same afternoon, which is most of them.
const when = iso => String(iso || "").replace("T", " ").slice(0, 16);

function countsFor(changed) {
  const n = {added: 0, removed: 0, status: 0};
  Object.values(changed).forEach(kind => { n[kind] = (n[kind] || 0) + 1; });
  return n;
}

function fillPicker() {
  const opts = [`<option value="">Everything in this scan</option>`];
  // A commit that touched only docs or config changed no scanned symbol, and picking it
  // draws an empty canvas that reads as a broken filter. The count belongs in the list, so
  // the choice is informed before it is made rather than explained afterwards.
  COMMITS.forEach((c, i) => {
    const n = c.counts || countsFor(c.changed || {});
    const total = n.added + n.removed + n.status;
    const tail = !c.baseline ? " · earliest scan" : total ? ` · ${total} changed` : " · no change";
    opts.push(
      `<option value="${i}">${c.head ? "HEAD · " : ""}${esc(c.sha.slice(0, 8))} · ` +
      `${esc(when(c.date))}${esc(tail)} · ${esc(c.subject)}</option>`);
  });
  picker.innerHTML = opts.join("");
  if (!COMMITS.length) {
    picker.disabled = true;
    note.textContent = "Only this commit has been scanned. Run --backfill to build history.";
  }
}

function selectCommit(index) {
  const c = COMMITS[index];
  if (c && !c.changed) {
    // What each commit changed, by id, is sized by the history and lives in a chunk
    // until a commit is picked.
    readChunk("commits").then(list => {
      COMMITS.forEach((k, i) => {
        const got = (list || [])[i] || {};
        k.changed = got.changed || {}; k.changes = got.changes || [];
      });
      if (String(picker.value) === String(index)) selectCommit(index);
    }, reportFailure);
    return;
  }
  if (!c) {
    CHANGED = MAPDATA.changed; only = false;
    note.textContent = ""; gone.innerHTML = "";
  } else {
    CHANGED = c.changed; only = true;
    const n = c.counts || countsFor(c.changed);
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
  fillPages(only ? (c.perPage || PAGES.map(p => lensed(p).filter(n => CHANGED[n.id]).length)) : null);
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
  // The line number carried as its own element: it is the coordinate someone is about to
  // type into an editor, and it read as the same grey as the path around it.
  const text = esc(file) + (line ? `:<span class="ln">${line}</span>` : "");
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
// Five places, and each is a different KIND of question. Frontend↔Backend, DOM Wiring,
// Backend Internals, CSS & Tokens and Integrations used to be menu points of their own -
// but every one of them was the map filtered to a set of kinds, which is exactly what the
// Layer control does. Five menu points and one dropdown were the same control built twice,
// and a reader landing on "Backend" could not tell why "Map" was also there.
//
// They are Layer values now. The menu answers "what am I doing", the Layer answers "which
// part of it", and neither pretends to be the other.
const MENU = ["overview", "map", "page", "findings", "files", "changes", "report"];
const SECTION_BY_KEY = Object.fromEntries((D.sections || []).map(sec => [sec.key, sec]));

function menuCount(key) {
  if (key === "page") return OBS_PAGES.length || null;
  if (key === "files") return FILES.length;
  const sec = SECTION_BY_KEY[key];
  if (!sec || sec.unavailable) return null;
  return sec.total ?? sec.rows.length;
}

const TITLES = {overview: "Overview", map: "Map", files: "Files",
                page: "The page a browser saw", report: "Send a report"};
const VIEWS = MENU
  .filter(key => key !== "page" || OBS_PAGES.length)
  .filter(key => key !== "report" || (typeof SHARE !== "undefined" && SHARE.symbols))
  .map(key => ({
  key,
  title: TITLES[key] || (SECTION_BY_KEY[key] || {}).title || key,
  count: key === "overview" || key === "map" ? null : menuCount(key),
}));

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
  // Every region, not two of them. Summing backend and frontend alone made the headline
  // read "0% of 0 symbols · nothing unresolved and nothing unused" directly above a
  // backlog listing four real findings, on any project whose symbols live in the store or
  // off-browser regions. The headline is the first thing read and it was the wrongest.
  const all = {};
  ["connected", "unresolved", "unused", "uncertain"].forEach(k => {
    all[k] = (D.backend[k] || 0) + (D.frontend[k] || 0)
           + ((D.store || {})[k] || 0) + ((D.offscreen || {})[k] || 0);
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
  // The same four regions the canvas draws. Two rows covered 14 kinds and silently
  // omitted every other symbol in the scan - a data-layer project opened on "0% of 0
  // symbols" with four findings listed directly underneath. A row with nothing in it is
  // dropped rather than shown as a zero, so an ordinary Django app still sees two.
  const rows = [["Frontend", D.frontend], ["Backend", D.backend],
                ["The store", D.store], ["No browser", D.offscreen]]
    .map(([name, c]) => ({
      name, total: sum(c || {}), finds: ((c || {}).unresolved || 0) + ((c || {}).unused || 0),
    }))
    .filter(r => r.total)
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
    <details class="explain"><summary>How to read these bars</summary>
      <p class="gloss">Both bars share one scale, so the sizes are comparable; the darker
        inset is that side's own findings. The rate is findings over that side's own
        symbols — a share of the whole project would only tell you which half is
        bigger.</p></details>`;
}

// The numbers, and nothing else on first sight. This screen had four headed sections, a
// four-term glossary, a two-sentence caveat and a numbered guide - roughly six hundred
// words in front of the counts a reader opened the page to read. Prose that answers a
// question nobody asked yet is not documentation, it is a wall; every word of it is still
// here, one tap away, under the terms it explains.
// The report a person can send back, shown in full before anything leaves. Nothing here
// contacts anything: the page has no network at all. It renders what `seamcheck share`
// would produce, offers to copy it, and offers a pre-filled issue that submits nothing
// until the reader presses the button on GitHub's own page.
// The same markdown `seamcheck share` prints, built from the embedded payload so the two
// surfaces cannot drift into saying different things about the same scan.
function reportMarkdown() {
  const p = SHARE || {}, st = p.by_status || {}, t = p.triage || {};
  const total = Object.values(st).reduce((a, v) => a + v, 0) || 1;
  const tbl = (title, o) => {
    const e = Object.entries(o || {}).slice(0, 20);
    if (!e.length) return [];
    return [`**${title}**`, "", "| | count |", "|---|---:|",
            ...e.map(([k, v]) => `| \`${k}\` | ${v} |`), ""];
  };
  return [
    "### Seamcheck scan report", "",
    "_Shape of a scan only. No file paths, names, routes, snippets or repository identity._", "",
    `- seamcheck **${p.seamcheck}** · Python ${p.python} · ${p.platform}`,
    `- adapters: **${(p.adapters || []).map(a => a.name + " (" + a.confidence + ")").join(", ") || "none detected"}**`,
    `- graph: **${p.symbols} symbols**, ${p.edges} edges (${p.size})`, "",
    "**Result**", "", "| status | count | share |", "|---|---:|---:|",
    ...["connected", "unresolved", "unused", "uncertain"].map(k =>
      `| ${k} | ${st[k] || 0} | ${Math.floor((st[k] || 0) * 100 / total)}% |`), "",
    ...(t.marked ? tbl(`Findings a person marked (${t.marked})`, t.shapes) : []),
    ...tbl("Uncertain, by cause", p.uncertain_causes),
    ...tbl("Findings by kind and status", p.kind_status),
    "**Anything wrong here?** If a number looks false for your project - findings against "
    + "things that do exist, or nothing found where there is plainly something - that is "
    + "the useful part. Say which, in your own words.", "",
  ].join("\n");
}

function reportHtml() {
  const p = SHARE || {};
  const st = p.by_status || {};
  const t = p.triage || {};
  const rows = o => Object.entries(o || {}).slice(0, 16)
    .map(([k, v]) => `<tr><td class="k">${esc(k)}</td><td class="v">${n(v)}</td></tr>`).join("");
  return `<h2>Send a report</h2>
    <p class="gloss">Seamcheck learns most from the scans it gets <b>wrong</b>, and those
      are private repositories nobody can send. This is the shape of your scan with none of
      your code in it — no file paths, no names, no routes, no snippets, no repository
      identity. Every value below is a number or a word seamcheck defines in its own
      source, which you can check by reading
      <code>seamcheck/share.py</code>.</p>
    <p class="gloss"><b>Nothing has been sent.</b> Seamcheck makes no network calls, and
      this page cannot either.</p>

    <h3 class="sec">What would be sent</h3>
    <div class="rep">
      <table class="reptab">
        <tr><td class="k">seamcheck</td><td class="v">${esc(p.seamcheck || "")}</td></tr>
        <tr><td class="k">adapters</td><td class="v">${esc((p.adapters || [])
          .map(a => a.name + " (" + a.confidence + ")").join(", ") || "none")}</td></tr>
        <tr><td class="k">symbols</td><td class="v">${n(p.symbols || 0)}</td></tr>
        ${["connected", "unresolved", "unused", "uncertain"]
          .map(k => `<tr><td class="k">${k}</td><td class="v">${n(st[k] || 0)}</td></tr>`).join("")}
      </table>
      ${t.marked ? `<h4 class="reph">Findings you marked wrong (${n(t.marked)})</h4>
        <table class="reptab">${rows(t.shapes)}</table>` : `<p class="gloss repnote">
        You have not marked any finding wrong yet. That is the part worth having — open a
        finding on the map, press <b>This is wrong</b>, and pick a reason. It records why,
        never what.</p>`}
      <h4 class="reph">Why things are uncertain</h4>
      <table class="reptab">${rows(p.uncertain_causes)}</table>
    </div>

    <h3 class="sec">How to send it</h3>
    <div class="acts repacts">
      <button type="button" id="repcopy">Copy the report</button>
      <a class="repbtn" id="repissue" target="_blank" rel="noreferrer noopener">Open a pre-filled issue</a>
    </div>
    <p class="gloss">The issue opens on GitHub with the report already in it and submits
      nothing until you press their button. Or paste it into an email — whatever you
      prefer.</p>
    <p class="gloss"><b>One thing worth saying plainly:</b> if this repository belongs to
      an employer or a client, sharing metrics about it is their decision rather than
      yours.</p>`;
}

function overviewHtml() {
  return `<h2>Overview</h2>

    ${heroHtml()}
    ${sidesHtml()}

    <h3 class="sec">Backlog by kind</h3>
    ${D.groups.length ? D.groups.map(g =>
      `<button type="button" class="row rowgo" data-kind="${esc(g[2])}">
       <span class="badge uncertain">${g[1]}</span>
       <div class="t">${esc(g[0])}</div><span class="go">Findings &rsaquo;</span></button>`).join("")
      : `<div class="gap">Nothing the scan is willing to claim.</div>`}

    <details class="explain">
      <summary>What the four words mean, and what this cannot see</summary>
      <div class="skey">${statusKey()}</div>
      <div class="caveat"><b>Two things to know before you read a number.</b>
        Seamcheck reads source; it never runs your code, so every row is evidence rather
        than a verdict. And ${esc(BLIND_SPOTS)}</div>
    </details>

    <details class="explain">
      <summary>Where to look next</summary>
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
    </details>

    <div class="colophon">
      <p>Built by
        <a href="https://github.com/dardameiz/seamcheck" target="_blank" rel="noreferrer">Seamcheck</a>
        — free, MIT. Got a finding wrong?
        <a href="https://github.com/dardameiz/seamcheck/issues" target="_blank" rel="noreferrer">Say so</a>.</p>
      <a href="https://github.com/sponsors/dardameiz" class="sponsor"
         target="_blank" rel="noreferrer">\u2665 Sponsor Seamcheck</a>
    </div>`;
}

// A folder tree, the shape the repository actually has. The map is rooted at pages,
// which is how a browser reaches code and not how anyone edits it: from the map you
// cannot tell whether a file's other twenty functions were ever considered.
let fileQuery = "";
// Two different questions share this view. "Scanned" answers what seamcheck read and how
// much of each file it could model. "All files" answers what is actually in the directory
// - which is the question anyone has about a repository they did not write, and which a
// list of only the 2,140 files that produced symbols cannot be asked at all.
let fileTab = "scanned", fileState = "", INVENTORY = null, invError = "";

// What each word in the All-files list is allowed to mean. Nothing here says "delete it".
const STATES = {
  read: ["read", "sig",
    "The scan parsed this and it produced symbols."],
  silent: ["nothing found", "uncertain",
    "A type the scan reads, which yielded no symbol. Usually there is nothing to " +
    "model - not evidence of anything."],
  named: ["named", "connected",
    "Not parsed, but its filename appears in the project's text, so something " +
    "references it."],
  maybe: ["maybe", "uncertain",
    "Not parsed, and its name never appears - but its folder is referenced, so a path " +
    "built at runtime could reach it. The scan cannot tell."],
  orphan: ["unreferenced", "unused",
    "Not parsed, its name appears nowhere in the project's text, and nothing " +
    "references its folder either."],
  derived: ["derived", "uncertain",
    "Built from something else in the tree (.gz, .br, .map, .pyc, .log). Present on " +
    "disk, not a source of truth."],
};

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
  return `<h2>Files</h2>${fileTabsHtml()}<p class="blurb"><b>Click a file to draw its symbols on the
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

function fileTabsHtml() {
  const all = INVENTORY ? INVENTORY.files.toLocaleString() : "\u2026";
  return `<div class="tabs">
    <button type="button" class="tab${fileTab === "scanned" ? " on" : ""}" data-tab="scanned">
      Scanned <b>${FILES.length.toLocaleString()}</b></button>
    <button type="button" class="tab${fileTab === "all" ? " on" : ""}" data-tab="all">
      All files <b>${all}</b></button>
  </div>`;
}

// The whole directory, with what can honestly be said about each file. The states are
// evidence, graded: "its name appears" is stronger than "its folder appears", and both
// are stronger than "nothing anywhere mentions it".
function inventoryHtml() {
  if (invError) {
    return `<h2>Files</h2>${fileTabsHtml()}<div class="gap">${esc(invError)}</div>`;
  }
  if (!INVENTORY) {
    return `<h2>Files</h2>${fileTabsHtml()}<div class="gap">Reading the directory\u2026
      This walks every file and reads the text of each one, so it takes a few seconds the
      first time.</div>`;
  }
  const t = INVENTORY.totals || {};
  const needle = fileQuery.toLowerCase();
  let listed = 0, folders = "";
  INVENTORY.folders.forEach(f => {
    const rows = f.files.filter(([name, state]) =>
      (!fileState || state === fileState) &&
      (!needle || (f.dir + "/" + name).toLowerCase().includes(needle)));
    if (!rows.length) return;
    listed += rows.length;
    // A folder of 4,000 generated images should not paint 4,000 rows before a reader can
    // decide they do not care about it. It says how many it holds and opens on request.
    const body = rows.slice(0, 400).map(([name, state, size, tracked]) => {
      const [word, tone, why] = STATES[state] || [state, "uncertain", ""];
      return `<div class="fl inv" title="${esc(why)}">
        <span class="fn">${esc(name)}</span>
        <span class="badge ${tone}">${esc(word)}</span>
        ${tracked ? "" : `<span class="badge uncertain" title="Present on disk, not tracked by git">untracked</span>`}
        <span class="covn">${size > 1024 ? Math.round(size / 1024).toLocaleString() + " KB" : size + " B"}</span>
      </div>`;
    }).join("") + (rows.length > 400
      ? `<div class="gap">and ${(rows.length - 400).toLocaleString()} more in this folder</div>` : "");
    folders += `<details${needle || fileState ? " open" : ""}>
      <summary>${esc(f.dir || "(repository root)")}
        <span class="covn">${rows.length.toLocaleString()}</span></summary>${body}</details>`;
  });
  const chip = (key, label, n) => n
    ? `<button type="button" class="tab${fileState === key ? " on" : ""}" data-state="${key}">
       ${label} <b>${n.toLocaleString()}</b></button>` : "";
  return `<h2>Files</h2>${fileTabsHtml()}
    <p class="blurb">Every file in the directory, including the ones no extractor reads.
      <b>Two questions, both answered from evidence and neither guessed at.</b>
      Does git track it — a file present on disk and untracked is generated, local or
      forgotten, and this tree holds
      ${(t.untracked || 0).toLocaleString()} of them against
      ${(INVENTORY.files - (t.untracked || 0)).toLocaleString()} tracked.
      And does anything name it — for a file the scan cannot parse, the honest evidence is
      whether its filename appears anywhere in the project's text.
      ${INVENTORY.git ? "" : "<br><br>This is not a git repository, so the tracked column is blank."}</p>
    <div class="tools"><input id="fq" type="search"
      placeholder="Filter ${listed.toLocaleString()} of ${INVENTORY.files.toLocaleString()} files"
      value="${esc(fileQuery)}"></div>
    <div class="tabs wrap">
      <button type="button" class="tab${fileState ? "" : " on"}" data-state="">Everything</button>
      ${chip("orphan", "unreferenced", t.orphan)}
      ${chip("maybe", "maybe", t.maybe)}
      ${chip("named", "named", t.named)}
      ${chip("read", "read", t.read)}
      ${chip("silent", "nothing found", t.silent)}
      ${chip("derived", "derived", t.derived)}
    </div>
    <details class="explain"><summary>What these six words mean</summary>
      <div class="doc"><ul>${Object.entries(STATES).map(([, [word, tone, why]]) =>
        `<li><span class="badge ${tone}">${word}</span> ${esc(why)}</li>`).join("")}</ul>
      <p><b>None of this says delete anything.</b> <code>unreferenced</code> means what it
      means everywhere else here: both ends are observable and nothing connects them. A
      name assembled at runtime is exactly the case the scan cannot see, which is why a
      file whose folder is referenced is reported as <code>maybe</code> instead.</p></div>
    </details>
    <div class="tree">${folders || `<div class="gap">No files match.</div>`}</div>`;
}

async function loadInventory() {
  if (INVENTORY || invError) return;
  if (location.protocol === "file:") {
    invError = "The directory listing is available when this map is served " +
      "(seamcheck map), not when it is opened as a file.";
    renderPanel();
    return;
  }
  try {
    const response = await fetch(location.pathname.replace(/\/$/, "") + "/inventory");
    if (!response.ok) throw new Error(String(response.status));
    INVENTORY = await response.json();
    if (INVENTORY.error) { invError = INVENTORY.error; INVENTORY = null; }
  } catch {
    invError = "Could not read the directory.";
  }
  renderPanel();
}

// ── What a browser actually saw ───────────────────────────────────────────
// The scan reads what the code DECLARES. This is the other half: `seamcheck observe`
// drives the real pages with Playwright and records where every element ended up. Drawn
// at true proportions and coloured by what the graph says about each one, it is the same
// page from the two sides at once - what a person sees, and what we see from the backend.
let observedAt = 0;

// An observed box carries an id and a class list; a graph symbol carries a label and a
// kind. They meet on the name.
// The recorder writes `cls` as an array; older files wrote a string. Both are read.
function classList(box) {
  const c = box.cls;
  if (Array.isArray(c)) return c.filter(Boolean);
  return String(c || "").split(/\s+/).filter(Boolean);
}

function symbolForBox(box) {
  if (box.id) {
    const byIdHit = OBS_INDEX.get("id:" + box.id);
    if (byIdHit) return byIdHit;
  }
  for (const cls of classList(box)) {
    const hit = OBS_INDEX.get("class:" + cls);
    if (hit) return hit;
  }
  return null;
}

let _obsIndex = null;
const OBS_INDEX = {
  get: key => {
    if (!_obsIndex) {
      // The index is a chunk, loaded on first use; until it is in, nothing matches and
      // the observed view is drawn again when it lands.
      const ix = searchIndex(() => { if (mode === "page") renderPanel(); });
      if (!ix) return undefined;
      _obsIndex = new Map();
      // An id matches an id and a class matches a class. Keying both against the same
      // label made every Tailwind utility on the page "match" a template element with the
      // same name, which is how the view reported 415 of 415 - a number that is a tell,
      // not a result. The symbol id already carries which one it is:
      //   dom_attr:class:main-push-area:template.html:1280
      const attr = MAPDATA.kinds.indexOf("dom_attr");
      for (let i = 0; i < ix.n; i++) {
        if (ix.kind[i] !== attr) continue;
        const row = ix.row(i);
        const sub = String(row.id).split(":")[1];
        if (sub !== "id" && sub !== "class") continue;
        const k = sub + ":" + row.label;
        // First wins, and a finding beats a clean one: if a name is declared in twelve
        // templates the interesting copy is the one with something wrong with it.
        const had = _obsIndex.get(k);
        if (!had || (had.status === "connected" && row.status !== "connected")) {
          _obsIndex.set(k, row);
        }
      }
    }
    return _obsIndex.get(key);
  },
};

function observedHtml() {
  if (!OBS_PAGES.length) {
    return `<h2>The page a browser saw</h2>
      <div class="gap">Nothing recorded yet. <code>seamcheck observe</code> drives your
      pages in a real browser and writes down where every element ended up.</div>`;
  }
  const o = OBS_PAGES[Math.min(observedAt, OBS_PAGES.length - 1)];
  const boxes = o.boxes || [];
  const w = Math.max(320, ...boxes.map(b => b.x + b.w));
  const h = Math.max(240, ...boxes.map(b => b.y + b.h));
  const tally = {connected: 0, unresolved: 0, unused: 0, uncertain: 0, unknown: 0};
  const drawn = boxes.map(b => {
    const sym = symbolForBox(b);
    const st = sym ? sym.status : "unknown";
    tally[st] = (tally[st] || 0) + 1;
    const name = b.id ? "#" + b.id : "." + (classList(b)[0] || "?");
    return `<rect class="ob ob-${esc(st)}" x="${b.x}" y="${b.y}" width="${b.w}"
      height="${b.h}" rx="3" data-sym="${sym ? esc(sym.id) : ""}"
      data-page="${sym ? sym.page : ""}"><title>${esc(name)} — ${esc(st)}</title></rect>`;
  }).join("");
  const known = boxes.length - tally.unknown;
  return `<h2>The page a browser saw</h2>
    <p class="blurb">Every element <code>seamcheck observe</code> found on the real page,
    at the size and place the browser put it — outlined by what the scan says about it.
    <b>${known} of ${boxes.length}</b> match something in the graph. Click one to open it
    on the map.${OBSERVED.current ? "" :
      ` <b>Recorded at ${esc((OBSERVED.at || "").slice(0, 8))}</b>, not at this commit —
        anything changed since is drawn from the older run.`}</p>
    <div class="tools">
      <select id="obpick">${OBS_PAGES.map((x, i) =>
        `<option value="${i}"${i === observedAt ? " selected" : ""}>${esc(x.page)}</option>`
      ).join("")}</select>
    </div>
    <div class="obkey">${["connected", "unresolved", "unused", "uncertain"]
      .filter(k => tally[k]).map(k =>
        `<span class="obk ob-${k}"><i></i>${k} ${tally[k]}</span>`).join("")}
      ${tally.unknown ? `<span class="obk ob-unknown"><i></i>not in the graph
        ${tally.unknown}</span>` : ""}</div>
    <div class="obwrap"><svg class="obshot" viewBox="0 0 ${w} ${h}"
      preserveAspectRatio="xMidYMin meet">${drawn}</svg></div>
    <p class="gloss">This is geometry, not a screenshot — the shape of the page as the
    browser laid it out. Anything grey is on screen and not in the graph, which is either a
    third-party widget or something the scan cannot see.</p>`;
}

function wireObserved() {
  const pick = document.getElementById("obpick");
  if (pick) pick.onchange = e => { observedAt = +e.target.value; renderPanel(); };
  panel.querySelectorAll(".ob[data-sym]").forEach(r => {
    if (!r.dataset.sym) return;
    r.style.cursor = "pointer";
    r.onclick = () => jumpTo(r.dataset.sym, +r.dataset.page);
  });
}

// Which page shows the most of one file. A file's symbols are spread across the pages
// that load it, and only one page can be drawn at a time.
function bestPageFor(path) {
  const at = (MAPDATA.filePage || {})[path];
  return typeof at === "number" ? at : current;
}

function renderPanel() {
  if (mode === "files") {
    const all = fileTab === "all";
    panel.innerHTML = all ? inventoryHtml() : treeHtml();
    panel.querySelectorAll(".tab[data-tab]").forEach(el => {
      el.onclick = () => {
        fileTab = el.dataset.tab; fileQuery = ""; fileState = "";
        renderPanel();
        if (fileTab === "all") loadInventory();
      };
    });
    panel.querySelectorAll(".tab[data-state]").forEach(el => {
      el.onclick = () => { fileState = el.dataset.state; renderPanel(); };
    });
    if (all) {
      const box = document.getElementById("fq");
      if (box) {
        box.oninput = e => {
          fileQuery = e.target.value; renderPanel();
          const again = document.getElementById("fq");
          again.focus(); again.setSelectionRange(again.value.length, again.value.length);
        };
      }
      return;
    }
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
  if (mode === "page") { panel.innerHTML = observedHtml(); wireObserved(); return; }
  if (mode === "overview") {
    panel.innerHTML = overviewHtml();
    // A count with no way in is decoration. Each backlog row opens Findings already
    // narrowed to that kind - which is the question the row raises.
    panel.querySelectorAll(".rowgo").forEach(el => {
      el.onclick = () => {
        ckind = el.dataset.kind; cstatus = ""; cq = ""; shown = ROWS_PER_PAGE;
        statusFilter.clear(); syncStatusKey();
        viewer.value = "findings"; switchTo("findings");
      };
    });
    return;
  }
  if (mode === "report") {
    panel.innerHTML = reportHtml();
    const text = reportMarkdown();
    const copy = document.getElementById("repcopy");
    copy.onclick = () => {
      navigator.clipboard?.writeText(text);
      copy.textContent = "Copied";
      setTimeout(() => { copy.textContent = "Copy the report"; }, 2000);
    };
    const issue = document.getElementById("repissue");
    // Truncated for the URL only; the copy button always carries the whole thing.
    const body = text.length > 5000
      ? text.slice(0, 5000) + "\n\n_(truncated - use Copy for the full report)_" : text;
    const st = (SHARE.by_status || {});
    issue.href = "https://github.com/dardameiz/seamcheck/issues/new?"
      + new URLSearchParams({
          title: "scan report: "
                 + ((SHARE.adapters || []).map(a => a.name).join("+") || "no adapter")
                 + ` - ${st.uncertain || 0} uncertain, ${st.unresolved || 0} unresolved`,
          body, labels: "scan-report",
        }).toString();
    return;
  }
  if (mode === "changes") { panel.innerHTML = changesHtml(); return; }
  if (mode === "map") { panel.innerHTML = mapListHtml(); return; }
  const sec = D.sections.find(x => x.key === mode);
  // No section owns this view. Returning left whatever the panel last held on screen -
  // which was the Overview - so "Show as list" on the map looked like it navigated away.
  if (!sec) { panel.innerHTML = mapListHtml(); return; }
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
  // ONE status filter. The panel had its own `cstatus` select while the pill set
  // `statusFilter`, so on Findings the control a reader could see was not the one doing
  // the filtering - pressing "unresolved" changed the button and nothing else.
  const rows = sec.rows.filter(r => (!statusFilter.size || statusFilter.has(r.status)) &&
    (!cstatus || r.status === cstatus) && (!ckind || r.kind === ckind) &&
    (!needle || (r.label + " " + r.file + " " + r.kind).toLowerCase().includes(needle)));
  const page = rows.slice(0, shown);
  const notes = whyOncePerRun(page);
  panel.innerHTML = `<h2>${esc(sec.title)}</h2><p class="blurb">${esc(sec.blurb)}</p>
    <div class="tools">
      <input id="cq" type="search" placeholder="Filter ${sec.rows.length} rows" value="${esc(cq)}">
      ${ckind ? `<button type="button" class="chip" id="ckoff">kind: ${esc(ckind)} ×</button>` : ""}
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
  const koff = document.getElementById("ckoff");
  if (koff) koff.onclick = () => { ckind = ""; shown = ROWS_PER_PAGE; renderPanel(); };
  document.getElementById("cst").onchange = e => {
    cstatus = e.target.value;
    // The pill shows the same thing; two controls disagreeing about one filter is how
    // this went wrong in the first place.
    statusFilter.clear();
    if (cstatus) statusFilter.add(cstatus);
    if (window.syncStatusKey) syncStatusKey();
    shown = ROWS_PER_PAGE; renderPanel();
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

// The map as a list: every node currently drawn, under the same page, layer and status
// filters as the canvas. On a phone a list of relationships is more legible than any
// node-link drawing, and it loses nothing - each row IS what the canvas would show.
function mapListHtml() {
  const p = currentPage();
  if (p && !p.nodes) {
    const want = currentPageIndex();
    ensurePage(want).then(() => { if (currentPageIndex() === want && asList) renderPanel(); }, reportFailure);
    return `<h2>${esc(p.title || p.page)}</h2><p class="blurb">Opening…</p>`;
  }
  const rows = p ? lensed(p).filter(n => n.kind !== "page") : [];
  const where = p ? (p.where ? `${p.title} · ${p.where}` : p.title) : "";
  const head = `<h2>${esc(where || "Map")}</h2>`;
  if (!rows.length) {
    return head + `<p class="blurb">Nothing matches the current filters.</p>
      <div class="gap">Clear the layer or the status filter to see the rest.</div>`;
  }
  const shown = rows.slice(0, 400);
  // The WALK, not the node. A row that says "social-login-btn · dom_selector" states that
  // a thing exists; the question a reader has is what reaches it and from where, and on a
  // phone the chain reads better as a line than any drawing of it does.
  return head +
    `<p class="blurb">${n(rows.length)} thing${rows.length === 1 ? "" : "s"} on this page,
      under the filters you have set. Each row is the path that reaches it.</p>` +
    shown.map(r => {
      // The real walk, from the page entry to this node - the same one the detail sheet
      // draws hop by hop. `chain` is the symbol's own breadcrumb and is usually one word,
      // which is a label, not a path.
      const ids = (routes(r.id) || {}).inbound || [];
      const steps = (ids.length > 1 ? ids : [r.id])
        .map(id => (byId.get(id) || {}).label || id);
      const walk = steps
        .map((step, i, all) => i === all.length - 1 ? `<b>${esc(step)}</b>` : esc(step))
        .join('<i class="arrow">\u2192</i>');
      return `<div class="row chainrow" data-open="${esc(r.id)}">
        <span class="badge ${esc(r.status)}">${esc(r.status)}</span>
        <div class="t">${walk}</div>
        <div class="w">${esc(r.kind)}${r.file ? " · " + loc(r.file, r.line) : ""}</div>
        ${r.note ? `<div class="n">${esc(r.note)}</div>` : ""}</div>`;
    }).join("") +
    (rows.length > shown.length
      ? `<div class="gloss">Showing ${n(shown.length)} of ${n(rows.length)}.</div>` : "");
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
  // Filters belong where they filter something. Overview is a page of numbers about the
  // whole scan, so a control offering to narrow it is offering something it cannot do -
  // and the pill was sitting there on every view, over content it had no relationship to.
  if (window.syncChrome) syncChrome();
  if (window.syncReadout) syncReadout();
  if (window.chromeMeasure) requestAnimationFrame(window.chromeMeasure);
  panel.hidden = drawable; svg.hidden = !drawable;
  const hasLens = SECTION_KINDS[mode] !== undefined;
  // The status filter narrows the DATA, not the drawing, so it survives the switch to a
  // list. Hiding it with the canvas left the list filtered by whatever was set before and
  // no control to change it - the filter was still on, and invisible.
  // syncChrome owns which controls a view actually has, on every screen size.
  // These two really are about the canvas.
  document.querySelector(".zoom").hidden = !drawable;
  document.getElementById("lg").hidden = !drawable;
  document.getElementById("crumb").hidden = !drawable;
  // The search reaches the whole scan, so it is never out of place.
  document.getElementById("q").hidden = false;
  pgwrap.hidden = !drawable;
  listToggle.hidden = !hasLens;
  // Says what you will GET, with a swap sign. It was the trigram U+2630, which a mono
  // face at 13px draws as something that reads like a small 3 beside a 0.
  listToggle.innerHTML = asList
    ? '<span class="swap">\u21c4</span>Map' : '<span class="swap">\u21c4</span>List';
  listToggle.title = asList ? "Show it as a map" : "Show it as a list";
  const label = document.getElementById("menulabel");
  if (label) {
    const item = rail.querySelector(`.nv[data-key="${mode}"] span`);
    label.textContent = item ? item.textContent.trim() : mode;
  }
  // Choosing where to go is the end of using the menu, so the menu goes away. It was
  // staying open over the thing it had just navigated to.
  if (window.setSheet) setSheet(false);
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
// ── The reading ───────────────────────────────────────────────────────────
// The count and its trend, drawn once. It answers "how bad is it, and which way is it
// going" before a reader has touched anything, which is the whole reason to open the map.
(function reading() {
  const box = document.getElementById("reading");
  const entries = (typeof SERIES !== "undefined" && SERIES && SERIES.entries) || [];
  let total = 0;
  const sections = (typeof D !== "undefined" && D.sections) || [];
  const findings = sections.find(x => x.key === "findings");
  if (findings) total = findings.total || (findings.rows || []).length;
  else if (entries.length) total = entries[entries.length - 1].findings;
  if (!total) return;
  box.hidden = false;
  document.getElementById("bignum").textContent = n(total);

  const spark = document.getElementById("spark");
  if (entries.length < 2) { spark.remove(); return; }
  const values = entries.map(e => e.findings);
  const top = Math.max(...values, 1);
  const x = i => i * 120 / (entries.length - 1);
  const y = v => 30 - (v / top) * 26;
  const points = entries.map((e, i) => `${x(i)},${y(e.findings)}`).join(" ");
  const down = values[values.length - 1] <= values[0];
  const colour = down ? "var(--ok)" : "var(--crit)";
  spark.innerHTML =
    `<polyline points="${points}" fill="none" stroke="${colour}" stroke-width="1.5"/>` +
    `<circle cx="${x(entries.length - 1)}" cy="${y(values[values.length - 1])}" r="2.5" ` +
    `fill="${colour}"/>`;
  spark.setAttribute("aria-label",
    `${values[0]} findings then, ${values[values.length - 1]} now, over ${entries.length} scans`);
})();

// ── Layers ────────────────────────────────────────────────────────────────
const ly = document.getElementById("ly");
(function fillLayers() {
  const available = layersPresent();
  // With nothing but the frontend there is one layer, and a control offering one choice
  // is furniture. Stripe and Celery are exactly why it appears.
  if (available.length <= 2) { document.getElementById("lywrap").hidden = true; return; }
  ly.innerHTML = available
    .map(([key, label]) => `<option value="${key}">${esc(label)}</option>`).join("");
})();
ly.onchange = e => {
  layer = e.target.value;
  focus = null;
  // Choosing a page inside "Stripe" is a question with no answer, so the control goes
  // away rather than sitting there offering counts of zero.
  const global = SERVICE_LAYERS.has(layer);
  const wrap = document.getElementById("pgwrap");
  if (wrap) wrap.hidden = global;
  markFiltered();
  fillPages();
  draw();
  if (panel && !panel.hidden) renderPanel();
  if (window.syncReadout) syncReadout();
};

// ── The floating chrome ───────────────────────────────────────────────────
// One dropdown on every screen size. The old build moved the filter row and the colour key
// into a phone-only sheet and left them stacked above the canvas on a desktop - two
// layouts, two sets of bugs, and eight controls over the map on the wider one.
(function chrome() {
  const menubtn = document.getElementById("menubtn");
  const mapsheet = document.getElementById("mapsheet");
  const readout = document.getElementById("readout");
  const key = document.getElementById("colourkey");

  window.setSheet = open => {
    if (!mapsheet) return;
    mapsheet.classList.toggle("open", !!open);
    menubtn.setAttribute("aria-expanded", open ? "true" : "false");
  };
  menubtn.addEventListener("click", e => {
    e.stopPropagation();
    setSheet(!mapsheet.classList.contains("open"));
  });
  // A click anywhere else shuts it; a click inside must not.
  mapsheet.addEventListener("click", e => e.stopPropagation());
  document.addEventListener("click", () => setSheet(false));
  document.addEventListener("keydown", e => { if (e.key === "Escape") setSheet(false); });

  // The readout says what you are looking at, and disappears when it has nothing to say -
  // an always-on label over a map is just a smaller map.
  window.syncReadout = () => {
    const crumb = document.getElementById("crumb");
    const has = crumb && !crumb.hidden && crumb.textContent.trim().length > 0;
    readout.classList.toggle("show", !!has);
    // opacity:0 still reserves a painted capsule on some engines, and an empty pill
    // floating over the map reads as a bug. Take it out of the tree entirely.
    readout.hidden = !has;
  };

  // How much of the canvas the floating controls occupy. The panel pads itself by these,
  // or its first and last rows sit under the dropdown and the pills.
  let lastTop = -1, lastBottom = -1;
  window.chromeMeasure = () => {
    const top = 14 + Math.round(menubtn.getBoundingClientRect().height);
    const bottom = key && !key.hidden
      ? 14 + Math.round(key.getBoundingClientRect().height) : 14;
    // Write only on a real change: the properties feed the panel's padding, the padding
    // changes layout, and an unguarded write is an observer loop that never settles.
    if (top === lastTop && bottom === lastBottom) return;
    lastTop = top; lastBottom = bottom;
    const root = document.documentElement.style;
    root.setProperty("--chrome-top", top + 10 + "px");
    root.setProperty("--chrome-bottom", bottom + 10 + "px");
  };

  // Which controls a view actually has. Overview is a page of numbers about the whole
  // scan, so a control offering to narrow it is offering something it cannot do.
  window.syncChrome = () => {
    const onMap = SECTION_KINDS[mode] !== undefined;
    const wants = {
      overview: [],
      map: ["cm", "pg", "ly", "status"],
      findings: ["cm", "status"],
      files: [],
      changes: ["cm"],
    }[mode] || (onMap ? ["cm", "pg", "ly", "status"] : ["cm"]);

    ["cm", "pg", "ly"].forEach(id => {
      const wrap = (document.getElementById(id) || {}).closest
        ? document.getElementById(id).closest("label") : null;
      if (wrap) wrap.hidden = !wants.includes(id);
    });
    if (key) key.hidden = !wants.includes("status");
    // Counts on the pills, from the page actually drawn - a pill that says "unresolved"
    // with no number is a control with no information in it.
    if (!key.hidden) {
      const counts = {};
      const p = typeof currentPage === "function" ? currentPage() : null;
      Object.assign(counts, p ? p.st : {});
      key.querySelectorAll(".seg button[data-status]").forEach(btn => {
        const st = btn.dataset.status;
        if (!st) return;
        const b = btn.querySelector("b");
        if (b) b.textContent = counts[st] ? counts[st].toLocaleString() : "";
        // A status this page has none of is not a filter, it is a dead control.
        btn.hidden = !counts[st] && !statusFilter.has(st);
      });
    }
    if (window.chromeMeasure) requestAnimationFrame(window.chromeMeasure);
  };

  syncChrome();
  syncReadout();
  chromeMeasure();
  window.addEventListener("resize", () => chromeMeasure());
  if (window.ResizeObserver) new ResizeObserver(() => chromeMeasure()).observe(menubtn);
})();
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
# What a node row carries on the wire. The three long strings - note, snippet, context -
# are not here: they were 34% of a real map's bytes and are read only when a sheet or the
# code box opens, so they travel in a detail chunk of their own (see _payload).
_NODE_FIELDS = ("id", "label", "kind", "status", "file", "line", "lang", "service")
_DETAIL_FIELDS = ("note", "snippet", "context")

# A service does not live on a page: a Stripe webhook is reached by Stripe and a Celery
# task by a worker. Each gets a page of its own, unioned across the map, hidden from the
# page picker and drawn when its layer is chosen. Built here rather than in the browser
# so it loads like any other page instead of needing every page's rows at once.
_SERVICE_LAYERS = (
    ("stripe", "Stripe", ("stripe_webhook", "stripe_event")),
    ("celery", "Celery", ("celery_task", "celery_schedule")),
    ("graphql", "GraphQL", ("graphql_field", "graphql_selection")),
)

# Below this a chunk is plain JSON, so a small map stays readable in the markup and the
# fixture tests can grep it; above it, gzip + base64. The crossover is where the base64
# overhead (4/3) is paid back several times over by the compression.
_CHUNK_INLINE_LIMIT = 4 * 1024


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


def _json(value) -> str:
    # </script> inside JSON would close the tag early; escaping the slash is the
    # standard defence and stays valid JSON.
    return json.dumps(value, separators=(",", ":")).replace("</", "<\\/")


def _chunk(name: str, value) -> str:
    """One block of data the page decodes on demand.

    `type="text/plain"` makes the parser skip it: nothing is evaluated, nothing is
    allocated beyond the text itself, until a reader asks for the page it belongs to.
    Above the inline limit the JSON is gzipped and base64'd - `DecompressionStream`
    reads it back from a `data:` URL, which is the one loader that works from `file://`
    (a `<script src>` needs a sibling file, and reading one from a page is refused).
    """
    text = _json(value)
    if len(text) < _CHUNK_INLINE_LIMIT:
        return f'<script type="text/plain" data-chunk="{name}" data-enc="json">{text}</script>'
    packed = gzip.compress(text.encode("utf-8"), compresslevel=6, mtime=0)
    return (f'<script type="text/plain" data-chunk="{name}" data-enc="gz">'
            f'{base64.b64encode(packed).decode("ascii")}</script>')


def _payload(connectivity_map: ConnectivityMap) -> tuple[str, str]:
    """The map as (inline meta, deferred chunks).

    The meta is what the page needs before a reader touches anything: the string
    tables, one row of counts per page, which page shows most of each file, and what
    each commit changed per page. Everything sized by the number of symbols - the rows,
    the long strings, the search index, the commit diffs - is a chunk.
    """
    kinds, statuses, files = _Table(), _Table(), _Table()
    # Interned like the others: a repository has a handful of languages and services, and
    # every node repeats one of each.
    langs, services = _Table(), _Table()
    changed = connectivity_map.changed
    commits = connectivity_map.commits or []

    def _row(node):
        # Trailing empties are dropped, not sent as "": most nodes carry no language or
        # service, and a bucket carries no file.
        row = [
            node.id, node.label, kinds(node.kind), statuses(node.status),
            files(node.file or ""), node.line,
            langs(node.lang or ""), services(node.service or ""),
        ]
        while len(row) > 4 and not row[-1]:
            row.pop()
        return row

    pages = [(page.page, page.title or page.page, page.where, "", page.nodes, page.edges)
             for page in connectivity_map.pages]
    for key, title, wanted in _SERVICE_LAYERS:
        want = set(wanted)
        seen: dict[str, object] = {}
        for page in connectivity_map.pages:
            for node in page.nodes:
                if node.kind in want and node.id not in seen:
                    seen[node.id] = node
        if not seen:
            continue
        edges, seen_edges = [], set()
        for page in connectivity_map.pages:
            for e in page.edges:
                pair = (e.source, e.target)
                if e.source in seen and e.target in seen and pair not in seen_edges:
                    seen_edges.add(pair)
                    edges.append(e)
        pages.append((f"layer:{key}", title, "", key, list(seen.values()), edges))

    meta_pages, chunks = [], []
    file_best: dict[str, tuple[int, int]] = {}
    search_rows, in_search = [], set()
    per_page_changed = []
    commit_per_page = [[] for _ in commits]
    for index, (name, title, where, layer, nodes, edges) in enumerate(pages):
        rows = [_row(node) for node in nodes]
        by_status: dict[str, int] = {}
        by_kind_status: dict[tuple[int, int], int] = {}
        file_counts: dict[str, int] = {}
        changed_here = 0
        commit_here = [0] * len(commits)
        for node, row in zip(nodes, rows, strict=True):
            by_status[node.status] = by_status.get(node.status, 0) + 1
            ks = (row[2], row[3])
            by_kind_status[ks] = by_kind_status.get(ks, 0) + 1
            if node.file:
                file_counts[node.file] = file_counts.get(node.file, 0) + 1
            if node.id in changed:
                changed_here += 1
            for c, commit in enumerate(commits):
                if node.id in (commit.get("changed") or {}):
                    commit_here[c] += 1
            if node.kind != "page" and node.id not in in_search and not layer:
                in_search.add(node.id)
                search_rows.append([node.id, node.label, row[2], row[3],
                                    files(node.file or ""), node.line, index])
        if not layer:
            for path, n in file_counts.items():
                if n > file_best.get(path, (-1, 0))[1]:
                    file_best[path] = (index, n)
        per_page_changed.append(changed_here)
        for c, n in enumerate(commit_here):
            commit_per_page[c].append(n)
        meta_pages.append({
            "page": name, "title": title, "where": where, "layer": layer,
            "n": len(nodes), "st": by_status,
            "ks": [[k, st, n] for (k, st), n in by_kind_status.items()],
        })
        chunks.append(_chunk(f"p{index}", {
            "nodes": rows,
            "edges": [[e.source, e.target, statuses(e.status)] for e in edges],
        }))
        chunks.append(_chunk(f"d{index}", {
            field: [getattr(node, field) or "" for node in nodes] for field in _DETAIL_FIELDS
        }))
    # Columnar, with the two text columns as ONE newline-joined string each. A row per
    # symbol as a small array cost ~300 bytes of heap once the page had turned each into
    # an object with a search string: 633 MB at two million symbols. One string of
    # labels is a memchr-speed scan and a fortieth of the heap; the numeric columns
    # become typed arrays on load. A label never holds a newline, so it is the safe
    # separator; an id is made safe the same way.
    def _column(values):
        return "\n".join(v.replace("\n", " ") for v in values)

    chunks.append(_chunk("search", {
        "n": len(search_rows),
        "ids": _column(r[0] for r in search_rows),
        "labels": _column(r[1] for r in search_rows),
        "kind": [r[2] for r in search_rows],
        "status": [r[3] for r in search_rows],
        "file": [r[4] for r in search_rows],
        "line": [r[5] or 0 for r in search_rows],
        "page": [r[6] for r in search_rows],
    }))
    chunks.append(_chunk("commits", [
        {"changed": c.get("changed") or {}, "changes": c.get("changes") or []}
        for c in commits
    ]))
    meta_commits = []
    for c, commit in enumerate(commits):
        counts = {"added": 0, "removed": 0, "status": 0}
        for kind in (commit.get("changed") or {}).values():
            counts[kind] = counts.get(kind, 0) + 1
        meta_commits.append({
            key: commit.get(key) for key in ("sha", "subject", "date", "symbols",
                                             "baseline", "head", "change_total")
        } | {"counts": counts, "perPage": commit_per_page[c]})
    meta = {
        "columns": _COLUMNS,
        "changed": changed,
        "changedPerPage": per_page_changed,
        "commits": meta_commits,
        "fields": _NODE_FIELDS,
        "detail": _DETAIL_FIELDS,
        "kinds": kinds.values,
        "statuses": statuses.values,
        "files": files.values,
        "langs": langs.values,
        "services": services.values,
        "filePage": {path: index for path, (index, _) in file_best.items()},
        "pages": meta_pages,
    }
    return _json(meta), "\n".join(chunks)


def _why_reasons() -> dict:
    """The triage vocabulary, read from the one place that defines it."""
    from seamcheck.triage import WHY_HELP

    return WHY_HELP


def _console_payload(console) -> str:
    """The review sections, or an empty shell so the page still renders without them."""
    from dataclasses import asdict

    if console is None:
        return json.dumps({"backend": {}, "frontend": {}, "store": {}, "offscreen": {},
                           "groups": [], "sections": []})
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
        # Named explicitly, like the two above. This dict is hand-built rather than an
        # asdict(), so a field added to Console reaches the page only when it is added
        # here too - which is why the store and off-browser counts arrived empty.
        "store": getattr(console, "store", {}) or {},
        "offscreen": getattr(console, "offscreen", {}) or {},
        "groups": [[title, count, gloss] for title, count, gloss in console.groups],
        "sections": [_section(section) for section in console.sections],
    }
    return json.dumps(data).replace("</", "<\\/")


def _js(value) -> str:
    """JSON for a <script> block.

    `</` is split so a string in the DATA cannot close the tag it sits inside. It lives in
    a function rather than inline in an f-string because a backslash inside an f-string
    EXPRESSION is Python 3.12 syntax (PEP 701), and this package runs on 3.9 - which is the
    Python most Macs already have.
    """
    return json.dumps(value).replace("</", "<" + chr(92) + "/")


def render(connectivity_map: ConnectivityMap, console=None, files=None,
           repo_root: str = "", editor: str | None = None, series=None,
           adapters=None, observed=None, share_payload=None) -> str:
    mode = (
        f"diff vs {_esc(connectivity_map.baseline_sha[:12])}"
        if connectivity_map.baseline_sha
        else "current"
    )
    meta, chunks = _payload(connectivity_map)
    legend = "".join(
        f'<div><span style="background:{colour}"></span>{name}</div>'
        for name, colour in (
            ("connected", "var(--ok)"), ("unresolved", "var(--crit)"),
            ("unused", "var(--warn)"), ("uncertain", "var(--dim)"),
        )
    )
    return "\n".join([
        "<!doctype html>",
        # The pack is stamped on the root so `body` and every panel inherit it, and it is
        # stamped in the markup rather than by script so the first paint is already Aurora
        # rather than a flash of something else.
        '<html lang="en" data-pack="aurora"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1, '
        'viewport-fit=cover">',
        # Every pack names real faces. They are loaded with a full local fallback stack, so
        # a map opened on a plane degrades to system type and still reads as itself.
        '<link rel="preconnect" href="https://fonts.googleapis.com">'
        '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
        '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?'
        'family=Archivo:wght@400;500;600;700&'
        'family=DM+Sans:opsz,wght@9..40,400;9..40,500;9..40,700&'
        'family=IBM+Plex+Mono:wght@400;500;600&'
        'family=Instrument+Serif&'
        'family=JetBrains+Mono:wght@400;500;700&'
        'family=Inter+Tight:wght@400;500;600;700&'
        'family=Space+Mono:wght@400;700&display=swap">',
        f"<title>Seamcheck — {_esc(connectivity_map.git_sha[:12])}</title>",
        f"<style>{_CSS}</style></head><body>",
        # NOTHING SITS ABOVE THE MAP ANY MORE. The header used to stack eight controls -
        # brand, the big number, a view select, three filter selects, a breadcrumb row with
        # a search box, and a five-button colour key - over the one thing a reader opened
        # the page for. All of it now floats ON the canvas: one dropdown holds the menu and
        # every filter, four status pills carry the whole vocabulary, and that is the lot.
        '<div class="shell"><div class="content">',
        '<main class="main">',
        '<div class="cvclip"><div class="cvlayer" id="cvlayer"><svg id="cv"></svg></div></div>',
        '<div class="panel" id="panel" hidden></div>',

        # ── the one dropdown ──────────────────────────────────────────────────
        '<div class="hud tl"><div class="menuwrap">'
        '<button type="button" class="menubtn" id="menubtn" aria-expanded="false">'
        '<span class="bars"><i></i><i></i><i></i></span>'
        '<span id="menulabel">Map</span></button>'
        '<div class="mapsheet" id="mapsheet">'
        '<div class="mlab">View</div>'
        '<div class="nav" id="nav"></div>'
        '<div class="msep"></div>'
        '<div class="mlab">Narrow it down</div>'
        '<div class="mfilters">'
        '<label><span>Commit</span><select id="cm"></select></label>'
        '<label id="lywrap"><span>Emphasis</span><select id="ly"></select></label>'
        '</div>'
        '<div class="msep"></div>'
        '<div class="mlab">Search everything</div>'
        '<div class="msearch"><input id="q" type="search" placeholder="Any symbol, file, '
        'route or element"><span id="qn" class="qn"></span></div>'
        # Kept because switchTo() writes to it; the nav above is what a reader touches.
        '<select id="vw" hidden></select>'
        '</div></div>'
        # Choosing WHICH page you are looking at is the thing a reader does most often, and
        # it was two taps deep inside a dropdown. It sits on the glass, next to the view.
        '<label id="pgwrap" class="pagepick"><select id="pg"></select></label>'
        "</div>",

        # ── appearance, top right ─────────────────────────────────────────────
        '<div class="hud tr">'
        '<button type="button" class="iconbtn" id="up" hidden aria-label="Back">\u2190</button>'
        '<button type="button" class="iconbtn wide" id="aslist" hidden>List</button>'
        '<span class="packwrap">'
        '<button type="button" class="tmode" id="tmode" aria-label="Appearance">Aurora</button>'
        '<div class="packmenu" id="packmenu"></div></span>'
        "</div>",

        # ── what you are looking at, only when there is something to say ──────
        '<div class="readout" id="readout"><span id="crumb"></span></div>',

        # ── the four words the whole tool is built on ─────────────────────────
        '<div class="hud bl">'
        '<div id="colourkey">'
        '<div class="seg" role="group" aria-label="Show only these statuses">'
        '<button type="button" class="s-connected" data-status="connected" '
        'title="Something reaches it, evidence attached">'
        '<i></i>connected<b></b></button>'
        '<button type="button" class="s-unresolved" data-status="unresolved" '
        'title="Something reaches for it and it is not there">'
        '<i></i>unresolved<b></b></button>'
        '<button type="button" class="s-unused" data-status="unused" '
        'title="Both ends observable, nothing uses it">'
        '<i></i>unused<b></b></button>'
        '<button type="button" class="s-uncertain" data-status="uncertain" '
        'title="No evidence either way \u2014 not a claim it is dead">'
        '<i></i>uncertain<b></b></button>'
        "</div></div>"
        # A filter that empties the canvas has to offer its own way out, WHERE THE READER
        # IS LOOKING. This used to live in the header; the header is gone and the button
        # went with it, so choosing Stripe on a page that has none stranded you with an
        # empty map and no visible control that would undo it.
        '<div class="fnote" id="fnote" hidden></div>'
        "</div>",

        # Every element the script still writes to, kept in the tree and out of the way.
        '<div class="offstage">'
        '<div class="reading" id="reading" hidden>'
        '<div class="big"><span>to look at</span><b id="bignum">0</b></div>'
        '<svg class="spark" id="spark" viewBox="0 0 120 34" preserveAspectRatio="none"></svg>'
        "</div>"
        f'<div class="brand"><b>Seamcheck</b>'
        f'<span class="meta">HEAD {_esc(connectivity_map.git_sha[:12])} · {_esc(mode)}'
        f'{_adapter_label(adapters)}</span></div>'
        '<div class="crumbrow"></div>'
        '<div class="note" id="cmnote"></div>'
        '<div class="note capnote" id="capnote"></div>'
        '<div class="gone" id="gone"></div>'
        "</div>",
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
        f"<script>const MAPDATA={meta};</script>",
        f"<script>const CONSOLE={_console_payload(console)};</script>",
        f"<script>const SERIES={_js(series or {'entries': []})};</script>",
        f"<script>const FILES={_js(files or [])};</script>",
        f"<script>const OBSERVED={_js(observed or [])};</script>",
        # Locations are stored relative to the repo; an editor URL needs an absolute
        # path. Ship the root once and let the page join, rather than absolutising
        # every one of tens of thousands of rows.
        f"<script>const OPEN={json.dumps({'root': repo_root, 'href': editors.scheme(editor)})};</script>",
        f"<script>const MEANING={_js(meaning.table())};</script>",
        f"<script>const BLIND_SPOTS={json.dumps(meaning.BLIND_SPOTS)};</script>",
        # The fixed reasons a finding can be wrong, and the code-free report itself. Both
        # come from the same source the CLI uses, so the page and the terminal can never
        # offer a different vocabulary.
        f"<script>const WHY={_js(_why_reasons())};</script>",
        f"<script>const SHARE={_js(share_payload or {})};</script>",
        # The rows themselves, after everything the script reads at once: inert text
        # blocks the loader decodes one page at a time (see _chunk).
        chunks,
        f"<script>{_SCRIPT}</script>",
        "</body></html>",
    ])
