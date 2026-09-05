"""Redis keys: what writes them against what reads them.

Redis has no schema, so there is nothing to check a key name against - which is exactly why
nobody checks them, and why a mistyped key is invisible. It fails as a permanent cache miss:
the read returns None, the code falls through to the slow path, and everything keeps working
while being wrong. Nothing raises, nothing logs, and the only symptom is a number that never
updates.

But a key does not need a schema to be checkable. It needs a **counterpart**. A key written
and never read is a cache nobody uses; a key read and never written is a lookup that can
only ever miss. Those are the same two verdicts this tool gives everywhere else, applied to
a namespace instead of a route:

    r.set(f"user:{uid}:stats", value)     ← written here
    r.get(f"user:{uid}:stat")             ← read here, one character short

The interpolated part is not the interesting part, so patterns are normalised before they
are compared: `user:{uid}:stats`, `user:${id}:stats` and `user:%s:stats` are one key. What
differs is only which language wrote it.

Two other things fall out of reading these calls, and both are real:

  · A `set()` with no expiry on a key that names itself a cache. Redis keeps it forever;
    the memory is never reclaimed and nobody notices until the instance is full.
  · Two calls to the same key through different clients. A project with more than one Redis
    connection can write on one and delete on the other - the delete succeeds, deletes
    nothing, and the stale value survives. That one is reported rather than guessed at,
    because the client a call went through is visible in the source.
"""

from __future__ import annotations

import ast
import collections
import os
import re
from dataclasses import replace

from seamcheck.adapters.discovery import SKIP_DIRS
from seamcheck.extractors.js_extractor import _walk, iter_parsed
from seamcheck.graph import Edge, Status, Symbol
from seamcheck.nodetools import node_line
from seamcheck.pyscope import owners_of

# What a call to Redis is called, and whether it reads or writes. Names shared with dicts
# and Maps (`get`, `set`, `keys`) are the reason a receiver check matters below.
_READS = frozenset({
    "get", "mget", "getdel", "hget", "hmget", "hgetall", "hkeys", "hvals", "exists",
    # A pop CONSUMES: it is the reading half of a queue that a push fills. Listing it as a
    # write reported the one line that drains `import:csv:*:rows` as proof nothing read it.
    "lpop", "rpop", "blpop", "brpop", "lindex", "rpoplpush",
    "smembers", "sismember", "scard", "zrange", "zrevrange", "zscore", "zcard",
    "lrange", "llen", "ttl", "pttl", "getrange", "type", "scan",
    # Found by checking the Lua paths against the source: `HINCRBY` was a write and
    # `HINCRBYFLOAT` on the next line was not a command at all. Same for the sorted-set
    # ranges, which is most of what a leaderboard does.
    "zrangebyscore", "zrevrangebyscore", "zrank", "zrevrank", "zcount", "zrandmember",
    "hexists", "hlen", "hrandfield", "srandmember", "sinter", "sunion", "sdiff",
    "bitcount", "getbit", "strlen", "pfcount", "lpos", "getex", "object", "zdiff",
    # Django's async cache API. `adelete` was in the invalidation set and these were in
    # nothing at all - 96 `aget` and 56 `aset` calls invisible on the reference project
    # while 23 `adelete` were read, which is the entire reason 45 keys came back "only
    # ever invalidated here". The tool was describing its own blind spot.
    "aget", "aget_many", "ahas_key", "aget_or_set",
})
_WRITES = frozenset({
    "set", "setex", "setnx", "psetex", "mset", "getset", "hset", "hmset", "hsetnx",
    "hincrby", "incr", "incrby", "decr", "decrby", "expire", "pexpire", "delete", "del",
    "unlink", "sadd", "srem", "zadd", "zrem", "zincrby", "lpush", "rpush",
    "append", "setbit", "publish",
    "hincrbyfloat", "incrbyfloat", "zremrangebyscore", "zremrangebyrank",
    "zremrangebylex", "hdel", "lrem", "ltrim", "lset", "linsert", "spop", "setrange",
    "persist", "pfadd", "rename", "renamenx", "copy", "restore", "touch", "lpushx",
    "rpushx", "smove", "sinterstore", "sunionstore", "sdiffstore", "zunionstore",
    "zinterstore", "xadd", "geoadd",
    "aset", "aset_many", "aadd", "add", "aincr", "adecr", "atouch",
    # `adelete` was in the INVALIDATIONS set and in no command table, so it was never a
    # command at all: every async delete in the project was invisible while the sync ones
    # were read. The set that decides what a delete MEANS is not the set that decides
    # whether a call is Redis.
    "adelete", "adelete_many", "delete_many", "clear", "aclear", "aexpire",
})
# Commands that ANSWER with the new value. A rate limiter never reads its counter back -
# it uses what the write returned - so `count = ar.incr(key)` is the read, and reporting
# the key as "written, never read" describes a limiter that works.
# Deliberately the commands whose answer is the STATE, not a size: `getset` hands back
# what was there, `spop` hands back a member, `sadd` answers "was it already in?" - the
# dedupe idiom. `rpush` returning the new length is not evidence anyone read the list, so
# it is not here: the cost of a wrong entry is a dead key that looks alive, which is worse
# than the red it removes.
_COUNTERS = frozenset({"incr", "incrby", "incrbyfloat", "decr", "decrby",
                       "hincrby", "hincrbyfloat", "zincrby", "aincr", "adecr",
                       "getset", "spop", "sadd"})

# A Lua script: the keys it touches are named at the call, the commands it runs are not.
# `evalsha(sha, 1, WS_CONNECTIONS_KEY)` is how the connection counter is incremented, and
# reading only the plain calls reported that counter as a key nothing writes.
_SCRIPTS = frozenset({"eval", "evalsha", "eval_ro", "evalsha_ro", "fcall", "fcall_ro"})
# A sweep over a keyspace. The key is the `match` pattern, never the first argument.
_SCANNERS = frozenset({"scan", "scan_iter", "hscan", "hscan_iter", "sscan", "sscan_iter",
                       "zscan", "zscan_iter", "keys"})
_ALL = _READS | _WRITES | _SCRIPTS
# Writes that REMOVE or merely touch, and so can never need an expiry.
_NOT_STORES = frozenset({"delete", "del", "unlink", "expire", "pexpire", "publish",
                         "srem", "zrem", "lpop", "rpop", "hdel", "lrem", "ltrim", "spop",
                         "persist", "zremrangebyscore", "zremrangebyrank",
                         "zremrangebylex", "touch", "rename", "renamenx"})

# Removing a key, as opposed to storing one. A group made ENTIRELY of these has a writer
# the scan never saw - you cannot invalidate what nothing wrote.
_INVALIDATIONS = frozenset({"delete", "del", "adelete", "delete_many", "adelete_many",
                            "unlink", "expire", "pexpire", "clear"})

# Erasure and teardown: a delete whose whole purpose is to find nothing. A GDPR wipe walks
# every key an account might hold, a logout clears a session that may already be gone, a
# test harness resets a keyspace it did not fill. "Nothing writes this" is TRUE of all of
# them and can never be acted on - the code is correct as written. Reported, because a dead
# key is worth knowing; reported apart, because ranking it beside an invalidation that was
# supposed to bust a hot cache is what buried the one finding that mattered.
_CLEANUP_CONTEXT = re.compile(
    r"erase|anonymi[sz]e|gdpr|forget|purge|wipe|cleanup|clean_up|teardown|tear_down|"
    r"reset|logout|log_out|leave|disconnect|expire_|delete_account|deactivate",
    re.I)

# A receiver that says "this is Redis" rather than a dict. Without it, every `d.get(k)` in
# a Python codebase becomes a Redis key and the finding list is noise.
# `ar` is the async client throughout the reference project - and `ar` is not `redis`,
# `cache`, `client` or exactly `r`, so every async wrapper (`async def a_safe_rpush(ar,
# key, *values)`) read as not-Redis and the whole async write path was invisible.
_RECEIVER = re.compile(r"redis|cache|kv|_r\b|^a?r$|client|conn|pool", re.I)

# Keys whose name says they are disposable. A `set()` on one of these with no expiry is
# the leak this catches.
_CACHE_ISH = re.compile(r"^(cache|tmp|temp|session|otp|rate_?limit|lock|throttle)[:_.]", re.I)

_TTL_KWARGS = ("ex", "px", "exat", "pxat", "expire", "ttl", "timeout", "nx", "keepttl")

# Tests too. A key a test writes with a Lua script and reads back is a fact about the
# harness; three of one project's "read, never written" keys were exactly that, and a
# fourth was the Django test client being mistaken for a Redis one.
_PY_SKIP = SKIP_DIRS | {"test", "tests", "__tests__", "e2e", "spec", "specs", "testing"}
# A minified bundle is the same code already read from source, and megabytes of it.
_MAX_BYTES = 400_000
# A minified bundle is source already read, and megabytes of it.
_MAX_BYTES = 400_000
_JS_NEEDLES = ("redis", "Redis", "cache", "Cache")
_JS_EXTENSIONS = (".js", ".mjs", ".cjs", ".jsx", ".ts", ".mts", ".cts", ".tsx")


def _mentions(path: str, needles: tuple) -> bool:
    """Whether a file is worth handing to the parser at all.

    Reading a file as text costs microseconds; parsing it costs milliseconds and a
    subprocess. Feeding every JavaScript file in a large repo to the parser to look for a
    handful of call sites CRASHED it - and a crashed parser loses the entire JavaScript half
    of the graph, not just this extractor's part of it. Measured on a 1,900-file project:
    js_call went from 367 to 0.
    """
    try:
        if os.path.getsize(path) > _MAX_BYTES:
            return False
        with open(path, encoding="utf-8", errors="replace") as handle:
            text = handle.read()
    except OSError:
        return False
    return any(needle in text for needle in needles)


def _normalise(pattern: str) -> str:
    """One spelling for a key however its language interpolates.

    `user:{uid}:stats`, `user:${id}:stats`, `user:%s:stats` and `user:%(uid)s:stats` are
    the same key with four syntaxes. Comparing them literally means a Python writer and a
    JavaScript reader of one key never meet, which is precisely the case worth catching.
    """
    out = re.sub(r"\$\{[^}]*\}", "*", pattern)          # `${id}`
    out = re.sub(r"\{[^}]*\}", "*", out)                 # `{uid}` and f-string fields
    out = re.sub(r"%\([^)]*\)[sdifr]", "*", out)         # `%(uid)s`
    out = re.sub(r"%[sdifr]", "*", out)                  # `%s`
    out = re.sub(r"\*+", "*", out)
    return out.strip()


def _is_fragment(pattern: str) -> bool:
    """`":sum"`, `"user:"` - half a name being concatenated, not a key.

    Lua builds hash fields the same way keys are built: `prev_input .. ":sum"`. Reading
    the suffix as a key produced rows labelled `:sum` whose ids collided with each other.
    """
    return pattern.startswith(":") or pattern.endswith(":")


