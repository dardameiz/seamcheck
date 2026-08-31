from pathlib import Path

from django.test import SimpleTestCase, override_settings

from seamcheck import api
from seamcheck.pipeline import SCAN_PHASES
from seamcheck.progress import Progress

FIXTURES_DIR = str(Path(__file__).parent / "fixtures")
_CONFIG = {
    "urlconf_module": "seamcheck.tests.fixtures.fixture_urls",
    "js_entry_files": ["fixture_entry.js"],
    "js_project_root": FIXTURES_DIR,
    "entry_point_files": [],
}


class _Counter(Progress):
    """A Progress that records what it was told, instead of drawing it."""

    def __init__(self):
        super().__init__(0, enabled=False)
        self.labels = []

    def step(self, label=""):
        self.labels.append(label)
        super().step(label)


class ScanStepTests(SimpleTestCase):
    def test_a_real_scan_reports_exactly_the_steps_the_bar_was_sized_for(self):
        # SCAN_STEPS is what the management command builds the bar with. If a phase is
        # added to run_scan and not to SCAN_PHASES, the bar reads 18/17 - or worse, sits
        # at 100% while the scan keeps going, which is a bar that lies. This is the guard.
        bar = _Counter()

        with override_settings(SEAMCHECK_CONFIG=_CONFIG):
            api.scan(FIXTURES_DIR, bar)

        self.assertEqual(len(bar.labels), api.SCAN_STEPS)

    def test_every_phase_is_reported_even_when_it_had_nothing_to_do(self):
        # These fixtures have no ASGI file, no models and no stylesheets. A bar whose
        # total shrinks or jumps depending on what a project happens to contain is worse
        # than one that moves in uneven steps.
        bar = _Counter()

        with override_settings(SEAMCHECK_CONFIG=_CONFIG):
            api.scan(FIXTURES_DIR, bar)

        self.assertEqual(bar.labels[len(api.PREFLIGHT_PHASES):], list(SCAN_PHASES))
        self.assertEqual(bar.labels[:len(api.PREFLIGHT_PHASES)], list(api.PREFLIGHT_PHASES))

    def test_no_phase_is_named_twice(self):
        # Two phases were both called "entry points" and two both "stylesheets"; a bar
        # that names the same phase twice reads as having gone backwards.
        every = list(api.PREFLIGHT_PHASES) + list(SCAN_PHASES) + list(api.MAP_PHASES)

        self.assertEqual(len(every), len(set(every)))

    def test_a_scan_with_no_progress_given_still_runs(self):
        # Every library caller - the MCP server, the backfill driver - passes nothing.
        with override_settings(SEAMCHECK_CONFIG=_CONFIG):
            graph = api.scan(FIXTURES_DIR)

        self.assertTrue(graph.symbols)
