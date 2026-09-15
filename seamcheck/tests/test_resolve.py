import os
import pathlib
import tempfile
import unittest

from seamcheck.resolve import Resolver, norm_path


def _repo(files: dict[str, str]) -> str:
    root = pathlib.Path(tempfile.mkdtemp())
    for name, body in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    return str(root)


def _resolve(root: str, from_file: str, import_path: str):
    return Resolver(root).resolve(os.path.join(root, from_file), import_path)


class RelativeImportTests(unittest.TestCase):
    def test_a_relative_import_resolves_with_an_inferred_extension(self):
        root = _repo({"src/a.ts": "", "src/b.ts": ""})
        resolved = _resolve(root, "src/b.ts", "./a")
        self.assertEqual((resolved.file, resolved.asset, resolved.third_party),
                         (os.path.join(root, "src/a.ts"), None, False))

    def test_a_folder_import_resolves_to_its_index(self):
        root = _repo({"src/lib/index.ts": "", "src/b.ts": ""})
        self.assertEqual(_resolve(root, "src/b.ts", "./lib").file, os.path.join(root, "src/lib/index.ts"))

    def test_a_folder_holding_index_js_and_index_ts_resolves_to_index_js_as_it_always_did(self):
        root = _repo({"src/lib/index.js": "", "src/lib/index.ts": "", "src/b.ts": ""})
        self.assertEqual(_resolve(root, "src/b.ts", "./lib").file, os.path.join(root, "src/lib/index.js"))

    def test_a_stylesheet_import_is_an_asset_not_a_file(self):
        root = _repo({"src/globals.css": "", "src/b.ts": ""})
        resolved = _resolve(root, "src/b.ts", "./globals.css")
        self.assertEqual((resolved.file, resolved.asset), (None, os.path.join(root, "src/globals.css")))

    def test_a_relative_import_of_nothing_resolves_to_nothing_and_is_not_a_package(self):
        root = _repo({"src/b.ts": ""})
        resolved = _resolve(root, "src/b.ts", "./missing")
        self.assertEqual((resolved.file, resolved.asset, resolved.third_party), (None, None, False))


class TsconfigAliasTests(unittest.TestCase):
    def test_a_wildcard_alias_resolves_against_baseurl(self):
        root = _repo({
            "tsconfig.json": '{"compilerOptions": {"baseUrl": ".", "paths": {"@/*": ["./src/*"]}}}',
            "src/components/Foo.tsx": "",
            "app/page.tsx": "",
        })
        self.assertEqual(_resolve(root, "app/page.tsx", "@/components/Foo").file,
                         os.path.join(root, "src/components/Foo.tsx"))

    def test_a_schema_url_in_the_config_does_not_hide_its_aliases(self):
        # Most tsconfig.json files open with a $schema URL. Stripping `//...` as a comment
        # without regard for strings cut the URL, broke the JSON, and lost every alias.
        root = _repo({
            "tsconfig.json": (
                '{\n  "$schema": "https://json.schemastore.org/tsconfig",\n'
                '  "compilerOptions": {"baseUrl": ".", "paths": {"@/*": ["./*"]}}\n}\n'
            ),
            "lib/util.ts": "",
            "app/page.tsx": "",
        })
        self.assertEqual(_resolve(root, "app/page.tsx", "@/lib/util").file, os.path.join(root, "lib/util.ts"))

    def test_comments_and_trailing_commas_do_not_break_the_config(self):
        root = _repo({
            "tsconfig.json": (
                "{\n  // line comment\n  /* block\n     comment */\n"
                '  "compilerOptions": {"baseUrl": ".", "paths": {"@/*": ["./src/*",],},},\n}\n'
            ),
            "src/x.ts": "",
            "app/page.tsx": "",
        })
        self.assertEqual(_resolve(root, "app/page.tsx", "@/x").file, os.path.join(root, "src/x.ts"))

    def test_extends_is_followed_for_paths_and_baseurl(self):
        root = _repo({
            "tsconfig.base.json": '{"compilerOptions": {"baseUrl": ".", "paths": {"@/*": ["./src/*"]}}}',
            "tsconfig.json": '{"extends": "./tsconfig.base.json", "compilerOptions": {}}',
            "src/lib/util.ts": "",
            "app/page.tsx": "",
        })
        self.assertEqual(_resolve(root, "app/page.tsx", "@/lib/util").file,
                         os.path.join(root, "src/lib/util.ts"))

    def test_the_nearer_config_wins_a_colliding_alias(self):
        root = _repo({
            "tsconfig.base.json": '{"compilerOptions": {"baseUrl": ".", "paths": {"@/*": ["./old/*"]}}}',
            "tsconfig.json": ('{"extends": "./tsconfig.base.json", '
                              '"compilerOptions": {"paths": {"@/*": ["./src/*"]}}}'),
            "src/x.ts": "",
            "old/x.ts": "",
            "app/page.tsx": "",
        })
        self.assertEqual(_resolve(root, "app/page.tsx", "@/x").file, os.path.join(root, "src/x.ts"))

    def test_a_config_that_extends_itself_does_not_loop(self):
        root = _repo({
            "tsconfig.json": ('{"extends": "./tsconfig.json", '
                              '"compilerOptions": {"baseUrl": ".", "paths": {"@/*": ["./*"]}}}'),
            "x.ts": "",
            "app/page.tsx": "",
        })
        self.assertEqual(_resolve(root, "app/page.tsx", "@/x").file, os.path.join(root, "x.ts"))


class WorkspacePackageTests(unittest.TestCase):
    def test_a_workspace_package_resolves_through_its_manifest(self):
        root = _repo({
            "package.json": '{"workspaces": ["packages/*"]}',
            "packages/ui/package.json": '{"name": "@scope/ui", "main": "src/index.ts"}',
            "packages/ui/src/index.ts": "",
            "app/page.tsx": "",
        })
        self.assertEqual(_resolve(root, "app/page.tsx", "@scope/ui").file,
                         os.path.join(root, "packages/ui/src/index.ts"))

    def test_a_deep_import_into_a_workspace_package_resolves_beside_its_entry(self):
        root = _repo({
            "package.json": '{"workspaces": ["packages/*"]}',
            "packages/ui/package.json": '{"name": "@scope/ui", "main": "src/index.ts"}',
            "packages/ui/src/Button.ts": "",
            "app/page.tsx": "",
        })
        self.assertEqual(_resolve(root, "app/page.tsx", "@scope/ui/Button").file,
                         os.path.join(root, "packages/ui/src/Button.ts"))


class ThirdPartyTests(unittest.TestCase):
    def test_a_package_with_no_first_party_match_is_third_party(self):
        root = _repo({"app/page.tsx": ""})
        resolved = _resolve(root, "app/page.tsx", "react")
        self.assertEqual((resolved.file, resolved.asset, resolved.third_party), (None, None, True))


class NormPathTests(unittest.TestCase):
    def test_a_path_is_repo_relative_with_forward_slashes(self):
        root = _repo({"src/a.ts": ""})
        self.assertEqual(norm_path(os.path.join(root, "src", "a.ts"), root), "src/a.ts")
