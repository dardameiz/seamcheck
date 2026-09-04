"""A command sent through a pipeline is a command.

`r.pipeline()` returns an object whose method calls are the same command set, and the
receiver test matched the client by NAME - so `pipe.set(...)`, `pipe.hincrby(...)` and
`hist_pipe.hset(...)` were invisible. Pipelining is not an edge case in the reference
project, it is the house style: the page-render path alone queues 30+ operations on one
pipe, so the lens read the cold paths correctly and mis-reported the hottest ones.
"""
import pathlib
import tempfile
import textwrap

from django.test import SimpleTestCase

from seamcheck.extractors.redis_extractor import extract_redis


def _project(files: dict[str, str]) -> str:
    root = pathlib.Path(tempfile.mkdtemp())
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text), encoding="utf-8")
    return str(root)


def _keys(root: str) -> dict[str, tuple[str, str]]:
    symbols, _ = extract_redis(root)
    return {s.label: (s.status.value, s.sub) for s in symbols if s.kind == "redis_key"}


def _note(root: str, label: str) -> str:
    symbols, _ = extract_redis(root)
    return next((s.note or "" for s in symbols
                 if s.kind == "redis_key" and s.label == label), "")


class PipelineWritesTests(SimpleTestCase):
    def test_a_write_through_a_pipeline_is_a_write(self):
        keys = _keys(_project({"views.py": """
            import redis
            r = redis.Redis()

            def read_it():
                return r.get('admin:global_stats')

            def bump(n):
                pipe = r.pipeline()
                pipe.hincrby('admin:global_stats', 'lifetime_pushes', n)
                pipe.execute()
        """}))
        # Read directly, written through the pipe: connected, not "read here and written
        # nowhere in this repo".
        self.assertEqual(keys["admin:global_stats"][0], "connected")

    def test_the_with_form_counts_too(self):
        keys = _keys(_project({"views.py": """
            import redis
            r = redis.Redis()

            def history():
                with r.pipeline() as hist_pipe:
                    hist_pipe.hset('analytics:history:concurrent', 'x', 1)
        """}))
        self.assertIn("analytics:history:concurrent", keys)

    def test_a_pipeline_is_not_a_second_client(self):
        # The "touched through more than one client" check exists to catch a write on one
        # Redis instance and a delete on another. A pipeline is the client it came from,
        # and counting it as another one turned sixteen connected keys uncertain.
        keys = _keys(_project({"views.py": """
            import redis
            r = redis.Redis()

            def touch():
                pipe = r.pipeline()
                pipe.set('user:1:stats', 1)
                return r.get('user:1:stats')
        """}))
        self.assertEqual(keys["user:1:stats"][0], "connected")

    def test_a_pipe_that_is_not_a_redis_pipeline_is_ignored(self):
        # By ASSIGNMENT, not by name: a variable is a pipeline because it came from
        # `.pipeline()`. Matching the name would count every `pipe` in an
        # image-processing module as a Redis client.
        keys = _keys(_project({"images.py": """
            def render(source):
                pipe = source.open_pipe()
                pipe.set('not:a:redis:key', 1)
        """}))
        self.assertEqual(keys, {})

    def test_javascript_pipelines_too(self):
        keys = _keys(_project({
            "package.json": '{"dependencies": {"ioredis": "^5"}}',
            "app.js": """
                import { redis } from './client';
                export async function bump() {
                  const pipe = redis.pipeline();
                  pipe.set('queue:jobs', 1);
                  await pipe.exec();
                }
                export async function read() {
                  return redis.get('queue:jobs');
                }
            """,
        }))
        self.assertEqual(keys.get("queue:jobs", ("", ""))[0], "connected")


