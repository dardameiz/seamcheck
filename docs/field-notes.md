# Field notes

Real findings from the project Seamcheck is measured against — what each one cost, and how it was found.

## Field notes from the measurement project

Real findings from the 511,000-line Django + vanilla-JS project this tool is measured against —
what each one cost, and how it was actually found. Dated, so you can see what is new.

### 2026-09-02 · What a dead selector actually cost, once

A finding that reads like lint is not always lint. Three `dom_selector` findings — rows that looked
exactly like an unused CSS class — turned out to be this:

| | endpoint | every | on | requests/second at 50k users |
|---|---|---|---|---:|
| a latency readout | `/api/get-user-stats/` — **per-user** | **5 seconds** | every arena page | **~10,000** |
| a leaderboard | `/get_leaderboard_data/` | 30 s / 120 s | **every page but one** | ~417–1,667 |
| the same leaderboard again, in a second file | `/get_leaderboard_data/` | 30 s / 120 s | every arena page | ~417–1,667 |

Each one fetched on a timer and wrote the result into elements that **exist in no template and are
built by no script**. The first did not even bail when its three target ids came back `null` — it
just kept measuring latency and assigning it to nothing, every five seconds, per user.

Nothing failed. No error, no ticket, no failing test — which is precisely why all three had been
running for as long as anyone could remember. The markup for the leaderboard had been deleted
*once*; two independent pollers survived it, in two different files, neither aware of the other.

**~12,000 requests per second of pure waste, at the concurrency the application is designed for.**

**The query that found them.** Not a multi-writer finding and not a code review — all three were
ordinary `unresolved` rows. What separated them from the cosmetic ones was one pass over the
findings:

> a dead selector within a few lines of a `fetch(`, a `setInterval(` or a `new MutationObserver`

The scan already held both halves — *this selector never matches* and *there is a timer in the same
function*. It had simply never put them together. If you are reading your own findings and they
look like housekeeping, sort them by that question first.

### 2026-09-02 · What to do about `uncertain`

`uncertain` is the tool declining to guess, so the goal is never to make the number smaller — it is
to convert each cause into evidence. On the measurement project 2,814 of them break down by cause,
and each cause has a different answer:

| Cause, as the scan reports it | Share | What actually resolves it |
|---|---:|---|
| *javascript puts this class on an element* | ~960 | **`seamcheck observe`.** The browser sees `classList.add()` happen. Nothing static can. |
| *selector built at runtime* — `` `[data-tab="${x}"]` `` | ~175 | Partly static: the **attribute name is a literal** even when the value is not. Credit `data-tab` as read, leave the value unknown. The rest is `observe`. |
| *fetch target built at runtime* | ~240 | `observe` — it records the URL actually requested. |
| *a class-name prefix, not a class* | ~200 | Static: report the prefix as a prefix, never as a missing class. |
| *nothing references this rule, but its…* | ~300 | Usually a vendor or framework stylesheet. Read it, then decline to judge it. |

Two rules that keep the number honest while you shrink it:

1. **Never convert `uncertain` to `unused` by assumption.** A tool that guesses to look decisive is
   the failure mode this project exists to avoid. Convert with evidence or leave it.
2. **A page the run never visited leaves no trace, and looks exactly like a page that is broken.**
   Everything `observe` promotes is labelled as observed for that reason — so `uncertain` going down
   should always be traceable to a specific run over a specific set of pages.
