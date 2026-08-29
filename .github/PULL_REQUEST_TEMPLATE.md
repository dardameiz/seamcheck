## What this changes

<!-- One or two sentences. -->

## Evidence

<!-- For an extractor or matcher change, the two numbers that disagreed and which one was
     wrong. Fixtures prove the logic; a real codebase proves the assumptions. -->

## Checklist

- [ ] Test written first, and **watched fail** with the fix removed
- [ ] Tests are `SimpleTestCase` methods (a bare `def test_*()` is silently skipped)
- [ ] `ruff check signal_map/` clean
- [ ] Run against a real project, not only fixtures
- [ ] Nothing new is claimed `unused` without both sides of the contract observable
- [ ] Conventional Commit prefix (`feat:`, `fix:`, `test:`, `docs:`, `refactor:`, `chore:`)
