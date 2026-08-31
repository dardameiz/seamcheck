"""Express and Fastify route reading.

The adapter adds no parser - it reads the acorn AST Seamcheck was already building for
every .js file and discarding. What it has to get right is composition: `app.use('/api',
router)` is Express's mount, and in CommonJS the router crosses the file boundary
anonymously (`module.exports = router` on one side, `require('./routes/users')` on the
other), so a router is keyed by the FILE it comes from rather than by any variable name.

The factory case is not exotic - it is the dominant idiom. Ghost writes every route module
as `module.exports = function apiRoutes() { const router = express.Router(); ... }`, so a
reader that only understands `module.exports = router` loses all of them.
"""

from __future__ import annotations

import pathlib
import tempfile
import unittest

from seamcheck.adapters.express_adapter import ExpressAdapter, _join, _resolve
from seamcheck.graph import Status
from seamcheck.progress import null


def _repo(files: dict[str, str]) -> str:
    root = pathlib.Path(tempfile.mkdtemp())
    for name, body in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    return str(root)


def _paths(root: str) -> dict[str, str]:
    scan = ExpressAdapter().scan(root, {}, null())
    return {s.label: s.sub for s in scan.symbols if s.kind == "url"}


APP = "const express = require('express');\nconst app = express();\n"


class RouteReading(unittest.TestCase):
    def test_a_route_on_the_app_is_read(self):
        root = _repo({"server.js": APP + "app.get('/health', (req,res) => res.send('ok'));\n"})
        self.assertIn("/health", _paths(root))

    def test_methods_on_one_path_become_one_route(self):
        root = _repo({"server.js": APP + "app.get('/x', h);\napp.post('/x', h);\n"})
        paths = _paths(root)
        self.assertEqual(list(paths), ["/x"])
        self.assertEqual(paths["/x"], "GET/POST")

    def test_a_settings_read_is_not_a_route(self):
        """`app.get('view engine')` is Express's config reader: no slash, no handler."""
        root = _repo({"server.js": APP + "const engine = app.get('view engine');\n"})
        self.assertEqual(_paths(root), {})

    def test_template_literal_paths_are_read(self):
        root = _repo({"server.js": APP + "app.get(`/health`, h);\n"})
        self.assertIn("/health", _paths(root))


class MountComposition(unittest.TestCase):
    def test_commonjs_router_inherits_its_mount_prefix(self):
        root = _repo({
            "server.js": APP + "const users = require('./routes/users');\n"
                               "app.use('/api/v1', users);\n",
            "routes/users.js": "const express = require('express');\n"
                               "const router = express.Router();\n"
                               "router.get('/users', h);\nmodule.exports = router;\n",
        })
        self.assertIn("/api/v1/users", _paths(root))

    def test_a_factory_export_is_followed(self):
        """`module.exports = function () { const router = ...; }` - Ghost's whole API."""
        root = _repo({
            "server.js": APP + "const routes = require('./routes');\n"
                               "app.use('/api/comments', routes());\n",
            "routes.js": "const express = require('express');\n"
                         "module.exports = function apiRoutes() {\n"
                         "  const router = express.Router();\n"
                         "  router.get('/counts', h);\n  return router;\n};\n",
        })
        self.assertIn("/api/comments/counts", _paths(root))

    def test_a_re_export_is_followed(self):
        root = _repo({
            "server.js": APP + "const c = require('./comments');\napp.use('/api', c());\n",
            "comments/index.js": "module.exports = require('./routes');\n",
            "comments/routes.js": "const express = require('express');\n"
                                  "module.exports = function () {\n"
                                  "  const router = express.Router();\n"
                                  "  router.get('/counts', h);\n  return router;\n};\n",
        })
        self.assertIn("/api/counts", _paths(root))

    def test_an_inline_require_mount_is_followed(self):
        root = _repo({
            "server.js": APP + "app.use('/api', require('./routes'));\n",
            "routes.js": "const express = require('express');\n"
                         "const router = express.Router();\nrouter.get('/x', h);\n"
                         "module.exports = router;\n",
        })
        self.assertIn("/api/x", _paths(root))

    def test_prefixes_compose_through_two_levels(self):
        root = _repo({
            "server.js": APP + "app.use('/api', require('./api'));\n",
            "api.js": "const express = require('express');\nconst router = express.Router();\n"
                      "router.use('/v1', require('./v1'));\nmodule.exports = router;\n",
            "v1.js": "const express = require('express');\nconst router = express.Router();\n"
                     "router.get('/items', h);\nmodule.exports = router;\n",
        })
        self.assertIn("/api/v1/items", _paths(root))

    def test_two_routers_behind_one_factory_is_not_guessed(self):
        """Ambiguous: which router does the factory return? No evidence, so no claim."""
        root = _repo({
            "server.js": APP + "app.use('/api', require('./routes')());\n",
            "routes.js": "const express = require('express');\n"
                         "module.exports = function () {\n"
                         "  const a = express.Router();\n  const b = express.Router();\n"
                         "  a.get('/one', h);\n  return a;\n};\n",
        })
        self.assertIn("/one", _paths(root))


