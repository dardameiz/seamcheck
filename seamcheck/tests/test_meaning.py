from django.test import SimpleTestCase

from seamcheck.console import BACKEND_KINDS, FRONTEND_KINDS
from seamcheck.graph import Status
from seamcheck.meaning import meaning, table


class MeaningTests(SimpleTestCase):
    def test_every_kind_and_status_a_row_can_carry_has_an_explanation(self):
        # A row with a status word and no explanation is the thing this module exists to
        # remove, so the fallback has to cover every pair the console can actually emit.
        for kind in BACKEND_KINDS + FRONTEND_KINDS + ("json_field", "management_command"):
            for status in Status:
                with self.subTest(kind=kind, status=status.value):
                    means, check = meaning(kind, status.value)

                    self.assertTrue(means)
                    self.assertTrue(check)

    def test_a_specific_kind_beats_the_generic_status_text(self):
        generic, _ = meaning("nothing_in_particular", "unresolved")
        specific, _ = meaning("fetch_target", "unresolved")

        self.assertNotEqual(specific, generic)
        self.assertIn("URL pattern", specific)

    def test_an_unknown_status_says_nothing_rather_than_inventing_something(self):
        self.assertEqual(meaning("url", "banana"), ("", ""))

    def test_uncertain_is_never_written_up_as_a_finding(self):
        # The single most damaging thing this tool could claim is that unmeasured means
        # dead. It says so in the text, and this is the assertion that keeps it there.
        means, check = meaning("", "uncertain")

        self.assertIn("not a claim", means.lower())
        self.assertIn("unmeasured", check.lower())


class TableTests(SimpleTestCase):
    def test_it_ships_the_status_fallbacks_the_page_looks_up(self):
        # The map reads MEANING[kind|status] and falls back to MEANING["*|status"], so
        # every status needs a starred entry or a row renders with nothing under it.
        keys = table()

        for status in Status:
            self.assertIn(f"*|{status.value}", keys)

    def test_every_entry_carries_both_halves(self):
        for key, entry in table().items():
            with self.subTest(key=key):
                self.assertTrue(entry["means"])
                self.assertTrue(entry["check"])
