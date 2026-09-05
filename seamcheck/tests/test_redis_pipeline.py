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
        # This used to assert the key was NOT found: the wrapper rule wants a
        # client-shaped FIRST parameter and `store(value, key, r)` has not got one, so
        # nothing here was a wrapper. Following the argument into the parameter answers
        # it properly - the caller hands `key` a literal and the body writes it - and
        # a limitation is not a rule worth keeping.
        self.assertEqual(keys["cache:third:arg"], ("unused", "1 write / 0 read"))


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
        # `redis.call("HINCRBY", stats_key, ...)` is right there in the script, so this
        # is a write, not a mystery - see LuaBodyTests for the commands themselves.
        self.assertEqual(_keys(root)["pps:stats:by_input"][0], "connected")
        self.assertEqual(_keys(root)["pps:stats:by_input"][1], "1 write / 1 read")

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

    def test_a_specific_pattern_does_vouch_for_the_names_under_it(self):
        # `pps:board:i:*` carries three literal segments - it IS that keyspace, and
        # `pps:board:i:mouse` is one of its names. A sweep like `user:*:*` carries one
        # and is not evidence about anything; the difference is how much of the key the
        # pattern actually spells out.
        rows = _keys(_project({
            "app/write.py": """
                from app.redis import get_redis_client

                def seed(r, kind, score):
                    r.zadd(f"pps:board:i:{kind}", {"1": score})
            """,
            "app/read.py": """
                from app.redis import get_redis_client

                def board(r):
                    return r.zrevrange("pps:board:i:mouse", 0, 9)
            """,
        }))
        self.assertEqual(rows["pps:board:i:mouse"][0], "connected")

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


class ProxyClientTests(SimpleTestCase):
    def test_a_lazy_proxy_is_the_client_it_forwards_to(self):
        # `class RedisClient: def __getattr__(self, n): return getattr(get_redis_client(), n)`
        # and `redis_client = RedisClient()`. Two names for one connection, and the
        # "touched through more than one client" warning fired on 33 keys because of it.
        rows = _keys(_project({
            "app/redis_client.py": """
                def get_redis_client():
                    return _connect()

                class RedisClient:
                    def __getattr__(self, name):
                        return getattr(get_redis_client(), name)

                redis_client = RedisClient()
            """,
            "app/write.py": """
                from app.redis_client import redis_client

                def store(uid):
                    redis_client.set(f"user:{uid}:stats", "1")
            """,
            "app/read.py": """
                from app.redis_client import get_redis_client

                def load(uid):
                    r = get_redis_client()
                    return r.get(f"user:{uid}:stats")
            """,
        }))
        self.assertEqual(rows["user:*:stats"][0], "connected")

    def test_a_proxy_onto_a_different_factory_is_a_different_client(self):
        rows = _keys(_project({
            "app/redis_client.py": """
                class MonitoringRedisClient:
                    def __getattr__(self, name):
                        return getattr(get_monitoring_redis_client(), name)

                monitoring_redis_client = MonitoringRedisClient()
            """,
            "app/write.py": """
                from app.redis_client import monitoring_redis_client

                def store(uid):
                    monitoring_redis_client.set(f"user:{uid}:stats", "1")
            """,
            "app/read.py": """
                from app.redis_client import get_redis_client

                def load(uid):
                    r = get_redis_client()
                    return r.get(f"user:{uid}:stats")
            """,
        }))
        self.assertEqual(rows["user:*:stats"][0], "uncertain")


