import os
from pathlib import Path

from django.test import SimpleTestCase

from seamcheck.build_graph import find_build_gaps, read_build_manifest
from seamcheck.graph import Status
from seamcheck.pipeline import run_scan

FIXTURES_DIR = str(Path(__file__).parent / "fixtures")
MANIFEST = os.path.join(FIXTURES_DIR, "fixture_vite_manifest.json")
GAP_MANIFEST = os.path.join(FIXTURES_DIR, "fixture_vite_manifest_gap.json")
URLCONF = "seamcheck.tests.fixtures.fixture_urls"


class ReadBuildManifestTests(SimpleTestCase):
    def test_reads_every_source_path_the_manifest_names(self):
        paths = read_build_manifest(MANIFEST)

        self.assertEqual(
            paths,
            {"js/main.js", "buttons/js/aurora_borealis.js", "_vendor-chunk.js"},
        )

    def test_a_missing_manifest_returns_none_not_a_crash(self):
        self.assertIsNone(read_build_manifest(os.path.join(FIXTURES_DIR, "does-not-exist.json")))

    def test_a_malformed_manifest_returns_none_not_a_crash(self):
        # fixture_dom.css is real, on disk, and is not JSON at all.
        self.assertIsNone(read_build_manifest(os.path.join(FIXTURES_DIR, "fixture_dom.css")))


class FindBuildGapsTests(SimpleTestCase):
    def _reachable(self, *rel_paths):
        return [os.path.join(FIXTURES_DIR, path) for path in rel_paths]

    def test_a_bundled_module_is_not_a_gap(self):
        findings = find_build_gaps(self._reachable("js/main.js"), MANIFEST, FIXTURES_DIR)

        self.assertEqual(findings, [])

    def test_a_reachable_module_missing_from_the_manifest_is_flagged(self):
        # The reference case: reachable by a real import(), never bundled.
        findings = find_build_gaps(
            self._reachable("js/main.js", "buttons/js/ghost_button.js"), MANIFEST, FIXTURES_DIR,
        )

        self.assertEqual(len(findings), 1)
        gap = findings[0]
        self.assertEqual(gap.kind, "build_gap")
        self.assertEqual(gap.label, "buttons/js/ghost_button.js")
        self.assertEqual(gap.status, Status.UNRESOLVED)
        self.assertIn("build manifest", gap.note)

    def test_an_explicitly_ignored_path_is_never_flagged(self):
        # A deliberately externalised module, a CDN load, a dev-only entry - the caller
        # names it, this function never guesses.
        findings = find_build_gaps(
            self._reachable("js/main.js", "buttons/js/ghost_button.js"), MANIFEST, FIXTURES_DIR,
            ignore=frozenset({"buttons/js/ghost_button.js"}),
        )

        self.assertEqual(findings, [])

    def test_an_unreadable_manifest_yields_no_findings_rather_than_false_positives(self):
        # No build step, or not built yet, is not the same claim as "this is a gap".
        findings = find_build_gaps(
            self._reachable("buttons/js/ghost_button.js"),
            os.path.join(FIXTURES_DIR, "does-not-exist.json"),
            FIXTURES_DIR,
        )

        self.assertEqual(findings, [])

    def test_a_build_root_that_matches_nothing_yields_no_findings(self):
        # A wrong build_root - the caller's best guess, not necessarily the exact
        # directory Vite was configured with - would make EVERY reachable file miss the
        # manifest, which is a false-positive avalanche from one bad guess, not "the
        # whole project silently unbundled". Requires at least one real match first.
        wrong_root = os.path.join(FIXTURES_DIR, "datalayer")
        findings = find_build_gaps(self._reachable("js/main.js"), MANIFEST, wrong_root)

        self.assertEqual(findings, [])

    def test_each_gap_is_reported_once_even_if_reachable_more_than_once(self):
        findings = find_build_gaps(
            self._reachable(
                "js/main.js", "buttons/js/ghost_button.js", "buttons/js/ghost_button.js",
            ),
            MANIFEST, FIXTURES_DIR,
        )

        self.assertEqual(len(findings), 1)

    def test_a_statically_shared_module_with_no_manifest_key_of_its_own_is_not_a_gap(self):
        # The false-positive class this function's callers must avoid: checking every
        # reachable file (not just dynamic-import targets) against a real Vite manifest
        # flagged ~190 ordinary statically-shared modules as "gaps" on this tool's own
        # reference project, because Vite folds them into a shared chunk with no
        # standalone manifest key - normal, not missing. This function itself has no way
        # to know a file is "only ever statically imported" (that distinction lives in
        # discover_dynamic_import_targets); it can only report what it is asked to check.
        # This test documents that responsibility sits with the caller, not here.
        findings = find_build_gaps(
            self._reachable("js/main.js", "a/statically/shared/module.js"),
            MANIFEST, FIXTURES_DIR,
        )

        # Given the FULL reachable set (as a caller must NOT do for real gap detection),
        # this function still reports it - proving the scoping decision belongs to
        # discover_dynamic_import_targets, not to a heuristic buried in here.
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].label, "a/statically/shared/module.js")

    def test_entry_files_anchor_the_root_sanity_check_when_check_files_all_miss(self):
        # The scenario the earlier, single-set guard got backwards: if EVERY dynamic-
        # import target in the whole project were broken by an obfuscator (not just some
        # of them), check_files would be 100% disjoint from the manifest even with a
        # CORRECT build_root - indistinguishable, by check_files alone, from a wrong root.
        # entry_files (which always have real manifest keys) is what tells them apart.
        findings = find_build_gaps(
            self._reachable("buttons/js/ghost_button.js"),  # 0% of these ever match
            MANIFEST, FIXTURES_DIR,
            entry_files=self._reachable("js/main.js"),  # but the true entry does
        )

        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].label, "buttons/js/ghost_button.js")

    def test_entry_files_that_also_miss_still_back_off(self):
        # If even the entries do not match, it is still the wrong root - passing
        # entry_files must not turn the guard off, only re-anchor it correctly.
        findings = find_build_gaps(
            self._reachable("buttons/js/ghost_button.js"),
            MANIFEST, FIXTURES_DIR,
            entry_files=self._reachable("nothing/here/at/all.js"),
        )

        self.assertEqual(findings, [])


