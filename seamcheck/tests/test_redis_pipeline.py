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