class LuaBodyTests(SimpleTestCase):
    """The commands inside an embedded Lua script, which are plain text in the source.

    47 keys on the reference project read "only ever passed to a Lua script" - true, and
    the script is right there in a Python string. `local lb_key = "pps:leaderboard"` and
    `redis.call("ZSCORE", lb_key, uid)` says as much about that key as any Python line.
    """

    SCRIPT = '''
        from app.redis import get_redis_client

        LUA = """
        local user_key  = "pps:user:" .. uid
        local lb_key    = "pps:leaderboard"
        local stats_key = "pps:stats:by_input"

        local best = redis.call("HGET", user_key, "best_pps")
        redis.call("ZADD", lb_key, new_score, uid)
        redis.call("HINCRBY", stats_key, ARGV[1], 1)
        """

        def run(r, uid):
            return r.eval(LUA, 0)
    '''

    def test_a_read_inside_the_script_is_a_read(self):
        rows = _keys(_project({"app/pps.py": self.SCRIPT}))
        self.assertEqual(rows["pps:user:*"][1], "0 write / 1 read")

    def test_a_write_inside_the_script_is_a_write(self):
        rows = _keys(_project({"app/pps.py": self.SCRIPT}))
        self.assertEqual(rows["pps:leaderboard"][1], "1 write / 0 read")
        self.assertEqual(rows["pps:stats:by_input"][1], "1 write / 0 read")

    def test_the_script_meets_the_python_that_reads_the_same_key(self):
        rows = _keys(_project({
            "app/pps.py": self.SCRIPT,
            "app/views.py": """
                from app.redis import get_redis_client

                def board():
                    return get_redis_client().zrevrange("pps:leaderboard", 0, 9)
            """,
        }))
        self.assertEqual(rows["pps:leaderboard"][0], "connected")

    def test_a_key_the_script_only_names_is_still_only_named(self):
        # `KEYS[1]` is passed in from Python and the script's own text says nothing
        # about which key that is.
        rows = _keys(_project({"app/x.py": '''
            LUA = """
            redis.call("INCRBY", KEYS[1], 1)
            local other = "cache:known:thing"
            redis.call("DEL", other)
            """
        '''}))
        self.assertEqual(rows["cache:known:thing"][1], "1 invalidate / 0 read")

    def test_a_docstring_that_explains_lua_is_not_lua(self):
        # Caught by this tool scanning itself: the docstring that describes this very
        # feature quotes `local lb_key = "pps:leaderboard"` and a `redis.call`, and the
        # lens read the explanation as a script. Prose about code is not code.
        symbols, _ = extract_redis(_project({"app/x.py": '''
            """Reading Lua bodies.

            `local lb_key = "pps:leaderboard"` and then `redis.call("ZADD", lb_key, x)`
            says as much about that key as any Python line does.
            """

            def helper():
                """Also not Lua: redis.call("HGET", "cache:example:thing", f) explained."""
        '''}))
        self.assertEqual([s.label for s in symbols if s.kind == "redis_key"], [])

    def test_a_lua_command_points_at_its_own_line(self):
        # The whole script is one Python string, so every command in it shares the
        # string's line number - and the map row for a write on line 40 of the script
        # opened line 1 of it. A path that points at the wrong line is a path a reader
        # cannot follow.
        symbols, _ = extract_redis(_project({"app/pps.py": '''
            LUA = """
            local a = "cache:first:key"
            local b = "cache:second:key"

            redis.call("HGET", a, "x")
            redis.call("ZADD", b, 1, "y")
            """
        '''}))
        lines = {s.label: s.line for s in symbols if s.kind == "redis_key_use"}
        self.assertLess(lines["cache:first:key"], lines["cache:second:key"])

    def test_a_script_touch_does_not_say_it_reads(self):
        # `evalsha(sha, 1, KEY)` names the key and hides the command. Rendering it as
        # "reads" puts a claim on the map that the evidence does not support.
        symbols, _ = extract_redis(_project({"app/g.py": """
            from app.redis import get_redis_client

            KEY = "ws:active_connections"

            async def joined(ar, sha):
                await ar.evalsha(sha, 1, KEY)
        """}))
        subs = {s.sub for s in symbols if s.kind == "redis_key_use"}
        self.assertEqual(subs, {"runs a script over"})

    def test_the_float_and_range_commands_are_commands(self):
        # Checking the Lua paths against the source turned this up: `HINCRBY` was a
        # write and `HINCRBYFLOAT` on the very next line was "runs a script over",
        # because the table had one and not the other. Same for the sorted-set ranges,
        # which is most of what a leaderboard does.
        symbols, _ = extract_redis(_project({"app/pps.py": '''
            LUA = """
            redis.call("HINCRBYFLOAT", "cache:agg:sums", "x", 1.5)
            redis.call("ZREMRANGEBYSCORE", "cache:window:hits", 0, 100)
            local seen = redis.call("ZRANGEBYSCORE", "cache:window:hits", 0, 100)
            local n = redis.call("HDEL", "cache:agg:sums", "y")
            """
        '''}))
        rows = {s.label: s.sub for s in symbols if s.kind == "redis_key"}
        self.assertEqual(rows["cache:agg:sums"], "2 write / 0 read")
        self.assertEqual(rows["cache:window:hits"], "1 write / 1 read")

    def test_a_script_belongs_to_the_function_that_runs_it(self):
        # The Lua body is a module-level constant, so every key it touched was owned by
        # nobody - and the function filter on the map, which is how a reader asks "what
        # does this handler touch", could not reach a single one of those writes. The
        # script belongs to whoever runs it.
        symbols, _ = extract_redis(_project({"app/pps.py": '''
            SUBMIT = """
            redis.call("ZADD", "pps:leaderboard", 1, uid)
            """

            class PPSService:
                def submit(self, r, uid):
                    script = r.register_script(SUBMIT)
                    return script(keys=[], args=[uid])
        '''}))
        owners = {s.owner for s in symbols
                  if s.kind == "redis_key_use" and s.label == "pps:leaderboard"}
        self.assertIn("PPSService.submit", owners)

    def test_a_script_nobody_runs_stays_where_it_is_written(self):
        symbols, _ = extract_redis(_project({"app/pps.py": '''
            UNUSED_SCRIPT = """
            redis.call("ZADD", "pps:leaderboard", 1, uid)
            """
        '''}))
        self.assertEqual([s.label for s in symbols if s.kind == "redis_key_use"],
                         ["pps:leaderboard"])

    def test_one_row_per_key_per_call_site(self):
        # A script with eight commands on one key, run from one line, produced eight
        # rows sharing a single id - and the map row a reader clicks is a place in the
        # code, not a command in a string. A write outranks a read: if the script writes
        # the key at all, that is what the site does.
        symbols, _ = extract_redis(_project({"app/pps.py": '''
            SUBMIT = """
            local n = redis.call("HGET", "pps:stats:by_input", "x")
            redis.call("HINCRBY", "pps:stats:by_input", "a", 1)
            redis.call("HINCRBYFLOAT", "pps:stats:by_input", "b", 1.5)
            """

            def submit(r):
                return r.eval(SUBMIT, 0)
        '''}))
        rows = [s for s in symbols
                if s.kind == "redis_key_use" and s.label == "pps:stats:by_input"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].sub, "writes")
        self.assertEqual(len({s.id for s in symbols}), len(symbols))

    def test_a_lua_field_suffix_is_not_a_key(self):
        # `redis.call("HINCRBYFLOAT", stats_key, prev_input .. ":sum", x)` - `":sum"` is
        # half a hash field being concatenated, and reading it as a key produced rows
        # labelled `:sum` whose ids collided with each other.
        symbols, _ = extract_redis(_project({"app/pps.py": '''
            LUA = """
            redis.call("HINCRBYFLOAT", "cache:agg:sums", prev .. ":sum", -best)
            redis.call("HINCRBY", "cache:agg:sums", input .. ":n", 1)
            """
        '''}))
        labels = {s.label for s in symbols if s.kind == "redis_key"}
        self.assertEqual(labels, {"cache:agg:sums"})

    def test_a_hash_field_argument_is_not_a_key(self):
        # `redis.call("HINCRBY", stats_key, "unknown:n", -1)` - the third argument is a
        # field inside the hash. Scooping every quoted string out of the script made
        # `unknown:n` and `unknown:sum` into keys of their own.
        symbols, _ = extract_redis(_project({"app/pps.py": '''
            LUA = """
            local stats_key = "cache:agg:sums"
            local spare_key = "cache:agg:spare"
            redis.call("HINCRBY", stats_key, "unknown:n", -1)
            """
        '''}))
        labels = {s.label for s in symbols if s.kind == "redis_key"}
        # The declared locals are keys; the field argument is not.
        self.assertEqual(labels, {"cache:agg:sums", "cache:agg:spare"})


