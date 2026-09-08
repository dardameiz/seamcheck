"""Whether a newer seamcheck exists on PyPI than the one running - the one network call
this package makes, and the only reason it makes any.

**This is a deliberate, narrow exception to "seamcheck makes no network call."** Every
other claim of that shape in this repository (README, SECURITY.md, docs/reporting.md, the
`share` command, the map's own "Send a report" panel) is about the SCAN - your file paths,
symbol names, repository identity - never leaving the machine without you pressing a
button. That promise is untouched: this module sends nothing about the project it is
running in. It asks PyPI's public JSON API for the current `seamcheck` package version,
the same unauthenticated GET a browser tab pointed at
https://pypi.org/pypi/seamcheck/json would make, and reads one field back.

Why ship it anyway, given the cost of that exception: a tool a person installed once and
now runs from muscle memory has no other way to learn a fix exists. `seamcheck --version`
already answers the question - it always did - but only for someone who thinks to ask it,
and the whole reason to check is that they do not know there is anything to ask about.

Off switch: `SEAMCHECK_NO_UPDATE_CHECK=1`. Also skipped automatically when `CI` is set
(the common convention every package manager already follows) and whenever the installed
version cannot be read at all - a source checkout with no `pip install` behind it has
nothing to compare a PyPI number against.

Cost control: at most one real request per `_CHECK_INTERVAL`, cached to disk next to the
scan cache (`scancache._cache_root`'s ladder - XDG_CACHE_HOME, then `~/.cache`). A slow or
unreachable network must look exactly like "no update" to the caller - never a delay, and
never an exception - so `notice()` catches everything the fetch can raise and a short
`_TIMEOUT` bounds how long a cold cache is allowed to make a command wait.
"""

from __future__ import annotations

import json
import os
import pathlib
import time
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _installed_version

_PYPI_URL = "https://pypi.org/pypi/seamcheck/json"
_CHECK_INTERVAL = 24 * 60 * 60  # seconds between real PyPI requests
_TIMEOUT = 2.0  # seconds - a cold cache must not turn into a slow command
_ENV_OFF = "SEAMCHECK_NO_UPDATE_CHECK"


def _cache_path(env: dict | None = None) -> pathlib.Path:
    """Where the last PyPI answer is remembered. Same ladder as scancache._cache_root -
    one convention for one machine's seamcheck state, not two."""
    env = os.environ if env is None else env
    base = env.get("XDG_CACHE_HOME") or os.path.join(
        env.get("HOME") or os.path.expanduser("~"), ".cache")
    return pathlib.Path(base) / "seamcheck" / "update_check.json"


def _read_cache(path: pathlib.Path) -> dict:
    """A cache that cannot be read is the same as no cache - not a reason to fail."""
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _write_cache(path: pathlib.Path, data: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")
    except OSError:
        pass  # a machine where this cannot be written is one where checking every run
        # is not appreciably worse - it is not a reason to fail the command over.


def _fetch_latest(timeout: float = _TIMEOUT) -> str | None:
    """The version PyPI currently serves for `seamcheck`, or None if anything went wrong.

    Imported here, not at module load: every `import seamcheck.cli` and every MCP tool
    call would otherwise pull in urllib.request whether or not a check ever runs.
    """
    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen(_PYPI_URL, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        latest = payload.get("info", {}).get("version")
        return latest if isinstance(latest, str) else None
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return None


def _parse(v: str) -> tuple[int, ...]:
    """`"0.13.0"` -> `(0, 13, 0)`, good enough for the plain X.Y.Z this project tags.

    Only the LEADING digits of each component count, so a pre-release suffix
    (`"0rc1"`) reads as its release number (`0`) rather than raising - a `0.14.0rc1`
    compares equal to, never greater than, the `0.14.0` it precedes.
    """
    parts = []
    for piece in v.split(".")[:3]:
        digits = ""
        for ch in piece:
            if not ch.isdigit():
                break
            digits += ch
        parts.append(int(digits) if digits else 0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts)


def enabled(env: dict | None = None) -> bool:
    """Off switch, checked before anything else touches the network or the cache."""
    env = os.environ if env is None else env
    if (env.get(_ENV_OFF) or "").strip().lower() in ("1", "true", "yes", "on"):
        return False
    return not env.get("CI")


def latest_version(env: dict | None = None, fetch=None, now: float | None = None) -> str | None:
    """The newest version on PyPI, refetched at most once per `_CHECK_INTERVAL`.

    `fetch` and `now` are keyword-overridable for the same reason `env` always is here -
    a test drives this without touching the real network or the real clock. `fetch`
    defaults to None rather than to `_fetch_latest` itself: a default bakes in the
    function OBJECT at import time, so a test-suite-wide `monkeypatch.setattr(
    "seamcheck.updatecheck._fetch_latest", ...)` safety net - installed once so no test
    anywhere can reach the real network by omission - would silently miss every caller
    that relies on the default. Resolving the name inside the body instead reads
    whatever `_fetch_latest` is bound to AT CALL TIME.
    """
    fetch = fetch or _fetch_latest
    path = _cache_path(env)
    cache = _read_cache(path)
    clock = time.time() if now is None else now
    checked_at = cache.get("checked_at")
    if isinstance(checked_at, (int, float)) and clock - checked_at < _CHECK_INTERVAL:
        return cache.get("latest")

    latest = fetch()
    if latest is not None:
        _write_cache(path, {"checked_at": clock, "latest": latest})
        return latest
    # The request failed - keep serving the last good answer rather than losing it to
    # one bad network blip; only an empty cache has nothing to fall back to.
    return cache.get("latest")


def notice(env: dict | None = None, fetch=None, now: float | None = None) -> str | None:
    """One message to print when a newer seamcheck exists, or None - never raises.

    `None` covers every reason there is nothing to say: disabled, CI, no installed
    version to compare against, PyPI unreachable, or already current. A caller does not
    need to tell those apart; it only needs to know whether to print a line.
    """
    if not enabled(env):
        return None
    try:
        installed = _installed_version("seamcheck")
    except PackageNotFoundError:  # a source tree with no `pip install` behind it
        return None
    try:
        latest = latest_version(env, fetch=fetch, now=now)
    except Exception:  # noqa: BLE001 - this line is not worth a command failing over
        return None
    if not latest or _parse(latest) <= _parse(installed):
        return None
    return (
        f"seamcheck: a newer version is available ({installed} → {latest})\n"
        "  pip install -U seamcheck   (pipx: pipx upgrade seamcheck)"
    )
