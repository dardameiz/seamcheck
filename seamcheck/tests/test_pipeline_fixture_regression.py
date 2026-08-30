"""The mandatory gate: run_scan must get every planted case right, every time.

Planted cases:
  get_thing / api/get-thing/    called by fixture_entry.js            -> CONNECTED
  nested_thing / sub/nested/    reached only via a nested include()   -> CONNECTED
  /api/does-not-exist/          fetched, no matching URL              -> UNRESOLVED
  callDynamic()                 target built from a template literal  -> UNCERTAIN
  orphan_view / api/orphan/     no JS caller, no evidence either way  -> UNCERTAIN, never UNUSED
  fixture_signals receivers     self-certain via their decorator      -> CONNECTED
"""

from pathlib import Path

from django.test import SimpleTestCase

from seamcheck.graph import Status
from seamcheck.pipeline import run_scan

FIXTURES_DIR = str(Path(__file__).parent / "fixtures")
URLCONF = "seamcheck.tests.fixtures.fixture_urls"


class PipelineRegressionGateTests(SimpleTestCase):
    def setUp(self):
        self.graph = run_scan(
            urlconf_module=URLCONF,
            js_entry_files=["fixture_entry.js"],
            js_project_root=FIXTURES_DIR,
        )
        self.by_label = {
            s.label: s for s in self.graph.symbols if s.kind in ("view", "fetch_target")
        }

    def test_a_view_called_by_js_is_connected(self):
        self.assertEqual(self.by_label["get_thing"].status, Status.CONNECTED)

    def test_a_view_behind_a_nested_include_is_connected(self):
        self.assertEqual(self.by_label["nested_thing"].status, Status.CONNECTED)

    def test_a_fetch_to_no_such_url_is_unresolved(self):
        self.assertEqual(self.by_label["/api/does-not-exist/"].status, Status.UNRESOLVED)

    def test_a_dynamic_fetch_target_is_uncertain(self):
        dynamic = [
            s for s in self.graph.symbols
            if s.kind == "js_call" and s.status == Status.UNCERTAIN
        ]

        self.assertTrue(dynamic)

    def test_a_view_with_no_caller_is_uncertain_not_unused(self):
        orphan = self.by_label["orphan_view"]

        self.assertEqual(orphan.status, Status.UNCERTAIN)
        self.assertIn("not claimed unused", orphan.note.lower())

    def test_nothing_is_ever_claimed_unused_without_evidence(self):
        # The over-reporting guard, scoped to the kinds whose consumers are invisible.
        # UNUSED is claimable only when BOTH sides of a contract are observable: a
        # json_field is (the view sends it, the matched JS either reads it or does not),
        # a page URL is not (Core Pipeline sees no {% url %}, <a href> or navigation).
        claimed = [
            s for s in self.graph.symbols
            if s.status == Status.UNUSED and s.kind in ("url", "view", "js_call", "fetch_target")
        ]

        self.assertEqual(claimed, [])

    def test_response_fields_never_claim_more_than_the_js_scope_supports(self):
        # run_scan pairs one view function against a whole JS module, so a field can
        # be proven read but never proven unread. UNUSED stays available to the unit
        # API, where the caller supplies a genuinely scoped pair.
        fields = [s for s in self.graph.symbols if s.kind == "json_field"]

        self.assertTrue(fields)
        self.assertTrue({s.status for s in fields} <= {Status.CONNECTED, Status.UNCERTAIN})

    def test_every_symbol_carries_its_evidence(self):
        for symbol in self.graph.symbols:
            self.assertTrue(symbol.id)
            self.assertTrue(symbol.snippet, symbol.id)

    def test_signal_receiver_is_connected_when_fed_through_the_pipeline(self):
        graph = run_scan(
            urlconf_module=URLCONF,
            js_entry_files=["fixture_entry.js"],
            js_project_root=FIXTURES_DIR,
            entry_point_files={str(Path(FIXTURES_DIR) / "fixture_signals.py")},
        )
        receivers = [s for s in graph.symbols if s.kind == "signal_receiver"]

        self.assertTrue(receivers)
        self.assertTrue(all(s.status == Status.CONNECTED for s in receivers))
