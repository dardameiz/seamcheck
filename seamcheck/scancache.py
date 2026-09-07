"""One scan, remembered, so asking a second question is cheap.

Measured before this existed: every command and every MCP tool called `api.scan` afresh, so
five explanations on the reference project were five 90-second scans - and a mistyped symbol
id cost the same 88.5 seconds as a correct one, to be told the id was wrong.

The key is a hash of the file tree's SHAPE - every input file's relative path, size and
mtime - plus the tool version, the Django settings module name, and `SEAMCHECK_CONFIG` AS
WRITTEN (`autoconfig.declared_config()`). Content is deliberately left out: hashing a
100M-line repository to decide whether to scan it would cost more than the scan. The
EFFECTIVE config (`autoconfig.effective()`, detection merged with what was declared) is
left out too, but not for the same reason - `effective()`'s ~7-second auto-detection walk
would genuinely eat most of the win, and everything it adds ON TOP of `declared_config()`
is derived from files this walk already hashes (the URLconf, the templates directory, ...),
so a real change there already moves the key through the file walk alone.
`declared_config()` itself is a single settings-attribute read - about 89us, measured on
the reference project - and it is NOT redundant with the file walk: a project that builds
`SEAMCHECK_CONFIG` from an environment variable at settings-import time (`os.environ.get(
...)` inside settings.py, an entirely ordinary pattern) can change what a scan means with
NO file on disk moving at all. Proved cross-process, the way it would actually bite: two
fresh interpreters, same files, `SEAMCHECK_CONFIG` built from a different env var each time
- before this hashed the declared config, the second process silently got the first one's
graph back, wrong URLconf and all, with nothing anywhere saying so. The settings module
NAME stays in the key too - nearly free, and it still tells apart two runs that would
otherwise build different graphs from the same files even when neither has set
`SEAMCHECK_CONFIG` at all.

What this guard actually covers: a cache entry is trusted only when every input file's mtime
is STRICTLY OLDER than the entry's own timestamp, which is read from the wall clock ONCE,
before the scan that produced the entry runs - not after. A scan takes on the order of a
minute on a real project; stamping the entry after it returns would mean any file saved
during that minute was already baked into the graph and would still be judged OLDER than the
entry that baked it in, so the stale graph would be served as fresh for as long as nothing
else changed. Reading the clock first means a file saved mid-scan is, correctly, newer than
the entry, and forces a rescan the next time anyone asks.

What it does NOT cover: this is an mtime check, not a content hash, so anything that changes
a file's bytes without advancing its mtime defeats it completely - an mtime-preserving
restore (`rsync -a`, `tar xpf`, a Docker build context that copies files with their original
timestamps) or a backwards step of the system clock both produce a tree that looks, by every
signal this module reads, exactly as old as it did before. There is no fix for this short of
hashing every byte, which is the cost this module exists to avoid paying. A caller that knows
its tree was touched by one of these - a fresh checkout, a restored backup, a clock that just
got corrected - should pass `refresh=True` to `cached_scan` from library code. From a
terminal that is `--refresh`, on `symbols`, `findings` and `diff` (wired in cli.py and the
management command, the two places that own that vocabulary) - the actual escape a person can
type, where the library keyword argument alone was not.

Two layers: a process memo, bounded to the `_MEMO_LIMIT` most recently used graphs so a
long-lived MCP session answering questions across many repositories cannot grow without
bound, and a file under this machine's own cache directory (`_cache_root` - never inside the
scanned repository, which this tool exists to scan, not to write into), for the next command
in the same shell. Both layers are judged by the same freshness rule, so neither can serve a
stale answer any differently from the other.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import pathlib
import time
from collections import OrderedDict

from seamcheck.adapters.discovery import SKIP_DIRS
from seamcheck.autoconfig import EXCLUDED_DIRS, declared_config
from seamcheck.graph import Graph, graph_from_dict, graph_to_dict
from seamcheck.observe import _STORE_DIR
from seamcheck.snapshot import _MAP_FILE, _SCANS_DIR
from seamcheck.trend import _TREND_PATH
from seamcheck.triage import _TRIAGE_FILE

# memo_key -> (graph, cached_at_ns). cached_at_ns is judged against a fresh walk's latest
# input mtime exactly the way a disk entry's own mtime is - see `cached_scan`. An
# OrderedDict so `_memo_put` can move an entry to the end on every use and evict from the
# front - a plain dict has no cheap way to know which entry is least recently used.
_MEMO: OrderedDict[str, tuple[Graph, int]] = OrderedDict()

# Each entry holds a whole graph - about 52,000 symbols, tens of MB, on the reference
# project - for the life of the process. The intended consumer is a long-lived MCP server
# answering questions about many repositories over one session, so nothing bounded this
# before: every repository ever asked about stayed resident until the process exited.
_MEMO_LIMIT = 2

# How many cache files one repository may keep before the oldest (by mtime) is evicted.
# Nothing evicted anything before this existed, so a long-lived repo's cache directory only
# ever grew.
_KEEP_PER_REPO = 3

# Directories whose contents never change what a scan says - derived from the two sets the
# SCAN ITSELF actually honours, not a third, hand-maintained list. `EXCLUDED_DIRS`
# (autoconfig.py) is what `roots.py`'s CSS discovery and autoconfig's own file-finding walk
# skip; `SKIP_DIRS` (adapters/discovery.py) is what `find_js_files` - the walk that supplies
# every scan's "extra" JavaScript - and adapter/manifest detection skip. Between them they
# cover every directory-pruning walk a real scan runs.
#
# This used to be its own set that blanket-skipped every dot-directory and `dist`/`build` by
# name. `dist` and `build` are still here - both sets already name them, because the scanner
# genuinely never reads inside either - but the blanket dot-rule is gone: a project's own
# `js_entry_files` or `templates_root` can point INTO a dot-directory (`.storybook/`,
# `.output/`) on purpose, and a walk that skipped it by convention alone made the cache key
# and the freshness mtime both blind to edits there - a stale graph looked fresh forever.
# `.git` stays excluded because both sets already name it explicitly, not through a rule.
_SCANNER_EXCLUDED = EXCLUDED_DIRS | SKIP_DIRS

# The tool's OWN state, which is not scanner input at all and must never feed the key or
# the freshness check. Each is written by a different command with no idea the others
# exist (`seamcheck scan` writes `_SCANS_DIR`, `_MAP_FILE`, and - via `api._marks(...,
# persist=True)` - any freshly-stale mark's expiry stamp in `_TRIAGE_FILE`; `api.triage`
# writes `_TRIAGE_FILE` too, for the same reason; `seamcheck observe` writes `_STORE_DIR`;
# every map render writes `_TREND_PATH`) - so treating any of them as scan input meant the
# documented "scan, then ask a question" sequence never hit the cache: a write busted the
# very cache entry the next call in the same repo needed.
#
# ONE registry, not five names each with their own exclusion check: `f3f2bff5b` enumerated
# two of these by hand, a third (`_MAP_FILE`) shipped unregistered one commit later, and
# `test_tool_state_writes.py` - which finds every `Path(repo_root) / …` write in this
# package's own source and checks it against THIS tuple, rather than trusting a
# hand-maintained list to stay complete - is what found the other two (`_STORE_DIR`,
# `_TREND_PATH`) already shipping unregistered. A path added here needs no matching code
# change below: `_is_tool_state` iterates the tuple itself.
#
# Matched by RELATIVE PATH, not by directory name - `_TRIAGE_FILE` sits inside a directory
# literally named `seamcheck`, which is this project's own source when this tool scans
# itself, and skipping that whole directory would skip the product it exists to scan; the
# same reasoning keeps every entry here excluded by its own path rather than by skipping
# all of `docs/` or `OTHER/`, either of which may hold plenty the scanner has every reason
# to see.
TOOL_STATE_PATHS = (_SCANS_DIR, _TRIAGE_FILE, _MAP_FILE, _STORE_DIR, _TREND_PATH)

# The html report and the map/console document are the ONE tool-state destination that
# is not a fixed path: `management/commands/seamcheck.py` writes each to
# `SEAMCHECK_CONFIG["report_output"/"map_output"]` when set, and to the default below
# otherwise - so TOOL_STATE_PATHS (a tuple of constants the audit test can read by simple
# membership) cannot name it, and `_is_tool_state` alone cannot decide it without also
# knowing what THIS repo's config says. `resolve_report_output`/`resolve_map_output` are
# the one place that resolution happens, called both by the command that writes there and
# by `_resolved_configured_paths` below for the cache's own exclusion check, so the two
# can never compute two different answers for the same repo.
_REPORT_OUTPUT_FALLBACK = pathlib.Path("docs") / "maps" / "connectivity-report.html"
_MAP_OUTPUT_FALLBACK = pathlib.Path("docs") / "maps" / "connectivity-map.html"

# Exported alongside TOOL_STATE_PATHS for test_tool_state_writes.py: that audit reads
# source, not a running repo's config, so it cannot resolve what SEAMCHECK_CONFIG would
# say - the DEFAULT is the one fixed thing it CAN check a write against, on top of the
# static core, the same "static core, plus the resolved/default configurable destination
# on top" shape `_resolved_configured_paths` uses for the real, runtime exclusion. A
# write matching only this (not TOOL_STATE_PATHS) is verified default-covered; whether a
# repo's own CONFIGURED override is also excluded is what test_scancache.py's dedicated
# configured-destination tests check, not this static audit.
CONFIGURABLE_TOOL_STATE_DEFAULTS = (_REPORT_OUTPUT_FALLBACK, _MAP_OUTPUT_FALLBACK)


def resolve_report_output(repo_root: str) -> pathlib.Path:
    """Where the html report goes: `SEAMCHECK_CONFIG["report_output"]`, or its default."""
    declared = declared_config()
    return pathlib.Path(repo_root) / (declared.get("report_output") or _REPORT_OUTPUT_FALLBACK)


def resolve_map_output(repo_root: str) -> pathlib.Path:
    """Where the map/console document goes: `SEAMCHECK_CONFIG["map_output"]`, or its
    default."""
    declared = declared_config()
    return pathlib.Path(repo_root) / (declared.get("map_output") or _MAP_OUTPUT_FALLBACK)


def _resolved_configured_paths(repo_root: str) -> tuple[pathlib.Path, ...]:
    """The tool-state paths that depend on THIS repo's own config, resolved fresh on
    every call - cheap, because `autoconfig.declared_config()` is a single
    settings-attribute read, not `autoconfig.effective()`'s ~7-second auto-detection walk
    (see the module docstring for why the scan key already refuses to pay that cost;
    reading the declared config on every `_scan_tree` call, cache hit or miss, must not
    reintroduce it under a different name).

    Filtered to destinations that resolve INSIDE `repo_root`: one that does not (an
    absolute override, a value equivalent to `--out` pointed elsewhere) never entered
    the walk this exists to guard, and excluding it by a path that escapes the root is
    how an exclusion starts matching things it should not.
    """
    root = pathlib.Path(repo_root).resolve()
    resolved = []
    for candidate in (resolve_report_output(repo_root), resolve_map_output(repo_root)):
        try:
            resolved.append(candidate.resolve().relative_to(root))
        except ValueError:
            continue  # outside repo_root - not a repo-root write, out of scope
    return tuple(resolved)


def _is_tool_state(relative_parts: tuple[str, ...], configured: tuple = ()) -> bool:
    """True when `relative_parts` names a registered path exactly, or a location under
    one - `_SCANS_DIR` is a directory holding one file per snapshot, so every file in it
    must match this without being named individually.

    `configured` (from `_resolved_configured_paths`, computed once per `_scan_tree` call
    - never per file) is matched the same way and by the same rule: exact resolved
    relative path, never by name, prefix or substring - a user's own `report.html`
    living somewhere else must not be excluded just because the tool could have written
    one there. Defaults to empty so the audit test can call this against the static
    `TOOL_STATE_PATHS` core alone.
    """
    return any(relative_parts[:len(state.parts)] == state.parts
              for state in TOOL_STATE_PATHS + configured)


def _version() -> str:
    """The installed version, or "0" outside an install (see `cli.version_line`)."""
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("seamcheck")
    except PackageNotFoundError:  # running straight from a source tree
        return "0"


def _memo_put(memo_key: str, value: tuple[Graph, int]) -> None:
    """Record the most-recently-used entry, evicting the least-recently-used past the cap."""
    _MEMO[memo_key] = value
    _MEMO.move_to_end(memo_key)
    while len(_MEMO) > _MEMO_LIMIT:
        _MEMO.popitem(last=False)


def _cache_root(env: dict | None = None) -> pathlib.Path:
    """Where this machine keeps every repository's cache - never inside a scanned tree.

    XDG first, `~/.cache` after it - the same ladder `usersettings.settings_path` climbs for
    `~/.config`, so the one convention a person already knows for settings applies unchanged
    to cache.
    """
    env = os.environ if env is None else env
    base = env.get("XDG_CACHE_HOME") or os.path.join(
        env.get("HOME") or os.path.expanduser("~"), ".cache")
    return pathlib.Path(base) / "seamcheck"


def _repo_cache_dir(repo_root: str, env: dict | None = None) -> pathlib.Path:
    """One repository's own slot, named from its real path rather than mirroring it.

    A hash rather than a mirrored path so two checkouts of the same project - a worktree, a
    second clone, a CI runner - never collide, and a checkout that moves or is renamed just
    misses its old entries instead of silently reading someone else's. `repo_root` is
    expected to already be resolved (every public function below resolves it once at the
    top), but resolving again here is idempotent and costs nothing.
    """
    real = str(pathlib.Path(repo_root).resolve())
    digest = hashlib.sha256(real.encode()).hexdigest()[:16]
    return _cache_root(env) / digest


def _scan_tree(repo_root: str) -> tuple[str, int]:
    """The key (identity) and the latest mtime seen (freshness), from one walk.

    Identity is (version, settings module, declared config, every file's relative path,
    size and mtime) - mtime included so that an edit which does not change a file's size (a
    route renamed to another name the same length) still changes the key, rather than being
    invisible to it. Freshness is judged separately, by the caller, against a cache entry's
    own timestamp; see the module docstring for what that check does and does not catch.
    """
    digest = hashlib.sha256()
    digest.update(_version().encode())
    digest.update(os.environ.get("DJANGO_SETTINGS_MODULE", "").encode())
    # declared_config(), never effective() - see the module docstring for the cost gap and
    # why this alone is enough to close the config-drift gap. sort_keys so two equal dicts
    # never hash differently because of iteration order; default=str so a value this
    # module has no reason to expect JSON-serialises to SOMETHING deterministic rather
    # than raising out of a cache lookup.
    digest.update(json.dumps(declared_config(), sort_keys=True, default=str).encode())
    root = pathlib.Path(repo_root)
    # Computed ONCE per walk, not per file - a config-driven destination reads
    # SEAMCHECK_CONFIG, and doing that for every file in a large tree would be wasteful
    # even though any one read is cheap. See `_resolved_configured_paths`'s own
    # docstring for the cost this must not reintroduce.
    configured = _resolved_configured_paths(repo_root)
    latest_mtime_ns = 0
    for current, directories, files in os.walk(root):
        here = pathlib.Path(current)
        directories[:] = sorted(
            d for d in directories
            if d not in _SCANNER_EXCLUDED
            and not _is_tool_state((here / d).relative_to(root).parts, configured)
        )
        for name in sorted(files):
            path = here / name
            if _is_tool_state(path.relative_to(root).parts, configured):
                # Tool state, not scanner input - see TOOL_STATE_PATHS above for which
                # command writes each one and why a read or a write to it must not look,
                # to the cache, like an edit to the project it just scanned.
                continue
            try:
                info = path.stat()
            except OSError:
                continue
            digest.update(str(path.relative_to(root)).encode())
            digest.update(str(info.st_size).encode())
            digest.update(str(info.st_mtime_ns).encode())
            latest_mtime_ns = max(latest_mtime_ns, info.st_mtime_ns)
    return digest.hexdigest()[:32], latest_mtime_ns


def _path(repo_root: str, key: str, env: dict | None = None) -> pathlib.Path:
    return _repo_cache_dir(repo_root, env) / f"{key}.json"


def _evict_oldest(directory: pathlib.Path, keep: int = _KEEP_PER_REPO) -> None:
    """Keep at most `keep` cache files in this repository's directory, oldest out first."""
    try:
        entries = sorted(directory.glob("*.json"), key=lambda p: p.stat().st_mtime_ns)
    except OSError:
        return
    for stale in entries[:-keep] if len(entries) > keep else []:
        with contextlib.suppress(OSError):
            stale.unlink()