class WrapperTests(SimpleTestCase):
    """A project's own wrapper around the client is still the client.

    `safe_set(r, "store:current_period", value, ex=3600)` is a SET. The reference project
    routes most of its Redis through `safe_get`/`safe_set` - wrappers that exist to
    recover from WRONGTYPE - so a key with six writes read as a key with none.
    """

    HELPERS = """
        import redis

        def safe_get(r, key, default=None):
            try:
                return r.get(key)
            except redis.ResponseError:
                return default

        def safe_set(r, key, value, ex=None):
            return r.set(key, value, ex=ex)
    """

    def test_a_wrapper_call_is_the_command_it_wraps(self):
        keys = _keys(_project({
            "helpers.py": self.HELPERS,
            "views.py": """
                from helpers import safe_get, safe_set
                r = None

                def write(value):
                    safe_set(r, "store:current_period", value, ex=3600)

                def read():
                    return safe_get(r, "store:current_period")
            """,
        }))
        self.assertEqual(keys.get("store:current_period", ("", ""))[0], "connected")

    def test_the_wrapper_is_learned_from_its_body_not_its_name(self):
        # A function called `set_the_table` is not a Redis command. The rule is that the
        # body calls `<first parameter>.<command>(<that parameter>)` - nothing else.
        keys = _keys(_project({
            "helpers.py": """
                def set_the_table(guests, key):
                    guests.append(key)
            """,
            "views.py": """
                from helpers import set_the_table
                redis_client = None

                def go():
                    set_the_table(redis_client, "cache:not:a:key")
            """,
        }))
        self.assertEqual(keys, {})

    def test_the_key_is_taken_from_the_argument_the_wrapper_passes(self):
        keys = _keys(_project({
            "helpers.py": """
                def store(value, key, r):
                    return r.setex(key, 60, value)
            """,
            "views.py": """
                from helpers import store
                r = None

                def go():
                    store(1, "cache:third:arg", r)
            """,
        }))
        # arg 0 is a client-shaped name only in the caller's eyes; the wrapper says the
        # key is argument 1, and that is what gets read.
        self.assertNotIn("cache:third:arg", keys)


class ClientIdentityTests(SimpleTestCase):
    """Which connection a client IS, rather than what the file happens to call it.

    The "touched through more than one client" warning is there for a real bug - a write
    on db 0 and a delete on db 6, where the stale value survives - and it compared
    variable NAMES. On the reference project the commonest pair was `ar` and `r`: the
    async and sync clients from the same factory, pointed at the same server, on 21 keys.
    """

    ASYNC_AND_SYNC = {
        "app/write.py": """
            from app.redis import get_async_redis_client

            async def store(uid):
                ar = get_async_redis_client()
                await ar.set(f"user:{uid}:stats", "1")
        """,
        "app/read.py": """
            from app.redis import get_redis_client

            def load(uid):
                r = get_redis_client()
                return r.get(f"user:{uid}:stats")
        """,
    }

    def test_the_async_and_sync_client_from_one_factory_are_one_client(self):
        rows = _keys(_project(self.ASYNC_AND_SYNC))
        self.assertEqual(rows["user:*:stats"][0], "connected")

    def test_a_different_factory_is_still_worth_saying(self):
        # `r_replica` is a read replica: a real second instance, and a write here with a
        # read there is the bug the warning exists for.
        rows = _keys(_project({
            "app/write.py": """
                from app.redis import get_redis_client

                def store(uid):
                    r = get_redis_client()
                    r.set(f"user:{uid}:stats", "1")
            """,
            "app/read.py": """
                from app.redis import get_replica_client

                def load(uid):
                    r_replica = get_replica_client()
                    return r_replica.get(f"user:{uid}:stats")
            """,
        }))
        self.assertEqual(rows["user:*:stats"][0], "uncertain")

    def test_an_explicit_database_number_is_a_different_client(self):
        rows = _keys(_project({
            "app/write.py": """
                from app.redis import get_redis_client

                def store(uid):
                    r = get_redis_client(db=0)
                    r.set(f"user:{uid}:stats", "1")
            """,
            "app/read.py": """
                from app.redis import get_redis_client

                def load(uid):
                    r = get_redis_client(db=6)
                    return r.get(f"user:{uid}:stats")
            """,
        }))
        self.assertEqual(rows["user:*:stats"][0], "uncertain")

    def test_a_client_this_could_not_trace_is_not_a_second_connection(self):
        # `r` here is a parameter: this file never sees what made it. Comparing that
        # against a resolved `get_redis_client` produced "touched through more than one
        # client (get_redis_client, r)" on 33 keys - a warning built from one name it
        # knew and one it did not. An unknown client is not evidence of a second one.
        rows = _keys(_project({
            "app/write.py": """
                from app.redis import get_redis_client

                def store(uid):
                    r = get_redis_client()
                    r.set(f"user:{uid}:stats", "1")
            """,
            "app/read.py": """
                def load(r, uid):
                    return r.get(f"user:{uid}:stats")
            """,
        }))
        self.assertEqual(rows["user:*:stats"][0], "connected")

    def test_an_awaited_factory_and_a_with_block_are_traced_too(self):
        root = _project({
            "app/write.py": """
                from app.redis import get_async_redis_client

                async def store(uid):
                    ar = await get_async_redis_client()
                    await ar.set(f"user:{uid}:stats", "1")
            """,
            "app/read.py": """
                from app.redis import get_replica_client

                def load(uid):
                    with get_replica_client() as replica:
                        return replica.get(f"user:{uid}:stats")
            """,
        })
        rows = _keys(root)
        # Two real connections, traced through two shapes neither of which is a plain
        # assignment of a plain call.
        self.assertEqual(rows["user:*:stats"][0], "uncertain")
        self.assertIn("get_replica_client", _note(root, "user:*:stats"))


