"""Keep the host project's start-up noise out of seamcheck's output.

Scanning a project means importing it, and importing a real Django project runs its
AppConfig.ready(), its monkey-patches and whatever warnings its dependencies feel like
raising. On the project this was built against that is four lines of someone else's
logging and a RuntimeWarning about database access, printed twice, before seamcheck has
said anything at all - and none of it is about connectivity.

The suppression is a level threshold, not a mute button: WARNING and below are dropped,
ERROR and CRITICAL still get through. A tidy terminal is not worth hiding a real failure,
and `--verbose` puts every line back for the times when the import itself is the problem.
"""

from __future__ import annotations

import contextlib
import logging
import warnings


@contextlib.contextmanager
def quiet(enabled: bool = True):
    """Silence the host project's warnings and sub-ERROR logging inside this block."""
    if not enabled:
        yield
        return
    # `manager.disable` is the live value of the last logging.disable() call; reading it
    # first means a caller who had already set one gets theirs back, not a hardcoded 0.
    previous = logging.root.manager.disable
    logging.disable(logging.WARNING)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            yield
    finally:
        logging.disable(previous)