class RunScanSurfacesBuildGapsTests(SimpleTestCase):
    """The wiring, not just the function: run_scan(build_manifest_path=...) end to end.

    fixture_dynamic_entry.js reaches fixture_dynamic_target.js by a real `import()`
    (JsFileDiscoveryTests already proves that edge exists). fixture_vite_manifest_gap.json
    is a manifest whose entry NAMES that dynamic import but never emits it as its own
    chunk - the exact shape of the reference bug (an obfuscator ate the specifier, Vite
    silently left the target unbundled).
    """

    def test_a_reachable_never_bundled_module_becomes_a_build_gap_symbol(self):
        graph = run_scan(
            urlconf_module=URLCONF,
            js_entry_files=["fixture_dynamic_entry.js"],
            js_project_root=FIXTURES_DIR,
            build_manifest_path=GAP_MANIFEST,
            build_root=FIXTURES_DIR,
        )

        gaps = {s.label for s in graph.symbols if s.kind == "build_gap"}

        self.assertIn("fixture_dynamic_target.js", gaps)

    def test_no_manifest_path_means_no_build_gap_checking_at_all(self):
        # The feature must be a no-op, not a crash, on every project that does not opt in.
        graph = run_scan(
            urlconf_module=URLCONF,
            js_entry_files=["fixture_dynamic_entry.js"],
            js_project_root=FIXTURES_DIR,
        )

        self.assertFalse([s for s in graph.symbols if s.kind == "build_gap"])