class KeyInAVariableTests(SimpleTestCase):
    """`cache_key = f"api:user_stats:{uid}"` and then `cache.set(cache_key, ...)`.

    65 keys on the reference project read "1 invalidate / 0 read": the DELETE writes the
    literal and the write does not, because the key was built into a local one line
    earlier. That is the house style for every cached endpoint in the project, so the
    lens saw every invalidation and none of the writes it proves must exist.
    """

    def test_a_key_built_into_a_local_is_still_that_key(self):
        rows = _keys(_project({"app/views.py": """
            from django.core.cache import cache

            def stats(uid):
                cache_key = f"api:user_stats:{uid}"
                found = cache.get(cache_key)
                if found is None:
                    found = build(uid)
                    cache.set(cache_key, found, timeout=30)
                return found
        """}))
        self.assertEqual(rows["api:user_stats:*"][0], "connected")
        self.assertEqual(rows["api:user_stats:*"][1], "1 write / 1 read")

    def test_a_name_reused_for_two_keys_means_the_nearest_one_above(self):
        # `err_key` is assigned in the 5xx branch and again in the 4xx branch, each with
        # its write right underneath. Dropping the name because it meant two things left
        # both error counters reported as read-by-nobody; taking whichever was assigned
        # LAST would have attached both writes to the 4xx key.
        rows = _keys(_project({"app/mw.py": """
            from app.redis import get_redis_client

            def record(endpoint, bucket, status):
                pipe = get_redis_client().pipeline()
                if status >= 500:
                    err_key = f"analytics:errors:5xx:{endpoint}:{bucket}"
                    pipe.incr(err_key)
                elif status >= 400:
                    err_key = f"analytics:errors:4xx:{endpoint}:{bucket}"
                    pipe.incr(err_key)
        """}))
        self.assertEqual(rows["analytics:errors:5xx:*:*"][1], "1 write / 0 read")
        self.assertEqual(rows["analytics:errors:4xx:*:*"][1], "1 write / 0 read")

    def test_two_functions_may_each_have_their_own_cache_key(self):
        rows = _keys(_project({"app/views.py": """
            from django.core.cache import cache

            def stats(uid):
                cache_key = f"api:user_stats:{uid}"
                cache.set(cache_key, 1, timeout=30)

            def pbits(uid):
                cache_key = f"api:current_pbits:{uid}"
                return cache.get(cache_key)
        """}))
        self.assertEqual(rows["api:user_stats:*"][1], "1 write / 0 read")
        self.assertEqual(rows["api:current_pbits:*"][1], "0 write / 1 read")