class AsyncCacheApiTests(SimpleTestCase):
    """Django's async cache API, which the lens could half-see.

    `adelete` was in the invalidation set and `aget`/`aset` were in nothing at all. On the
    reference project that is **96 aget and 56 aset calls invisible while 23 adelete were
    read** - so 45 keys came back "only ever invalidated here", a category built entirely
    out of the half of the API that was missing. The tool was describing its own blind
    spot and blaming the code.
    """

    def test_an_async_get_and_set_pair_like_any_other(self):
        rows = _keys(_project({"app/views.py": """
            from django.core.cache import cache

            async def stats(uid):
                cache_key = f"api:user_stats:{uid}"
                found = await cache.aget(cache_key)
                if found is None:
                    found = await build(uid)
                    await cache.aset(cache_key, found, 30)
                return found
        """}))
        self.assertEqual(rows["api:user_stats:*"], ("connected", "1 write / 1 read"))

    def test_an_async_delete_beside_an_async_write_is_not_invalidate_only(self):
        root = _project({
            "app/views.py": """
                from django.core.cache import cache

                async def show(uid):
                    return await cache.aget(f"api:current_pbits:{uid}")
            """,
            "app/store.py": """
                from django.core.cache import cache

                async def buy(uid, value):
                    await cache.aset(f"api:current_pbits:{uid}", value, 10)
                    await cache.adelete(f"api:current_pbits:{uid}")
            """,
        })
        self.assertEqual(_keys(root)["api:current_pbits:*"][0], "connected")
        self.assertNotIn("invalidated", _note(root, "api:current_pbits:*"))

    def test_add_sets_only_when_absent_and_its_return_is_the_read(self):
        # `cache.add(key, ...)` is Django's SETNX: it answers "did I get it", which is a
        # lock, and nothing will ever call get() on it.
        rows = _keys(_project({"app/lock.py": """
            from django.core.cache import cache

            async def once(uid):
                return await cache.aadd(f"lock:daily:{uid}", 1, 60)
        """}))
        self.assertEqual(rows["lock:daily:*"][0], "connected")


