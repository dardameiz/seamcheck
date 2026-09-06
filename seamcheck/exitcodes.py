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