def cached_scan(repo_root: str, *, refresh: bool = False) -> tuple[Graph, dict]:
    """The graph, from memory, from disk, or from a real scan - and which of the three."""
    from seamcheck import api

    repo_root = str(pathlib.Path(repo_root).resolve())
    key, latest_input_mtime_ns = _scan_tree(repo_root)
    memo_key = f"{repo_root}\0{key}"

    if not refresh:
        memoized = _MEMO.get(memo_key)
        if memoized is not None:
            graph, cached_at_ns = memoized
            if latest_input_mtime_ns < cached_at_ns:
                _MEMO.move_to_end(memo_key)  # touched: least likely of the two to be evicted next
                return graph, {"cached": True, "key": key, "seconds": 0.0, "from": "memory"}

    path = _path(repo_root, key)
    if not refresh and path.is_file():
        try:
            cache_mtime_ns = path.stat().st_mtime_ns
        except OSError:
            cache_mtime_ns = None
        if cache_mtime_ns is not None and latest_input_mtime_ns < cache_mtime_ns:
            try:
                graph = graph_from_dict(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                graph = None
            if graph is not None:
                _memo_put(memo_key, (graph, cache_mtime_ns))
                return graph, {"cached": True, "key": key, "seconds": 0.0, "from": "disk"}

    # The clock is read ONCE, here, before the scan starts - not after it returns. See the
    # module docstring: stamping the entry post-scan would let a file saved during the scan
    # be baked into the graph and still be judged older than the entry that baked it in.
    cached_at_ns = time.time_ns()
    started = time.monotonic()
    graph = api.scan(repo_root)
    took = round(time.monotonic() - started, 2)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(graph_to_dict(graph)), encoding="utf-8")
        # write_text() stamps the file with "now" - after the scan - which would reintroduce
        # the same bug for any OTHER process reading this entry from disk instead of the
        # memo. Force it back to the pre-scan read so both layers agree on one timestamp.
        os.utime(path, ns=(cached_at_ns, cached_at_ns))
        _evict_oldest(path.parent)
    except OSError:
        pass  # a read-only or missing cache directory still gets the process memo
    _memo_put(memo_key, (graph, cached_at_ns))
    return graph, {"cached": False, "key": key, "seconds": took, "from": "scan"}


