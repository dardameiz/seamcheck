"""The one seam between Seamcheck and the framework that serves the routes.

Why this seam matters more than it looks: with no route list, the matcher marks 100% of
fetch targets `unresolved` - not "unknown", actively wrong on every endpoint. So an adapter
is mandatory for the static seam check, and it is also the only thing a new framework needs.
"""

from __future__ import annotations

import pathlib
import tempfile
import unittest

from seamcheck.adapters import ADAPTERS, available, select
from seamcheck.adapters.base import ServerAdapter, ServerScan
from seamcheck.adapters.django_adapter import DjangoAdapter
from seamcheck.progress import null


def _repo(**files: str) -> str:
    root = pathlib.Path(tempfile.mkdtemp())
    for name, body in files.items():
        path = root / name.replace("__", "/")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    return str(root)


class AdapterRegistry(unittest.TestCase):
    def test_django_is_registered(self):
        self.assertIn("django", available())

    def test_every_registered_adapter_satisfies_the_protocol(self):
        for adapter in ADAPTERS:
            with self.subTest(adapter=adapter.name):
                self.assertIsInstance(adapter, ServerAdapter)
                self.assertTrue(adapter.name)

    def test_a_django_repo_is_detected(self):
        root = _repo(**{"manage.py": "", "myproject__settings.py": "", "myproject__urls.py": ""})
        adapter, confidence = select(root, {})
        self.assertEqual(adapter.name, "django")
        self.assertGreater(confidence, 0.5)

    def test_an_unrecognised_repo_still_returns_an_adapter(self):
        """Refusing to run helps nobody - the frontend half of the graph is still real."""
        adapter, confidence = select(_repo(**{"README.md": "# hi"}), {})
        self.assertIsNotNone(adapter)
        self.assertEqual(confidence, 0.0)

    def test_config_can_force_an_adapter(self):
        adapter, confidence = select(_repo(), {"server_adapter": "django"})
        self.assertEqual(adapter.name, "django")
        self.assertEqual(confidence, 1.0)

    def test_an_unknown_forced_adapter_is_an_error_not_a_silent_fallback(self):
        with self.assertRaises(ValueError) as caught:
            select(_repo(), {"server_adapter": "rails"})
        self.assertIn("django", str(caught.exception))


class DjangoAdapterBehaviour(unittest.TestCase):
    def setUp(self):
        self.adapter = DjangoAdapter()

    def test_manage_py_alone_is_suggestive(self):
        self.assertGreater(self.adapter.detect(_repo(**{"manage.py": ""}), {}), 0.0)

    def test_a_bare_directory_scores_zero(self):
        self.assertEqual(self.adapter.detect(_repo(**{"README.md": ""}), {}), 0.0)

    def test_a_urls_py_alone_is_enough_to_guess(self):
        self.assertGreater(self.adapter.detect(_repo(**{"app__urls.py": ""}), {}), 0.0)

    def test_confidence_never_exceeds_one(self):
        root = _repo(**{"manage.py": "", "myproject__settings.py": "", "myproject__urls.py": ""})
        self.assertLessEqual(self.adapter.detect(root, {"urlconf_module": "myproject.urls"}), 1.0)

    def test_no_urlconf_yields_an_empty_scan_rather_than_raising(self):
        """An adapter that cannot read a project must degrade, never explode."""
        result = self.adapter.scan(_repo(), {}, null())
        self.assertIsInstance(result, ServerScan)
        self.assertEqual(result.symbols, [])
        self.assertEqual(result.route_names, {})


class ServerScanShape(unittest.TestCase):
    def test_defaults_are_empty_and_independent(self):
        one, two = ServerScan(), ServerScan()
        one.symbols.append("x")
        self.assertEqual(two.symbols, [], "default_factory, not a shared mutable default")


if __name__ == "__main__":
    unittest.main()
