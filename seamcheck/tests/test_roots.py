import os
import tempfile
from pathlib import Path

from django.test import SimpleTestCase

from seamcheck.roots import discover_js_roots, static_js_references, vite_entries, vite_entry_map

FIXTURES_DIR = Path(__file__).parent / "fixtures"


class StaticJsReferenceTests(SimpleTestCase):
    def test_finds_both_quote_styles(self):
        found = static_js_references(str(FIXTURES_DIR))

        self.assertIn("pointless/js/pages/seasons_page.js", found)
        self.assertIn("pointless/buttons/js/button_manager.js", found)


class JsRootTests(SimpleTestCase):
    """Against this package's own fixtures. These used to name a specific project's
    vite.config.js and template root, so they described whichever codebase the package
    was installed beside and failed outright in a clone."""

    def _roots(self):
        return discover_js_roots(
            vite_config=str(FIXTURES_DIR / "fixture_vite.config.js"),
            templates_root=str(FIXTURES_DIR),
            static_root=str(FIXTURES_DIR),
        )

    def test_roots_are_vite_entries_plus_static_js_scripts(self):
        # Scripts loaded by {% static_js %} are never imported by a Vite entry, so a
        # Vite-only root set leaves their fetch() calls invisible and their endpoints
        # looking unused.
        roots = self._roots()

        self.assertTrue(any(r.endswith("fixture_entry.js") for r in roots), roots)
        self.assertTrue(any(r.endswith("fixture_module.js") for r in roots), roots)

    def test_every_root_is_an_existing_js_file(self):
        for root in self._roots():
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


class ViteEntryShapeTests(SimpleTestCase):
    """The shapes a real Vite config writes its entry in.

    saleor-dashboard declares its only entry as
    `createHtmlPlugin({ entry: path.resolve(__dirname, "src", "index.tsx") })` and this
    read none of it: the callee is dotted, so `\\w+\\(` never matched after the colon; the
    path is split across three arguments, so only the first would have been captured; and
    `.tsx` was dropped by a `.js`-only filter. With no entry the Vite project fell through
    to the fallback, which listed 1,240 unimported scripts - 902 of them tests and
    stories - as pages.
    """

    def _entries(self, source: str) -> dict:
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "vite.config.js"
            config.write_text(source, encoding="utf-8")
            return vite_entry_map(str(config))

    def test_a_dotted_callee_with_the_path_split_across_arguments(self):
        found = self._entries(
            'createHtmlPlugin({ entry: path.resolve(__dirname, "src", "index.tsx") })'
        )

        self.assertEqual(found, {"entry": "src/index.tsx"})

    def test_a_typescript_entry_is_an_entry(self):
        found = self._entries("export default { build: { rollupOptions: { input: {\n"
                              "  main: resolve(__dirname, 'src/main.ts'),\n"
                              "} } } }")

        self.assertEqual(found, {"main": "src/main.ts"})

    def test_an_input_written_as_one_string_is_an_entry(self):
        found = self._entries("export default { build: { rollupOptions: { "
                              "input: 'src/main.tsx' } } }")

        self.assertEqual(found, {"input": "src/main.tsx"})

    def test_an_alias_into_node_modules_is_not_an_entry(self):
        # `resolve.alias` names packages, not pages. saleor-dashboard's alias block holds
        # `moment: path.resolve(__dirname, "./node_modules/moment/min/moment-with-locales
        # .js")`, which ends in .js and would start a page at a vendored bundle.
        found = self._entries(
            'resolve: { alias: { moment: path.resolve(__dirname, '
            '"./node_modules/moment/min/moment-with-locales.js") } }'
        )

        self.assertEqual(found, {})

    def test_an_alias_to_a_directory_is_not_an_entry(self):
        found = self._entries('resolve: { alias: { src: path.resolve(__dirname, "./src") } }')

        self.assertEqual(found, {})

    def test_an_alias_pointing_at_a_stub_inside_the_repository_is_not_an_entry(self):
        # documenso stubs the `canvas` package out with a file of its own. An alias is
        # written exactly like an entry, so excluding node_modules and directories is not
        # enough to tell them apart - only the position in the config says which it is.
        # Read as an entry, it became a page called "empty-module".
        found = self._entries("resolve: { alias: { canvas: "
                              "path.resolve(__dirname, './app/types/empty-module.ts') } }")

        self.assertEqual(found, {})

    def test_another_plugins_entry_option_is_not_an_entry(self):
        # `serverAdapter({ entry: 'server/router.ts' })` is the Hono dev-server adapter's
        # own entry, not a page - documenso's, read as a page called "router". `entry`
        # names a page only where createHtmlPlugin takes it.
        found = self._entries("serverAdapter({ entry: 'server/router.ts', exclude: [] })")

        self.assertEqual(found, {})


class ViteEntriesResolveAgainstTheConfigTests(SimpleTestCase):
    """A Vite entry is written relative to `__dirname` - the directory holding the config -
    and was tested with `is_file()` on the literal string, which resolves against the
    process's cwd instead.

    So scanning a repository seamcheck was not sitting inside dropped every Vite entry it
    had just read: `--repo-root`, an editor plugin, the corpus harness. saleor-dashboard
    lost its only entry that way and fell through to the last-resort fallback, which is
    what put 1,240 unimported scripts in its page list.
    """

    def test_an_entry_is_found_when_the_command_runs_outside_the_repository(self):
        with tempfile.TemporaryDirectory() as repo, tempfile.TemporaryDirectory() as elsewhere:
            (Path(repo) / "src").mkdir()
            (Path(repo) / "src" / "index.tsx").write_text("", encoding="utf-8")
            (Path(repo) / "vite.config.js").write_text(
                'createHtmlPlugin({ entry: path.resolve(__dirname, "src", "index.tsx") })',
                encoding="utf-8")
            here = os.getcwd()
            try:
                os.chdir(elsewhere)
                roots = discover_js_roots(vite_config=str(Path(repo) / "vite.config.js"),
                                          templates_root=str(repo), static_root=str(repo))
            finally:
                os.chdir(here)

        self.assertEqual([Path(root).name for root in roots], ["index.tsx"])
