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
import os
import re

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
    "lrange", "llen", "ttl", "pttl", "getrange", "sscan", "hscan", "type",
})
_WRITES = frozenset({
    "set", "setex", "setnx", "psetex", "mset", "getset", "hset", "hmset", "hsetnx",
    "hincrby", "incr", "incrby", "decr", "decrby", "expire", "pexpire", "delete", "del",
    "unlink", "sadd", "srem", "zadd", "zrem", "zincrby", "lpush", "rpush",
    "append", "setbit", "publish",
})
_ALL = _READS | _WRITES
# Writes that REMOVE or merely touch, and so can never need an expiry.
_NOT_STORES = frozenset({"delete", "del", "unlink", "expire", "pexpire", "publish",
                         "srem", "zrem", "lpop", "rpop"})

# Removing a key, as opposed to storing one. A group made ENTIRELY of these has a writer
# the scan never saw - you cannot invalidate what nothing wrote.
_INVALIDATIONS = frozenset({"delete", "del", "adelete", "delete_many", "adelete_many",
                            "unlink", "expire", "pexpire", "clear"})

# A receiver that says "this is Redis" rather than a dict. Without it, every `d.get(k)` in
# a Python codebase becomes a Redis key and the finding list is noise.
_RECEIVER = re.compile(r"redis|cache|kv|_r\b|^r$|client|conn|pool", re.I)

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
def _py_pattern(node) -> str | None:
    """The key a Python expression names, with its interpolations left as holes."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):  # f-string
        parts = []
        for piece in node.values:
            if isinstance(piece, ast.Constant) and isinstance(piece.value, str):
                parts.append(piece.value)
            else:
                parts.append("{}")
        return "".join(parts)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod):
        return _py_pattern(node.left)
    if isinstance(node, ast.Call):
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == "format":
            return _py_pattern(func.value)
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


def _pipeline_names(tree: ast.AST) -> dict[str, str]:
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
    found: dict[str, str] = {}

    def source(node) -> str | None:
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr in _PIPELINE_FACTORIES):
            return _py_receiver(node.func) or ""
        return None

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            client = source(node.value)
            if client is not None:
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        found[target.id] = client
        elif isinstance(node, ast.withitem):
            client = source(node.context_expr)
            if client is not None and isinstance(node.optional_vars, ast.Name):
                found[node.optional_vars.id] = client
    return found


def _scan_python(root: str) -> list[_Hit]:
    hits: list[_Hit] = []
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
            owners = owners_of(tree)
            pipelines = _pipeline_names(tree)
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                    continue
                method = node.func.attr
                if method not in _ALL or not node.args:
                    continue
                receiver = _py_receiver(node.func)
                if receiver in pipelines:
                    # Reported as the client it came from, so a pipelined write and a
                    # direct read are one client, which is what they are.
                    receiver = pipelines[receiver] or receiver
                elif not _RECEIVER.search(receiver or ""):
                    continue
                pattern = _py_pattern(node.args[0])
                if not pattern or not _looks_like_key(pattern):
                    continue
                ttl = any(k.arg in _TTL_KWARGS for k in node.keywords if k.arg) or \
                    method in ("setex", "psetex", "expire", "pexpire")
                # `set(key, v, nx=True)` or setnx(): a lock, whose return value is the read.
                nx = method == "setnx" or any(k.arg == "nx" for k in node.keywords)
                hits.append(_Hit(
                    _normalise(pattern), pattern, rel, node.lineno,
                    method in _WRITES, ttl, receiver, method, nx,
                    owners.get(node.lineno, ""),
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


def extract_redis(root: str) -> tuple[list[Symbol], list[Edge]]:
    hits = _scan_python(root) + _scan_js(root)
    if not hits:
        return [], []

    by_key: dict[str, list[_Hit]] = {}
    for hit in hits:
        by_key.setdefault(hit.key, []).append(hit)

    symbols: list[Symbol] = []
    edges: list[Edge] = []

    for key, group in sorted(by_key.items()):
        writers = [h for h in group if h.write]
        readers = [h for h in group if not h.write]
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
            status = Status.UNCERTAIN
            note = ("Only ever invalidated here - deleted or expired, never stored and "
                    "never read. Deleting a key is evidence that something writes it, so "
                    "the writer is somewhere this scan cannot follow: usually a cache "
                    "helper that builds the key from its arguments, where the literal "
                    "never appears. Not claimed dead for that reason.")
        else:
            status = Status.UNUSED
            note = "Written here and read nowhere in this repo."

        # A key touched through two different clients is the wrong-instance bug: the write
        # lands on one connection and the delete on another, so the stale value survives.
        def language(hit):
            return "py" if hit.file.endswith(".py") else "js"

        same_language = [
            h for h in group if language(h) == language(group[0])
        ]
        clients = {h.receiver for h in same_language if h.receiver}
        if status is Status.CONNECTED and len(clients) > 1 and len(same_language) == len(group):
            status = Status.UNCERTAIN
            note = ("Touched through more than one client (" + ", ".join(sorted(clients)) +
                    "). If those are different Redis instances or databases, a write on "
                    "one and a delete on the other both succeed and the stale value "
                    "survives.")

        symbols.append(Symbol(
            id=f"redis_key:{key}", kind="redis_key", label=key,
            # Deleting is not writing, and a row reading "6 write / 0 read" over a key
            # that is only ever deleted describes the opposite of what the code does.
            sub=(f"{len(writers)} invalidate / {len(readers)} read"
                 if writers and all(h.method in _INVALIDATIONS for h in writers)
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
        for hit in group:
            use_id = f"redis_key_use:{key}:{hit.file}:{hit.line}"
            symbols.append(Symbol(
                id=use_id, kind="redis_key_use", label=key,
                sub="writes" if hit.write else "reads",
                file=hit.file, line=hit.line, status=Status.CONNECTED,
                snippet=hit.raw, chain=[key], owner=hit.owner,
                note=("This line " + ("writes" if hit.write else "reads") +
                      " the key. Whether the two halves agree is decided on the key "
                      "itself, not here."),
            ))
            edges.append(Edge(use_id, f"redis_key:{key}", Status.CONNECTED))

        # The TTL check, and only where the key says it is disposable: a permanent key with
        # no expiry is correct, and flagging those would bury the ones that matter.
        leaking = [
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

    return symbols, edges
