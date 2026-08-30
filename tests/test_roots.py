from pathlib import Path

from django.test import SimpleTestCase

from signal_map.roots import discover_js_roots, static_js_references, vite_entries

FIXTURES_DIR = Path(__file__).parent / "fixtures"


class StaticJsReferenceTests(SimpleTestCase):
    def test_finds_both_quote_styles(self):
        found = static_js_references(str(FIXTURES_DIR))

        self.assertIn("pointless/js/pages/seasons_page.js", found)
        self.assertIn("pointless/buttons/js/button_manager.js", found)


class JsRootTests(SimpleTestCase):
    def test_roots_are_vite_entries_plus_static_js_scripts(self):
        # Scripts loaded by {% static_js %} are never imported by a Vite entry, so a
        # Vite-only root set leaves their fetch() calls invisible and their endpoints
        # looking unused.
        roots = discover_js_roots(
            vite_config="vite.config.js",
            templates_root="pointless/templates",
            static_root="pointless/static",
        )

        self.assertTrue(any(r.endswith("base-main.js") for r in roots))
        self.assertTrue(any(r.endswith("button_manager.js") for r in roots))

    def test_every_root_is_an_existing_js_file(self):
        roots = discover_js_roots(
            vite_config="vite.config.js",
            templates_root="pointless/templates",
            static_root="pointless/static",
        )

        for root in roots:
            self.assertTrue(root.endswith(".js"), root)
            self.assertTrue(Path(root).is_file(), root)


class UnquotedViteEntryTests(SimpleTestCase):
    def test_an_unquoted_object_key_is_still_an_entry(self):
        # `main: resolve(...)` is valid JS. Requiring quotes silently skipped this
        # project's largest entry - the one importing all 65 button classes - leaving
        # 64 files unscanned and their DOM writes, fetch calls and CSS tokens invisible.
        import tempfile
        from pathlib import Path as _P

        with tempfile.TemporaryDirectory() as tmp:
            entry = _P(tmp) / "app.js"
            entry.write_text("")
            config = _P(tmp) / "vite.config.js"
            config.write_text(
                "export default { build: { rollupOptions: { input: {\n"
                "  'quoted': resolve(__dirname, 'app.js'),\n"
                "  main: resolve(__dirname, 'app.js'),\n"
                "} } } }"
            )

            found = vite_entries(str(config))

        self.assertEqual(found, ["app.js", "app.js"])
