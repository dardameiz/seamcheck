"""The function a line of Python sits in."""
import ast
import textwrap

from django.test import SimpleTestCase

from seamcheck.pyscope import owners_of


def _owners(source: str) -> dict[int, str]:
    return owners_of(ast.parse(textwrap.dedent(source)))


class OwnersTests(SimpleTestCase):
    def test_a_line_in_a_function_names_that_function(self):
        owners = _owners("""
            def submit_push(request):
                r.set("user:1:stats", 1)
        """)
        self.assertEqual(owners.get(3), "submit_push")

    def test_module_level_belongs_to_nobody(self):
        owners = _owners("""
            KEY = "user:1:stats"

            def submit_push(request):
                pass
        """)
        self.assertNotIn(2, owners)

    def test_a_method_is_named_with_its_class(self):
        owners = _owners("""
            class StoreManager:
                def apply(self, item):
                    r.set("cart:1", item)
        """)
        self.assertEqual(owners.get(4), "StoreManager.apply")

    def test_the_innermost_function_wins(self):
        owners = _owners("""
            def outer():
                def inner():
                    r.get("k")
                return inner
        """)
        self.assertEqual(owners.get(4), "outer.inner")
        self.assertEqual(owners.get(5), "outer")

    def test_an_async_function_counts(self):
        owners = _owners("""
            async def get_user_stats(request):
                await r.get("user:1:stats")
        """)
        self.assertEqual(owners.get(3), "get_user_stats")

    def test_a_decorator_belongs_to_the_function_it_decorates(self):
        # `@ratelimit(key="user:{id}")` reads as a line of the view, not of the module:
        # a key written in a decorator is the view's key, and attributing it to nobody
        # is how a Redis write ends up with no owner at all.
        owners = _owners("""
            @ratelimit(key="user:{id}:pushes")
            def submit_push(request):
                pass
        """)
        self.assertEqual(owners.get(2), "submit_push")

    def test_a_nested_class_keeps_the_whole_path(self):
        owners = _owners("""
            class Outer:
                class Meta:
                    def check(self):
                        pass
        """)
        self.assertEqual(owners.get(5), "Outer.Meta.check")

    def test_a_class_body_line_is_not_a_function(self):
        # A model field sits in a class, not in a function. Naming the class as its owner
        # would read as "this line runs when you call Outer", which is not true.
        owners = _owners("""
            class Season(models.Model):
                name = models.CharField()
        """)
        self.assertNotIn(3, owners)
