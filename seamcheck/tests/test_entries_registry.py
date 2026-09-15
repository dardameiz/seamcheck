import unittest
from unittest import mock

import seamcheck.entries as entries
from seamcheck.entries.base import Entry


class _Stub:
    def __init__(self, name, confidence, found=()):
        self.name = name
        self._confidence = confidence
        self._found = list(found)

    def detect(self, repo_root, config):
        return self._confidence

    def entries(self, repo_root, config, graph):
        return list(self._found)


def _entry(key, kind="page", roots=()):
    return Entry(key=key, kind=kind, roots=tuple(roots), title=key, where="")


class SelectAllTests(unittest.TestCase):
    def test_every_confident_source_runs(self):
        with mock.patch.object(entries, "_SOURCES", (_Stub("low", 0.2), _Stub("a", 0.9), _Stub("b", 0.6))):
            chosen = entries.select_all("/repo", {})
        self.assertEqual([source.name for source, _ in chosen], ["a", "b"])

    def test_when_nothing_is_confident_the_best_guess_still_runs(self):
        with mock.patch.object(entries, "_SOURCES", (_Stub("low", 0.1), _Stub("lower", 0.05))):
            chosen = entries.select_all("/repo", {})
        self.assertEqual([source.name for source, _ in chosen], ["low"])

    def test_entry_sources_forces_the_choice(self):
        with mock.patch.object(entries, "_SOURCES", (_Stub("a", 0.9), _Stub("b", 0.1))):
            chosen = entries.select_all("/repo", {"entry_sources": ["b"]})
        self.assertEqual([source.name for source, _ in chosen], ["b"])

    def test_entry_sources_as_one_string_means_that_one_source(self):
        with mock.patch.object(entries, "_SOURCES", (_Stub("a", 0.9), _Stub("b", 0.1))):
            chosen = entries.select_all("/repo", {"entry_sources": "b"})
        self.assertEqual([source.name for source, _ in chosen], ["b"])

    def test_an_unknown_forced_source_names_the_ones_that_exist(self):
        with mock.patch.object(entries, "_SOURCES", (_Stub("a", 0.9),)), \
             self.assertRaises(ValueError) as raised:
            entries.select_all("/repo", {"entry_sources": ["nope"]})
        self.assertIn("'nope'", str(raised.exception))
        self.assertIn("Available: a", str(raised.exception))


class AllEntriesTests(unittest.TestCase):
    def test_entries_from_every_selected_source_are_combined(self):
        sources = (_Stub("a", 0.9, [_entry("a1")]), _Stub("b", 0.9, [_entry("b1")]))
        with mock.patch.object(entries, "_SOURCES", sources):
            found = entries.all_entries("/repo", {}, graph=None)
        self.assertEqual({entry.key for entry in found}, {"a1", "b1"})

    def test_a_page_file_is_a_page_not_also_a_server_entry(self):
        page = _entry("next:/", "page", ["app/page.tsx", "app/layout.tsx"])
        same_file = _entry("server:app/page.tsx", "server", ["app/page.tsx"])
        handler = _entry("server:app/api/x/route.ts", "server", ["app/api/x/route.ts"])
        sources = (_Stub("next", 0.9, [page]), _Stub("server", 0.6, [same_file, handler]))
        with mock.patch.object(entries, "_SOURCES", sources):
            found = entries.all_entries("/repo", {}, graph=None)
        self.assertEqual([entry.key for entry in found], ["next:/", "server:app/api/x/route.ts"])

    def test_the_fallback_runs_only_when_everything_else_found_nothing(self):
        fallback = _entry("entry_file:main.js", "entry_file")
        with mock.patch.object(entries, "_SOURCES", (_Stub("empty", 0.9),)), \
             mock.patch.object(entries, "FallbackSource") as fallback_source:
            fallback_source.return_value.entries.return_value = [fallback]
            found = entries.all_entries("/repo", {}, graph=None)
        self.assertEqual(found, [fallback])

    def test_the_fallback_does_not_run_when_something_was_found(self):
        with mock.patch.object(entries, "_SOURCES", (_Stub("real", 0.9, [_entry("r1")]),)), \
             mock.patch.object(entries, "FallbackSource") as fallback_source:
            entries.all_entries("/repo", {}, graph=None)
        fallback_source.return_value.entries.assert_not_called()


class RegistryContentsTests(unittest.TestCase):
    def test_the_registry_holds_the_phase_one_sources(self):
        self.assertEqual(entries.available(), ["nextjs", "server", "legacy"])


class DescribeTests(unittest.TestCase):
    """What `seamcheck config` prints about entry sources: each one's confidence, and whether
    it runs - the design doc's "seamcheck config shows which sources ran and why"."""

    def test_each_source_says_its_confidence_and_whether_it_runs(self):
        with mock.patch.object(entries, "_SOURCES", (_Stub("a", 0.9), _Stub("b", 0.1))):
            lines = entries.describe("/repo", {})
        self.assertEqual(lines, ["a        0.90  runs", "b        0.10  does not run"])

    def test_a_forced_choice_says_it_was_forced(self):
        with mock.patch.object(entries, "_SOURCES", (_Stub("a", 0.9), _Stub("b", 0.1))):
            lines = entries.describe("/repo", {"entry_sources": ["b"]})
        self.assertEqual(lines, ["a        0.90  does not run",
                                 "b        0.10  runs (forced by entry_sources)"])
