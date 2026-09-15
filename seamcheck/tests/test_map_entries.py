import json
import unittest

from seamcheck.mapdata import ConnectivityMap, MapNode, PageMap
from seamcheck.renderers.map_html import _grouped, _payload


def _page(page, title="T", where="", group="", reached=0):
    root = MapNode(f"page:{page}", page, "page", "connected")
    return PageMap(page=page, nodes=[root], edges=[], title=title, where=where,
                   group=group, reached=reached)


class GroupedTests(unittest.TestCase):
    def test_entries_sharing_a_group_are_one_group_whatever_their_titles(self):
        groups = _grouped([_page("a", "Overview", group="/"), _page("b", "KPIs", group="/")])
        self.assertEqual([[p.page for p in members] for _, members in groups], [["a", "b"]])

    def test_without_a_group_entries_group_by_title_and_address_as_before(self):
        groups = _grouped([_page("a", "Home", "/ - x.html"), _page("b", "Home", "/ - y.html"),
                           _page("c", "Other", "/other")])
        self.assertEqual([[p.page for p in members] for _, members in groups], [["a", "b"], ["c"]])


class ReachedCountTests(unittest.TestCase):
    def test_a_page_says_how_many_symbols_its_files_hold(self):
        meta, _chunks, _files = _payload(ConnectivityMap(git_sha="abc", generated_at="t",
                                                         pages=[_page("home", reached=42)]))
        [page] = [p for p in json.loads(meta)["pages"] if p["page"] == "home"]
        self.assertEqual(page["rf"], 42)

    def test_a_page_whose_files_hold_nothing_carries_no_count(self):
        meta, _chunks, _files = _payload(ConnectivityMap(git_sha="abc", generated_at="t",
                                                         pages=[_page("home", reached=0)]))
        [page] = [p for p in json.loads(meta)["pages"] if p["page"] == "home"]
        self.assertNotIn("rf", page)
