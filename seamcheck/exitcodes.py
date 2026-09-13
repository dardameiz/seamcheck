"""What the process says to CI, in one place.

These were spread across two engines and disagreed: the same failure exited 2 through
`seamcheck` and 1 through `manage.py seamcheck`, and the documented "2 if no baseline" only
existed on the `--since` branch. A CI job reads nothing but this number, so it is worth a
module of its own.
"""

EXIT_CLEAN = 0
EXIT_FINDINGS = 1
EXIT_NO_BASELINE = 2
EXIT_USAGE = 3          # the command was wrong: unknown flag, bad enum value
EXIT_ENVIRONMENT = 4    # the machine was wrong: no adapter, missing import, no node

# The one string api.check() uses to say it had nothing to compare against.
NO_BASELINE = "No baseline snapshot stored"
# `explain` answers in prose, so its failure has no envelope to carry a code - the
# sentence IS the signal, and three places already matched on it by hand. Named here
# so the doors can exit on it rather than printing a failure and reporting success.
UNKNOWN_SYMBOL = "No symbol with id"


# Where each envelope.ERRORS code lands on the process-exit ladder, for `findings`/
# `symbols`/`diff` (see envelope_exit_code below). `bad_argument`/`too_large` are the
# caller typing something wrong - EXIT_USAGE, the same code `check --since <bad ref>`
# already uses. `no_git` is `queries.diff`'s own version of "the ref could not be
# resolved" (or this is not a git repository at all) - the same command-is-wrong
# question `check --since` answers with EXIT_USAGE for an unresolvable ref, so this
# must not be the weaker code. `no_baseline`/`stale_snapshot` both mean "nothing usable
# to diff against yet" - the same soft, "run scan again" answer `check --since`
# reports as EXIT_NO_BASELINE, which docs/ci.md's own CI recipe reads as "not a
# failure". `no_adapter`/`missing_dependency` are the MACHINE being wrong, not the
# command - EXIT_ENVIRONMENT, matching every other "nothing here this knows how to
# read" / "a dependency is missing" site in both CLI doors. `unknown_symbol` is not
# reachable through any of the three today (only `explain`/`triage` look a single
# symbol up, and neither returns an envelope) but is mapped for completeness, as a
# bad argument, so a future caller of it is not left with an unmapped code.
_ENVELOPE_EXIT_CODES: dict[str, int] = {
    "unknown_symbol": EXIT_USAGE,
    "no_baseline": EXIT_NO_BASELINE,
    "no_adapter": EXIT_ENVIRONMENT,
    "bad_argument": EXIT_USAGE,
    "missing_dependency": EXIT_ENVIRONMENT,
    "no_git": EXIT_USAGE,
    "too_large": EXIT_USAGE,
    "stale_snapshot": EXIT_NO_BASELINE,
    # "--scope push" with no upstream configured: the command cannot be answered as
    # asked, same class as an unresolvable --since ref.
    "no_upstream": EXIT_USAGE,
}


def envelope_exit_code(out: dict) -> int:
    """The exit code an envelope (`envelope.answer()`/`envelope.failure()`) earns.

    `findings`/`symbols`/`diff` used to print a correct `{"ok": false, "error": ...}`
    body and unconditionally exit 0 on both CLI doors - `check`'s own exit codes
    (Tasks 1-2) were wired through `gate_code()` below, but the newer commands built on
    top of the envelope never closed the loop back to the one signal a script or agent
    actually checks first (`$?`). `out["ok"] is False` with no recognised `error.code`
    (should never happen - `envelope.failure()` refuses to construct one - but this must
    never raise out of an exit-code lookup) still earns `EXIT_USAGE`: an envelope this
    function cannot classify is closer to "the command was wrong" than to a clean exit.
    """
    if out.get("ok"):
        return EXIT_CLEAN
    code = (out.get("error") or {}).get("code")
    return _ENVELOPE_EXIT_CODES.get(code, EXIT_USAGE)


def _scope_exit_code(out: dict) -> int:
    """`envelope_exit_code`, plus the one thing "--scope" alone needs: `ok=True` on its
    own only means "the question was answerable", not "nothing was found" - `symbols`/
    `findings`/`diff` are unopinionated lookups where that distinction does not exist,
    but "--scope" doubles as a gate for whoever wants one (a CI job, or an agent scripting
    its own build step), so a real finding in a touched page must earn EXIT_FINDINGS
    rather than the EXIT_CLEAN every other envelope-returning query would give it.
    """
    if not out.get("ok"):
        return envelope_exit_code(out)
    pages = (out.get("data") or {}).get("pages") or {}
    if any(page.get("findings") for page in pages.values()):
        return EXIT_FINDINGS
    return EXIT_CLEAN


def gate_code(outcome: dict, comparing: bool = False) -> int:
    """0 clean, 1 findings, 2 no baseline - but 2 only for a run that asked to compare.

    A bare `check` reports on the CURRENT scan: `outcome["passed"]` already says whether
    it has blocking findings, independent of whether a snapshot exists to diff against -
    so a bare check with no baseline still fails on real findings (1) and still passes
    when there are none (0). It never asked "what changed", so a missing baseline is not
    its problem to report.

    `check --since REF` asked exactly that question, and REF having no stored snapshot
    means it cannot be answered at all - that is genuinely a different outcome from "no
    findings", so `comparing=True` keeps the three-way ladder CI is promised: 2 when there
    is nothing to diff against, else 1 or 0 by the diff. Pass `comparing=True` only from a
    `--since` call site.
    """
    if comparing and str(outcome.get("message", "")).startswith(NO_BASELINE):
        return EXIT_NO_BASELINE
    return EXIT_CLEAN if outcome.get("passed") else EXIT_FINDINGS
