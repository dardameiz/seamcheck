# Pages, stores, and memory — 3 Sep 2026

Approved design for the batch raised after the 0.9.0 release test on the game. Five
commits, in this order; each passes the usual gates (ruff, self-scan, `verify_output
--self`, corpus TOTAL unchanged, game map ≤ 50 s and index ≤ 400 KB).

## 1. Credit line

The map footer says who built it: `Built by dardameiz — free, MIT. Got a finding wrong?
Say so.` The name links to the GitHub profile, "MIT" to the repository.

## 2. The geometry panel goes

"The page a browser saw" — the SVG of boxes, its `o<i>` chunks, `_observed_lazy`,
`symbolForBox`, the `.ob*` CSS, its menu entry — is removed, and `seamcheck observe`
stops recording box geometry nobody will draw. Observe still promotes elements the
browser proved exist; that half is evidence and stays.

## 3. Page → Section

One picker became two:

- **Page** — the HTML a person recognises, one entry per `title · where` (34 on the game).
- **Section** — the script inside it: "Whole page" by default, or one entry. A page with
  a single entry shows no section list.

"Whole page" is a union page built at write time (`group:<title>`), one chunk that loads
like any other page — never 77 chunks merged in the browser. Existing entry pages are
unchanged; the union page carries a `group` key and is hidden from the Section list.

Noticed on the way, logged OPEN: 70 of Push Arena's 77 entries are regression plugins
under `static/admin/regression/plugins/`, credited to the arena template.

## 4. Stores as global layers

`redis` and `database` join `stripe`/`celery` as SERVICE layers: a synthetic page unioned
across the whole map (reached pages AND the not-reached buckets, so a key nothing touches
is on it).

- `layer:redis` — every `redis_key`, `redis_key_use`, `redis_ttl`; keys grouped by their
  first segment (`user`, `leaderboard`) as the expandable tier.
- `layer:database` — `model` (added to the Database set; a Django project's Postgres was
  invisible without it), `db_table`, `db_column`, `db_function`, `db_policy`, their
  `_use` kinds, `edge_function*`, `storage_bucket`, `firestore_collection`,
  `firestore_rule`.

Every node on a layer page carries the list of ordinary pages that reach it. The Page
select, while a global layer is on, filters the layer to that page's nodes.

## 5. Shared across pages

`layer:shared`: nodes reached from two or more ordinary pages, with the page list on each
card. Ordinary pages show "Also on: …" on such a card. The list is written only for nodes
with ≥ 2 pages, as indexes into the page table, so the index stays small.

## 6. Memory of marks

`seamcheck/triage.json` already keeps every mark keyed to a fingerprint of the evidence.
Added:

- `seamcheck triage <id> --undo` removes the mark. The card of a marked finding shows the
  mark and an Undo button that puts the command on the clipboard.
- A mark whose fingerprint no longer matches is kept with `expired` set, and the finding
  is raised as **returned**: console summary (`N returned`), a pill in the findings list,
  a line on the card naming the date and the reason.
- The report gains a Returned count and one sentence on what each fixed word does for the
  author.

## 7. README figure

A headless, DPR-1 screenshot of the map on the browser-test fixture — browser tier, seam,
server tier, one wire lit — at `docs/images/tiers.png`, captioned. And `flush=True` on the
link lines `seamcheck map` prints, so a redirected stdout shows the URL before the server
exits.