class KeyPassedToAHelperTests(SimpleTestCase):
    """The key is built in the caller and written inside the helper it is handed to.

    ```
    cache_key = f"cache:avatar:{user_id}"
    cached = await cache.aget(cache_key)          # the read, visible
    return await _build(request, user, cache_key)  # ...and it goes in here
    ...
    async def _build(request, user, cache_key):
        await cache.aset(cache_key, data, 30)      # the write, invisible
    ```

    Inside the helper `cache_key` is a PARAMETER, so nothing in that file assigns it and
    the write cannot be seen. This is how every cached endpoint on the reference project
    is written, and it is most of what was left of "only ever invalidated here".
    """

    def test_a_key_handed_to_a_helper_is_written_there(self):
        rows = _keys(_project({"app/views.py": """
            from django.core.cache import cache

            async def avatar(request, user_id):
                cache_key = f"cache:avatar:{user_id}"
                found = await cache.aget(cache_key)
                if found is not None:
                    return found
                return await _build(request, cache_key)

            async def _build(request, cache_key):
                data = await compute(request)
                await cache.aset(cache_key, data, 30)
                return data
        """}))
        self.assertEqual(rows["cache:avatar:*"], ("connected", "1 write / 1 read"))

    def test_the_helper_may_live_in_another_file(self):
        rows = _keys(_project({
            "app/views.py": """
                from django.core.cache import cache

                from .build import build_pending

                async def pending(user):
                    key = f"pending_announcements:{user.id}"
                    found = await cache.aget(key)
                    return found if found else await build_pending(user, key)
            """,
            "app/build.py": """
                from django.core.cache import cache

                async def build_pending(user, pending_cache_key):
                    data = await collect(user)
                    await cache.aset(pending_cache_key, data, 30)
                    return data
            """,
        }))
        self.assertEqual(rows["pending_announcements:*"][0], "connected")

    def test_one_helper_called_with_two_keys_writes_both(self):
        rows = _keys(_project({"app/views.py": """
            from django.core.cache import cache

            async def one(uid):
                return await _store(f"cache:one:{uid}")

            async def two(uid):
                return await _store(f"cache:two:{uid}")

            async def _store(cache_key):
                await cache.aset(cache_key, 1, 30)
        """}))
        self.assertEqual(rows["cache:one:*"][1], "1 write / 0 read")
        self.assertEqual(rows["cache:two:*"][1], "1 write / 0 read")


