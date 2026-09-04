from dataclasses import replace

from django.test import SimpleTestCase

from seamcheck.console import build_console
from seamcheck.graph import Graph, Status, Symbol
from seamcheck.report import Report


def _symbol(id_, kind, status=Status.CONNECTED, label=None):
    return Symbol(
        id=id_, kind=kind, label=label or id_, sub="", file="a.py", line=1,
        status=status, snippet=id_, chain=[id_], note="",
    )


def _report(**kwargs):
    base = {
        "git_sha": "abc123def456", "generated_at": "2026-08-30T00:00:00",
        "baseline_sha": None, "baseline_message": "", "new_findings": [], "resolved": [],
        "triage_invalidated": [], "groups": [],
        "counts": {"connected": 1, "unresolved": 1, "unused": 0, "uncertain": 0},
    }
    base.update(kwargs)
    return Report(**base)


def _graph():
    return Graph(
        symbols=[
            _symbol("url:a", "url"),
            _symbol("view:a", "view"),
            _symbol("db_table:a", "db_table"),
            _symbol("fetch:/x/", "fetch_target", Status.UNRESOLVED),
            _symbol("dom_attr:z", "dom_attr", Status.UNUSED),
        ],
        edges=[],
    )


class ConsoleShapeTests(SimpleTestCase):
    def setUp(self):
        self.console = build_console(_graph(), _report())

    def test_it_has_every_section_the_spec_names(self):
        keys = [s.key for s in self.console.sections]

        for expected in ("changes", "boundary", "dom", "backend", "data", "css",
                         "findings"):
            self.assertIn(expected, keys)

    def test_backend_and_frontend_are_counted_separately(self):
        # `db_table` is a store kind, not a backend one - the database is its own region
        # of the map, and counting it as backend would double-count it.
        self.assertEqual(sum(self.console.backend.values()), 2)
        self.assertEqual(sum(self.console.frontend.values()), 2)
        self.assertEqual(sum(self.console.store.values()), 1)

    def test_no_section_opens_onto_an_apology(self):
        # Integrations and File Health used to ship as nav items that said "not
        # implemented yet". A reader who clicks one twice stops trusting the rest, so a
        # section now exists only once something feeds it. The Integrations blind spot
        # is stated on the opening panel instead, next to the counts it distorts.
        self.assertEqual([s.key for s in self.console.sections if s.unavailable], [])

    def test_findings_lists_worst_first(self):
        findings = next(s for s in self.console.sections if s.key == "findings")

        self.assertEqual([r.status for r in findings.rows], ["unresolved", "unused"])

    def test_the_database_has_its_own_section(self):
        data = next(s for s in self.console.sections if s.key == "data")

        self.assertIn("db_table", {r.kind for r in data.rows})


class BackendSectionTitleTests(SimpleTestCase):
    """The section is named after evidence, not after a hardcoded framework.

    Five backends can fill it now, and telling a FastAPI user about their "Django
    Internals" is the tool failing to read its own output.
    """

    @staticmethod
    def _graph(*kinds):
        from seamcheck.graph import Graph, Status, Symbol
        return Graph(symbols=[
            Symbol(id=f"{k}:x", kind=k, label="x", sub="", file="a.py", line=1,
                   status=Status.CONNECTED, snippet="", chain=[], note="")
            for k in kinds
        ], edges=[])

    def _title(self, *kinds):
        from seamcheck.console import _backend_title
        return _backend_title(self._graph(*kinds))

    def test_django_only_kinds_name_django(self):
        self.assertEqual(self._title("url", "view", "admin_action"), "Django Internals")

    def test_signal_receivers_name_django(self):
        self.assertEqual(self._title("url", "signal_receiver"), "Django Internals")

    def test_routes_and_views_alone_are_not_django(self):
        self.assertEqual(self._title("url", "view"), "Backend Internals")

    def test_an_empty_graph_is_not_django(self):
        self.assertEqual(self._title(), "Backend Internals")


class OneRowPerPlaceTests(SimpleTestCase):
    """`itemPurchaseModal` appeared four times in one report, on one line.

    A name read and written on the same line is two symbols and one thing to look at.
    Twelve per cent of one surface's rows were repeats, and a count nobody trusts is a
    report nobody reads.
    """

    def _symbol(self, symbol_id, status=Status.UNRESOLVED):
        return Symbol(id=symbol_id, kind="dom_selector", label="itemPurchaseModal",
                      sub="id:read", file="store.js", line=316, status=status,
                      snippet="getElementById('itemPurchaseModal')", chain=[], note="")

    def test_the_same_place_is_listed_once(self):
        from seamcheck.console import build_console
        from seamcheck.report import build_report

        graph = Graph(symbols=[self._symbol(f"dom_selector:id:x{i}") for i in range(4)],
                      edges=[])
        console = build_console(graph, build_report(graph=graph, diff=None, entries=[],
                                                    git_sha="0" * 12))
        # Per section: one finding legitimately appears in its themed list and in the
        # findings list, and that is two places to meet it rather than two findings.
        for section in console.sections:
            rows = [r for r in section.rows if r.label == "itemPurchaseModal"]
            self.assertLessEqual(len(rows), 1, f"{section.key}: {[r.id for r in rows]}")
        self.assertTrue(any(r.label == "itemPurchaseModal"
                            for section in console.sections for r in section.rows))

    def test_two_different_lines_are_two_rows(self):
        from seamcheck.console import build_console
        from seamcheck.report import build_report

        one = self._symbol("dom_selector:id:a")
        two = replace(self._symbol("dom_selector:id:b"), line=900)
        graph = Graph(symbols=[one, two], edges=[])
        console = build_console(graph, build_report(graph=graph, diff=None, entries=[],
                                                    git_sha="0" * 12))
        for section in console.sections:
            lines = sorted({r.line for r in section.rows
                            if r.label == "itemPurchaseModal"})
            if lines:
                self.assertEqual(lines, [316, 900], section.key)