def _looks_like_key(pattern: str) -> bool:
    """A Redis key, rather than a dictionary lookup that happens to be a string.

    The colon convention is doing the work. It is a convention rather than a rule, so this
    under-reports on projects that do not follow it - which is the right direction: a
    missed key costs a finding, an invented one costs trust.
    """
    if not pattern or len(pattern) > 200:
        return False
    # A path is not a key. `self.client.get("/api/x?status:inactive")` in a Django test
    # has a colon in the query string and a receiver named `client`, and was reported as a
    # Redis key nothing writes - seven times in one project.
    if pattern.startswith(("/", "http://", "https://")):
        return False
    return ":" in pattern or pattern.startswith("*")


class _Hit:
    __slots__ = ("key", "raw", "file", "line", "write", "ttl", "receiver", "method", "nx",
                 "owner")

    def __init__(self, key, raw, file, line, write, ttl, receiver, method="", nx=False,
                 owner=""):
        self.key, self.raw, self.file, self.line = key, raw, file, line
        self.nx = nx
        # The function this touch happens in. "Which handler writes this key" is the
        # question a reader actually has, and the key symbol alone cannot answer it.
        self.owner = owner
        # The op itself, not just whether it writes. `delete` is in _WRITES because it
        # changes the store, and the TTL check read that as "stored with no expiry" - so
        # every cache.delete() in one project was reported as a key kept forever. 0 of 8
        # true. A verdict about expiry needs to know the verb.
        self.method = method
        self.write, self.ttl, self.receiver = write, ttl, receiver


# ── Python ────────────────────────────────────────────────────────────────
def _py_pattern(node, known=None, owner: str = "", line: int = 0,
                builders=None) -> str | None:
    """The key a Python expression names, with its interpolations left as holes.

    A hole that is itself a key keeps its shape: `base = f"challenges:schedule:django_{id}"`
    and then `zscore(f"{base}:bonus_claimed")` is a key beginning
    `challenges:schedule:django_`, not the pattern `*:bonus_claimed` that matches nothing.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):  # f-string
        parts = []
        for piece in node.values:
            if isinstance(piece, ast.Constant) and isinstance(piece.value, str):
                parts.append(piece.value)
                continue
            inner = getattr(piece, "value", None)
            composed = (_key_named(known, owner, inner.id, line)
                        if known is not None and isinstance(inner, ast.Name) else "")
            parts.append(composed if composed else "{}")
        return "".join(parts)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod):
        return _py_pattern(node.left, known, owner, line, builders)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        # `_SETUP_COMPLETE_CACHE_PREFIX + str(request.user.pk)`. The prefix is a module
        # constant and the rest is a hole - the same shape as an f-string, spelled the
        # older way, and it was the whole key of a middleware cache that reported as
        # deleted-and-never-written.
        halves = []
        for side in (node.left, node.right):
            if isinstance(side, ast.Name) and known is not None:
                halves.append(_key_named(known, owner, side.id, line) or "{}")
                continue
            halves.append(_py_pattern(side, known, owner, line, builders) or "{}")
        joined = "".join(halves)
        return joined if joined.strip("{}") else None
    if isinstance(node, ast.Call):
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == "format":
            return _py_pattern(func.value, known, owner, line, builders)
        # `achievements_version_key(user_id)` - a function that exists to return a key.
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
        if builders and name in builders:
            made = builders[name]
            # One key: that key. A pair: the caller unpacks it, and which name got which
            # is decided at the assignment, not here.
            return made[0] if len(made) == 1 else None
    return None


def _py_receiver(func: ast.Attribute) -> str:
    node = func.value
    for _ in range(4):
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            return node.attr
        if isinstance(node, ast.Call):
            node = node.func
            continue
        return ""
    return ""


_PIPELINE_FACTORIES = ("pipeline", "multi")


def _pipeline_names(tree: ast.AST, owners: dict[int, str]) -> dict[tuple[str, str], str]:
    """Locals holding a pipeline, mapped to the CLIENT they were made from.

    `pipe = r.pipeline()` and `with r.pipeline() as pipe:`. By assignment rather than by
    name: a variable is a pipeline because it came from `.pipeline()`; calling it `pipe`
    proves nothing, and matching the name would count every `pipe` in an image-processing
    module as a Redis client.

    Mapped to the client rather than collected as a set, because a pipeline is not a
    second connection - it IS `r`. Recording `pipe` as its own receiver made the
    "touched through more than one client" check fire on every key that a pipeline and
    its own client both touch, which is most of them.
    """
    found: dict[tuple[str, str], str] = {}

    def source(node) -> str | None:
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr in _PIPELINE_FACTORIES):
            return _py_receiver(node.func) or ""
        return None

    # Scoped to the function. `pipe = main_r.pipeline()` in one handler and
    # `pipe = mon_r.pipeline()` in the next are two pipelines on two connections; one
    # table for the whole file gave every `pipe` the last one's client, and a read of
    # the main store came back as a read of the monitoring one - which is the
    # wrong-instance warning, invented.
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            client = source(node.value)
            if client is not None:
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        found[(owners.get(node.lineno, ""), target.id)] = client
        elif isinstance(node, ast.withitem):
            client = source(node.context_expr)
            if client is not None and isinstance(node.optional_vars, ast.Name):
                found[(owners.get(node.context_expr.lineno, ""),
                       node.optional_vars.id)] = client
    return found


_LUA_STRING = re.compile(r"""["']([A-Za-z0-9_:.\-{}*]{3,})["']""")
# `local lb_key = "pps:leaderboard"` and `local user_key = "pps:user:" .. uid`, where the
# concatenation is the same hole an f-string leaves.
_LUA_LOCAL = re.compile(
    r"""local\s+([A-Za-z_]\w*)\s*=\s*["']([^"'\n]+)["'](\s*\.\.\s*\S+)?""")
# `redis.call("HGET", user_key, ...)` - the command and whatever holds the key. The key
# may be spelled INLINE: `redis.call("ZADD", "pps:board:i:" .. input_type .. ":t:" ..
# technique, score, uid)`. Reading only the first quoted piece saw `pps:board:i:` - a
# fragment, dropped - and a sorted set the script maintains on every run was reported as
# written only by a seed command, and read by nobody.
_LUA_CALL = re.compile(
    r"""redis\.(?:call|pcall)\s*\(\s*["'](\w+)["']\s*,\s*"""
    r"""((?:[A-Za-z_][\w.]*(?:\[[^\]]*\])?|["'][^"'\n]+["'])"""
    r"""(?:\s*\.\.\s*(?:[A-Za-z_][\w.]*(?:\[[^\]]*\])?|["'][^"'\n]+["']))*)""")
_LUA_PIECE = re.compile(r"""["']([^"'\n]*)["']|([A-Za-z_][\w.]*(?:\[[^\]]*\])?)""")


def _lua_expression(text: str, locals_: dict[str, str]) -> str:
    """The key a Lua concatenation names, with every non-literal piece left as a hole.

    `"pps:board:i:" .. input_type .. ":t:" .. technique` is `pps:board:i:{}:t:{}` - the
    same shape an f-string leaves. A bare name is looked up among the script's locals,
    so `redis.call("HGET", user_key, …)` still resolves through `local user_key = …`.
    """
    parts: list[str] = []
    for literal, name in _LUA_PIECE.findall(text):
        if name:
            held = locals_.get(name)
            parts.append(held if held else "{}")
        else:
            parts.append(literal)
    return "".join(parts)


def _script_runs(tree: ast.AST, owners: dict[int, str]) -> dict[str, list[tuple[int, str]]]:
    """Constant holding a Lua body -> every (line, function) that runs it.

    The body is a module-level constant, so without this every key it touches belongs to
    nobody - and the map's function filter, which is how a reader asks "what does this
    handler touch", could not reach one of those writes. The script belongs to whoever
    runs it.
    """
    found: dict[str, list[tuple[int, str]]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = (node.func.attr if isinstance(node.func, ast.Attribute)
                else getattr(node.func, "id", ""))
        if name not in _SCRIPTS and name not in ("register_script", "Script"):
            continue
        for argument in node.args[:1]:
            if isinstance(argument, ast.Name):
                found.setdefault(argument.id, []).append(
                    (node.lineno, owners.get(node.lineno, "")))
    return found


def _lua_keys(tree: ast.AST, runs: dict[str, list[tuple[int, str]]] | None = None,
              ) -> list[tuple[str, int, str, str]]:
    """(key, line, command) for every key an embedded Lua script touches.

    The script is a Python string and its commands are plain text in it:
    `local lb_key = "pps:leaderboard"` then `redis.call("ZADD", lb_key, …)` says as much
    about that key as any Python line does. 47 keys on the reference project were known
    only as "passed to a script" while the script sat right there.

    A key the script only receives as `KEYS[1]` stays unnamed - that one really is
    decided by the caller.
    """
    # A docstring that EXPLAINS a script is not a script. Found by this tool scanning
    # itself: the docstring above quotes a `local` and a `redis.call`, and the lens read
    # its own explanation as a Lua body and reported `pps:leaderboard` as a key in this
    # repository. Prose about code is not code.
    prose = {
        id(item.value)
        for parent in ast.walk(tree)
        if isinstance(parent, (ast.Module, ast.ClassDef, ast.FunctionDef,
                               ast.AsyncFunctionDef))
        for item in parent.body[:1]
        if isinstance(item, ast.Expr) and isinstance(item.value, ast.Constant)
    }
    held: dict[int, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    held[id(node.value)] = target.id
    found: list[tuple[str, int, str, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        if id(node) in prose:
            continue
        body = node.value
        # Where this script is actually run, if anywhere. A script nobody runs stays
        # where it is written - there is no better place to put it.
        sites = (runs or {}).get(held.get(id(node), ""), [])
        if "redis.call" not in body and "redis.pcall" not in body:
            continue
        locals_: dict[str, str] = {}
        for name, literal, concatenated in _LUA_LOCAL.findall(body):
            locals_[name] = literal + ("{}" if concatenated else "")
        named: set[str] = set()
        for match in _LUA_CALL.finditer(body):
            command, holder = match.group(1), match.group(2)
            key = _lua_expression(holder, locals_)
            # A name the script never declared - `KEYS[1]`, a parameter - is the
            # caller's key, not this script's. Only a hole with a literal around it
            # is a shape worth reporting.
            if key == "{}":
                key = ""
            if key and _looks_like_key(key) and not _is_fragment(key):
                named.add(key)
                # The whole script is one Python string, so every command in it shared
                # the string's line number and every map row opened line 1 of it. A path
                # that points at the wrong line is a path a reader cannot follow.
                line = node.lineno + body.count("\n", 0, match.start())
                for at, owner in sites or [(line, "")]:
                    found.append((key, line if not sites else at, command.lower(), owner))
        # A key the script DECLARES but never passes to a command: still evidence it is
        # part of this script's keyspace, and nothing more.
        #
        # Declared, not merely quoted. `redis.call("HINCRBY", stats_key, "unknown:n", -1)`
        # passes a hash FIELD as its third argument, and scooping every quoted string out
        # of the script turned `unknown:n` and `unknown:sum` into keys of their own.
        for match in _LUA_LOCAL.finditer(body):
            declared = match.group(2) + ("{}" if match.group(3) else "")
            if declared in named or not _looks_like_key(declared) or _is_fragment(declared):
                continue
            line = node.lineno + body.count("\n", 0, match.start())
            for at, owner in sites or [(line, "")]:
                found.append((declared, line if not sites else at, "eval", owner))
    return found


def _script_keys(node: ast.Call) -> list[ast.AST]:
    """The KEYS a Lua call names: `evalsha(sha, 1, key)` or `evalsha(sha, keys=[key])`.

    Only when the count is a literal - `evalsha(sha, n, *rest)` cannot say where the keys
    stop and the script's arguments begin, and guessing turns plain arguments into keys.
    """
    for word in node.keywords:
        if word.arg == "keys" and isinstance(word.value, (ast.List, ast.Tuple)):
            return list(word.value.elts)
    if len(node.args) >= 3 and isinstance(node.args[1], ast.Constant) \
            and isinstance(node.args[1].value, int):
        return node.args[2:2 + node.args[1].value]
    return []


def _key_lists(tree: ast.AST, owners: dict[int, str], builders=None,
               ) -> dict[tuple[str, str], list[str]]:
    """(enclosing def, variable) -> the key patterns a LIST holds.

    ```
    keys_to_clear = ["push_arena:is_hourly_mode", "push_arena:reset_timezone"]
    r.delete(*keys_to_clear)
    ```

    and the same shape built by `.append(...)` in a loop. The literals are right there
    and the command is right there, and nothing joined them because the argument is a
    list rather than a key - which is how every bulk invalidation in the reference
    project is written.
    """
    found: dict[tuple[str, str], list[str]] = {}

    def add(where, node):
        pattern = _py_pattern(node, builders=builders)
        if pattern and _looks_like_key(pattern):
            found.setdefault(where, []).append(pattern)

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, (ast.List, ast.Tuple)):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    for element in node.value.elts:
                        add((owners.get(node.lineno, ""), target.id), element)
        # `redis_key = cps_board_key(cps_input, cps_technique)` - a builder that returns
        # one of several keys, so the name stands for every one of them.
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            called = (node.value.func.id if isinstance(node.value.func, ast.Name)
                      else getattr(node.value.func, "attr", ""))
            made = (builders or {}).get(called)
            if isinstance(made, _OneOf):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        found.setdefault(
                            (owners.get(node.lineno, ""), target.id), []).extend(made)
        # `keys.append(f"navbar:{rid}")` inside a loop.
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr in ("append", "add") and len(node.args) == 1
                and isinstance(node.func.value, ast.Name)):
            add((owners.get(node.lineno, ""), node.func.value.id), node.args[0])
    # `for key in keys_to_clear: r.exists(key)` - the loop variable stands for every key
    # in the list, one at a time. The other half of how bulk work is written.
    for node in ast.walk(tree):
        if not isinstance(node, (ast.For, ast.AsyncFor)):
            continue
        if not isinstance(node.target, ast.Name):
            continue
        where = (owners.get(node.lineno, ""), node.target.id)
        if isinstance(node.iter, (ast.List, ast.Tuple)):
            for element in node.iter.elts:
                add(where, element)
        elif isinstance(node.iter, ast.Name):
            held = (found.get((owners.get(node.lineno, ""), node.iter.id))
                    or found.get(("", node.iter.id)))
            if held:
                found.setdefault(where, []).extend(held)
    return found


def _scan_pattern(node: ast.Call) -> str:
    """The MATCH pattern a scan-shaped call sweeps, or ""."""
    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
        return ""
    method = node.func.attr
    if method not in _SCANNERS:
        return ""
    for word in node.keywords:
        if word.arg in ("match", "pattern"):
            return _py_pattern(word.value) or ""
    # `scan_iter("user:*")` / `keys("user:*")` - the pattern is positional. `scan(0, …)`
    # takes the cursor there, and a cursor is not a pattern.
    if method != "scan" and node.args:
        return _py_pattern(node.args[0]) or ""
    return ""


def _swept_lists(tree: ast.AST, owners: dict[int, str]) -> dict[tuple[str, str], list[str]]:
    """(enclosing def, variable) -> the keyspace a scan handed it.

    ```
    cursor, keys = r.scan(cursor, match="user:*:hourly_patterns")
    hourly_keys.extend(keys)
    for k in hourly_keys:
        pipe.hgetall(k)                      # a READ of user:*:hourly_patterns
    ```

    A scan enumerates a keyspace; what the caller does with the names decides whether
    the keyspace was read. Counting the scan itself as a read invented eight lookups
    from wipe scripts; counting it as nothing called a keyspace that is aggregated on
    every admin page load "written and never read". The read is the HGETALL, and the
    name it is given can be followed to it.

    Followed through unpacking, `.extend()`/`.append()`, `list(...)`, plain assignment
    and `for` - the shapes a cursor loop takes. Only the names: whether the command at
    the end reads or deletes is that command's business, and the caller decides.
    """
    found: dict[tuple[str, str], list[str]] = {}

    def hold(where, patterns):
        if patterns and _looks_like_key(patterns[0]):
            found.setdefault(where, []).extend(
                q for q in patterns if q not in found.get(where, []))

    def unwrap(value):
        # `list(r.scan_iter(...))`, `set(keys)`, `sorted(keys)`.
        if (isinstance(value, ast.Call) and isinstance(value.func, ast.Name)
                and value.func.id in ("list", "set", "sorted", "tuple") and value.args):
            return value.args[0]
        return value

    for node in ast.walk(tree):
        where = owners.get(getattr(node, "lineno", 0), "")
        if isinstance(node, ast.Assign):
            value = unwrap(node.value)
            pattern = _scan_pattern(value)
            if not pattern:
                continue
            for target in node.targets:
                if isinstance(target, ast.Name):
                    hold((where, target.id), [pattern])
                elif isinstance(target, ast.Tuple) and target.elts \
                        and isinstance(target.elts[-1], ast.Name):
                    # `cursor, keys = r.scan(...)` - the keys are the last element.
                    hold((where, target.elts[-1].id), [pattern])
        elif (isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension))
                and isinstance(node.target, ast.Name)):
            # `[r.hgetall(k) for k in r.scan_iter(...)]` has no line of its own; the
            # comprehension sits on the line of the expression that holds it.
            where = owners.get(getattr(node.iter, "lineno", 0), "") or where
            pattern = _scan_pattern(unwrap(node.iter))
            if pattern:
                hold((where, node.target.id), [pattern])
    if not found:
        return found
    # The names the scanned names flow into. Bounded: a cursor loop is three or four
    # hops, and the fixpoint is cheap on a file that has any scan at all.
    for _ in range(4):
        before = sum(len(v) for v in found.values())
        for node in ast.walk(tree):
            where = owners.get(getattr(node, "lineno", 0), "")
            if isinstance(node, ast.Assign) and isinstance(unwrap(node.value), ast.Name):
                held = found.get((where, unwrap(node.value).id))
                for target in node.targets:
                    if held and isinstance(target, ast.Name):
                        hold((where, target.id), held)
            elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr in ("extend", "append", "add", "update")
                    and len(node.args) == 1 and isinstance(node.func.value, ast.Name)
                    and isinstance(node.args[0], ast.Name)):
                held = found.get((where, node.args[0].id))
                if held:
                    hold((where, node.func.value.id), held)
            elif (isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension))
                    and isinstance(node.target, ast.Name)
                    and isinstance(unwrap(node.iter), ast.Name)):
                where = owners.get(getattr(node.iter, "lineno", 0), "") or where
                held = found.get((where, unwrap(node.iter).id))
                if held:
                    hold((where, node.target.id), held)
        if sum(len(v) for v in found.values()) == before:
            break
    return found