class ExpiryAndConstantsTests(SimpleTestCase):
    def test_an_expire_beside_the_write_is_the_expiry(self):
        # `pipe.incr(key)` then `pipe.expire(key, period, nx=True)` is how a fixed-window
        # rate limiter is written, and reading the write alone called it a key Redis
        # keeps forever. The expiry is a property of the KEY, not of one call.
        rows = _keys(_project({"app/limit.py": """
            from app.redis import get_redis_client

            def allow(request, period):
                r = get_redis_client()
                key = f"rate_limit:{request.user}:{request.path}"
                pipe = r.pipeline(transaction=True)
                pipe.incr(key)
                pipe.expire(key, period, nx=True)
                return pipe.execute()
        """}))
        self.assertIn("rate_limit:*:*", rows)
        symbols, _ = extract_redis(_project({"app/limit.py": """
            from app.redis import get_redis_client

            def allow(request, period):
                r = get_redis_client()
                key = f"rate_limit:{request.user}:{request.path}"
                pipe = r.pipeline(transaction=True)
                pipe.incr(key)
                pipe.expire(key, period, nx=True)
                return pipe.execute()
        """}))
        self.assertEqual([s for s in symbols if s.kind == "redis_ttl"], [])

    def test_a_key_held_in_a_module_constant_is_still_that_key(self):
        # `_CELERY_HEALTH_KEY_SUCCESS = 'celery:task:last_success'` at the top of the
        # file, written inside a handler further down. Scoping the lookup to the
        # enclosing def meant the write was never found and five live dashboard reads
        # were reported as "read here and written nowhere".
        rows = _keys(_project({
            "app/celery.py": """
                from app.redis import get_redis_client

                KEY_SUCCESS = "celery:task:last_success"

                def record(task):
                    get_redis_client().hset(KEY_SUCCESS, task, 1)
            """,
            "app/views.py": """
                from app.redis import get_redis_client

                def health():
                    return get_redis_client().hgetall("celery:task:last_success")
            """,
        }))
        self.assertEqual(rows["celery:task:last_success"][0], "connected")

    def test_an_unstated_database_is_not_a_different_database(self):
        # `get_redis_client()` and `get_redis_client(db=0)` are the same factory, and
        # whether the bare one means db 0 is not in the source. Treating the two as
        # different connections is the same mistake as treating `ar` and `r` as two.
        rows = _keys(_project({
            "app/write.py": """
                from app.redis import get_redis_client

                def store(uid):
                    r = get_redis_client(db=0)
                    r.set(f"user:{uid}:stats", "1")
            """,
            "app/read.py": """
                from app.redis import get_redis_client

                def load(uid):
                    r = get_redis_client()
                    return r.get(f"user:{uid}:stats")
            """,
        }))
        self.assertEqual(rows["user:*:stats"][0], "connected")

    def test_a_timeout_passed_positionally_is_still_a_timeout(self):
        # `cache.set(key, value, 3600)` is Django's signature and redis-py's `ex`. Only
        # reading `ex=`/`timeout=` keywords called six correctly-expiring caches leaks.
        symbols, _ = extract_redis(_project({"app/svc.py": """
            from django.core.cache import cache

            def warm(page, size):
                cache_key = f"cache:pps:lb:{page}:{size}"
                cache.set(cache_key, [], 300)
        """}))
        self.assertEqual([s.label for s in symbols if s.kind == "redis_ttl"], [])

    def test_a_cache_written_with_no_expiry_at_all_is_still_said(self):
        symbols, _ = extract_redis(_project({"app/svc.py": """
            from django.core.cache import cache

            def warm(page):
                cache_key = f"cache:pps:lb:{page}:all"
                cache.set(cache_key, [])
        """}))
        self.assertEqual([s.label for s in symbols if s.kind == "redis_ttl"],
                         ["cache:pps:lb:*:all"])


