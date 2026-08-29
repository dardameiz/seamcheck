from pathlib import Path

from django.test import SimpleTestCase

from signal_map.extractors.entry_points_extractor import extract_entry_points
from signal_map.graph import Status

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _kinds(symbols, kind):
    return [s for s in symbols if s.kind == kind]


class EntryPointExtractorTests(SimpleTestCase):
    def test_finds_a_signal_receiver(self):
        symbols = extract_entry_points({str(FIXTURES_DIR / "fixture_signals.py")})
        receivers = _kinds(symbols, "signal_receiver")

        self.assertEqual({r.label for r in receivers}, {"on_some_model_saved", "on_some_model_saved_async"})
        self.assertTrue(all(r.status == Status.CONNECTED for r in receivers))

    def test_finds_an_async_signal_receiver(self):
        # `async def` is ast.AsyncFunctionDef, a separate node type. Matching only
        # ast.FunctionDef would report a live receiver as an orphan.
        symbols = extract_entry_points({str(FIXTURES_DIR / "fixture_signals.py")})

        self.assertIn("on_some_model_saved_async", {s.label for s in _kinds(symbols, "signal_receiver")})

    def test_finds_an_admin_action_referenced_by_name(self):
        symbols = extract_entry_points({str(FIXTURES_DIR / "fixture_admin.py")})
        actions = _kinds(symbols, "admin_action")

        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0].label, "do_thing")
        self.assertEqual(actions[0].status, Status.CONNECTED)

    def test_an_actions_entry_with_no_matching_method_is_not_claimed(self):
        symbols = extract_entry_points({str(FIXTURES_DIR / "fixture_admin.py")})

        self.assertNotIn("not_a_method_here", {s.label for s in symbols})

    def test_finds_every_register_tag_flavour(self):
        symbols = extract_entry_points({str(FIXTURES_DIR / "fixture_template_tags.py")})

        self.assertEqual(
            {s.label for s in _kinds(symbols, "template_tag")},
            {"shout", "now_ish", "render_box"},
        )

    def test_every_symbol_carries_a_line_number_as_evidence(self):
        symbols = extract_entry_points({str(FIXTURES_DIR / "fixture_signals.py")})

        self.assertTrue(all(s.line and s.line > 0 for s in symbols))

    def test_files_with_neither_pattern_produce_nothing(self):
        self.assertEqual(extract_entry_points(set()), [])