def _key_variables(tree: ast.AST, owners: dict[int, str], builders=None,
                   ) -> dict[tuple[str, str], str]:
    """(enclosing def, variable) -> the key pattern assigned to it.

    `cache_key = f"api:user_stats:{uid}"` and then `cache.set(cache_key, ...)` is the
    house style for every cached endpoint on the reference project, and it made the
    write invisible: 65 keys read "1 invalidate / 0 read" because the DELETE spells the
    literal out and the write one line above does not.

    Scoped to the function, and kept as a LIST: `err_key` is assigned in the 5xx branch
    and again in the 4xx branch, each with its own write underneath, so a name means
    whichever key was assigned nearest above the line that uses it. Dropping the name for
    meaning two things left both error counters reported as read by nobody.
    """
    found: dict[tuple[str, str], list[tuple[int, str]]] = {}
    # Twice: the second pass can see what the first learned, so a key assembled from a
    # constant assigned earlier in the file resolves. `_SETUP_COMPLETE_CACHE_PREFIX +
    # str(user.pk)` is one name plus a hole, and with no names to hand it is two holes
    # and no key at all.
    for attempt in range(2):
      for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        else:
            continue
        # No `_looks_like_key` here: `schedule_id = f"django_{obj.id}"` is not a key -
        # no namespace, no colon - and it is what the key is built AROUND. Filtering it
        # out left the hole as `*`, and one keyspace was read under two spellings.
        # `fresh, stale = swr_keys(name, uid)` - a builder returning a pair, unpacked.
        if (len(targets) == 1 and isinstance(targets[0], ast.Tuple)
                and isinstance(value, ast.Call)):
            called = (value.func.id if isinstance(value.func, ast.Name)
                      else getattr(value.func, "attr", ""))
            made = (builders or {}).get(called) or []
            if len(made) == len(targets[0].elts) and not isinstance(made, _OneOf):
                for name_node, one in zip(targets[0].elts, made, strict=True):
                    if isinstance(name_node, ast.Name):
                        held = found.setdefault(
                            (owners.get(node.lineno, ""), name_node.id), [])
                        if (node.lineno, one) not in held:
                            held.append((node.lineno, one))
                continue
        pattern = _py_pattern(value, found if attempt else None,
                              owners.get(node.lineno, ""), node.lineno, builders)
        if not pattern:
            continue
        for target in targets:
            if not isinstance(target, ast.Name):
                continue
            held = found.setdefault((owners.get(node.lineno, ""), target.id), [])
            if (node.lineno, pattern) not in held:
                held.append((node.lineno, pattern))
    for assignments in found.values():
        assignments.sort()
    return found


def _key_named(key_names: dict, owner: str, name: str, line: int) -> str:
    """The key a variable holds at `line`: the nearest assignment above it, in this
    function first and then at module level."""
    for where in ((owner, name), ("", name)):
        above = [p for at, p in key_names.get(where, []) if at <= line]
        if above:
            return above[-1]
    return ""


# Filled once every file is read: a function whose returns are all clients is itself a
# factory, and `reader = _swr_reader(uid)` is then a client like any other.
_CLIENT_FACTORIES: dict[str, str] = {}
# "a client, connection unknown" - distinct from "" which means "not a client at all".
_ANY_CLIENT = "\x00any"