def clear(repo_root: str = "") -> None:
    """Forget everything in memory, or one repository's memory AND its disk cache.

    No argument sweeps only the process memo - there is no single global disk root to walk
    once every repository keeps its own hashed slot under `_cache_root()`, and a machine-wide
    disk wipe was never asked for. With a `repo_root`, both layers are actually emptied: the
    memo entries for it, and the cache directory itself - a caller that clears and then reads
    again, even in a fresh process, gets a real scan, not whatever was left on disk.

    `repo_root` is resolved once, here, before either layer is touched - the disk directory
    was always keyed by the resolved path (`_repo_cache_dir`), but the memo used to be keyed
    by whatever string the caller passed in, so clearing via a symlink or via `"."` matched
    the disk directory but missed the memo entry, which survived under its own, differently
    spelled key and was served right back on the next read.
    """
    if not repo_root:
        _MEMO.clear()
        return
    repo_root = str(pathlib.Path(repo_root).resolve())
    for memo_key in [k for k in _MEMO if k.startswith(f"{repo_root}\0")]:
        del _MEMO[memo_key]
    directory = _repo_cache_dir(repo_root)
    if not directory.is_dir():
        return
    for entry in directory.glob("*.json"):
        with contextlib.suppress(OSError):
            entry.unlink()
    with contextlib.suppress(OSError):
        directory.rmdir()  # not empty (a concurrent write raced us), or already gone either way
