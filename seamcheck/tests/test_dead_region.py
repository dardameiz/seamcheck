"""A guard that always fires, and the code below it that therefore never runs."""
import pathlib
import tempfile
import textwrap

from django.test import SimpleTestCase

from seamcheck.dead_region import find_dead_regions


def _js(source: str) -> str:
    path = pathlib.Path(tempfile.mkdtemp()) / "app.js"
    path.write_text(textwrap.dedent(source), encoding="utf-8")
    return str(path)


class DeadRegionTests(SimpleTestCase):
    def test_a_guard_on_missing_elements_makes_the_rest_of_the_function_dead(self):
        # The shape found on the reference project: three ids deleted from the markup
        # long ago, a guard that returns if any is absent, and 292 lines below it that
        # have not run since. The scan reported fourteen separate findings.
        symbols, edges = find_dead_regions([_js("""
            function updateLeaderboard() {
              const list = document.getElementById('leaderboard-list');
              const pioneers = document.getElementById('pioneers-btn');
              if (!list || !pioneers) return;
              fetch('/get_leaderboard_data/');
              list.innerHTML = render();
              wireTheButtons();
            }
        """)], {"leaderboard-list", "pioneers-btn"})
        self.assertEqual(len(symbols), 1, [s.label for s in symbols])
        region = symbols[0]
        self.assertEqual(region.kind, "dead_region")
        self.assertEqual(region.label, "updateLeaderboard")
        self.assertEqual(region.line, 5)
        for word in ("leaderboard-list", "pioneers-btn"):
            self.assertIn(word, region.note)
        self.assertEqual(region.status.value, "unresolved")

    def test_one_missing_element_of_two_is_not_a_dead_region(self):
        # `if (!a || !b) return` fires when EITHER is missing, so one unresolved id is
        # enough to kill the region - but `if (!a && !b)` needs both, and a guard the
        # scan cannot read the logic of must not be claimed at all.
        symbols, _ = find_dead_regions([_js("""
            function show() {
              const a = document.getElementById('gone');
              const b = document.getElementById('still-here');
              if (!a && !b) return;
              paint();
            }
        """)], {"gone"})
        self.assertEqual(symbols, [])

    def test_an_either_guard_dies_on_one_missing_element(self):
        symbols, _ = find_dead_regions([_js("""
            function show() {
              const a = document.getElementById('gone');
              const b = document.getElementById('still-here');
              if (!a || !b) return;
              paint();
            }
        """)], {"gone"})
        self.assertEqual(len(symbols), 1)
        self.assertIn("gone", symbols[0].note)

    def test_a_guard_whose_elements_all_exist_is_not_reported(self):
        symbols, _ = find_dead_regions([_js("""
            function show() {
              const a = document.getElementById('here');
              if (!a) return;
              paint();
            }
        """)], set())
        self.assertEqual(symbols, [])

    def test_a_guard_with_nothing_after_it_is_not_a_region(self):
        # The guard is the last statement: there is no code below it to be dead, and
        # reporting "a region of nothing" is the kind of finding that teaches people to
        # skim.
        symbols, _ = find_dead_regions([_js("""
            function show() {
              const a = document.getElementById('gone');
              if (!a) return;
            }
        """)], {"gone"})
        self.assertEqual(symbols, [])

    def test_a_guard_inside_a_branch_kills_only_that_branch(self):
        # Not implemented, and deliberately: a return inside an `if` block ends the
        # function too, but working out whether that block always runs is the analysis
        # this tool does not do. Silence beats a claim about the wrong lines.
        symbols, _ = find_dead_regions([_js("""
            function show(flag) {
              if (flag) {
                const a = document.getElementById('gone');
                if (!a) return;
                paint();
              }
              always();
            }
        """)], {"gone"})
        self.assertEqual(symbols, [])

    def test_the_region_says_how_much_code_is_in_it(self):
        symbols, _ = find_dead_regions([_js("""
            function big() {
              const a = document.getElementById('gone');
              if (!a) return;
              one();
              two();
              three();
            }
        """)], {"gone"})
        self.assertIn("3 lines", symbols[0].note)

    def test_a_querySelector_guard_counts_too(self):
        symbols, _ = find_dead_regions([_js("""
            function show() {
              const el = document.querySelector('.gone-class');
              if (!el) return;
              paint();
            }
        """)], {"gone-class"})
        self.assertEqual(len(symbols), 1)
        self.assertIn("gone-class", symbols[0].note)