def _client_identity(node: ast.AST) -> str:
    """Which CONNECTION a factory call returns, or "" if it is not one.

    The multi-client warning exists for a real bug - a write on db 0 and a delete on
    db 6, where the stale value survives - and it compared what the file happened to
    call the variable. On the reference project the commonest pair was `ar` and `r`:
    the async and the sync client from the same factory, pointed at the same server,
    on 21 keys. Async is not a different Redis; an explicit `db=` is.
    """
    if isinstance(node, ast.Await):
        node = node.value
    if not isinstance(node, ast.Call):
        return ""
    func = node.func
    name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
    # A function that returns a client is a client factory, one hop out.
    if name in _CLIENT_FACTORIES:
        return _CLIENT_FACTORIES[name]
    lowered = name.lower()
    # The same test the receiver check uses, applied to the FACTORY instead of the
    # variable - so a client is one because of what made it, not what it is called.
    # `ar = get_async_redis_client()` matched nothing at all before this: `ar` is not
    # `redis`, `cache`, `client` or `^r$`, so every write through it was invisible.
    if not _RECEIVER.search(lowered):
        return ""
    for prefix, replacement in (("async_", ""), ("_async", ""), ("async", "")):
        lowered = lowered.replace(prefix, replacement)
    for word in node.keywords:
        # `get_redis_client(db=6)` is a different store from `get_redis_client(db=0)`,
        # and that difference is the whole point of the warning.
        if word.arg == "db" and isinstance(word.value, ast.Constant):
            return f"{lowered}#{word.value.value}"
    return lowered


def _script_names(tree: ast.AST) -> set[str]:
    """Locals holding a Lua script: `cap = r.register_script(src)`.

    redis-py hands back a callable, so the call that runs it is `cap(keys=[…])` and its
    method name is whatever the variable is called. Without this the hero counter's
    INCRBY - which is inside the script - was not a touch of the key at all.
    """
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
            continue
        func = node.value.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        if name in ("register_script", "Script"):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    found.add(target.id)
    return found


def _proxy_classes(tree: ast.AST) -> dict[str, str]:
    """Classes that forward every attribute to a factory, mapped to that connection.

    `class RedisClient: def __getattr__(self, n): return getattr(get_redis_client(), n)`
    and then `redis_client = RedisClient()`. Two names for one connection - and the
    "touched through more than one client" warning fired on 33 keys over that difference.
    A proxy onto a DIFFERENT factory is a different client, which is the half worth
    keeping.
    """
    found: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        for item in node.body:
            if not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if item.name not in ("__getattr__", "__getattribute__"):
                continue
            for inner in ast.walk(item):
                if (isinstance(inner, ast.Call)
                        and getattr(inner.func, "id", "") == "getattr" and inner.args):
                    identity = _client_identity(inner.args[0])
                    if identity:
                        found[node.name] = identity
    return found


def _proxy_singletons(tree: ast.AST) -> dict[str, str]:
    """Module-level `x = SomeProxy()`, by the name other modules import it under."""
    proxies = _proxy_classes(tree)
    found: dict[str, str] = {}
    for node in getattr(tree, "body", []):
        if (isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)
                and getattr(node.value.func, "id", "") in proxies):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    found[target.id] = proxies[node.value.func.id]
    return found


def _client_names(tree: ast.AST, owners: dict[int, str]) -> dict[tuple[str, str], str]:
    """(enclosing def, local) holding a Redis client -> the connection it holds.

    Scoped to the function, with "" for the module. `r = get_monitoring_redis_client()`
    in one view and `r = get_redis_client()` in the next are two connections; one table
    per file gave the whole file the first, and a key written on the main store and read
    on the main store was warned about as touched through two clients.
    """
    found: dict[tuple[str, str], str] = {}
    proxies = _proxy_classes(tree)
    for node in ast.walk(tree):
        if (isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)
                and getattr(node.value.func, "id", "") in proxies):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    found[(owners.get(node.lineno, ""), target.id)] = \
                        proxies[node.value.func.id]
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            where = owners.get(node.lineno, "")
            identity = _client_identity(node.value)
            if identity == _ANY_CLIENT:
                identity = ""
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        found.setdefault((where, target.id), "")
            elif identity:
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        # setdefault: `redis_client = RedisClient()` reads as a factory
                        # by name too, and the proxy above already knows which connection
                        # that class forwards to, which is the more specific answer.
                        found.setdefault((where, target.id), identity)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            identity = _client_identity(node.value)
            if identity:
                found[(owners.get(node.lineno, ""), node.target.id)] = identity
        elif isinstance(node, ast.withitem) and isinstance(node.optional_vars, ast.Name):
            identity = _client_identity(node.context_expr)
            if identity:
                found[(owners.get(node.context_expr.lineno, ""),
                       node.optional_vars.id)] = identity
        elif isinstance(node, ast.ImportFrom):
            # `from app.redis_client import redis_client as _r_ann`: the alias is not
            # client-shaped and the import is the only place its real name appears.
            # Deferred with `?`, because the imported name is usually a singleton whose
            # own connection is declared in the module it comes from - taking the name
            # itself as the identity made `redis_client` and `get_redis_client` two
            # connections again, on 49 keys.
            for alias in node.names:
                if alias.asname and _RECEIVER.search(alias.name):
                    found[("", alias.asname)] = f"?{alias.name}"
    return found


