"""NestJS: routes are decorators, which is why this adapter waited for the parser.

`@Controller('users')` on a class and `@Get(':id')` on a method compose to `/users/:id`.
None of that is hard to read - but acorn could not parse a decorator at all, so before
@babel/parser landed a NestJS file was not partially understood, it was lost entirely.

Validated against the RealWorld reference implementation, which gives an unusually strong
oracle: the SAME API spec is implemented in FastAPI and in NestJS by different authors, and
two independent adapters reading two independent codebases agree on 12 of 15 routes - with
every difference traceable to the source rather than to a reader.
"""

from __future__ import annotations

import pathlib
import tempfile
import unittest

from seamcheck.adapters.nestjs_adapter import NestJSAdapter, _join
from seamcheck.progress import null


def _repo(files: dict[str, str]) -> str:
    root = pathlib.Path(tempfile.mkdtemp())
    for name, body in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    return str(root)


def _paths(root: str) -> dict[str, str]:
    scan = NestJSAdapter().scan(root, {}, null())
    return {s.label: s.sub for s in scan.symbols if s.kind == "url"}


class RouteComposition(unittest.TestCase):
    def test_controller_prefix_and_method_path_compose(self):
        root = _repo({"users.controller.ts":
                      "@Controller('users')\nexport class C {\n  @Get(':id')\n"
                      "  findOne(@Param('id') id: string) { return 1; }\n}\n"})
        self.assertIn("/users/:id", _paths(root))

    def test_a_bare_method_decorator_is_the_controller_itself(self):
        root = _repo({"users.controller.ts":
                      "@Controller('users')\nexport class C {\n  @Post()\n  create() {}\n}\n"})
        self.assertEqual(_paths(root), {"/users": "POST"})

    def test_methods_on_one_path_become_one_route(self):
        root = _repo({"c.controller.ts":
                      "@Controller('u')\nexport class C {\n  @Get()\n  a() {}\n"
                      "  @Post()\n  b() {}\n}\n"})
        self.assertEqual(_paths(root)["/u"], "GET/POST")

    def test_the_object_form_of_controller_is_read(self):
        """`@Controller({ path: 'users', version: '1' })` is the documented form."""
        root = _repo({"c.controller.ts":
                      "@Controller({ path: 'users', version: '1' })\nexport class C {\n"
                      "  @Get()\n  all() {}\n}\n"})
        self.assertIn("/users", _paths(root))

    def test_a_global_prefix_moves_every_route(self):
        root = _repo({
            "main.ts": "async function bootstrap() {\n"
                       "  const app = await NestFactory.create(AppModule);\n"
                       "  app.setGlobalPrefix('api');\n}\n",
            "users.controller.ts": "@Controller('users')\nexport class C {\n"
                                   "  @Get()\n  all() {}\n}\n",
        })
        self.assertIn("/api/users", _paths(root))

    def test_a_controller_with_no_prefix_is_the_root(self):
        root = _repo({"app.controller.ts":
                      "@Controller()\nexport class C {\n  @Get()\n  root() {}\n}\n"})
        self.assertIn("/", _paths(root))

    def test_parameter_decorators_do_not_break_the_read(self):
        root = _repo({"c.controller.ts":
                      "@Controller('u')\nexport class C {\n  @Post()\n"
                      "  create(@Body() dto: Dto, @Param('id') id: string) {}\n}\n"})
        self.assertIn("/u", _paths(root))


class WhatIsNotARoute(unittest.TestCase):
    def test_a_class_with_no_controller_decorator_is_ignored(self):
        root = _repo({"s.service.ts":
                      "@Injectable()\nexport class S {\n  find() { return 1; }\n}\n"})
        self.assertEqual(_paths(root), {})

    def test_spec_files_are_not_scanned(self):
        root = _repo({"c.controller.spec.ts":
                      "@Controller('test')\nexport class C {\n  @Get()\n  a() {}\n}\n"})
        self.assertEqual(_paths(root), {})

    def test_a_file_that_will_not_parse_does_not_stop_the_others(self):
        root = _repo({
            "broken.ts": "class ( { : : :\n",
            "c.controller.ts": "@Controller('ok')\nexport class C {\n  @Get()\n  a() {}\n}\n",
        })
        self.assertIn("/ok", _paths(root))


class Detection(unittest.TestCase):
    def test_the_core_dependency_is_decisive(self):
        root = _repo({"package.json": '{"dependencies": {"@nestjs/core": "^10.0.0"}}'})
        self.assertGreaterEqual(NestJSAdapter().detect(root, {}), 0.9)

    def test_a_controller_import_is_enough_without_a_manifest(self):
        root = _repo({"c.controller.ts":
                      "import { Controller, Get } from '@nestjs/common';\n"
                      "@Controller('u')\nexport class C {}\n"})
        self.assertGreater(NestJSAdapter().detect(root, {}), 0.5)

    def test_an_unrelated_repo_scores_zero(self):
        self.assertEqual(NestJSAdapter().detect(_repo({"a.ts": "const x: number = 1;"}), {}), 0.0)


class Helpers(unittest.TestCase):
    def test_join_collapses_slashes_and_empties(self):
        self.assertEqual(_join("api", "", "users/"), "/api/users")
        self.assertEqual(_join("", "", ""), "/")


if __name__ == "__main__":
    unittest.main()