class CrossFileNamesTests(SimpleTestCase):
    def test_a_key_constant_is_followed_into_the_file_that_uses_it(self):
        # `WS_CONNECTIONS_KEY = 'ws:active_connections'` is declared beside the writer
        # and read from a monitoring module three packages away, which spells the
        # literal out. Only the reader was visible, so a live counter was reported as a
        # lookup that can only ever miss.
        rows = _keys(_project({
            "app/gateway.py": """
                from app.redis import get_redis_client

                WS_CONNECTIONS_KEY = "ws:active_connections"

                def joined():
                    get_redis_client().incr(WS_CONNECTIONS_KEY)
            """,
            "app/scaling.py": """
                from app.redis import get_redis_client

                def load():
                    return int(get_redis_client().get("ws:active_connections") or 0)
            """,
        }))
        self.assertEqual(rows["ws:active_connections"][0], "connected")

    def test_a_key_a_lua_script_touches_is_not_a_key_nothing_writes(self):
        # `ar.evalsha(sha, 1, WS_CONNECTIONS_KEY)` is how the connection counter is
        # incremented, and the increment is inside the script. The read is plain and
        # visible, so the key read "0 write / 1 read" - a lookup that can only ever
        # miss - about a counter that is written on every WebSocket connect.
        root = _project({"app/gateway.py": """
            from app.redis import get_redis_client

            WS_CONNECTIONS_KEY = "ws:active_connections"

            async def joined(ar, sha):
                await ar.evalsha(sha, 1, WS_CONNECTIONS_KEY)

            async def count(ar):
                return await ar.get(WS_CONNECTIONS_KEY)
        """})
        rows = _keys(root)
        self.assertEqual(rows["ws:active_connections"][0], "uncertain")
        self.assertIn("script", _note(root, "ws:active_connections").lower())

    def test_a_lua_key_given_by_keyword_counts_too(self):
        root = _project({"app/hero.py": """
            from app.redis import get_redis_client

            HERO_PUSH_KEY = "index:hero_push_count"

            async def bump(ar, sha, amount):
                await ar.evalsha(sha, keys=[HERO_PUSH_KEY], args=[amount])

            async def show(ar):
                return await ar.get(HERO_PUSH_KEY)
        """})
        self.assertEqual(_keys(root)["index:hero_push_count"][0], "uncertain")

    def test_an_async_wrapper_is_a_wrapper(self):
        # `async def a_safe_rpush(ar, key, *values)` - the async half of every safe_*
        # helper in the reference project. `ar` is not `redis`, `cache`, `client` or
        # exactly `r`, so the whole async write path read as not-Redis.
        rows = _keys(_project({
            "app/helpers.py": """
                async def a_safe_rpush(ar, key, *values):
                    return await ar.rpush(key, *values)
            """,
            "app/store.py": """
                from app.helpers import a_safe_rpush

                async def gift(ar, recipient_id, data):
                    await a_safe_rpush(ar, f"user:{recipient_id}:pending_gifts", data)
            """,
            "app/read.py": """
                from app.redis import get_redis_client

                def pending(uid):
                    return get_redis_client().lrange(f"user:{uid}:pending_gifts", 0, -1)
            """,
        }))
        self.assertEqual(rows["user:*:pending_gifts"][0], "connected")

    def test_a_registered_script_is_a_script(self):
        # `cap_script = ar.register_script(src)` then `await cap_script(keys=[KEY], ...)`.
        # The call is on the Script object redis-py hands back, so the method is not
        # `evalsha` and nothing said the key was touched at all.
        root = _project({"app/hero.py": """
            from app.redis import get_redis_client

            HERO_PUSH_KEY = "index:hero_push_count"

            async def bump(ar, amount):
                cap_script = ar.register_script("return redis.call('INCRBY', KEYS[1], 1)")
                return int(await cap_script(keys=[HERO_PUSH_KEY], args=[amount]))

            async def show(ar):
                return await ar.get(HERO_PUSH_KEY)
        """})
        self.assertEqual(_keys(root)["index:hero_push_count"][0], "uncertain")

    def test_a_wildcard_read_meets_the_concrete_keys_it_matches(self):
        # `zrevrange(f"pps:records:t{name}")` reads `pps:records:t*`; the writes name
        # `pps:records:tnormal` and `pps:records:tdrag`. Two spellings of one keyspace,
        # and the read was reported as a lookup that can only ever miss.
        rows = _keys(_project({
            "app/read.py": """
                from app.redis import get_redis_client

                def best(name):
                    return get_redis_client().zrevrange(f"pps:records:t{name}", 0, 9)
            """,
            "app/write.py": """
                from app.redis import get_redis_client

                def record(score):
                    get_redis_client().zadd("pps:records:tnormal", {"1": score})
            """,
        }))
        self.assertEqual(rows["pps:records:t*"][0], "connected")


