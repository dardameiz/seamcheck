# Reading the map

What each screen is for.

## What it looks like

**Click a red one.** The chain that reaches it lights up and everything else recedes, so
you can see where the request came from and where it stopped — with the file and line for
every hop, and a sentence saying what it means and what to check.

![A broken request, clicked, with its chain lit](images/chain.png)

<sub>`checkout.js` asks for `/api/shipping/quotes`. The server serves
`/api/shipping/quote`. One character, valid on both sides, and nothing else would have told
you. The lit line runs straight and carries an arrow, so the direction is the request's
direction; everything not on the path recedes.</sub>

The number it opens on is the only one that matters: **how much is worth looking at**, and
what the rest of the scan is instead.

![The score](images/overview.png)

<sub>Ten of 101 symbols are findings. The other 91 are named rather than hidden — 76
connected with evidence attached, 15 uncertain, which is the scan declining to guess. Each
region carries its own rate, so "the store is 11% findings" is a sentence you can act on
and "the frontend is bigger" is not.</sub>

It reads your data layer as the **second seam** — a query crosses a boundary and lands on a
table the same way a request crosses one and lands on a route.

![The store band](images/database.png)

<sub>Lanes by store, because each fails differently. Every lane says whether it has an
oracle: `schema in repo` means a name can be checked, `no schema · pairing only` means it
cannot, and a grey card there is unknowable rather than dead. Redis never has one — nothing
declares a key — so it can only ever show you that two halves of your own code disagree.</sub>

## A page, then its sections

Two pickers. **Page** lists the pages a reader knows — a Django or Vite page
(`Push Arena · /push_arena/`), a Next.js page (`Pricing · /pricing/[locale]`), each found the
way that framework declares it. Below the pages come **server entries**: every file that
handles a request, titled by the routes it serves (`/api/checkout`), whichever backend
wrote it. **Section** lists what a page loads: every script tag is its own section, named
after the script — `push-arena-main`, `cookie_consent` — and **Whole page** at the top is
all of them together. A page that loads one script has no Section picker at all.

A page stays in the list even when nothing on it starts a chain to draw; its canvas says
how many symbols its files hold instead, because a page that silently vanished looked
exactly like one that did not exist. Click the page itself to see why it is an entry — "a
page file, routed by the filesystem", "a declared JavaScript entry point".

A section is *the code that actually runs from that script tag*, followed through every
import — a tsconfig alias like `@/components/x` and a workspace package included — to the
selectors it queries, the URLs it fetches, the keys those handlers touch. The
`achievements` widget on a page with forty modules is a section of its own, so you can
check it without the other thirty-nine drawn over it. After the entries, **On a page,
nothing to draw** holds what a page's files contain that starts no chain — a class applied,
a rule in an imported stylesheet — and a symbol in a file no entry reaches lands in the
**Not reached from any page** buckets at the end of the Page list, which is itself a
finding worth reading.

Whole page is built when the map is written, as one page like any other, so opening it
costs one chunk and not seventy. Its nodes are the union of the sections' nodes, each drawn
once, and it is left out of search because every one of them is already there under its
own section.

There is still no "whole codebase" page, on purpose: a 40,000-node hairball would show you
none of them. The findings list and the search box are the cross-cutting views; they see
everything.

## A function, and everything it touches

The third picker is the one you use while you are writing code. Type and it offers every
function the scan saw defined, prefix matches first, with the file and how many symbols
each one owns. On the reference project that is 9,664 functions answering a keystroke in a
tenth of a millisecond, from an index that loads once.

Picking one leaves the pages behind. A function's symbols are never all on one page — the
page holds the route it answers, the store layer holds the keys it writes, and whatever no
page reaches sits in a bucket — so the view is a page of its own, unioned across all of
them, holding:

- **what it touches** — every symbol the function owns: the requests it makes, the keys it
  writes, the tables it queries, the tasks it queues, the elements it fills;
- **what its helpers touch** — the same, for the functions it calls, three levels down.
  Most handlers delegate, and a view of just the handler's own body would show the route
  and nothing else;
- **one hop out** — whatever reaches those: the route that dispatches to it, the request
  that calls the route. **Widen by one hop** goes further; it is a button rather than the
  default because everything a hot handler transitively reaches is the whole application.

Under the canvas, **Touches** counts the round-trips per call by lane, and **Called by**
names every function that calls this one, each a click away. `submit_push() — 11 symbols
(1 its own, 10 through helpers) · Redis 10` is a push costing ten Redis operations; the
same line reading `Postgres 1` on a handler meant to be Redis-only is a diagnosis.

**How calls are resolved, and what is left out.** A def in the same file; a
`from x import y` of a module in this project; `self.method` inside its own class; and a
method whose simple name is defined exactly once in the whole project — `order.save()`
where four classes define `save` resolves to nothing, and a call into an imported library
never borrows a project name. Python only, for now, and keyed by the name you would type,
so two files that both define `reset` share one entry.

## A store, across every page

Redis and the database are not pages, so they are not drawn like one. Pick either from the
menu and the whole store is drawn once — every key and every table the scan found,
unioned over every page and over the not-reached buckets, so a key nothing touches sits
beside the ones that are used. Keys are parked by their first segment (`user:*`,
`challenges:*`, `other`) and open one namespace at a time, which is what keeps 754 keys
readable.

The Page picker stays on. **Every page** first, then only the pages that reach something
in the store; pick one and the store narrows to what that page touches. A card's sheet
lists the pages it is on, and tapping one jumps to that page with the card open.

One limit, written down in the changelog: a view is tied to a key or table only when the
use is in the same file. A project whose views hand off to a cache module will show its
whole store under Every page and nothing under any one page — the store is right, the page
column is empty.

## What two pages share

**Shared across pages** is the layer of everything reached from two or more pages — the
helper every page imports, the endpoint three pages call, the selector two templates
write. It is the answer to "what else does this change touch": a card there, and the same
card on any ordinary page, says **on N pages**, and its sheet names them. Two sections of
one page count as one page.

One menu, and the counts are the current page's.

![The menu](images/menu.png)

<sub>Views on top, then the lenses: the whole scan, or just the database, Redis,
configuration or background jobs.</sub>

The findings list at the top of this page is the other half of the same view: everything
it is willing to claim, each one explained in a sentence, worst first, with the file and
line on both sides.

It reads on a phone, because that is where you end up looking at it.

<img src="images/phone.png" width="320" alt="The map on a phone">

`seamcheck map` prints two addresses: one for this computer and one to type on a phone on
the same wifi. The phone is often somewhere else — a link gets sent, opened on a train,
opened by whoever you sent it to — and the wifi address is then just a number that times
out. `seamcheck config --tunnel always` remembers, for this machine and every project on
it, that each run should also print a public HTTPS address that works from anywhere.

It is stored rather than defaulted because it is the one thing seamcheck does that leaves
your machine. While the command runs, anyone holding that link can read the report; it
dies with the command. `seamcheck config --tunnel never` puts it back, `--local-only`
overrules it for one run, and `seamcheck config` always says which answer is in force and
where it came from.

Five looks, if you care. Aurora is the default.

![The design packs](images/packs.png)