class ClassConstantKeyTests(SimpleTestCase):
    def test_a_key_held_on_the_class_is_still_that_key(self):
        # `CACHE_KEY = "level_rewards:config"` in the class body, read as
        # `cache.get(self.CACHE_KEY)`. The lookup only understood a bare name, so the
        # read was invisible and the key read as one that is only ever deleted.
        rows = _keys(_project({"app/service.py": """
            from django.core.cache import cache

            class LevelRewardService:
                CACHE_KEY = "level_rewards:config"

                def get(self):
                    found = cache.get(self.CACHE_KEY)
                    if found is None:
                        found = build()
                        cache.set(self.CACHE_KEY, found, 300)
                    return found

                @classmethod
                def bust(cls):
                    cache.delete(cls.CACHE_KEY)
        """}))
        # Two writes: the `set` and the `delete`. An invalidation counts as a write
        # here - it only reads as "invalidate" when EVERY writer is one.
        self.assertEqual(rows["level_rewards:config"], ("connected", "2 write / 1 read"))


class InvalidationWithNoWriterTests(SimpleTestCase):
    """A key only ever deleted, once the reasons not to say so are gone.

    This used to be `uncertain`, on the reasoning that *"deleting a key is evidence that
    something writes it, so the writer is somewhere this scan cannot follow"*. That was
    the right call while the writer usually WAS somewhere it could not follow: the async
    cache API was unreadable, a key built into a local was unreadable, and a key handed
    to a helper was unreadable.

    All three are readable now, and the reasoning has inverted. On the reference project
    `api:user_stats:{uid}` appears fourteen times and **every one is a delete** - the
    endpoint moved to the `swr:user_stats:*` keyspace and the invalidations were left
    behind, so fourteen round-trips on the purchase and push paths clear a key nothing
    writes. Hedging on that hides it.
    """

    def test_a_key_only_ever_deleted_is_a_finding(self):
        root = _project({
            "app/views.py": """
                from django.core.cache import cache

                async def buy(uid):
                    await cache.adelete(f"api:user_stats:{uid}")

                async def gift(uid):
                    await cache.adelete(f"api:user_stats:{uid}")
            """,
        })
        rows = _keys(root)
        self.assertEqual(rows["api:user_stats:*"][0], "unused")
        self.assertEqual(rows["api:user_stats:*"][1], "2 invalidate / 0 read")
        self.assertIn("nothing in this repository writes", _note(root, "api:user_stats:*"))

    def test_a_key_that_is_written_somewhere_is_not(self):
        rows = _keys(_project({
            "app/views.py": """
                from django.core.cache import cache

                async def buy(uid):
                    await cache.adelete(f"api:user_stats:{uid}")
            """,
            "app/api.py": """
                from django.core.cache import cache

                async def stats(uid):
                    found = await cache.aget(f"api:user_stats:{uid}")
                    if found is None:
                        await cache.aset(f"api:user_stats:{uid}", 1, 30)
                    return found
            """,
        }))
        self.assertEqual(rows["api:user_stats:*"][0], "connected")

    def test_a_key_named_in_a_file_this_cannot_read_is_still_not_claimed(self):
        root = _project({
            "app/views.py": """
                from django.core.cache import cache

                def bust():
                    cache.delete("cache:warm:index")
            """,
            "warm.sh": """
                #!/bin/bash
                redis-cli SET cache:warm:index 1
            """,
        })
        self.assertEqual(_keys(root)["cache:warm:index"][0], "uncertain")


