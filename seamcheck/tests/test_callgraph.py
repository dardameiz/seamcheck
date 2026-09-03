"""Which function calls which - and, as much as it matters, which it refuses to guess."""
import pathlib
import tempfile
import textwrap

from django.test import SimpleTestCase

from seamcheck.callgraph import python_calls


def _project(files: dict[str, str]) -> str:
    root = tempfile.mkdtemp()
    for name, text in files.items():
        path = pathlib.Path(root) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text), encoding="utf-8")
    return root


class PythonCallsTests(SimpleTestCase):
    def test_a_call_to_a_def_in_the_same_file(self):
        root = _project({"views.py": """
            def record(user):
                pass

            def submit_push(request):
                record(request.user)
        """})
        self.assertEqual(python_calls(root), {"submit_push": ["record"]})

    def test_a_call_through_an_import_of_a_project_module(self):
        root = _project({
            "services/push.py": "def record(user):\n    pass\n",
            "views.py": """
                from services.push import record

                def submit_push(request):
                    record(request.user)
            """,
        })
        self.assertEqual(python_calls(root)["submit_push"], ["record"])

    def test_a_call_through_a_module_alias(self):
        root = _project({
            "services/push.py": "def record(user):\n    pass\n",
            "views.py": """
                import services.push as push

                def submit_push(request):
                    push.record(request.user)
            """,
        })
        self.assertEqual(python_calls(root)["submit_push"], ["record"])

    def test_self_dot_method_inside_its_own_class(self):
        root = _project({"store.py": """
            class StoreManager:
                def helper(self):
                    pass

                def apply(self, item):
                    self.helper()
        """})
        self.assertEqual(python_calls(root)["StoreManager.apply"], ["StoreManager.helper"])

    def test_a_third_party_call_is_not_claimed(self):
        # `requests.get` is not this project's function, and inventing an edge to it
        # would put somebody else's library in the middle of your own call graph.
        root = _project({"views.py": """
            import requests

            def submit_push(request):
                requests.get("https://example.com")
        """})
        self.assertEqual(python_calls(root), {})

    def test_a_method_on_an_unknown_object_is_not_guessed(self):
        # Knowing what `order.save()` is needs types. Absent is the honest answer, and
        # the same contract the statuses keep: never a claim the scan cannot see.
        root = _project({"views.py": """
            def submit_push(request):
                order = get_order()
                order.save()
        """})
        self.assertEqual(python_calls(root), {})

    def test_a_call_at_import_time_belongs_to_no_function(self):
        root = _project({"boot.py": """
            def warm():
                pass

            warm()
        """})
        self.assertEqual(python_calls(root), {})

    def test_recursion_is_not_an_edge_to_itself(self):
        root = _project({"m.py": """
            def walk(node):
                walk(node.child)
        """})
        self.assertEqual(python_calls(root), {})

    def test_the_chain_a_delegating_handler_makes(self):
        # The shape the whole feature exists for: a view that does nothing but delegate.
        root = _project({
            "services/push.py": """
                def touch_redis(user):
                    pass

                def record(user):
                    touch_redis(user)
            """,
            "views.py": """
                from services.push import record

                def submit_push(request):
                    record(request.user)
            """,
        })
        calls = python_calls(root)
        self.assertEqual(calls["submit_push"], ["record"])
        self.assertEqual(calls["record"], ["touch_redis"])


class ByNameAloneTests(SimpleTestCase):
    """A method on an object of unknown type, resolved only when the name is unique."""

    def test_a_method_defined_once_in_the_project_is_that_method(self):
        root = _project({
            "services/push.py": """
                class PushService:
                    def process_batch_unified(self, batch):
                        pass
            """,
            "views.py": """
                def submit_push(request):
                    service = get_service()
                    service.process_batch_unified(request.data)
            """,
        })
        self.assertEqual(python_calls(root)["submit_push"],
                         ["PushService.process_batch_unified"])

    def test_a_name_defined_twice_resolves_to_neither(self):
        root = _project({
            "a.py": "class A:\n    def save(self):\n        pass\n",
            "b.py": "class B:\n    def save(self):\n        pass\n",
            "views.py": """
                def submit_push(request):
                    order = get_order()
                    order.save()
            """,
        })
        self.assertEqual(python_calls(root), {})

    def test_a_library_call_never_borrows_a_unique_project_name(self):
        # `requests.get` is requests', even in a project whose only `get` is its own.
        root = _project({
            "cache.py": "class Cache:\n    def get(self, key):\n        pass\n",
            "views.py": """
                import requests

                def submit_push(request):
                    requests.get("https://example.com")
            """,
        })
        self.assertEqual(python_calls(root), {})
