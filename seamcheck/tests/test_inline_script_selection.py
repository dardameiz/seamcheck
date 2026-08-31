"""Which <script> elements are JavaScript, and which only look like it.

Every case here was found by running the probe against a real 511k-line project, where
24 of 77 inline blocks failed to parse. None of them were JavaScript:

  - 20 were `application/ld+json`, the structured-data block every SEO-conscious site
    ships. JSON is not a JavaScript statement and can never parse as one.
  - 3 were English prose inside `{% comment %}`, explaining why a tag must stay put. The
    sentence contained the words `<script src>`, and the src-detecting lookahead wants
    `src=`, so the paragraph after it was read as a script body.
  - 1 was `{% if %}0{% else %}null{% endif %}`, which is one value in the rendered page
    and the invalid `0null` once the tags are stripped.
"""

from __future__ import annotations

import pathlib
import tempfile
import unittest

from seamcheck.extractors.js_extractor import inline_script_blocks


def _template(body: str) -> list[str]:
    path = pathlib.Path(tempfile.mkdtemp()) / "page.html"
    path.write_text(body, encoding="utf-8")
    return [str(path)]


class InlineScriptSelection(unittest.TestCase):
    def test_plain_inline_script_is_collected(self):
        blocks = inline_script_blocks(_template("<script>fetch('/api/x/');</script>"))
        self.assertEqual(len(blocks), 1)
        self.assertIn("fetch", blocks[0][1])

    def test_module_and_text_javascript_are_collected(self):
        for declared in ('type="module"', 'type="text/javascript"'):
            with self.subTest(declared=declared):
                blocks = inline_script_blocks(
                    _template(f"<script {declared}>fetch('/api/x/');</script>")
                )
                self.assertEqual(len(blocks), 1)

    def test_json_ld_is_not_javascript(self):
        body = '<script type="application/ld+json">{"@context":"https://schema.org"}</script>'
        self.assertEqual(inline_script_blocks(_template(body)), [])

    def test_json_and_template_types_are_not_javascript(self):
        for declared in ("application/json", "text/x-template", "importmap"):
            with self.subTest(declared=declared):
                body = f'<script type="{declared}">{{"a": 1}}</script>'
                self.assertEqual(inline_script_blocks(_template(body)), [])

    def test_prose_in_a_django_comment_is_not_a_script(self):
        body = (
            "{% comment %}\n"
            "It MUST stay a plain <script src> at THIS position: the order matters.\n"
            "{% endcomment %}\n"
            "<script>fetch('/api/real/');</script>"
        )
        blocks = inline_script_blocks(_template(body))
        self.assertEqual(len(blocks), 1, "only the real script survives")
        self.assertIn("/api/real/", blocks[0][1])

    def test_html_comment_containing_a_script_is_ignored(self):
        body = "<!-- <script>fetch('/api/old/');</script> -->\n<script>fetch('/api/new/');</script>"
        blocks = inline_script_blocks(_template(body))
        self.assertEqual(len(blocks), 1)
        self.assertIn("/api/new/", blocks[0][1])
        self.assertNotIn("/api/old/", blocks[0][1])

    def test_if_else_keeps_one_branch_not_both(self):
        body = "<script>const v = [{% if x %}0{% else %}null{% endif %}];</script>"
        blocks = inline_script_blocks(_template(body))
        self.assertEqual(len(blocks), 1)
        self.assertNotIn("0null", blocks[0][1], "both branches concatenated is invalid JS")

    def test_line_offset_survives_neutralising(self):
        """Comments are blanked, never deleted -- a shifted line points at the wrong code."""
        body = "<!--\n\n\n-->\n{% comment %}\n\n{% endcomment %}\n<script>fetch('/api/x/');</script>"
        blocks = inline_script_blocks(_template(body))
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0][2], 7, "offset must count the blanked lines")


if __name__ == "__main__":
    unittest.main()
