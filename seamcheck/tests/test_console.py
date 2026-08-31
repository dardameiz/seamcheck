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
            _symbol("model:a", "model"),
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

        for expected in ("changes", "boundary", "dom", "django", "css", "findings"):
            self.assertIn(expected, keys)

    def test_backend_and_frontend_are_counted_separately(self):
        self.assertEqual(sum(self.console.backend.values()), 3)
        self.assertEqual(sum(self.console.frontend.values()), 2)

    def test_no_section_opens_onto_an_apology(self):
        # Integrations and File Health used to ship as nav items that said "not
        # implemented yet". A reader who clicks one twice stops trusting the rest, so a
        # section now exists only once something feeds it. The Integrations blind spot
        # is stated on the opening panel instead, next to the counts it distorts.
        self.assertEqual([s.key for s in self.console.sections if s.unavailable], [])

    def test_findings_lists_worst_first(self):
        findings = next(s for s in self.console.sections if s.key == "findings")

        self.assertEqual([r.status for r in findings.rows], ["unresolved", "unused"])

    def test_django_internals_includes_models(self):
        django = next(s for s in self.console.sections if s.key == "django")

        self.assertIn("model", {r.kind for r in django.rows})
