# Contributing to Seamcheck

## The one rule that matters

**Seamcheck must never claim something is dead without evidence.** A false `unused` costs
a maintainer a deletion, a revert, and their trust in the tool. A missing finding costs
nothing by comparison. When in doubt the answer is `uncertain` with a note naming the
evidence the scan does not have.

Every pull request is read against that rule first.

## Development setup

```bash
pip install -e ".[models,mcp]"
npm install --save-dev acorn postcss     # only needed for the JS/CSS extractors
python manage.py test seamcheck
ruff check seamcheck/
```

Seamcheck is developed inside a host Django project so it is always run against real
code. See [PACKAGING.md](PACKAGING.md).

## Test-driven, and prove the test fails

Every change is written test-first, and a new test is not finished until you have watched
it fail. Disable the line your test protects, re-run, and confirm it goes red. A test that
passes with its own fix removed is asserting nothing — this project has already shipped
two of those and caught them only by checking.

Tests are methods on `django.test.SimpleTestCase` subclasses. A bare `def test_*()`
function is silently skipped by Django's runner, which reports `Ran 0 tests` **and exits
0**.

## Validate against real code, not just fixtures

Fixtures prove the logic; only a real codebase proves the assumptions. Before opening a
PR that touches an extractor or matcher, run it over a real project and compare the result
against an independent count. Every significant bug found in this codebase so far came
from that comparison and not from the unit tests:

- write detection matched only same-statement mutations — 70 writes found instead of 940
- the CSS tokeniser stopped at Tailwind's escapes — 659 false unresolved classes
- the reachability walk read only `import` statements — 1.1% of a Django project's imports

When the two counts disagree, find out which side is wrong before changing anything. An
AST is usually right and the regex baseline usually over-counts (comments, strings).

## Commits

[Conventional Commits](https://www.conventionalcommits.org/): `feat:`, `fix:`, `test:`,
`docs:`, `refactor:`, `chore:`. The prefix drives release tooling and cannot be
retrofitted onto history.

## Code style

- Comments explain a non-obvious **why**, never restate what the next line does.
- One canonical implementation per concern. Before adding a helper, grep for one that
  already exists and extend it. Two copies of a formula drift apart silently.
- Every symbol carries `file`, `line` where known, and the snippet that produced it. No
  classification without the evidence behind it.
- No hardcoded project-specific strings in extractor or classifier code — those come from
  `SEAMCHECK_CONFIG`.

## Reporting a bug

The most useful report is a minimal fixture plus the two numbers that disagree: what
Seamcheck said, and what the codebase actually contains.
