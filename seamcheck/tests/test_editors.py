from django.test import SimpleTestCase

from seamcheck import editors


class SchemeTests(SimpleTestCase):
    def test_the_default_is_vscode(self):
        self.assertEqual(editors.scheme(None), "vscode://file{path}:{line}")

    def test_a_name_is_matched_case_and_space_insensitively(self):
        # It comes out of a settings dict a human typed.
        self.assertEqual(editors.scheme("  Cursor "), editors.SCHEMES["cursor"])

    def test_an_unknown_name_costs_the_links_not_the_report(self):
        # A typo in a config key should not take the whole map down with it.
        self.assertEqual(editors.scheme("emcas"), "")

    def test_none_is_a_real_opt_out(self):
        self.assertEqual(editors.scheme("none"), "")


class LinkTests(SimpleTestCase):
    def test_it_builds_an_absolute_editor_url(self):
        href = editors.link("vscode", "/repo/pointless/views.py", 42)

        self.assertEqual(href, "vscode://file/repo/pointless/views.py:42")

    def test_a_symbol_with_no_line_points_at_the_top_of_the_file(self):
        # Line 0 does not exist, and several schemes reject it outright.
        self.assertEqual(editors.link("vscode", "/repo/a.py", None), "vscode://file/repo/a.py:1")

    def test_nothing_to_point_at_gives_no_link(self):
        self.assertEqual(editors.link("vscode", "", 3), "")
        self.assertEqual(editors.link("none", "/repo/a.py", 3), "")

    def test_every_scheme_names_both_placeholders(self):
        # A scheme that forgot {line} would silently drop every reader at line 1.
        for name, template in editors.SCHEMES.items():
            if not template:
                continue
            with self.subTest(editor=name):
                self.assertIn("{path}", template)
                self.assertIn("{line}", template)
