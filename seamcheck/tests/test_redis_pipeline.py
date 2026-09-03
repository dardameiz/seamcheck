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
