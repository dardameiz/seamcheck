from django.test import SimpleTestCase

from signal_map.coverage import compute_coverage


class CoverageBucketingTests(SimpleTestCase):
    def test_buckets_files_correctly(self):
        result = compute_coverage(
            tracked_files=["a.py", "b.py", "c.py"],
            reachable_files={"a.py", "b.py"},
            symbol_producing_files={"a.py"},
        )

        self.assertEqual(result.modeled, ["a.py"])
        self.assertEqual(result.recognized_but_empty, ["b.py"])
        self.assertEqual(result.unscoped, ["c.py"])

    def test_every_tracked_file_appears_in_exactly_one_bucket(self):
        tracked = ["a.py", "b.py", "c.py", "d.py"]

        result = compute_coverage(
            tracked_files=tracked,
            reachable_files={"a.py", "c.py"},
            symbol_producing_files={"a.py", "c.py"},
        )

        bucketed = result.modeled + result.recognized_but_empty + result.unscoped
        self.assertEqual(sorted(bucketed), sorted(tracked))
        self.assertEqual(len(bucketed), len(set(bucketed)))

    def test_a_producing_file_that_is_not_reachable_is_still_unscoped(self):
        # Reachability is the gate: producing symbols from a file nothing imports means
        # the root set is wrong, and saying "modeled" would hide that.
        result = compute_coverage(
            tracked_files=["ghost.py"], reachable_files=set(), symbol_producing_files={"ghost.py"}
        )

        self.assertEqual(result.unscoped, ["ghost.py"])
