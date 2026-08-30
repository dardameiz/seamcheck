import pathlib
import tempfile

from django.test import SimpleTestCase

from seamcheck.filetree import build_file_tree
from seamcheck.graph import Graph, Status, Symbol


def _symbol(label, file, line, kind="view", status=Status.CONNECTED):
    return Symbol(id=f"{kind}:{label}", kind=kind, label=label, sub="", file=file,
                  line=line, status=status, snippet="", chain=[label], note="")


class FileCoverageTests(SimpleTestCase):
    def _file(self, name, body):
        directory = tempfile.mkdtemp()
        path = pathlib.Path(directory) / name
        path.write_text(body, encoding="utf-8")
        return str(path)

    def test_it_counts_a_files_declarations_against_what_the_graph_knows(self):
        path = self._file("v.py", "def seen():\n    pass\n\n\ndef unseen():\n    pass\n")

        record = build_file_tree(Graph([_symbol("seen", path, 1)], []))[0]

        self.assertEqual((record.declarations, record.known), (2, 1))

    def test_a_decorated_function_is_matched_despite_its_symbol_sitting_above_the_def(self):
        # inspect reports a decorated function from its first decorator, so the symbol's
        # line is above the def ast reports - matching on the range alone found nothing
        # in files that are entirely decorated views.
        path = self._file("v.py", "@csrf_exempt\n@require_POST\nasync def push(request):\n    pass\n")

        record = build_file_tree(Graph([_symbol("push", path, 1)], []))[0]

        self.assertEqual((record.declarations, record.known), (1, 1))

    def test_status_counts_travel_with_the_file(self):
        path = self._file("v.py", "def a():\n    pass\n")
        graph = Graph([
            _symbol("a", path, 1),
            _symbol("b", path, 1, status=Status.UNRESOLVED),
        ], [])

        self.assertEqual(build_file_tree(graph)[0].counts,
                         {"connected": 1, "unresolved": 1})

    def test_a_file_with_no_declarations_is_reported_as_such_not_as_zero_percent(self):
        path = self._file("s.css", ".a { color: red; }\n")

        record = build_file_tree(Graph([_symbol("a", path, 1, kind="css_selector")], []))[0]

        self.assertEqual(record.declarations, 0)

    def test_an_unreadable_file_does_not_take_the_tree_down(self):
        record = build_file_tree(Graph([_symbol("x", "does/not/exist.py", 1)], []))[0]

        self.assertEqual(record.declarations, 0)