class KeyBuilderFunctionTests(SimpleTestCase):
    def test_a_function_that_returns_a_key_names_that_key(self):
        # ```
        # def achievements_version_key(user_id):
        #     return f'user:{user_id}:achievements_ver'
        # ...
        # key = achievements_version_key(user_id)
        # cache.incr(key)
        # ```
        # The literal appears once, in the builder, and never at a call site. The read
        # elsewhere spells it out, so the key came back "read here and written nowhere"
        # about a counter that is incremented on every achievement change.
        rows = _keys(_project({
            "app/cache.py": """
                from django.core.cache import cache

                def achievements_version_key(user_id):
                    return f'user:{user_id}:achievements_ver'

                def bump(user_id):
                    key = achievements_version_key(user_id)
                    cache.incr(key)
            """,
            "app/views.py": """
                from django.core.cache import cache

                async def show(user_id):
                    return await cache.aget(f'user:{user_id}:achievements_ver') or '0'
            """,
        }))
        self.assertEqual(rows["user:*:achievements_ver"][0], "connected")

    def test_the_builder_may_be_called_straight_into_the_command(self):
        rows = _keys(_project({"app/cache.py": """
            from django.core.cache import cache

            def version_key(user_id):
                return f'user:{user_id}:ach_ver'

            def bump(user_id):
                cache.set(version_key(user_id), 1, 86400)

            def read(user_id):
                return cache.get(version_key(user_id))
        """}))
        self.assertEqual(rows["user:*:ach_ver"], ("connected", "1 write / 1 read"))

    def test_a_function_that_returns_something_else_is_not_a_builder(self):
        rows = _keys(_project({"app/cache.py": """
            from django.core.cache import cache

            def label(user_id):
                return f'Player {user_id}'

            def go(user_id):
                cache.set(label(user_id), 1, 60)
        """}))
        self.assertEqual(rows, {})


