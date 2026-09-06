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


def gate_code(outcome: dict) -> int:
    """0 clean, 1 findings, 2 no baseline - the same answer on every path."""
    if str(outcome.get("message", "")).startswith(NO_BASELINE):
        return EXIT_NO_BASELINE
    return EXIT_CLEAN if outcome.get("passed") else EXIT_FINDINGS