def _wrappers_in(tree: ast.AST) -> dict[str, tuple[int, str]]:
    """`def safe_set(r, key, ...)` whose body calls `r.set(key, ...)`.

    Returns name -> (which argument is the key, which command it is). Read from the body:
    the first parameter has to be the receiver of the call, and the key argument has to be
    the parameter the call passes first. That is a thin wrapper, and it is how a codebase
    that has ever hit a WRONGTYPE writes every Redis line it has.
    """
    found: dict[str, tuple[int, str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        params = [a.arg for a in node.args.args]
        if len(params) < 2:
            continue
        client = params[0]
        # ...and the receiver has to LOOK like a client. `append` is a Redis command and
        # also what every list does, so `def set_the_table(guests, key): guests.append(
        # key)` reads as a wrapper for APPEND without this. Two independent signals: the
        # parameter is client-shaped here, and the argument is client-shaped at the call.
        if not _RECEIVER.search(client):
            continue
        for inner in ast.walk(node):
            if not isinstance(inner, ast.Call) or not isinstance(inner.func, ast.Attribute):
                continue
            method = inner.func.attr
            if method not in _ALL or not inner.args:
                continue
            if not (isinstance(inner.func.value, ast.Name) and inner.func.value.id == client):
                continue
            first = inner.args[0]
            if isinstance(first, ast.Name) and first.id in params[1:]:
                found[node.name] = (params.index(first.id), method)
                break
    return found


def ttl_of(node: ast.Call, method: str) -> bool:
    """Whether this call gives the key an expiry.

    A third positional argument to `set` is the expiry in BOTH APIs - Django's
    `cache.set(key, value, timeout)` and redis-py's `ex`.
    """
    return (any(k.arg in _TTL_KWARGS for k in node.keywords if k.arg)
            or method in ("setex", "psetex", "expire", "pexpire")
            or (method in ("set", "aset") and len(node.args) >= 3))


def nx_of(node: ast.Call, method: str) -> bool:
    """`set(key, v, nx=True)`, `setnx()` or Django's `add()`: a lock, whose RETURN VALUE
    is the read, so nothing will ever call get() on it."""
    return method in ("setnx", "add", "aadd") or any(k.arg == "nx" for k in node.keywords)


class _OneOf(list):
    """A builder's keys when it returns ONE of them, chosen by branch - as opposed to a
    pair the caller unpacks. Same list, different meaning at the assignment."""


def _key_builders(tree: ast.AST) -> dict[str, list[str]]:
    """Functions whose whole job is to return a key: name -> the pattern they return.

    ```
    def achievements_version_key(user_id):
        return f'user:{user_id}:achievements_ver'
    ```

    The literal appears once, in the builder, and never at a call site - so a read
    elsewhere that DOES spell it out came back "read here and written nowhere" about a
    counter incremented on every achievement change.

    One return of a key shape, or one per branch:

    ```
    def cps_board_key(input_filter=None, technique_filter=None):
        if i and t:
            return f'pps:board:i:{i}:t:{t}'
        if i:
            return f'pps:board:i:{i}'
        return LEADERBOARD_KEYS['pps']
    ```

    A caller of that holds ONE of three keys, and a read through it reads all three. A
    return that resolves to nothing - a dictionary lookup, a name - is tolerated; a
    return that resolves to something that is not a key is not, because then the
    function is not a builder.
    """
    found: dict[str, list[str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        returns = [inner for inner in ast.walk(node)
                   if isinstance(inner, ast.Return) and inner.value is not None]
        if not returns:
            continue
        if len(returns) > 1:
            branches: list[str] = []
            for one in returns:
                if isinstance(one.value, (ast.Tuple, ast.List)):
                    branches = []
                    break
                pattern = _py_pattern(one.value)
                if pattern is None:
                    # `return 42` resolves fine and is not a key; `return None` is the
                    # no-key branch a builder is allowed to have.
                    if (isinstance(one.value, ast.Constant)
                            and one.value.value is not None):
                        branches = []
                        break
                    continue
                if not _looks_like_key(pattern):
                    branches = []
                    break
                branches.append(pattern)
            if branches:
                found[node.name] = _OneOf(dict.fromkeys(branches))
            continue
        value = returns[0].value
        if isinstance(value, (ast.Tuple, ast.List)):
            # `return fresh_key, stale_key` - a serve-stale-while-revalidate cache keeps
            # two copies, so its builder returns two. Following only a single return left
            # the whole SWR keyspace unreadable, which is what every cached endpoint on
            # the reference project moved ONTO.
            pair = [_py_pattern(e) for e in value.elts]
            good = [q for q in pair if q and _looks_like_key(q)]
            if good and len(good) == len(pair):
                found[node.name] = good
            continue
        pattern = _py_pattern(value)
        if pattern and _looks_like_key(pattern):
            found[node.name] = [pattern]
    return found


def _client_factories(tree: ast.AST) -> dict[str, str]:
    """Functions that RETURN a client: name -> the connection they hand back.

    `reader = _swr_reader(user_id)` - the variable is not client-shaped and neither is
    the function, so every read through it was invisible while the writes, which went
    through a pipeline off a recognisable client, were seen. That is the SWR cache
    reading as write-only: the keyspace every cached endpoint on the reference project
    moved ONTO.

    One hop, and only when every return is a client. A function that sometimes returns a
    client and sometimes a string is not a factory.
    """
    found: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        returns = [inner for inner in ast.walk(node)
                   if isinstance(inner, ast.Return) and inner.value is not None]
        if not returns:
            continue
        made = {_client_identity(r.value) for r in returns}
        if "" in made:
            continue
        # Every branch returns a client. If they agree, that is the connection; if they
        # do not - `_swr_reader` hands back the replica or the user's shard depending on
        # config - it is a client whose connection is not known, and an unknown
        # connection is not a second one. Requiring agreement left that read path
        # invisible, which is the whole SWR cache.
        found[node.name] = made.pop() if len(made) == 1 else _ANY_CLIENT
    return found


def _scoped(table: dict, name: str, where: str):
    """The enclosing function's binding of `name`, else the module's, else None."""
    if (where, name) in table:
        return table[(where, name)]
    return table.get(("", name))


def _clients_into_parameters(
    tree: ast.AST, owners: dict[int, str],
    clients: dict[tuple[str, str], str], pipelines: dict[tuple[str, str], str],
) -> dict[tuple[str, str], str]:
    """(function, parameter) -> the connection its callers hand it.

    A helper that takes the pipeline as an argument -

        def _build_end_commands(pipe, endpoint, bucket):
            pipe.incr(f"analytics:req:total:{endpoint}")

        pipe = r.pipeline(); _build_end_commands(pipe, ...)

    - never assigns `pipe`, so scoping the client table to the function left every
    write in it with no receiver at all, and four keys the middleware writes on every
    request were reported as never written. The caller knows the connection; carry it
    across the call, the way the key builders already travel into a wrapper's parameter.
    Callers that disagree leave the parameter an unknown connection, not a second one.
    """
    signatures = _signatures(tree)
    found: dict[tuple[str, str], str] = {}

    def resolve(argument, where) -> str | None:
        if not isinstance(argument, ast.Name):
            return None
        name = argument.id
        piped = _scoped(pipelines, name, where)
        if piped is not None:
            name = piped or name
        known = _scoped(clients, name, where)
        if known is not None:
            return known
        return f"?{name}" if _RECEIVER.search(name) else None

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = (node.func.id if isinstance(node.func, ast.Name)
                else getattr(node.func, "attr", ""))
        params = next(iter(signatures.get(name, ())), None)
        if not params:
            continue
        where = owners.get(node.lineno, "")
        handed = [(params[i], a) for i, a in enumerate(node.args) if i < len(params)]
        handed += [(w.arg, w.value) for w in node.keywords if w.arg in params]
        for param, argument in handed:
            identity = resolve(argument, where)
            if identity is None:
                continue
            seen = found.get((name, param))
            if seen is None or seen == identity:
                found[(name, param)] = identity
            else:
                found[(name, param)] = ""
    return found


def _signatures(tree: ast.AST) -> dict[str, list[list[str]]]:
    """Function name -> every parameter list defined under that name.

    Every list, because one name can be two functions - and when it is, keeping only the
    last one silently misreads every call to the other. `safe_get(r, key, default=None)`
    is the reference project's Redis wrapper; a helper nested inside a GDPR export is
    also called `safe_get(key, default=None)`, and it happened to be read second. So
    `safe_get(self.r, cache_key)` filed its key under the parameter named `default`, the
    wrapper's own `r.get(key)` found nothing under `key`, and a cache read in constant
    use reported as a key written and never read. Trying each candidate costs nothing:
    an argument that is not key-shaped is dropped either way.
    """
    found: dict[str, list[list[str]]] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            params = [a.arg for a in node.args.args] + \
                [a.arg for a in node.args.kwonlyargs]
            known = found.setdefault(node.name, [])
            if params not in known:
                known.append(params)
    return found


def _merge_signatures(into: dict[str, list[list[str]]],
                      more: dict[str, list[list[str]]]) -> None:
    """`dict.update` would drop the other file's function of the same name."""
    for name, lists in more.items():
        known = into.setdefault(name, [])
        for params in lists:
            if params not in known:
                known.append(params)


def _keys_into_parameters(
    trees: list[tuple[str, ast.AST, dict[int, str], dict, dict]],
    signatures: dict[str, list[list[str]]],
    builders: dict[str, str] | None = None,
) -> dict[tuple[str, str], set[str]]:
    """(function, parameter) -> the key patterns callers hand it.

    ```
    cache_key = f"cache:avatar:{user_id}"
    return await _build(request, cache_key)     # here
    ...
    async def _build(request, cache_key):
        await cache.aset(cache_key, data, 30)   # ...and the write is here
    ```

    Inside the helper the name is a PARAMETER, so nothing in that file assigns it and the
    write cannot be seen. That is how every cached endpoint on the reference project is
    written, and it is most of what was left of "only ever invalidated here" - a category
    that named the delete, which spells the key out, and missed the write, which does not.

    One helper called with two different keys writes both; that is not ambiguity, it is
    two writes.
    """
    found: dict[tuple[str, str], set[str]] = {}

    def patterns_of(argument, key_names, listed, owner, line) -> list[str]:
        one = _py_pattern(argument, key_names, owner, line, builders)
        if one:
            return [one]
        if isinstance(argument, ast.Call):
            called = (argument.func.id if isinstance(argument.func, ast.Name)
                      else getattr(argument.func, "attr", ""))
            made = (builders or {}).get(called)
            if isinstance(made, _OneOf):
                return list(made)
        if isinstance(argument, ast.Name):
            named = _key_named(key_names, owner, argument.id, line)
            if named:
                return [named]
            # `redis_key = cps_board_key(...)` then `get_entries(redis_key, ...)` - a
            # name standing for one of several keys.
            held = listed.get((owner, argument.id)) or listed.get(("", argument.id))
            if held:
                return list(held)
            # ...or a parameter of THIS function, handed straight on:
            # `def get_entries(redis_key, …): return safe_zrevrange(r, redis_key, …)`.
            # What the caller gave this function, this function gives the next one.
            # The fixpoint below is what makes the second hop see the first.
            here = owner.rpartition(".")[2]
            if any(argument.id in params for params in signatures.get(here, ())):
                return sorted(found.get((here, argument.id), ()))
        return []

    # Two passes: the second sees what the first handed to a parameter, so a key can
    # travel caller -> helper -> wrapper. Deeper chains are rarer than they are costly.
    for _ in range(2):
        before = sum(len(v) for v in found.values())
        for _rel, tree, owners, key_names, listed in trees:
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                name = (node.func.id if isinstance(node.func, ast.Name)
                        else getattr(node.func, "attr", ""))
                every = signatures.get(name)
                if not every:
                    continue
                owner, line = owners.get(node.lineno, ""), node.lineno
                # Every signature this name has. A key-shaped argument lands under the
                # parameter name each candidate gives it; the candidate that is wrong
                # names a parameter nothing ever reads back.
                params = [one for candidate in every for one in candidate]
                for position, argument in enumerate(node.args):
                    at = [candidate[position] for candidate in every
                          if position < len(candidate)]
                    if not at:
                        break
                    for pattern in patterns_of(argument, key_names, listed, owner, line):
                        if _looks_like_key(pattern):
                            for param in at:
                                found.setdefault((name, param), set()).add(pattern)
                for word in node.keywords:
                    if not word.arg or word.arg not in params:
                        continue
                    for pattern in patterns_of(word.value, key_names, listed, owner, line):
                        if _looks_like_key(pattern):
                            found.setdefault((name, word.arg), set()).add(pattern)
        if sum(len(v) for v in found.values()) == before:
            break
    return found


def _counter_reads(tree: ast.AST, owners: dict[int, str]) -> set[int]:
    """Lines where a counter's RETURN VALUE is the read.

    ```python
    count = await ar.incr(f"appeal_rate:{ip}")   # reported "0 read". This IS the read.
    if count > 3:
        return 429
    ```

    Nothing will ever call `get()` on that key, so waiting for one reports every rate
    limiter in the project as a write nobody reads. The same shape arrives positionally
    out of a pipeline, which is the harder half - `p.incr(k)` is a bare statement and the
    value comes back from `p.execute()` several lines later:

    ```python
    p.incr(f"user:{uid}:high_pps_count"); p.expire(...)
    mismatch_count, _ = p.execute()              # the read, by tuple position
    ```

    So a counter counts as a read when its own value is used, or when it was queued on a
    pipeline whose `execute()` result is bound to a name in the same scope. A pipeline
    whose result is thrown away stays a write, which is the honest reading of it.
    """
    discarded: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Expr):
            value = node.value
            discarded.add(id(value.value if isinstance(value, ast.Await) else value))

    def bound(value) -> ast.Call | None:
        inner = value.value if isinstance(value, ast.Await) else value
        return inner if isinstance(inner, ast.Call) else None

    drained: set[tuple[str, str]] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        call = bound(node.value) if node.value is not None else None
        if (call is not None and isinstance(call.func, ast.Attribute)
                and call.func.attr in ("execute", "aexecute")
                and isinstance(call.func.value, ast.Name)):
            drained.add((owners.get(node.lineno, ""), call.func.value.id))

    lines: set[int] = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        if node.func.attr not in _COUNTERS:
            continue
        if id(node) not in discarded:
            lines.add(node.lineno)
        elif isinstance(node.func.value, ast.Name):
            where = owners.get(node.lineno, "")
            if ((where, node.func.value.id) in drained
                    or ("", node.func.value.id) in drained):
                lines.add(node.lineno)
    return lines


def _scan_python(root: str) -> list[_Hit]:
    hits: list[_Hit] = []
    # name -> (key argument index, command), and the call sites waiting for it: a wrapper
    # is usually defined in one module and called from forty others, so the table cannot
    # be complete until every file has been read.
    wrappers: dict[str, tuple[int, str]] = {}
    pending: list[tuple] = []
    # Module-level client singletons, which are global names in practice.
    singletons: dict[str, str] = {}
    # Parsed once. A key built in one function and written inside the helper it is handed
    # to cannot be resolved until every call site has been read, so the trees are kept
    # rather than re-parsed.
    trees: list[tuple[str, ast.AST, dict[int, str]]] = []
    signatures: dict[str, list[list[str]]] = {}
    builders: dict[str, list[str]] = {}
    # Functions that return a client, so a wrapper around a factory is a factory.
    factories: dict[str, str] = {}
    for here, subdirectories, names in os.walk(root):
        subdirectories[:] = [
            d for d in subdirectories if d not in _PY_SKIP and not d.startswith(".")
        ]
        for name in names:
            if not name.endswith(".py"):
                continue
            path = os.path.join(here, name)
            try:
                with open(path, encoding="utf-8", errors="replace") as handle:
                    tree = ast.parse(handle.read())
            except (OSError, SyntaxError, ValueError):
                continue
            rel = os.path.relpath(path, root)
            trees.append((rel, tree, owners_of(tree)))
            _merge_signatures(signatures, _signatures(tree))
            builders.update(_key_builders(tree))
            factories.update(_client_factories(tree))

    # A builder is a project-wide name, so nothing that depends on one can be resolved
    # until every file has been read.
    _CLIENT_FACTORIES.clear()
    _CLIENT_FACTORIES.update(factories)
    key_lists = {rel: _key_lists(tree, owners, builders)
                 for rel, tree, owners in trees}
    parsed = [(rel, tree, owners, _key_variables(tree, owners, builders), key_lists[rel])
              for rel, tree, owners in trees]
    parameter_keys = _keys_into_parameters(parsed, signatures, builders)

    counter_reads = {rel: _counter_reads(tree, owners) for rel, tree, owners in trees}

    for rel, tree, owners, key_names, _listed in parsed:
            pipelines = _pipeline_names(tree, owners)
            clients = _client_names(tree, owners)
            singletons.update({
                name: identity for (where, name), identity in clients.items() if not where
            })

            passed_clients = _clients_into_parameters(tree, owners, clients, pipelines)
            singletons.update(_proxy_singletons(tree))
            scripts = _script_names(tree)
            lists_here = key_lists.get(rel, {})
            swept_here = _swept_lists(tree, owners)
            for quoted, line, command, owner in _lua_keys(tree, _script_runs(tree, owners)):
                hits.append(_Hit(
                    _normalise(quoted), quoted, rel, line,
                    command in _WRITES, command in ("setex", "psetex", "expire", "pexpire"),
                    "", command if command in _ALL else "eval", False,
                    owner or owners.get(line, ""),
                ))
            wrappers.update(_wrappers_in(tree))
            for node in ast.walk(tree):
                # `safe_set(r, "key", ...)` - a plain call, whose first argument is a
                # client and whose key is a literal. Resolved after every file is read.
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                        and len(node.args) >= 2
                        and isinstance(node.args[0], ast.Name)
                        and _RECEIVER.search(node.args[0].id)):
                    pending.append((node.func.id, node, rel, owners.get(node.lineno, ""),
                                    node.args[0].id))
                if not isinstance(node, ast.Call):
                    continue
                keyed = any(w.arg == "keys" and isinstance(w.value, (ast.List, ast.Tuple))
                            for w in node.keywords)
                if keyed or (isinstance(node.func, ast.Name) and node.func.id in scripts):
                    # A Script object being run. `keys=` IS redis-py's Script signature,
                    # so the keyword is the evidence - the callee may be any expression,
                    # and `_get_max_cas_script(r)(keys=[...])` matched no name at all.
                    # Same evidence as EVALSHA: it names its keys, hides its commands.
                    for element in _script_keys(node):
                        # With the names this file knows: a script's key is as likely to
                        # come out of a builder (`_cold_slots_key(name)`) as to be spelled
                        # at the call, and reading only literals lost the whole keyspace
                        # of the scripts that run on the hottest path.
                        pattern = _py_pattern(element, key_names,
                                              owners.get(node.lineno, ""), node.lineno,
                                              builders) or (
                            _key_named(key_names, owners.get(node.lineno, ""),
                                       element.id, node.lineno)
                            if isinstance(element, ast.Name) else "")
                        if pattern and _looks_like_key(pattern):
                            hits.append(_Hit(
                                _normalise(pattern), pattern, rel, node.lineno,
                                False, False, "", "eval", False,
                                owners.get(node.lineno, ""),
                            ))
                    continue
                if not isinstance(node.func, ast.Attribute):
                    continue
                method = node.func.attr
                # `scan(cursor, match="user:*:hourly_patterns")` - the key is not the
                # first argument, it is the MATCH pattern, and sweeping a keyspace to
                # read the keys in it is a read of that keyspace. The cursor sits where
                # a key normally would, so nothing here looked like a key at all.
                if method in _SCANNERS:
                    for word in node.keywords:
                        if word.arg not in ("match", "pattern"):
                            continue
                        found = _py_pattern(word.value, key_names,
                                            owners.get(node.lineno, ""), node.lineno,
                                            builders)
                        if found and _looks_like_key(found):
                            hits.append(_Hit(
                                _normalise(found), found, rel, node.lineno, False, False,
                                "", "scan", False, owners.get(node.lineno, ""),
                            ))
                    continue
                if method not in _ALL or not node.args:
                    continue
                receiver = _py_receiver(node.func)
                where = owners.get(node.lineno, "")
                piped = _scoped(pipelines, receiver, where)
                if piped is not None:
                    # Reported as the client it came from, so a pipelined write and a
                    # direct read are one client, which is what they are.
                    receiver = piped or receiver
                known = _scoped(clients, receiver, where)
                if known is None:
                    # A parameter: the connection is whatever the callers pass in.
                    known = passed_clients.get((where.rpartition(".")[2], receiver))
                if known is None and not _RECEIVER.search(receiver or ""):
                    continue
                # The connection it IS, not the name this file gave it. A name this file
                # never sees the source of is asked about again once every file is read -
                # a lazy proxy (`redis_client = RedisClient()`) is declared in one module
                # and imported by forty - and is dropped if nothing answers. An unresolved
                # name is an unknown connection, not a second one, and comparing one
                # against a resolved factory warned on 33 keys for no reason.
                receiver = known or f"?{receiver}"
                if receiver.startswith("??"):
                    receiver = receiver[1:]
                if method in _SCRIPTS:
                    for element in _script_keys(node):
                        # With this file's names and the project's builders: the key a
                        # script is given is as often `_cold_slots_key(name)` as a
                        # literal, and reading only literals lost the keyspace of the
                        # scripts that run on the hottest path.
                        pattern = _py_pattern(element, key_names,
                                              owners.get(node.lineno, ""), node.lineno,
                                              builders)
                        if not pattern and isinstance(element, ast.Name):
                            pattern = _key_named(key_names,
                                                 owners.get(node.lineno, ""),
                                                 element.id, node.lineno)
                        if pattern and _looks_like_key(pattern):
                            hits.append(_Hit(
                                _normalise(pattern), pattern, rel, node.lineno,
                                False, False, receiver, "eval", False,
                                owners.get(node.lineno, ""),
                            ))
                    continue
                # A command handed a LIST of keys touches every one of them: inline,
                # by name, or splatted. `r.delete(*keys_to_clear)` and
                # `cache.delete_many([...])` are how bulk invalidation is written.
                held = node.args[0]
                inner = held.value if isinstance(held, ast.Starred) else held
                listed: list[str] = []
                if isinstance(inner, (ast.List, ast.Tuple)):
                    listed = [q for q in
                              (_py_pattern(e, key_names, owners.get(node.lineno, ""),
                                           node.lineno, builders) for e in inner.elts)
                              if q and _looks_like_key(q)]
                elif isinstance(inner, ast.Name):
                    where = owners.get(node.lineno, "")
                    listed = (lists_here.get((where, inner.id))
                              or lists_here.get(("", inner.id)) or [])
                    # A name that came out of a scan. Fed to a read, that is the read of
                    # the keyspace the scan swept; fed to a delete, it is the sweep the
                    # scan already stands for, and stays evidence rather than a claim -
                    # or every wipe script reports the keys it wipes as invalidated.
                    if not listed and method not in _WRITES:
                        listed = swept_here.get((where, inner.id)) or []
                if listed:
                    for one in dict.fromkeys(listed):
                        hits.append(_Hit(
                            _normalise(one), one, rel, node.lineno,
                            method in _WRITES, ttl_of(node, method), receiver, method,
                            nx_of(node, method), owners.get(node.lineno, ""),
                        ))
                    continue
                pattern = _py_pattern(node.args[0], key_names,
                                      owners.get(node.lineno, ""), node.lineno, builders)
                # `self.CACHE_KEY` / `cls.CACHE_KEY` / `Service.CACHE_KEY`: a constant on
                # the class body, which is where a service keeps the name of its own
                # cache. Only a bare name was understood, so those reads were invisible
                # and the key read as one that is only ever deleted.
                held = node.args[0]
                if not pattern and isinstance(held, ast.Attribute) \
                        and isinstance(held.value, ast.Name):
                    pattern = _key_named(key_names, "", held.attr, node.lineno) \
                        or _key_named(key_names, owners.get(node.lineno, ""), held.attr,
                                      node.lineno)
                if not pattern and isinstance(node.args[0], ast.Name):
                    # The key was built into a local a line or two above.
                    # The enclosing def first, then the module: a key held in a constant
                    # at the top of the file is written inside a handler further down,
                    # which is how the Celery health hashes are named.
                    here_owner = owners.get(node.lineno, "")
                    pattern = _key_named(key_names, here_owner, node.args[0].id,
                                         node.lineno)
                    if pattern and not _looks_like_key(pattern):
                        # A fragment, not a key. Good enough to splice into an f-string,
                        # not good enough to be one on its own.
                        pattern = ""
                    if not pattern:
                        # ...and last, a key this function was HANDED. One helper called
                        # with two keys writes both, so every pattern gets its own hit.
                        handed = parameter_keys.get(
                            (here_owner.rpartition(".")[2], node.args[0].id), ())
                        for given in sorted(handed):
                            hits.append(_Hit(
                                _normalise(given), given, rel, node.lineno,
                                method in _WRITES, ttl_of(node, method), receiver,
                                method, nx_of(node, method), here_owner,
                            ))
                        if handed:
                            continue
                if not pattern or not _looks_like_key(pattern):
                    continue
                ttl = ttl_of(node, method)
                # A third positional argument to `set` is the expiry in BOTH APIs -
                # Django's `cache.set(key, value, timeout)` and redis-py's `ex`. Reading
                # only `ex=`/`timeout=` keywords called six expiring caches leaks.
                # `set(key, v, nx=True)` or setnx(): a lock, whose return value is the read.
                nx = nx_of(node, method)
                hits.append(_Hit(
                    _normalise(pattern), pattern, rel, node.lineno,
                    method in _WRITES, ttl, receiver, method, nx,
                    owners.get(node.lineno, ""),
                ))

    # A counter that answered is a read as well as a write - one line, both halves, which
    # is what the code does. Done here rather than at each of the five places a hit is
    # made, so the rule has one home.
    for hit in list(hits):
        if (hit.write and hit.method in _COUNTERS
                and hit.line in counter_reads.get(hit.file, ())):
            hits.append(_Hit(hit.key, hit.raw, hit.file, hit.line, False, hit.ttl,
                             hit.receiver, hit.method, hit.nx, hit.owner))

    # The names no single file could answer for.
    for hit in hits:
        if hit.receiver.startswith("?"):
            # Two hops at most: an alias for a singleton for a factory.
            name = singletons.get(hit.receiver[1:], "")
            hit.receiver = singletons.get(name.lstrip("?"), name) if name.startswith("?") \
                else name

    for name, node, rel, owner, _client in pending:
        known = wrappers.get(name)
        if not known:
            continue
        index, method = known
        if index >= len(node.args):
            continue
        pattern = _py_pattern(node.args[index])
        if not pattern or not _looks_like_key(pattern):
            continue
        ttl = any(k.arg in _TTL_KWARGS for k in node.keywords if k.arg) or \
            method in ("setex", "psetex", "expire", "pexpire")
        nx = method == "setnx" or any(k.arg == "nx" for k in node.keywords)
        hits.append(_Hit(
            # No receiver. The caller's name for the client - `r` here, `redis_client`
            # there, `cache` in the third module - says nothing about WHICH connection it
            # is, and the "touched through more than one client" check compares names.
            # Feeding it three aliases for one client turned eleven correct keys
            # uncertain, which is the same mistake the pipeline fix had to undo.
            _normalise(pattern), pattern, rel, node.lineno,
            method in _WRITES, ttl, "", method, nx, owner,
        ))
    return hits


# ── JavaScript ────────────────────────────────────────────────────────────
def _has_nx_option(node) -> bool:
    """`{nx: true}` or `{NX: true}` as an options object argument."""
    if not isinstance(node, dict) or node.get("type") != "ObjectExpression":
        return False
    for prop in node.get("properties") or []:
        key = prop.get("key") or {}
        name = key.get("name") or key.get("value")
        if isinstance(name, str) and name.lower() == "nx":
            return True
    return False


def _js_pattern(node) -> str | None:
    if not isinstance(node, dict):
        return None
    if node.get("type") == "Literal" and isinstance(node.get("value"), str):
        return node["value"]
    if node.get("type") == "TemplateLiteral":
        parts = []
        quasis = node.get("quasis") or []
        for index, piece in enumerate(quasis):
            parts.append(((piece.get("value") or {}).get("cooked")) or "")
            if index < len(quasis) - 1:
                parts.append("${}")
        return "".join(parts)
    return None


def _js_pipeline_names(tree: dict) -> dict[str, str]:
    """`const pipe = redis.pipeline()` / `redis.multi()`, mapped to the client."""
    found: dict[str, str] = {}
    for node, _ in _walk(tree):
        if node.get("type") != "VariableDeclarator":
            continue
        name = (node.get("id") or {}).get("name")
        init = node.get("init") or {}
        if not name or init.get("type") != "CallExpression":
            continue
        callee = init.get("callee") or {}
        if ((callee.get("property") or {}).get("name")) in _PIPELINE_FACTORIES:
            owner = callee.get("object") or {}
            found[name] = (owner.get("name")
                           or ((owner.get("property") or {}).get("name")) or "")
    return found


def _scan_js(root: str) -> list[_Hit]:
    paths: list[str] = []
    for here, subdirectories, names in os.walk(root):
        subdirectories[:] = [
            d for d in subdirectories if d not in _PY_SKIP and not d.startswith(".")
        ]
        paths.extend(
            os.path.join(here, name) for name in names if name.endswith(_JS_EXTENSIONS)
        )
    paths = [p for p in paths if _mentions(p, _JS_NEEDLES)]
    if not paths:
        return []
    hits: list[_Hit] = []
    for path, tree in iter_parsed(paths, report_failures=False):
        rel = os.path.relpath(path, root)
        pipelines = _js_pipeline_names(tree)
        for node, _ in _walk(tree):
            if node.get("type") != "CallExpression":
                continue
            callee = node.get("callee") or {}
            if callee.get("type") != "MemberExpression":
                continue
            method = ((callee.get("property") or {}).get("name")) or ""
            if method.lower() not in _ALL:
                continue
            args = node.get("arguments") or []
            if not args:
                continue
            obj = callee.get("object") or {}
            receiver = obj.get("name") or ((obj.get("property") or {}).get("name")) or ""
            if receiver in pipelines:
                receiver = pipelines[receiver] or receiver
            elif not _RECEIVER.search(receiver):
                continue
            pattern = _js_pattern(args[0])
            if not pattern or not _looks_like_key(pattern):
                continue
            lowered = method.lower()
            ttl = lowered in ("setex", "psetex", "expire", "pexpire") or any(
                _js_pattern(a) in ("EX", "PX") for a in args[1:]
            )
            # ioredis: set(key, v, 'NX') · upstash/node-redis: set(key, v, {nx: true})
            nx = lowered == "setnx" or any(
                _js_pattern(a) == "NX" or _has_nx_option(a) for a in args[1:]
            )
            line = node_line(node) or 0
            hits.append(_Hit(
                _normalise(pattern), pattern, rel, line,
                lowered in _WRITES, ttl, receiver, lowered, nx,
            ))
    return hits


# Files this reads no code from, but which are still part of the project. A key named
# in one is written in this repository - `celery:beat:crashloop` is set by the beat
# wrapper, a shell script with Python inside it - and "written nowhere in this repo" is
# then simply false.
_UNPARSED = (".sh", ".bash", ".zsh", ".lua", ".yml", ".yaml", ".toml", ".conf", ".cfg",
             ".ini", ".env", ".sql", ".tf", ".rb", ".go", ".php", ".rs", ".java", ".kt")


def _named_elsewhere(root: str, keys: set[str]) -> dict[str, str]:
    """key -> the first unparsed file that spells it out, for the keys that need it.

    Only for keys about to be CLAIMED: the search is a plain substring scan and its
    whole job is to stop the tool saying "nowhere in this repo" about something that is
    demonstrably in the repo.
    """
    if not keys:
        return {}
    found: dict[str, str] = {}
    for here, subdirectories, names in os.walk(root):
        subdirectories[:] = [
            d for d in subdirectories if d not in SKIP_DIRS and not d.startswith(".")
        ]
        for name in names:
            if not name.endswith(_UNPARSED):
                continue
            path = os.path.join(here, name)
            try:
                with open(path, encoding="utf-8", errors="replace") as handle:
                    text = handle.read()
            except OSError:
                continue
            for key in keys - set(found):
                if key in text:
                    found[key] = os.path.relpath(path, root)
    return found


_CLEANUP_NOTE = (
    "Only ever invalidated, and every one of those deletes is erasure or teardown - a "
    "GDPR wipe, a logout, a harness reset. Finding nothing is what those are FOR, so this "
    "is true and unactionable: the key is dead, the code is correct, and removing the "
    "delete would only mean an old account could keep a key forever. Kept apart from the "
    "invalidations that were supposed to clear something, which are the ones worth reading.")


def _is_cleanup_context(hit: _Hit) -> bool:
    """A delete inside an erasure or teardown path - by the name of the function it sits in,
    or of the module, since a `gdpr_service.py` is that whole file's job."""
    return bool(_CLEANUP_CONTEXT.search(hit.owner or "")
                or _CLEANUP_CONTEXT.search(os.path.basename(hit.file or "")))


def extract_redis(root: str) -> tuple[list[Symbol], list[Edge]]:
    hits = _scan_python(root) + _scan_js(root)
    if not hits:
        return [], []

    by_key: dict[str, list[_Hit]] = {}
    for hit in hits:
        by_key.setdefault(hit.key, []).append(hit)

    symbols: list[Symbol] = []
    edges: list[Edge] = []

    # `pps:records:t*` read here and `pps:records:tnormal` written there are two
    # spellings of one keyspace. Matched rather than merged, so the concrete keys keep
    # their own rows and the pattern stops being reported as a read nothing satisfies.
    def overlaps(one: str, other: str) -> bool:
        """Can these two patterns name the same key?

        Segment by segment, because a Redis key is a colon-separated namespace and its
        depth is part of its identity. `user:*` and `user:*:current_streak` share a
        prefix and are not the same key - letting the first vouch for the second
        silently cleared four real findings, counters read in two places and written in
        none. Within a segment a `*` is a hole and may match either way round.
        """
        left, right = one.split(":"), other.split(":")
        if len(left) != len(right):
            return False
        for a, b in zip(left, right, strict=True):
            if a == b:
                continue
            if "*" not in a and "*" not in b:
                return False
            # A bare `*` where this key has a name: is the OTHER key a namespace SWEEP,
            # or is it that keyspace? `user:*:*` spells out one segment and is what a
            # GDPR wipe scans - letting it vouch cleared four real findings.
            # `pps:board:i:*` spells out three and simply IS the board. The difference is
            # how much of the key the pattern actually names.
            if b == "*" and "*" not in a:
                spelt = sum(1 for part in right if part and "*" not in part)
                if spelt < 3:
                    return False
            shape_a = re.compile("^" + ".*".join(re.escape(p) for p in a.split("*")) + "$")
            shape_b = re.compile("^" + ".*".join(re.escape(p) for p in b.split("*")) + "$")
            if not (shape_a.match(b) or shape_b.match(a)):
                return False
        return True

    matches: dict[str, list[str]] = {}
    for key in by_key:
        # A pattern with no namespace of its own - `*:progress` - would match every key
        # that happens to end in the same word. That is a connection built from a shared
        # noun, not from evidence, so a key has to begin with something real.
        #
        # Concrete keys are candidates too: `pps:board:i:mouse` is one of the names
        # `pps:board:i:*` covers, and only wildcards were ever matched, so a concrete key
        # could never be reconciled with the pattern that writes it.
        if len(key.split("*")[0]) < 4:
            continue
        # `overlaps(key, other)`, not the other way round: the question is whether the
        # OTHER key is specific enough to be evidence for this one.
        hit = [k for k in by_key if k != key and overlaps(key, k)]
        if hit:
            matches[key] = hit

    elsewhere: set[str] = set()
    pending_claims: list[int] = []
    for key, group in sorted(by_key.items()):
        # A Lua call names its keys and hides its commands: EVALSHA is neither a read
        # nor a write here, it is evidence that something this cannot read touches the
        # key. Counting it as a read reported the WebSocket connection counter - which
        # a script increments on every connect - as a lookup that can only ever miss.
        # A scan sweeps a keyspace; whether the next line reads the values or deletes
        # them is not this line's business. Evidence that the keyspace is visited, like
        # a Lua call - never a read, or every cleanup script invents one.
        scripted = [h for h in group if h.method in ("eval", "scan")]
        plain = [h for h in group if h.method not in ("eval", "scan")]
        writers = [h for h in plain if h.write]
        readers = [h for h in plain if not h.write]
        if not plain:
            symbols.append(Symbol(
                id=f"redis_key:{key}", kind="redis_key", label=key,
                sub=(f"{len(scripted)} scan" if scripted[0].method == "scan"
                     else f"{len(scripted)} script"), file=scripted[0].file,
                line=scripted[0].line, status=Status.UNCERTAIN, snippet=scripted[0].raw,
                chain=[key], owner=scripted[0].owner,
                note=("Only ever swept by a `scan(match=…)`. That enumerates the "
                      "keyspace; whether the caller then reads the values or deletes "
                      "them is the next line's business, and not something this can see."
                      if scripted[0].method == "scan" else
                      "Only ever passed to a Lua script (EVAL/EVALSHA). The script names "
                      "this key and its commands are not in this source, so what it does "
                      "with it cannot be read here."),
            ))
            # ...and where. Skipping the per-site rows left these keys on the map with no
            # path to any line at all - the reader could see the key and not one place it
            # is touched, which is the opposite of what the map is for.
            for hit in scripted:
                use_id = f"redis_key_use:{key}:{hit.file}:{hit.line}"
                symbols.append(Symbol(
                    id=use_id, kind="redis_key_use", label=key,
                    sub="runs a script over", file=hit.file, line=hit.line,
                    status=Status.CONNECTED, snippet=hit.raw, chain=[key], owner=hit.owner,
                    note="This line hands the key to a Lua script. What the script does "
                         "with it is decided by the script, not here.",
                ))
                edges.append(Edge(use_id, f"redis_key:{key}", Status.CONNECTED))
            continue
        where = writers[0] if writers else readers[0]

        # A `set(key, …, nx=True)` is a lock or a dedupe guard: the RETURN VALUE is the read
        # (`if not acquired: return`), and nothing will ever call get() on it. Four of one
        # project's nine "written, never read" keys were exactly this pattern, by design.
        locks = [h for h in writers if h.nx]
        if writers and readers:
            status = Status.CONNECTED
            note = ""
        elif locks and not readers:
            status = Status.CONNECTED
            note = ("Set with NX - a lock or dedupe guard. Its return value is the read, "
                    "so no get() is expected.")
        elif readers:
            status = Status.UNRESOLVED
            note = ("Read here and written nowhere in this repo, so this lookup can only "
                    "ever miss - which fails silently as a permanent cache miss.")
        elif all(h.method in _INVALIDATIONS for h in writers):
            # Only ever invalidated: deleted, unlinked, expired. Never stored, never read.
            #
            # That is not a dead key, it is the opposite - deleting a key is a statement
            # that something PUT it there, and the only way to see no store is for the
            # store to be somewhere this scan cannot follow. Measured on the reference
            # project: `api:user_stats:*` was reported "6 write / 0 read - unused" while
            # every one of the six was a `cache.delete(f"api:user_stats:{uid}")` and the
            # actual write lives inside a cache helper that builds the key from its
            # arguments (`swr_get(name="user_stats", user_id=...)`), so the literal never
            # appears at the write site at all.
            #
            # This was the one finding class a consumer said they could not act on: 107
            # rows left untouched, not because they were checked and dismissed, but
            # because the category could not be trusted. Deleting is evidence, so this
            # declines to call it dead and says which evidence is missing.
            status = Status.UNUSED
            note = ("Only ever invalidated - deleted or expired, never stored and never "
                    "read, and nothing in this repository writes it. This was hedged for "
                    "a long time on the reasoning that a delete proves a writer exists "
                    "somewhere the scan could not follow. The three places it could not "
                    "follow - the async cache API, a key built into a local, a key handed "
                    "to a helper - are all read now, so the hedge was hiding the finding: "
                    "an invalidation left behind after the cache it cleared moved or went "
                    "away. Every delete of it is a round-trip that clears nothing.")
        else:
            status = Status.UNUSED
            note = "Written here and read nowhere in this repo."

        if key.startswith("*") and status in (Status.UNRESOLVED, Status.UNUSED):
            # No namespace of its own: the prefix is decided at runtime, so there is no
            # saying which key this is - let alone that nothing else touches it.
            status = Status.UNCERTAIN
            note = ("This key's namespace is decided at runtime - the prefix comes from a "
                    "lookup, not from the source - so which key it names is not something "
                    "that can be read here.")
        if key in matches and status in (Status.UNRESOLVED, Status.UNUSED):
            other = ", ".join(sorted(matches[key])[:3])
            status = Status.CONNECTED
            note = (f"The other half is spelled out: `{other}` matches this pattern and "
                    f"is touched elsewhere in this repository.")
        if status in (Status.UNRESOLVED, Status.UNUSED) and "*" not in key:
            elsewhere.add(key)
        if scripted and status in (Status.UNRESOLVED, Status.UNUSED):
            status = Status.UNCERTAIN
            note = (("Also swept by a `scan(match=…)`. "
                     if any(h.method == "scan" for h in scripted) else
                     "Also passed to a Lua script (EVAL/EVALSHA), whose commands are not "
                     "in this source. ") + note.rstrip(".") +
                    " - unless the script is the other half, which is what a script "
                    "given this key usually is.")

        # A key touched through two different clients is the wrong-instance bug: the write
        # lands on one connection and the delete on another, so the stale value survives.
        def language(hit):
            return "py" if hit.file.endswith(".py") else "js"

        same_language = [
            h for h in group if language(h) == language(group[0])
        ]
        clients = {h.receiver for h in same_language if h.receiver}
        # A read replica is the primary's own copy: a write on the primary and a read on
        # the replica are the same store, one to two seconds apart. Only READS go there -
        # a replica refuses writes - so a replica-only client can never be the half that
        # holds the stale value. Dropped from the comparison, never from the evidence.
        if len(clients) > 1:
            replicas = {name for name in clients if "replica" in name.lower()}
            if not any(h.write and h.receiver in replicas for h in same_language):
                clients = clients - replicas
        # `get_redis_client` and `get_redis_client#0` are one factory, and whether the
        # bare call means db 0 is not in the source. An unstated database is not a
        # different one - the same mistake as reading `ar` and `r` as two connections.
        factories = collections.defaultdict(set)
        for name in clients:
            base, _, db = name.partition("#")
            factories[base].add(db)
        differ = len(factories) > 1 or any(
            len(dbs - {""}) > 1 for dbs in factories.values())
        if status is Status.CONNECTED and differ and len(same_language) == len(group):
            status = Status.UNCERTAIN
            note = ("Touched through more than one client (" + ", ".join(sorted(clients)) +
                    "). If those are different Redis instances or databases, a write on "
                    "one and a delete on the other both succeed and the stale value "
                    "survives.")

        if status in (Status.UNRESOLVED, Status.UNUSED):
            pending_claims.append(len(symbols))

        # Deleting is not writing, and a row reading "6 write / 0 read" over a key that is
        # only ever deleted describes the opposite of what the code does.
        invalidation_only = bool(writers) and all(
            h.method in _INVALIDATIONS for h in writers)

        # A write with no reader and a DELETE with no writer are opposite findings sharing
        # one red, and on the reference project they shared it 43 : 17 - with every fix
        # that mattered coming from the 17. A write nobody reads is usually deliberate:
        # telemetry and audit trails exist to be read by a human with redis-cli after an
        # incident, and no scan will ever see that reader. A delete nobody writes is
        # almost always a bug AND is invisible by construction - DEL on a missing key
        # returns 0, raises nothing, logs nothing, and the surrounding code reads as
        # working invalidation. That is how a stale-cache defect survived a full test
        # suite: the suite compared the API against the DOM, and a stale cache makes both
        # surfaces agree. Two views of one number can only prove they came from the same
        # place. So they are two kinds, ranked apart, rather than one number to tune.
        kind = "redis_key"
        if invalidation_only and status is Status.UNUSED:
            kind = ("redis_cleanup"
                    if all(_is_cleanup_context(h) for h in writers)
                    else "redis_invalidation")
            note = (_CLEANUP_NOTE if kind == "redis_cleanup" else note)
        symbols.append(Symbol(
            id=f"redis_key:{key}", kind=kind, label=key,
            sub=(f"{len(writers)} invalidate / {len(readers)} read"
                 if invalidation_only
                 else f"{len(writers)} write / {len(readers)} read"),
            file=where.file, line=where.line, status=status,
            snippet=where.raw, chain=sorted({h.file for h in group})[:4], note=note,
            owner=where.owner,
        ))

        # Where each touch of this key actually happens. The key symbol above is one per
        # NAME - which is right for pairing a write against a read, and wrong for saying
        # which handler does it: a key is written in a cache module and read in another,
        # and the symbol lands on whichever site was seen first.
        #
        # Without a per-site anchor a chain stops dead at the handler, so the map can draw
        # browser -> seam -> server and then nothing, even though the server plainly talks
        # to a store. `db_table_use` has always worked this way; Redis simply never did.
        #
        # Evidence, never a claim: this says a line touches a key, which is not a verdict
        # about anything. The verdict stays on the key symbol, where both halves are known.
        # One row per key per PLACE. A script with eight commands on one key, run from
        # one line, produced eight rows sharing a single id - and the row a reader clicks
        # is a place in the code, not a command inside a string. A write outranks a read
        # outranks a bare script touch: if the site writes the key at all, that is what
        # it does.
        best: dict[tuple[str, int], _Hit] = {}
        for hit in group:
            rank = 2 if hit.write else 1 if hit.method != "eval" else 0
            at = (hit.file, hit.line)
            standing = best.get(at)
            if standing is None or rank > (
                    2 if standing.write else 1 if standing.method != "eval" else 0):
                best[at] = hit
        for hit in best.values():
            use_id = f"redis_key_use:{key}:{hit.file}:{hit.line}"
            symbols.append(Symbol(
                id=use_id, kind="redis_key_use", label=key,
                sub=("runs a script over" if hit.method == "eval"
                     else "writes" if hit.write else "reads"),
                file=hit.file, line=hit.line, status=Status.CONNECTED,
                snippet=hit.raw, chain=[key], owner=hit.owner,
                note=("This line hands the key to a Lua script; what the script does "
                      "with it is decided by the script, not here."
                      if hit.method == "eval" else
                      "This line " + ("writes" if hit.write else "reads") +
                      " the key. Whether the two halves agree is decided on the key "
                      "itself, not here."),
            ))
            edges.append(Edge(use_id, f"redis_key:{key}", Status.CONNECTED))

        # The TTL check, and only where the key says it is disposable: a permanent key with
        # no expiry is correct, and flagging those would bury the ones that matter.
        # A separate `expire(key, ...)` beside the write IS the expiry: a fixed-window
        # rate limiter is `incr(key)` then `expire(key, period, nx=True)`, and reading
        # the write on its own called it a key Redis keeps forever.
        expires = any(h.ttl for h in group)
        leaking = [] if expires else [
            h for h in writers
            if (not h.ttl and _CACHE_ISH.match(h.raw) and h.key.split(":")[0] != "*"
                    and h.method not in _NOT_STORES)
        ]
        for hit in leaking:
            symbols.append(Symbol(
                id=f"redis_ttl:{key}:{hit.file}:{hit.line}", kind="redis_ttl",
                label=key, sub="no expiry", file=hit.file, line=hit.line,
                status=Status.UNRESOLVED, snippet=hit.raw, chain=[key], owner=hit.owner,
                note="This key names itself a cache and is written without an expiry, so "
                     "Redis keeps it forever.",
            ))
            edges.append(Edge(
                f"redis_ttl:{key}:{hit.file}:{hit.line}", f"redis_key:{key}",
                Status.UNRESOLVED,
            ))

    # One pass over the files this reads no code from, for the keys it was about to
    # claim. A key the beat wrapper sets is written in this repository.
    seen = _named_elsewhere(root, elsewhere)
    for index in pending_claims:
        symbol = symbols[index]
        where = seen.get(symbol.label)
        if not where:
            continue
        symbols[index] = replace(
            symbol, status=Status.UNCERTAIN,
            note=(f"Named in `{where}`, which this reads no code from - a shell wrapper, "
                  f"a job definition or a Lua file. It is not written nowhere; it is "
                  f"written somewhere this cannot follow. " + (symbol.note or "")),
        )

    return symbols, edges