class FastifySupport(unittest.TestCase):
    def test_fastify_routes_and_register_prefix(self):
        root = _repo({
            "server.js": "const fastify = require('fastify')();\n"
                         "fastify.register(require('./users'), { prefix: '/api' });\n",
            "users.js": "const router = require('express').Router();\n"
                        "router.get('/users', h);\nmodule.exports = router;\n",
        })
        self.assertIn("/api/users", _paths(root))


class HonestyAboutUnknownPaths(unittest.TestCase):
    def test_an_unmounted_router_is_uncertain(self):
        root = _repo({"routes.js": "const express = require('express');\n"
                                   "const router = express.Router();\nrouter.get('/x', h);\n"})
        scan = ExpressAdapter().scan(root, {}, null())
        route = next(s for s in scan.symbols if s.kind == "url")
        self.assertEqual(route.status, Status.UNCERTAIN)
        self.assertIn("runtime", route.note)

    def test_a_route_on_the_app_itself_is_connected(self):
        root = _repo({"server.js": APP + "app.get('/health', h);\n"})
        scan = ExpressAdapter().scan(root, {}, null())
        self.assertEqual(next(s for s in scan.symbols if s.kind == "url").status, Status.CONNECTED)

    def test_express_has_no_route_names(self):
        root = _repo({"server.js": APP + "app.get('/x', h);\n"})
        self.assertEqual(ExpressAdapter().scan(root, {}, null()).route_names, {})


class Detection(unittest.TestCase):
    def test_package_json_dependency_is_decisive(self):
        root = _repo({"package.json": '{"dependencies": {"express": "^4.18.0"}}'})
        self.assertGreaterEqual(ExpressAdapter().detect(root, {}), 0.9)

    def test_a_require_is_enough_without_a_manifest(self):
        root = _repo({"server.js": "const express = require('express');\n"})
        self.assertGreater(ExpressAdapter().detect(root, {}), 0.5)

    def test_an_unrelated_repo_scores_zero(self):
        self.assertEqual(ExpressAdapter().detect(_repo({"a.js": "console.error(1)"}), {}), 0.0)


class Helpers(unittest.TestCase):
    def test_join_collapses_slashes(self):
        self.assertEqual(_join("/api/", "/v1", "items/"), "/api/v1/items")

    def test_resolve_prefers_a_real_file_then_index(self):
        root = pathlib.Path(_repo({"a/index.js": "", "b.js": ""}))
        self.assertTrue(_resolve(str(root / "s.js"), "./a").endswith("a/index.js"))
        self.assertTrue(_resolve(str(root / "s.js"), "./b").endswith("b.js"))


if __name__ == "__main__":
    unittest.main()