class FoldingSymptomsTests(SimpleTestCase):
    """One finding, not fourteen: everything inside the region points at the region."""

    def _symbol(self, label, file="app.js", line=10, status=None):
        from seamcheck.graph import Status, Symbol

        return Symbol(id=f"dom_selector:id:{label}:{file}:{line}", kind="dom_selector",
                      label=label, sub="id:read", file=file, line=line,
                      status=status or Status.UNCERTAIN, snippet=label, chain=[label],
                      note="")

    def test_a_finding_inside_the_region_stops_being_a_claim(self):
        from seamcheck.dead_region import fold_into_regions
        from seamcheck.graph import Edge, Status

        inside = self._symbol("stat-box", line=12)
        outside = self._symbol("elsewhere", line=99)
        edges = [
            Edge(from_id=inside.id, to_id=inside.id, status=Status.UNRESOLVED),
            Edge(from_id=outside.id, to_id=outside.id, status=Status.UNRESOLVED),
        ]
        spans = [("app.js", 6, 40, "dead_region:app.js:5")]
        folded = fold_into_regions(spans, [inside, outside], edges)
        by_from = {e.from_id: e for e in folded}
        self.assertEqual(by_from[inside.id].status, Status.UNCERTAIN)
        self.assertEqual(by_from[inside.id].to_id, "dead_region:app.js:5")
        self.assertIn("never runs", by_from[inside.id].note)
        # ...and a finding outside it is untouched.
        self.assertEqual(by_from[outside.id].status, Status.UNRESOLVED)
        self.assertEqual(by_from[outside.id].to_id, outside.id)

    def test_a_connected_edge_inside_a_region_is_left_alone(self):
        # Only claims are folded. An edge that says "this reaches that" is still true
        # inside dead code - the code does not run, but the reference is real, and
        # rewriting it would lose a chain the map draws.
        from seamcheck.dead_region import fold_into_regions
        from seamcheck.graph import Edge, Status

        inside = self._symbol("stat-box", line=12)
        edges = [Edge(from_id=inside.id, to_id="dom_attr:id:stat-box:page.html:3",
                      status=Status.CONNECTED)]
        folded = fold_into_regions([("app.js", 6, 40, "dead_region:app.js:5")],
                                   [inside], edges)
        self.assertEqual(folded, edges)


class DeadWriterTests(SimpleTestCase):
    """A multi-writer whose second writer never runs is not a fight.

    Found by the person acting on these findings: deleting one dead function retired two
    multi-writer reports, because one of the two writers had been unreachable all along.
    """

    def _write(self, label, file, line):
        from seamcheck.graph import Status, Symbol

        return Symbol(id=f"dom_selector:id:{label}:{file}:{line}", kind="dom_selector",
                      label=label, sub="id:write", file=file, line=line,
                      status=Status.UNCERTAIN, snippet=label, chain=[label], note="")

    def _finding(self, label):
        from seamcheck.graph import Status, Symbol

        return Symbol(id=f"multi_writer_element:{label}", kind="multi_writer_element",
                      label=label, sub="id", file="a.js", line=3,
                      status=Status.UNRESOLVED, snippet=label, chain=["a.js", "b.js"],
                      note="More than one file writes this element.")

    def test_one_live_writer_and_one_dead_is_not_a_conflict(self):
        from seamcheck.dead_region import demote_dead_writers
        from seamcheck.graph import Status

        spans = [("b.js", 10, 40, "dead_region:b.js:9")]
        out = demote_dead_writers(spans, [self._finding("level-name")],
                                  [self._write("level-name", "a.js", 3),
                                   self._write("level-name", "b.js", 20)])
        self.assertEqual(out[0].status, Status.UNCERTAIN)
        self.assertIn("never runs", out[0].note)
        self.assertIn("b.js:20", out[0].note)
        self.assertIn("one live writer", out[0].note)

    def test_two_live_writers_are_still_a_conflict(self):
        from seamcheck.dead_region import demote_dead_writers
        from seamcheck.graph import Status

        spans = [("c.js", 10, 40, "dead_region:c.js:9")]
        out = demote_dead_writers(spans, [self._finding("level-name")],
                                  [self._write("level-name", "a.js", 3),
                                   self._write("level-name", "b.js", 4),
                                   self._write("level-name", "c.js", 20)])
        self.assertEqual(out[0].status, Status.UNRESOLVED)
        self.assertIn("More than one file", out[0].note)

    def test_a_finding_with_no_dead_writer_is_untouched(self):
        from seamcheck.dead_region import demote_dead_writers

        finding = self._finding("level-name")
        out = demote_dead_writers([("z.js", 1, 2, "dead_region:z.js:1")], [finding],
                                  [self._write("level-name", "a.js", 3)])
        self.assertEqual(out, [finding])
