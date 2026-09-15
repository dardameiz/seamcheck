import unittest

from seamcheck.entries.base import Entry, EntrySource


class EntryTests(unittest.TestCase):
    def test_a_plain_entry_carries_its_fields(self):
        entry = Entry(
            key="next:/pricing/[locale]", kind="page",
            roots=("app/pricing/[locale]/page.tsx",),
            title="Pricing", where="/pricing/[locale] - app/pricing/[locale]/page.tsx",
        )
        self.assertEqual(entry.key, "next:/pricing/[locale]")
        self.assertEqual(entry.kind, "page")
        self.assertEqual(entry.roots, ("app/pricing/[locale]/page.tsx",))
        self.assertEqual((entry.group, entry.evidence, entry.note, entry.label), ("", "", "", ""))

    def test_an_entry_cannot_be_changed_after_it_is_made(self):
        entry = Entry(key="k", kind="page", roots=(), title="T", where="W")
        with self.assertRaises(AttributeError):
            entry.title = "changed"


class EntrySourceProtocolTests(unittest.TestCase):
    def test_an_object_with_a_name_detect_and_entries_is_a_source(self):
        class Stub:
            name = "stub"

            def detect(self, repo_root, config):
                return 0.0

            def entries(self, repo_root, config, graph):
                return []

        self.assertIsInstance(Stub(), EntrySource)

    def test_an_object_without_them_is_not(self):
        class NotASource:
            pass

        self.assertNotIsInstance(NotASource(), EntrySource)