class OtherSpellingsTests(SimpleTestCase):
    def test_a_keys_keyword_on_any_call_is_a_script_run(self):
        # `_get_max_cas_script(r)(keys=[...], args=[...])`: the callee is an expression,
        # so nothing matched a script name. `keys=` IS the signature - that is what it
        # is for - so the keyword is the evidence, not the callee.
        root = _project({"app/an.py": """
            from app.redis import get_redis_client

            def peak(r, total):
                return int(_cas(r)(keys=['analytics:history:concurrent:max'],
                                   args=[total, 90000]))

            def show(r):
                return r.get('analytics:history:concurrent:max')
        """})
        self.assertEqual(_keys(root)["analytics:history:concurrent:max"][0], "uncertain")

    def test_a_client_imported_under_another_name_is_a_client(self):
        # `from app.redis_client import redis_client as _r_ann`. The alias is not
        # client-shaped and the import is the only place its real name appears.
        rows = _keys(_project({
            "app/admin.py": """
                def broadcast():
                    from app.redis_client import redis_client as _r_ann
                    _r_ann.set("announcements:new_broadcast", "1", ex=60)
            """,
            "app/views.py": """
                from app.redis import get_redis_client

                def unread():
                    return get_redis_client().get("announcements:new_broadcast")
            """,
        }))
        self.assertEqual(rows["announcements:new_broadcast"][0], "connected")

    def test_a_key_written_inside_an_embedded_lua_script_counts(self):
        # `local stats_key = "pps:stats:by_input"` inside the Lua source, which is a
        # Python string. The HSET is in the script; the read outside it is plain, so the
        # key read as one nothing writes.
        root = _project({"app/pps.py": """
            from app.redis import get_redis_client

            SCRIPT = '''
            local stats_key = "pps:stats:by_input"
            redis.call("HINCRBY", stats_key, ARGV[1], 1)
            '''

            def show():
                return get_redis_client().hgetall('pps:stats:by_input')
        """})
        self.assertEqual(_keys(root)["pps:stats:by_input"][0], "uncertain")
        self.assertIn("script", _note(root, "pps:stats:by_input").lower())

    def test_a_key_named_in_a_file_this_does_not_parse_is_not_claimed(self):
        # `celery:beat:crashloop` is set by a shell script that embeds Python. It IS
        # written in this repository, and "written nowhere in this repo" is false.
        root = _project({
            "app/health.py": """
                from app.redis import get_redis_client

                def beat():
                    return bool(get_redis_client().get('celery:beat:crashloop'))
            """,
            "start_beat.sh": """
                #!/bin/bash
                python - <<'EOF'
                r.set('celery:beat:crashloop', '1', ex=900)
                EOF
            """,
        })
        self.assertEqual(_keys(root)["celery:beat:crashloop"][0], "uncertain")
        self.assertIn("start_beat.sh", _note(root, "celery:beat:crashloop"))

    def test_a_key_built_on_top_of_another_key_keeps_its_prefix(self):
        # `base = f'challenges:schedule:django_{id}'` then `zscore(f'{base}:bonus_claimed')`.
        # Turning every `{...}` into `*` made the composed key `*:bonus_claimed`, which
        # matches nothing and reads as a lookup that can only ever miss.
        rows = _keys(_project({"app/admin.py": """
            from app.redis import get_redis_client

            def claimed(r, schedule_id, uid):
                base = f'challenges:schedule:django_{schedule_id}'
                return r.zscore(f'{base}:bonus_claimed', uid)

            def award(r, schedule_id, uid):
                r.zadd(f'challenges:schedule:django_{schedule_id}:bonus_claimed', {uid: 1})
        """}))
        self.assertIn("challenges:schedule:django_*:bonus_claimed", rows)
        self.assertEqual(rows["challenges:schedule:django_*:bonus_claimed"][0], "connected")

    def test_two_patterns_that_describe_one_keyspace_meet(self):
        rows = _keys(_project({
            "app/read.py": """
                from app.redis import get_redis_client

                def joined(r, sid):
                    return r.zcard(f'challenges:schedule:django_{sid}:joined')
            """,
            "app/write.py": """
                from app.redis import get_redis_client

                def join(r, kind, sid, uid):
                    r.zadd(f'challenges:schedule:{kind}_{sid}:joined', {uid: 1})
            """,
        }))
        self.assertEqual(rows["challenges:schedule:django_*:joined"][0], "connected")

    def test_a_pattern_with_nothing_specific_in_it_matches_nothing(self):
        # `*:progress` against every key that happens to end in `:progress` would be a
        # connection built from a shared word, not from evidence. It is `uncertain`
        # either way - see the runtime-namespace test below - and what matters here is
        # that the unrelated key is not offered as its other half.
        root = _project({
            "app/read.py": """
                from app.redis import get_redis_client

                def show(r, sk, uid):
                    return r.zscore(f"{sk}:progress", uid)
            """,
            "app/write.py": """
                from app.redis import get_redis_client

                def bump(r, uid):
                    r.zadd("unrelated:daily:progress", {uid: 1})
            """,
        })
        rows = _keys(root)
        self.assertEqual(rows["*:progress"][0], "uncertain")
        self.assertNotIn("unrelated", _note(root, "*:progress"))

    def test_a_namespace_prefix_does_not_vouch_for_everything_under_it(self):
        # `user:*` and `user:*:current_streak` are not the same key, and letting the
        # first stand as evidence for the second silently cleared four real findings -
        # counters read in two places that nothing in the project writes.
        rows = _keys(_project({
            "app/read.py": """
                from app.redis import get_redis_client

                def streak(r, uid):
                    return r.get(f"user:{uid}:current_streak")
            """,
            "app/scan.py": """
                from app.redis import get_redis_client

                def wipe(r, uid):
                    r.delete(f"user:{uid}:{'*'}")
                    r.delete(f"user:{uid}:{'x'}")
            """,
            "app/write.py": """
                from app.redis import get_redis_client

                def stats(r, uid):
                    r.hset(f"user:{uid}:stats", "pushes", 1)
            """,
        }))
        self.assertEqual(rows["user:*:current_streak"][0], "unresolved")

    def test_a_fragment_held_in_a_variable_is_spliced_in_too(self):
        # `schedule_id = f'django_{obj.id}'` is not a key on its own - no namespace, no
        # colon - and the key is built around it. Leaving that hole as `*` made the
        # writer `challenges:schedule:*:joined` and the reader
        # `challenges:schedule:django_*:joined`: one keyspace, two spellings, three
        # findings.
        rows = _keys(_project({
            "app/admin.py": """
                from app.redis import get_redis_client

                def sync(r, obj, uid, when):
                    schedule_id = f'django_{obj.schedule.id}'
                    r.zadd(f'challenges:schedule:{schedule_id}:joined', {uid: when})
            """,
            "app/read.py": """
                from app.redis import get_redis_client

                def joined(r, s):
                    return r.zcard(f'challenges:schedule:django_{s.id}:joined')
            """,
        }))
        self.assertEqual(rows["challenges:schedule:django_*:joined"][0], "connected")
        self.assertEqual(rows["challenges:schedule:django_*:joined"][1], "1 write / 1 read")

    def test_a_key_whose_namespace_is_a_hole_is_never_claimed(self):
        # `sk = sched_map.get(id)` then `f"{sk}:progress"`: the namespace is decided at
        # runtime, so this cannot say what key it is - let alone that nothing writes it.
        root = _project({"app/admin.py": """
            from app.redis import get_redis_client

            def progress(r, sched_map, obj, uid):
                sk = sched_map.get(str(obj.schedule_id))
                return r.zscore(f"{sk}:progress", uid)
        """})
        self.assertEqual(_keys(root)["*:progress"][0], "uncertain")
        self.assertIn("decided at runtime", _note(root, "*:progress"))