class ListOfKeysTests(SimpleTestCase):
    """A command handed a LIST of keys touches every one of them.

    ```
    keys_to_clear = ["push_arena:is_hourly_mode", "push_arena:reset_timezone"]
    r.delete(*keys_to_clear)
    ```

    and `cache.delete_many([...])`, and `keys.append(f"...")` then `delete_many(keys)`.
    The literals are right there and the command is right there, and nothing joined them
    because the argument is a list rather than a key. This is how every bulk
    invalidation in the reference project is written.
    """

    def test_a_splatted_list_of_literals_is_a_touch_of_each(self):
        rows = _keys(_project({"app/signals.py": """
            from app.redis import get_redis_client

            def on_mode_change(instance):
                r = get_redis_client()
                keys_to_clear = [
                    "push_arena:is_hourly_mode",
                    "push_arena:reset_timezone",
                ]
                r.delete(*keys_to_clear)
        """}))
        self.assertEqual(rows["push_arena:is_hourly_mode"][1], "1 invalidate / 0 read")
        self.assertEqual(rows["push_arena:reset_timezone"][1], "1 invalidate / 0 read")

    def test_delete_many_takes_its_list_inline(self):
        rows = _keys(_project({"app/caches.py": """
            from django.core.cache import cache

            def bust(user_id):
                cache.delete_many([
                    f'cache:avatar:{user_id}',
                    f'lobby_sidebar:{user_id}',
                ])
        """}))
        self.assertIn("cache:avatar:*", rows)
        self.assertIn("lobby_sidebar:*", rows)

    def test_a_list_built_by_append_is_followed(self):
        rows = _keys(_project({"app/lobby.py": """
            from django.core.cache import cache

            def share(recipients):
                keys_to_drop = []
                for rid in recipients:
                    keys_to_drop.append(f'navbar:{rid}')
                    keys_to_drop.append(f'api:current_pbits:{rid}')
                cache.delete_many(keys_to_drop)
        """}))
        self.assertIn("navbar:*", rows)
        self.assertIn("api:current_pbits:*", rows)

    def test_a_list_of_things_that_are_not_keys_is_not_a_touch(self):
        rows = _keys(_project({"app/x.py": """
            from app.redis import get_redis_client

            def go():
                names = ["alice", "bob"]
                get_redis_client().delete(*names)
        """}))
        self.assertEqual(rows, {})

    def test_a_loop_over_a_key_list_touches_every_one(self):
        # `for key in keys_to_clear: r.exists(key)` - the loop variable stands for every
        # key in the list, one at a time, which is the other half of how bulk work is
        # written here.
        rows = _keys(_project({"app/cmd.py": """
            from app.redis import get_redis_client

            def clear():
                r = get_redis_client()
                keys_to_clear = [
                    "push_arena:is_hourly_mode",
                    "push_arena:reset_timezone",
                ]
                for key in keys_to_clear:
                    if r.exists(key):
                        r.delete(key)
        """}))
        self.assertIn("push_arena:is_hourly_mode", rows)
        self.assertIn("push_arena:reset_timezone", rows)


class TupleKeyBuilderTests(SimpleTestCase):
    def test_a_builder_that_returns_a_pair_names_both_keys(self):
        # ```
        # def swr_keys(name, user_id):
        #     return f"swr:{name}:fresh:{user_id}", f"swr:{name}:stale:{user_id}"
        # ...
        # fresh, stale = swr_keys(name, uid)
        # ```
        # A cache with a fresh copy and a stale copy is the whole point of
        # serve-stale-while-revalidate, so its key builder returns two. Only a single
        # return was followed, which left the entire SWR keyspace - the thing every
        # cached endpoint on the reference project moved ONTO - unreadable.
        rows = _keys(_project({
            "app/swr.py": """
                from django.core.cache import cache

                def swr_keys(name, user_id):
                    return f"swr:{name}:fresh:{user_id}", f"swr:{name}:stale:{user_id}"

                def store(name, user_id, value):
                    fresh, stale = swr_keys(name, user_id)
                    cache.set(fresh, value, 30)
                    cache.set(stale, value, 600)
            """,
            "app/read.py": """
                from django.core.cache import cache

                from .swr import swr_keys

                def load(name, user_id):
                    fresh, stale = swr_keys(name, user_id)
                    return cache.get(fresh) or cache.get(stale)
            """,
        }))
        self.assertEqual(rows["swr:*:fresh:*"][0], "connected")
        self.assertEqual(rows["swr:*:stale:*"][0], "connected")


