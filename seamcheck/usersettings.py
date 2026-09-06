"""What a person decided once, for every project they scan on this machine.

`SEAMCHECK_CONFIG` belongs to a PROJECT: it names that repository's URLconf, its
templates, its static roots, and it means nothing on the next one. The public phone link
is not that kind of setting. It is a decision about this machine and the person sitting
at it - "my phone is not on this wifi, and I would rather have a link that works" - and
it should survive moving to the next repository.

It is also the one thing in seamcheck that leaves the machine, which is why it is stored
rather than defaulted. A default that published every scan would be a different tool: on
a consultant's laptop, `seamcheck map` on a client's private codebase would put a
readable report on the public internet without anybody choosing that. So the answer is
opt in ONCE - `seamcheck config --tunnel always` - and after that every run has a link
that works from a train.

The file is JSON rather than TOML because the supported floor is Python 3.10 and
`tomllib` arrived in 3.11; a config format is not worth a dependency or a version bump.
"""
from __future__ import annotations

import json
import os
import pathlib

# The values a person can store. Anything else is treated as unset: "maybe" is not a
# decision to publish somebody's codebase.
ALWAYS, NEVER = "always", "never"
_YES = {ALWAYS, "1", "true", "yes", "on"}
_NO = {NEVER, "0", "false", "no", "off"}

_ENV_VAR = "SEAMCHECK_TUNNEL"


def settings_path(env=None) -> pathlib.Path:
    """Where this machine's settings live. XDG first, the home directory after it."""
    env = os.environ if env is None else env
    base = env.get("XDG_CONFIG_HOME") or os.path.join(
        env.get("HOME") or os.path.expanduser("~"), ".config")
    return pathlib.Path(base) / "seamcheck" / "settings.json"


def read(env=None) -> dict:
    """The stored settings, or nothing at all.

    A file that cannot be read is the same as no file. Half a JSON document - a laptop
    that slept mid-write, an editor that saved badly - is not a reason for `seamcheck
    map` to stop working, and the cost of ignoring it is one setting, not a scan.
    """
    path = settings_path(env)
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def write(key: str, value: str, env=None) -> pathlib.Path:
    """Store one setting and answer with the file it went into.

    The path is returned rather than printed so the caller can show it: a setting a
    person cannot find is one they cannot undo.
    """
    path = settings_path(env)
    path.parent.mkdir(parents=True, exist_ok=True)
    settings = read(env)
    settings[key] = value
    path.write_text(json.dumps(settings, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")
    return path


def wants_tunnel(flag: bool = False, local_only: bool = False, env=None) -> tuple[bool, str]:
    """Should this run open a public link, and what decided it.

    The ladder, highest first, because each step is a narrower statement of intent than
    the one below it:

    1. `--local-only`, whose entire meaning is "nothing off this machine". A stored
       preference that could overrule it would make the flag a lie.
    2. `--tunnel`, typed for this run.
    3. `SEAMCHECK_TUNNEL`, for a shell where the answer differs from this machine's usual
       one: CI, a customer's laptop, a screen share.
    4. The machine's own setting.
    5. Nothing leaves the machine.

    The reason travels with the answer so `seamcheck config` can show where it came from.
    Detection that cannot explain itself is the thing this project keeps refusing to ship.
    """
    env = os.environ if env is None else env
    if local_only:
        return False, "--local-only"
    if flag:
        return True, "--tunnel"
    from_env = (env.get(_ENV_VAR) or "").strip().lower()
    if from_env in _YES:
        return True, f"{_ENV_VAR}={from_env}"
    if from_env in _NO:
        return False, f"{_ENV_VAR}={from_env}"
    stored = str(read(env).get("tunnel", "")).strip().lower()
    where = settings_path(env)
    if stored in _YES:
        return True, f"tunnel: {stored} in {where}"
    if stored in _NO:
        return False, f"tunnel: {stored} in {where}"
    return False, f"not set ({where})"
