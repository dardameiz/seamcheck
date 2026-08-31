"""TypeScript, JSX and decorators, and the two ways one file used to cost a whole run.

Before this, `_JS_EXTENSIONS` was ('.js', '.mjs', '.jsx'): TypeScript was not discovered,
and acorn could not have read it anyway. That gap took Next.js and NestJS with it, since
both are TypeScript by default.

The parser is now @babel/parser in ESTree mode rather than acorn plus a transpiler.
Nothing is transformed, so a node's line number is the line in the file a reader will
open - by construction rather than by a transpiler's promise - and type-only and unused
imports survive, which matters because this tool walks the import graph.

Two failures here were found on a 6,378-file repository and each lost EVERY file:

  - Babel puts a BigInt literal's value in the AST as a real BigInt, and JSON.stringify
    refuses it outright. One `123n` anywhere threw during serialisation.
  - Python's splitlines() breaks on U+2028 and U+2029, which are legal unescaped inside a
    JSON string and appear verbatim in real JavaScript. One of them cut a record in half.
"""

from __future__ import annotations

import pathlib
import tempfile
import unittest

from seamcheck.extractors.js_extractor import _JS_EXTENSIONS, _parse_files, extract_js


def _write(name: str, body: str) -> str:
    path = pathlib.Path(tempfile.mkdtemp()) / name
    path.write_text(body, encoding="utf-8")
    return str(path)


def _parses(name: str, body: str) -> bool:
    path = _write(name, body)
    return path in _parse_files([path], report_failures=False)


class ExtensionsAreDiscovered(unittest.TestCase):
    def test_typescript_extensions_are_in_the_list(self):
        for extension in (".ts", ".tsx", ".jsx", ".mts", ".cts", ".cjs"):
            with self.subTest(extension=extension):
                self.assertIn(extension, _JS_EXTENSIONS)


class SyntaxThatMustParse(unittest.TestCase):
    def test_typescript_annotations(self):
        self.assertTrue(_parses("a.ts", "const el: HTMLElement | null = "
                                        'document.querySelector("#c");\n'))

    def test_typescript_interfaces_and_generics(self):
        self.assertTrue(_parses("a.ts", "interface O { id: number }\n"
                                        "async function g(): Promise<O> { return null as any; }\n"))

    def test_tsx_components(self):
        self.assertTrue(_parses("a.tsx", "export const C = ({i}: {i: string[]}) => "
                                         '<div className="cart">{i.length}</div>;\n'))

    def test_jsx_in_a_plain_js_file(self):
        """Docusaurus and CRA both ship JSX under a .js extension."""
        self.assertTrue(_parses("a.js", 'export const C = () => <span className="hot"/>;\n'))

    def test_legacy_parameter_decorators(self):
        """NestJS decorates parameters; the stage-3 proposal forbids that outright."""
        self.assertTrue(_parses("a.ts", "@Controller()\nexport class A {\n"
                                        "  @Get()\n  root(@Body() dto: Dto): string { return 'x'; }\n}\n"))

    def test_ambient_declaration_files(self):
        self.assertTrue(_parses("a.d.ts", "declare const db: Knex;\nexport = db;\n"))

    def test_a_bigint_literal_does_not_kill_the_record(self):
        self.assertTrue(_parses("a.js", "const big = 9007199254740993n;\nfetch('/api/x/');\n"))

    def test_a_line_separator_does_not_corrupt_the_stream(self):
        """U+2028 is legal unescaped in JSON and breaks Python's splitlines()."""
        self.assertTrue(_parses("a.js", 'const s = "a b";\nfetch("/api/x/");\n'))


class OneBadFileDoesNotCostTheRest(unittest.TestCase):
    def test_a_broken_file_loses_only_itself(self):
        directory = pathlib.Path(tempfile.mkdtemp())
        good = directory / "good.ts"
        good.write_text('const a: number = 1;\nfetch("/api/good/");\n', encoding="utf-8")
        bad = directory / "bad.ts"
        bad.write_text("function ( { : : :\n", encoding="utf-8")
        parsed = _parse_files([str(good), str(bad)], report_failures=False)
        self.assertIn(str(good), parsed)
        self.assertNotIn(str(bad), parsed)

    def test_a_bigint_file_loses_nothing(self):
        directory = pathlib.Path(tempfile.mkdtemp())
        paths = []
        for index, body in enumerate(("const b = 1n;\n", 'fetch("/api/x/");\n', "const c = 2n;\n")):
            path = directory / f"f{index}.js"
            path.write_text(body, encoding="utf-8")
            paths.append(str(path))
        self.assertEqual(len(_parse_files(paths, report_failures=False)), 3)


class SymbolsAndLines(unittest.TestCase):
    def test_a_fetch_in_typescript_is_found_at_the_right_line(self):
        directory = pathlib.Path(tempfile.mkdtemp())
        entry = directory / "main.ts"
        entry.write_text(
            "// a comment\n"
            "const el: HTMLElement | null = document.querySelector('#c');\n"
            "\n"
            "fetch('/api/orders/');\n",
            encoding="utf-8",
        )
        symbols, _ = extract_js([str(entry)], str(directory))
        target = next(s for s in symbols if s.kind == "fetch_target")
        self.assertEqual(target.label, "/api/orders/")
        self.assertEqual(target.line, 4, "no transform runs, so lines are exact")

    def test_typescript_and_javascript_reach_the_same_result(self):
        results = {}
        for extension in ("js", "ts"):
            directory = pathlib.Path(tempfile.mkdtemp())
            (directory / f"helper.{extension}").write_text(
                "export const helper = () => fetch('/api/helper/');\n", encoding="utf-8")
            entry = directory / f"main.{extension}"
            entry.write_text("import { helper } from './helper';\n"
                             "fetch('/api/orders/');\nhelper();\n", encoding="utf-8")
            symbols, _ = extract_js([str(entry)], str(directory))
            results[extension] = sorted(s.label for s in symbols if s.kind == "fetch_target")
        self.assertEqual(results["ts"], results["js"])
        self.assertIn("/api/helper/", results["ts"], "the import graph is walked in TS too")

    def test_an_import_resolves_to_a_typescript_file(self):
        directory = pathlib.Path(tempfile.mkdtemp())
        (directory / "helper.ts").write_text("export const h = () => fetch('/api/h/');\n",
                                             encoding="utf-8")
        entry = directory / "main.ts"
        entry.write_text("import { h } from './helper';\nh();\n", encoding="utf-8")
        symbols, _ = extract_js([str(entry)], str(directory))
        self.assertIn("/api/h/", [s.label for s in symbols if s.kind == "fetch_target"])


if __name__ == "__main__":
    unittest.main()
