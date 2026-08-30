from pathlib import Path

from django.test import SimpleTestCase

from seamcheck.extractors.reachability import discover_reachable_modules

FIXTURES_DIR = Path(__file__).parent / "fixtures"
# tests/ -> seamcheck/ -> repo root. parents[3] would be the directory ABOVE the repo,
# where no first-party module resolves and every walk returns just its roots.
REPO_ROOT = Path(__file__).resolve().parents[2]


class ReachabilityTests(SimpleTestCase):
    def setUp(self):
        root = str(FIXTURES_DIR / "fixture_reachability_root.py")
        self.reached = discover_reachable_modules([root], str(REPO_ROOT), ["seamcheck"])

    def test_follows_first_party_imports_transitively(self):
        self.assertIn(str(FIXTURES_DIR / "fixture_reachability_root.py"), self.reached)
        self.assertIn(str(FIXTURES_DIR / "fixture_reachability_a.py"), self.reached)
        self.assertIn(str(FIXTURES_DIR / "fixture_reachability_b.py"), self.reached)

    def test_does_not_follow_third_party_imports(self):
        self.assertFalse(
            any("django/conf" in p.replace("\\", "/") for p in self.reached)
        )

    def test_a_file_nothing_imports_is_not_in_the_reached_set(self):
        self.assertNotIn(str(FIXTURES_DIR / "fixture_unreachable.py"), self.reached)


class RelativeImportReachabilityTests(SimpleTestCase):
    def test_follows_a_relative_import(self):
        root = str(FIXTURES_DIR / "fixture_relative_importer.py")

        reached = discover_reachable_modules([root], str(REPO_ROOT), ["seamcheck"])

        self.assertIn(str(FIXTURES_DIR / "fixture_reachability_b.py"), reached)


class StringReferenceReachabilityTests(SimpleTestCase):
    def test_follows_a_module_named_only_by_a_string(self):
        # include("app.urls") is how Django mounts a URLconf; there is no import
        # statement, so an import-only walk never reaches the app's whole API surface.
        root = str(FIXTURES_DIR / "fixture_string_reference.py")

        reached = discover_reachable_modules([root], str(REPO_ROOT), ["seamcheck"])

        self.assertIn(str(FIXTURES_DIR / "fixture_reachability_b.py"), reached)
