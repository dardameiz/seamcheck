"""One answer shape, so an agent parses seamcheck once rather than per command.

Today `check` prints a Python dict repr, `explain` prints prose, `triage` prints a sentence
and `json` prints 72 MB. Every one of those is a different parser to write, and two of them
are not parseable at all.
"""
import json

from django.test import SimpleTestCase

from seamcheck import envelope


class EnvelopeTests(SimpleTestCase):
    def test_an_answer_is_json_serialisable_and_versioned(self):
        out = envelope.answer("check", {"passed": True}, repo="/x", sha="abc123")

        text = json.dumps(out)  # must not raise
        self.assertEqual(out["schema"], 1)
        self.assertTrue(out["ok"])
        self.assertEqual(out["command"], "check")
        self.assertEqual(out["data"], {"passed": True})
        self.assertIsNone(out["error"])
        self.assertIn("abc123", text)

    def test_a_failure_carries_a_code_a_program_can_branch_on(self):
        out = envelope.failure("explain", "unknown_symbol", "No symbol with id `x`.",
                               hint="Try `seamcheck symbols --search x`.")

        self.assertFalse(out["ok"])
        self.assertEqual(out["error"]["code"], "unknown_symbol")
        self.assertIn("symbols --search", out["error"]["hint"])
        self.assertIsNone(out["data"])

    def test_every_error_code_is_documented(self):
        # A code an agent cannot look up is a string, not an interface.
        for code in ("unknown_symbol", "no_baseline", "no_adapter", "bad_argument"):
            self.assertIn(code, envelope.ERRORS)

    def test_a_page_reports_what_it_left_out(self):
        rows = [{"id": f"x{i}"} for i in range(100)]

        shown, cut = envelope.page(rows, limit=10)

        self.assertEqual(len(shown), 10)
        self.assertEqual(cut["returned"], 10)
        self.assertEqual(cut["total"], 100)
        self.assertTrue(cut["cursor"], "there is more, so there is a cursor")

    def test_the_cursor_continues_where_the_page_stopped(self):
        rows = [{"id": f"x{i}"} for i in range(30)]
        _, cut = envelope.page(rows, limit=10)

        shown, again = envelope.page(rows, limit=10, cursor=cut["cursor"])

        self.assertEqual(shown[0]["id"], "x10")
        self.assertEqual(again["returned"], 10)

    def test_the_last_page_has_no_cursor(self):
        rows = [{"id": "only"}]

        _, cut = envelope.page(rows, limit=10)

        self.assertEqual(cut["cursor"], "")

    def test_a_cursor_that_cannot_be_parsed_starts_from_the_beginning(self):
        # "²".isdigit() is True and int("²") raises, so the digit check alone was a crash.
        rows = [{"id": f"x{i}"} for i in range(5)]

        for bad in ("²", "abc", "-5", "12.5", ""):
            shown, cut = envelope.page(rows, limit=2, cursor=bad)

            self.assertEqual(shown[0]["id"], "x0", f"cursor {bad!r} must fall back, not raise")
            self.assertEqual(cut["offset"], 0)
