"""Every symbol says which function it lives in."""
import os
import pathlib
import tempfile

from django.test import SimpleTestCase

from seamcheck.extractors.js_extractor import extract_js
from seamcheck.extractors.redis_extractor import extract_redis


def _project(files: dict[str, str]) -> str:
    root = tempfile.mkdtemp()
    for name, text in files.items():
        path = pathlib.Path(root) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


class PythonOwnerTests(SimpleTestCase):
    def test_a_redis_touch_names_the_view_it_happens_in(self):
        root = _project({"views.py": (
            "import redis\n"
            "r = redis.Redis()\n"
            "\n"
            "def submit_push(request):\n"
            "    r.set('user:1:stats', 1)\n"
            "\n"
            "def get_user_stats(request):\n"
            "    return r.get('user:1:stats')\n"
        )})
        symbols, _ = extract_redis(root)
        by_line = {
            (s.file, s.line): s.owner for s in symbols if s.kind == "redis_key_use"
        }
        self.assertEqual(by_line[("views.py", 5)], "submit_push")
        self.assertEqual(by_line[("views.py", 8)], "get_user_stats")

    def test_a_key_written_at_module_level_is_owned_by_nobody(self):
        root = _project({"boot.py": (
            "import redis\n"
            "r = redis.Redis()\n"
            "r.set('cache:warm', 1)\n"
        )})
        symbols, _ = extract_redis(root)
        uses = [s for s in symbols if s.kind == "redis_key_use"]
        self.assertTrue(uses)
        self.assertEqual([s.owner for s in uses], [""])

    def test_a_method_carries_its_class(self):
        root = _project({"store.py": (
            "import redis\n"
            "r = redis.Redis()\n"
            "\n"
            "class StoreManager:\n"
            "    def apply(self, item):\n"
            "        r.set('cart:1:items', item)\n"
        )})
        symbols, _ = extract_redis(root)
        uses = [s for s in symbols if s.kind == "redis_key_use"]
        self.assertEqual([s.owner for s in uses], ["StoreManager.apply"])


class JavaScriptOwnerTests(SimpleTestCase):
    def test_a_fetch_names_the_function_that_makes_it(self):
        root = _project({"app.js": (
            "async function loadOrders() {\n"
            "  await fetch('/api/orders/');\n"
            "}\n"
        )})
        entry = os.path.join(root, "app.js")
        symbols, _ = extract_js([entry], root)
        calls = {s.kind: s.owner for s in symbols if s.kind in ("js_call", "fetch_target")}
        self.assertEqual(calls.get("js_call"), "loadOrders")
        self.assertEqual(calls.get("fetch_target"), "loadOrders")

    def test_a_fetch_at_module_level_is_owned_by_nobody(self):
        root = _project({"boot.js": "fetch('/api/boot/');\n"})
        entry = os.path.join(root, "boot.js")
        symbols, _ = extract_js([entry], root)
        calls = [s for s in symbols if s.kind == "js_call"]
        self.assertTrue(calls)
        self.assertEqual([s.owner for s in calls], [""])
