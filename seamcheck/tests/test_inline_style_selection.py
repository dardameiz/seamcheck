"""A template's <style> block is not necessarily CSS.

Found by the first live probe run. One block on the reference project carried a
`{% comment %}` explaining why some furniture is hidden; postcss refused the whole block,
so every selector defined there looked undefined and every class it styled looked unused.

The block is also written to a scratch file named by index, so the generic failure message
named "148-70.css" - a path no reader can open. It names the template now.
"""

from __future__ import annotations

import pathlib
import tempfile
import unittest

from seamcheck.extractors.css_extractor import _neutralise_css, extract_template_css


def _template(body: str) -> list[str]:
    path = pathlib.Path(tempfile.mkdtemp()) / "page.html"
    path.write_text(body, encoding="utf-8")
    return [str(path)]


class InlineStyleNeutralising(unittest.TestCase):
    def test_django_comment_body_is_removed_not_just_its_tags(self):
        block = "{% comment %}why this is hidden{% endcomment %}\n.a { color: red; }"
        cleaned = _neutralise_css(block)
        self.assertNotIn("why this is hidden", cleaned)
        self.assertIn(".a { color: red; }", cleaned)

    def test_interpolated_value_becomes_something_postcss_accepts(self):
        cleaned = _neutralise_css(".a { width: {{ w }}px; }")
        self.assertNotIn("{{", cleaned)
        self.assertIn("width:", cleaned)

    def test_line_count_is_preserved(self):
        """Blanked, never deleted - a shifted line points at the wrong rule."""
        block = "{% comment %}\n\n\n{% endcomment %}\n.a { color: red; }"
        self.assertEqual(_neutralise_css(block).count("\n"), block.count("\n"))

    def test_selectors_survive_a_block_that_contains_template_syntax(self):
        body = (
            "<style>\n"
            "{% comment %}PB-NOTE: division-only furniture disappears{% endcomment %}\n"
            ".arena-panel { color: red; }\n"
            "#league-rail { display: none; }\n"
            "</style>"
        )
        labels = {symbol.label for symbol in extract_template_css(_template(body))}
        self.assertIn("arena-panel", labels)
        self.assertIn("league-rail", labels)

    def test_plain_block_still_works(self):
        body = "<style>.plain { color: blue; }</style>"
        labels = {symbol.label for symbol in extract_template_css(_template(body))}
        self.assertIn("plain", labels)


if __name__ == "__main__":
    unittest.main()
