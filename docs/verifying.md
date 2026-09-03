# Checking this tool

A tool that reports precision should say how to measure it, and hand you the same
instrument it used. This is that instrument, the protocol, and the four ways a careful
person gets the answer wrong — every one of them made here, on this project, while
grading its output.

## What counts as a claim

Only **`unresolved`** and **`unused`**.

Those two assert something about your code: something is missing, or something is dead.
A wrong one sends a person to change working code, which is the only kind of wrong that
costs anything.

`connected` is evidenced by construction — the evidence is attached to the symbol.
`uncertain` asserts nothing at all; it is the scan saying it cannot see the evidence
either way. **Counting `uncertain` rows as findings puts the tool's honesty in the
denominator**, and it is the first mistake anyone grading a large output makes. On one
surface of the reference project, 393 of 712 rows were `uncertain`: graded as claims, the
tool scored 13.2%; over the 298 rows that were actually claims, it scored 24.5%.

## The protocol

```bash
python tools/precision.py --sample <repo> 25     # 25 unlabelled claims, with evidence
# ...read the code for each one, then write tools/labels/<repo>.json
python tools/precision.py                        # score every labelled repo
```

A label is a verdict and a reason:

```json
{"css_selector:class:schedule": {
   "verdict": "false",
   "why": "className=\"schedule\" at ScheduleDialog.jsx:196 - JSX className not scanned"}}
```

**Write the reason, always.** The verdict alone is a number; the reason is what turns
twenty labels into one fix. Every extractor change worth making on this project came out
of a `why` field, not out of a percentage.

The report is three tables, and the last two decide what to work on:

- **per repository** — is this one good?
- **per stack** — Django is one adapter of six, and an average across them describes
  none of them. The adapter with the most attention carries the mean, and every stack
  behind it looks fine from there.
- **per lens** — "which repository is noisy" and "which extractor is noisy" are different
  questions, and only the second one says what to change.

## The four traps

**1. A token index under-reports hyphenated names.** Splitting a codebase into identifier
tokens and looking each label up is fast and wrong: `achievement-category` never appears
as its own token in a file containing `achievement-category-title`, because the tokeniser
takes the longest run. Measured while grading this project: **5 of 52 verdicts were wrong
this way**, every one of them a live class graded dead. Plain substring search is the
correct method — and then compare **whole tokens**, because the same trap runs in the
other direction: a substring search says `obtained-buttons` is in the template when what
is there is `obtained-buttons-modal`, and a true finding gets graded false.

**2. The corpus filter decides the answer.** Two directories will lie to you:

- `staticfiles/`, `build/`, `dist/` — what a build step *copied*. A name that exists only
  there exists only as an artefact.
- the tool's own committed output — a map or a report checked into the repository
  contains every symbol it found, so grepping it "proves" everything.

Counting either as usage makes every finding look connected. A first sample graded here
scored **10 out of 10 wrong** from this alone.

**3. A mention is not a use.** The evidence for "this is used" has to be a *read*.
`data-base-achieved` is rendered by the template and set by `dataset.baseAchieved = count`
in two places, and read by nothing — no `getAttribute`, no `[data-base-achieved]`
selector, no stylesheet rule. Graded false on "referenced from 2 files"; it is a true
finding. Two writes and a duplicate declaration are three mentions and zero uses.

**4. A label goes stale, and stale labels flatter nobody.** A verdict recorded against an
older build describes that build. Re-checking this project's own label files a few months
on, **four of the causes they recorded had already been fixed** — JSX `className`,
CSS-module member access, Express mount prefixes, `SET NX` read as a write — so the
recorded reasons still read as live defects while the tool no longer makes those claims.
`score()` refuses to count a claim that has disappeared, which keeps the number honest in
one direction; the other direction is on you. **Re-verify a sample before quoting a
precision figure**, and prefer fresh labels to a large old file.

## Three traps in the script you write to check it

Verification is usually a script, and a script fails in ways a person does not.

**A failed search and a true negative look identical.** `grep --include=*.js` under zsh
with no match errors *before* grep runs, prints to stderr, and returns nothing — which
reads exactly like "this class is referenced nowhere". A live stylesheet was declared
orphaned that way. **Anything that self-verifies must assert the search RAN**: count the
files it opened, and fail loudly when that count is zero.

**A grep for a name matches the comment you just wrote.** Three removal scripts aborted on
`assert 'name' not in source` after a *correct* edit, because the edit left a comment
saying which name had been removed. Assert on the code form — the call, the selector, the
attribute — or strip comments before asserting.

**A brace count is not a syntax check.** A regex that prunes CSS rules can balance 59
braces against 59 and still produce `Unexpected }`, because the rule it cut sat inside an
`@media` block. Anything that edits a language must re-parse its own output before
writing it.

## What to do with a false claim

File the *cause*, not the instance. One recorded reason is worth more than a hundred
labels, because a cause is fixable and an instance is not:

- `docs/FINDINGS-FROM-POINTLESSBUTTON.md` is what that looks like when the project being
  graded is private.
- A cause that reproduces in ten lines belongs in `seamcheck/tests/` as a fixture, and
  then it can never come back quietly.

And the rule that governs every fix: **judge it twice.** How much noise does it remove,
and does any true finding disappear with it? A change that improves precision by
suppressing real findings has made the tool worse, and the ratio will not say so.
