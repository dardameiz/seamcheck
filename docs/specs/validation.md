# Validation phase — proving it on code we did not write

**Status:** plan. The README does not change until this produces numbers.

## The question that decides the shape of this phase

> If we run it on public repositories and it finds things, are we shaming them?

**Yes — if we publish findings against a named repo. So we do not.**

Running a read-only analyser over public source is unremarkable and raises nothing. What
matters is what gets published afterwards. A maintainer who has given their work away for
free, and who wakes up to *"Seamcheck found 40 dead routes in your project"* on a landing
page, has been handed unpaid criticism as someone else's marketing. That earns hostility,
deservedly, and it is a bad trade even ignoring the manners: our own measured precision is
98.3%, so roughly one finding in sixty is wrong — and a wrong finding published against a
named project is a public accusation about working code.

There is also a case where it is worse than rude. If a finding is security-relevant — a route
with no auth check, an admin path exposed — that is coordinated disclosure, not a screenshot.

### The policy

| what | allowed |
|---|---|
| Aggregate numbers across many repos, unnamed | ✅ always |
| Naming repos in a list of *what was scanned*, with no findings | ✅ fine |
| Naming a repo **with** its findings | ❌ never, unless the maintainer asked |
| A finding we **fixed**, linked to the merged PR | ✅ **the best version of all** |
| Anything security-relevant | ❌ private disclosure, never public |

### The version that is better than shaming

**Find something, fix it, open the PR, then link the PR.**

> *"Seamcheck found a `{% url %}` tag naming a route that no longer exists in
> <project>. [PR #1234], merged."*

That is not criticism, it is a contribution, and the merged link is stronger proof the tool
works than any screenshot could be. It also puts the maintainer on our side rather than the
other one. Three of those are worth more than a hundred anonymous findings.

Budget it honestly: each one is a real PR against a real project — read their contributing
guide, match their style, be prepared to be wrong in public. Two or three is a realistic
target, not twenty.

### The demo stays fictional

Every screenshot in the README already uses the invented bookshop in `docs/mockups/`. That
stays. It is a demo, it is labelled a demo, it can be made to show exactly the finding worth
showing, and nobody's project is in it.

---

## What "it works" means, per repo

Five gates, in order. A repo that fails an early one tells us more than one that passes.

1. **It runs.** Config detection picks a URLconf; static mode parses it; the scan finishes
   without an exception. *Failure here is the most valuable result of the whole phase* — it
   is a shape of project we have never seen.
2. **It finds the routes.** Compare against a ground truth. Where the repo installs cleanly,
   run import mode too and diff, exactly as was done to measure static mode at 95%. Where it
   does not, count routes by hand in a sample.
3. **The findings are plausible.** Sample 20 per repo and hand-check, the same protocol that
   took the reference project from 73% to 98.3%.
4. **It is not absurd.** 4,000 findings on a 5,000-line project means an extractor is
   misfiring, whatever the precision on the sample says.
5. **The output is sound.** `tools/verify_output.py` — added 2026-09-02, because gates 1–4
   all stop at the graph and every one of them can pass while the document a person opens is
   broken. It renders the real map and interrogates it: every emitted `<script>` parses under
   `node --check`, each node row carries kind and status indices that are in range, no edge
   points at a node that is not on its page, a repo with pages produces pages, and nothing
   leaked into the page as raw text. The map's JavaScript is built from a Python string, so a
   stray character is a **blank page** that no Python test and no linter can see.

Gates 1 and 5 are run by machine on every commit (see CLAUDE.md). Gates 2 and 4 are reported
by `tools/corpus.py`. Gate 3 is a human reading a sample and cannot be automated.

## Choosing the repos

Aim for **shapes we have never seen**, not for famous names. The reference project is one
codebase with one set of habits; the entire point is to find the habits it does not have.

Selection criteria:

- Django, since that is the only backend adapter — but deliberately unlike the reference:
  class-based views, DRF, HTMX, django-ninja, a `views/` package versus a single module,
  namespaced URLconfs, `i18n_patterns`, a monorepo with several apps.
- Range of size: 2k lines to 200k. Small repos catch different bugs than large ones.
- Permissively licensed, so quoting a line in a bug report is uncontroversial.
- Active enough that git history means something — the history oracle needs commits.

Ten to fifteen is enough for the first pass. The corpus proper comes later.

## The two oracles, unchanged

- **Git history as a self-labelling answer key.** Scan at commit `C`; if we said `X` is
  unused and the maintainers deleted `X` at `C+n` without reverting, that is a confirmed
  true positive that nobody had to label by hand.
- **Mutation testing for recall.** Take a repo that scans clean, rename one route, break one
  selector, delete one token, and check we report exactly that and nothing else. **Recall is
  still completely unmeasured**, and a false `connected` is invisible by construction — this
  is the only thing that reaches it.

## What the README gets, and what it does not

Only what this phase actually measures.

✅ **Earned by the work:**
- *"Scanned N public Django repositories · M symbols · 0 crashes"*
- *"Precision measured on X repos by hand-checking every finding in a sample: Y%"*
- *"On Z repos with usable history, W% of `unused` findings were later deleted by their own
  maintainers"* — the strongest sentence available to this category, and nobody publishes one
- A **merged PR link** or two, for a finding we fixed
- The shapes that broke it, and what changed as a result. A tool that lists what it got wrong
  and fixed is more credible than one that lists only wins.

❌ **Not earned, and not written:**
- Named repos beside their findings
- "Universal" — that word waits for a second server adapter
- Any per-repo screenshot that is not the fictional demo

## Everything else that belongs in this phase

1. **Live-run the runtime probe.** Built and unit-tested; it has never driven a real page. It
   must work on the reference project before it goes near anyone else's.
2. **`ServerAdapter` seam**, Django as sole implementation. Needed before a second adapter
   and safest done while 600 tests are green and nothing else is moving.
3. **Stripe, static half.** Cheap, unclaimed, and the one transport where a miss is money.
4. **Celery.** Same technique, known outage class.
5. **FastAPI adapter.** Proves the seam from step 2 is real, at the lowest possible cost.
6. **The public-repo run**, gates 1–4 above, ten to fifteen repos.
7. **README rewrite**, using only what steps 1–6 measured.

**A harness is a prerequisite, not an afterthought.** Cloning, scanning and diffing fifteen
repos by hand is a day that produces no reusable artefact. `OTHER/corpus/` with a repo list,
a clone-and-scan script and a results table costs a couple of hours and turns every future
adapter into a re-run rather than a fresh day of work.

Then, and only then: the corpus proper, Rails or Laravel, Redis and WebSockets, and the
clickable page.

## The risks worth naming now

- **Most public Django repos will not install.** That is why static mode exists, and gate 2
  gets harder because ground truth is harder — hand-counting is the fallback.
- **Precision will be lower than 98.3%.** That number was earned by nine extractor fixes
  against one codebase's habits. A new codebase has new habits. **Expect a drop and publish
  the honest figure**; a number that only ever goes up is a number nobody should believe.
- **Fixing a false positive for repo A can regress repo B.** The fixture suite is the guard,
  and every fix from this phase needs a regression test built from the shape that caused it.
- **Time.** Fifteen repos at ~30 seconds a scan is nothing; fifteen repos of hand-checking
  twenty findings each is 300 judgements. That is the real cost, and it is the part that
  cannot be automated away — it is also exactly the work that produced every improvement so
  far.
