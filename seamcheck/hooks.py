"""Git hooks that surface `--scope`'s answer at the exact moment it matters.

Plain `pre-commit` / `pre-push` shell scripts - not Claude-specific, not seamcheck-CLI-
specific in their trigger - so they fire the same way whether a human typed `git commit`,
an agent drove one through a shell tool, or any other program shelled out to git. They
call back into THIS module (`<sys.executable> <path/to/hooks.py> <commit|push>` - see
`_hook_script`, not a bare `python3 -m seamcheck.hooks`) rather than the
main `seamcheck` CLI, because the main CLI's `--scope` is JSON-only (the agent/CI
contract every other query in `queries.py` already keeps) and a human staring at a raw
JSON dump after every `git commit` is not what "a quick snapshot to imagine what's
happening" was asking for - this module owns the short, human-readable rendering instead.

Advisory only, always. `--scope` itself has a real exit code (EXIT_FINDINGS when a
touched page has one) for whoever wants a hard gate - a CI job can call it directly. These
hooks never propagate that: the owner-stated requirement was "no hard gate, only
recommendations", so both `run_precommit`/`run_prepush` always return 0, whatever they
found or failed to find.
"""

from __future__ import annotations

import contextlib
import os
import stat
import sys


def _hook_script(mode: str) -> str:
    """The literal shell script `install_hooks()` writes into `.git/hooks/<name>`.

    Runs `sys.executable` (the interpreter that ran `install-hooks`, so it is
    guaranteed to have seamcheck importable) directly against this file's own absolute
    path - never a bare `python3 -m seamcheck.hooks`, which has two independent failure
    modes: `python3` can resolve to a completely different interpreter that never had
    seamcheck installed, and `-m` prepends the CURRENT WORKING DIRECTORY to `sys.path`
    - so a hook that fires from inside a repo that happens to have a directory literally
    named `seamcheck` (this tool's own reference project keeps a gitignored clone at
    exactly that path) imports THAT directory as a namespace package instead of the real
    one, and every call inside the hook silently does nothing.
    """
    verb = "commit" if mode == "commit" else "push"
    python = sys.executable
    script_path = os.path.abspath(__file__)
    return (
        "#!/bin/sh\n"
        f"# Installed by `seamcheck --install-hooks`. Advisory only - never blocks the {verb}.\n"
        f'"{python}" "{script_path}" {mode}\n'
        "exit 0\n"
    )


def _summary(result: dict) -> str:
    """One short block: what was touched, and what seamcheck currently says about it.

    Never raises - a hook that crashes because a scan hit an edge case is worse than a
    hook that silently has nothing to say, because the crash is what a person sees
    instead of their own `git commit` output.
    """
    pages = result.get("pages") or {}
    if not pages:
        changed = result.get("changed_files") or []
        if not changed:
            return "seamcheck: nothing staged/unpushed to check."
        return f"seamcheck: {len(changed)} file(s) changed, none map to a known page."

    lines = ["seamcheck - what you're touching:"]
    for page, info in sorted(pages.items()):
        findings = info.get("findings") or []
        features = info.get("features") or []
        where = f" ({', '.join(features)})" if features else ""
        if findings:
            lines.append(f"  {page}{where}: {len(findings)} unresolved/unused finding(s)")
            for finding in findings[:5]:
                lines.append(f"    - {finding['kind']} {finding['label']} ({finding['file']})")
            if len(findings) > 5:
                lines.append(f"    ...and {len(findings) - 5} more")
        else:
            lines.append(f"  {page}{where}: clean")
    return "\n".join(lines)


def _run(repo_root: str, mode: str) -> str:
    """The text this hook prints, for `mode` ("commit" or "push"). Never raises."""
    try:
        from seamcheck import api
        from seamcheck.cli import setup_django_if_any

        # A git hook runs in a bare subprocess - none of the bootstrap `seamcheck scope`
        # itself gets via `_dispatch`. Without this, a Django project is read from
        # source instead of imported, and misses every route Django builds at runtime
        # (the admin's) - a real, silent gap this had on its first real run.
        setup_django_if_any()
        result = api.scoped_findings(repo_root, mode)
        return _summary(result)
    except Exception as error:  # noqa: BLE001 - a hook must never crash a commit/push
        return f"seamcheck: could not check this {mode} ({error})."


def run_precommit(repo_root: str = ".") -> int:
    print(_run(repo_root, "commit"))
    return 0


def run_prepush(repo_root: str = ".") -> int:
    print(_run(repo_root, "push"))
    return 0


def install_hooks(repo_root: str = ".") -> str:
    """Write pre-commit and pre-push into `.git/hooks/`, overwriting only what this
    function itself wrote before (a marker line, checked before overwriting anything -
    a hand-written hook already there is left alone and named in the returned message,
    never silently replaced).
    """
    marker = "# Installed by `seamcheck --install-hooks`."
    hooks_dir = os.path.join(repo_root, ".git", "hooks")
    if not os.path.isdir(hooks_dir):
        return (
            f"{hooks_dir} does not exist - is {repo_root!r} a git repository (not a "
            "worktree or submodule, whose hooks dir lives elsewhere)?"
        )

    written, skipped = [], []
    for name, mode in (("pre-commit", "commit"), ("pre-push", "push")):
        path = os.path.join(hooks_dir, name)
        if os.path.exists(path):
            existing = ""
            with contextlib.suppress(OSError), open(path, encoding="utf-8") as handle:
                existing = handle.read()
            if marker not in existing:
                skipped.append(name)
                continue
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(_hook_script(mode))
        mode = os.stat(path).st_mode
        os.chmod(path, mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
        written.append(name)

    lines = []
    if written:
        lines.append(f"Installed: {', '.join(written)}.")
    if skipped:
        lines.append(
            f"Left alone (already exists, not one of ours): {', '.join(skipped)}. "
            "Remove it first if you want seamcheck's version."
        )
    return "\n".join(lines) if lines else "Nothing to do."


if __name__ == "__main__":
    _mode = sys.argv[1] if len(sys.argv) > 1 else "commit"
    if _mode == "push":
        raise SystemExit(run_prepush("."))
    raise SystemExit(run_precommit("."))
