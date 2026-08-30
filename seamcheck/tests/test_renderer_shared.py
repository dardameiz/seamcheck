import os

from django.test import SimpleTestCase

from seamcheck.graph import Status, Symbol
from seamcheck.renderers._shared import where


def _symbol(file, line=7):
    return Symbol(
        id="s", kind="url", label="s", sub="", file=file, line=line,
        status=Status.UNRESOLVED, snippet="<s>", chain=["s"], note="",
    )


class WhereTests(SimpleTestCase):
    def test_a_repo_relative_path_is_kept_as_is(self):
        self.assertEqual(where(_symbol("pointless/static/x.js")), "pointless/static/x.js:7")

    def test_a_leading_dot_slash_is_stripped(self):
        # os.path.join(".", "a/b") and similar path-building in the extractors leaves a
        # literal "./" prefix - the same location as the plain relative form.
        self.assertEqual(where(_symbol("./pointless/static/x.js")), "pointless/static/x.js:7")

    def test_an_absolute_path_is_made_repo_relative(self):
        # entry_points_extractor symbols carry os.path.abspath() paths (967 in a real
        # scan) - an absolute path leaks the machine's directory layout into a document
        # the README tells people to publish. Build the absolute form from the real
        # cwd (the repo root the tests run from) so this does not depend on where this
        # checkout happens to live on disk.
        absolute = os.path.join(os.getcwd(), "pointless", "static", "x.js")

        self.assertEqual(where(_symbol(absolute)), "pointless/static/x.js:7")

    def test_all_three_forms_of_the_same_location_render_identically(self):
        absolute = os.path.join(os.getcwd(), "pointless", "static", "x.js")
        forms = [
            where(_symbol("pointless/static/x.js")),
            where(_symbol("./pointless/static/x.js")),
            where(_symbol(absolute)),
        ]

        self.assertEqual(len(set(forms)), 1)

    def test_no_line_means_no_trailing_colon(self):
        self.assertEqual(where(_symbol("pointless/static/x.js", line=None)), "pointless/static/x.js")

    def test_no_file_means_empty_string(self):
        self.assertEqual(where(_symbol("")), "")