class ClientFactoryChainTests(SimpleTestCase):
    def test_a_function_that_returns_a_client_is_a_client_factory(self):
        # `reader = _swr_reader(user_id)` - the name is not client-shaped, so every read
        # through it was invisible while the writes (through a pipeline off a
        # recognisable client) were seen. That is the SWR cache: the keyspace every
        # cached endpoint on the reference project moved onto, reading as write-only.
        rows = _keys(_project({"app/swr.py": """
            from app.redis import get_async_redis_client

            def _swr_reader(user_id):
                return get_async_redis_client(db=6)

            async def load(user_id):
                reader = _swr_reader(user_id)
                return await reader.get(f"swr:user_stats:fresh:{user_id}")

            async def store(user_id, value):
                writer = get_async_redis_client(db=6)
                await writer.set(f"swr:user_stats:fresh:{user_id}", value, 30)
        """}))
        self.assertEqual(rows["swr:user_stats:fresh:*"][0], "connected")

    def test_a_function_returning_something_else_is_not_a_client(self):
        rows = _keys(_project({"app/x.py": """
            from app.redis import get_redis_client

            def label(uid):
                return "player-" + str(uid)

            def go(uid):
                name = label(uid)
                return name.get("cache:thing:one")
        """}))
        self.assertEqual(rows, {})

    def test_a_factory_that_picks_between_two_clients_is_still_a_client(self):
        # `_swr_reader` returns the replica or the user's shard depending on config.
        # Both are clients, so reads through it are reads - the connection is simply not
        # known, and an unknown connection is not a second one. Requiring the two
        # branches to AGREE left the SWR read path invisible.
        rows = _keys(_project({"app/swr.py": """
            from app.redis import get_async_redis_replica, get_async_user_redis

            def _swr_reader(user_id):
                if READ_FROM_REPLICA:
                    return get_async_redis_replica(db=6)
                return get_async_user_redis(user_id, db=6)

            async def load(user_id):
                reader = _swr_reader(user_id)
                return await reader.get(f"swr:user_stats:fresh:{user_id}")

            async def store(user_id, value):
                writer = get_async_user_redis(user_id, db=6)
                await writer.set(f"swr:user_stats:fresh:{user_id}", value, 30)
        """}))
        self.assertEqual(rows["swr:user_stats:fresh:*"][0], "connected")


class ScanMatchTests(SimpleTestCase):
    def test_a_scan_never_invents_a_read_of_a_keyspace_nothing_writes(self):
        # Every cleanup, GDPR wipe and reset script sweeps for keys to DELETE. Counting
        # those sweeps as reads produced eight "read here and written nowhere" claims
        # about keyspaces whose only visitor was a wiper.
        rows = _keys(_project({"app/gdpr.py": """
            from app.redis import get_redis_client

            def wipe():
                r = get_redis_client()
                for key in r.scan_iter(match="hof:gains:*"):
                    r.delete(key)
        """}))
        self.assertNotEqual(rows.get("hof:gains:*", ("",))[0], "unresolved")

    def test_a_scan_with_a_match_pattern_is_evidence_the_keyspace_is_touched(self):
        # `main_r.scan(cursor, match='user:*:hourly_patterns', count=200)` - the key is
        # not the first argument, it is the `match` pattern, and sweeping a keyspace to
        # read every key in it is a read of that keyspace. Missing it called a live
        # analytics read a key nobody looks at.
        rows = _keys(_project({
            "app/write.py": """
                from app.redis import get_redis_client

                def record(uid, data):
                    get_redis_client().set(f"user:{uid}:hourly_patterns", data)
            """,
            "app/analytics.py": """
                from app.redis import get_redis_client

                def sweep():
                    r = get_redis_client()
                    cursor, keys = r.scan(0, match='user:*:hourly_patterns', count=200)
                    return keys
            """,
        }))
        # Not `connected`: a scan ENUMERATES a keyspace, and whether it then reads the
        # values or deletes them is the next line's business, not this one's. What it
        # must not do is let the key be called dead - which is what happened while the
        # sweep was invisible.
        self.assertEqual(rows["user:*:hourly_patterns"][0], "uncertain")
        self.assertNotEqual(rows["user:*:hourly_patterns"][0], "unused")

    def test_scan_iter_counts_too(self):
        rows = _keys(_project({
            "app/gdpr.py": """
                from app.redis import get_redis_client

                def wipe(uid):
                    r = get_redis_client()
                    r.set(f"user:{uid}:easter_eggs", 1)
                    for key in r.scan_iter(match=f"user:{uid}:easter_eggs"):
                        r.delete(key)
            """,
        }))
        self.assertIn("user:*:easter_eggs", rows)
