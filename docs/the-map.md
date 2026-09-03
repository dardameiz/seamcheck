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

## One page per script, not one map of everything

The page dropdown does not list your templates. It lists **entry points**: every script a
page loads is its own map page, named after the script — `Push Arena · /push_arena/ ·
push-arena-main`, and next to it `… · cookie_consent`. A template that loads three scripts
is three pages.

That is deliberate, and it is what keeps a large codebase readable. A page is *the code
that actually runs when a reader opens that URL*, followed from the script tag through
every import to the selectors it queries, the URLs it fetches, the keys those handlers
touch. The `achievements` widget on a page with forty modules is a page of its own, so you
can check it without the other thirty-nine drawn over it — and a symbol that no page's
scripts ever reach lands in the **Not reached from any page** buckets at the end of the
list, which is itself a finding worth reading.

There is no "whole codebase" page, on purpose: every symbol in the scan is on exactly one
of these pages or in a bucket, and a 40,000-node hairball would show you none of them.
The findings list and the search box are the cross-cutting views; they see everything.

One menu, and the counts are the current page's.

![The menu](images/menu.png)

<sub>Views on top, then the lenses: the whole scan, or just the database, Redis,
configuration or background jobs.</sub>

The findings list at the top of this page is the other half of the same view: everything
it is willing to claim, each one explained in a sentence, worst first, with the file and
line on both sides.

It reads on a phone, because that is where you end up looking at it.

<img src="images/phone.png" width="320" alt="The map on a phone">

Five looks, if you care. Aurora is the default.

![The design packs](images/packs.png)
