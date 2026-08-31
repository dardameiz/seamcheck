"""Flask: decorators again, blueprints instead of routers, and one thing unique to it.

`methods=` matters more here than in any other framework the tool reads. Flask defaults to
GET ALONE, so a rule declared without it answers GET and nothing else - and a POST to it is
a 405, not a 404. Getting that wrong is not a rounding error, it is the tool describing a
different application.
"""

from __future__ import annotations

import pathlib
import tempfile
import unittest

from seamcheck.adapters.flask_adapter import FlaskAdapter, _join
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
    scan = FlaskAdapter().scan(root, {}, null())
    return {s.label: s.sub for s in scan.symbols if s.kind == "url"}


APP = "from flask import Flask\napp = Flask(__name__)\n"


class Routes(unittest.TestCase):
    def test_a_route_defaults_to_get_alone(self):
        paths = _paths(_repo({"app.py": APP + "@app.route('/health')\ndef health(): pass\n"}))
        self.assertEqual(paths, {"/health": "GET"})

    def test_methods_are_read_from_the_decorator(self):
        paths = _paths(_repo({"app.py": APP +
            "@app.route('/users', methods=['GET', 'POST'])\ndef users(): pass\n"}))
        self.assertEqual(paths["/users"], "GET/POST")

    def test_a_methods_tuple_works_too(self):
        paths = _paths(_repo({"app.py": APP +
            "@app.route('/x', methods=('PUT', 'PATCH'))\ndef x(): pass\n"}))
        self.assertEqual(paths["/x"], "PATCH/PUT")

    def test_the_flask_two_shortcuts_are_read(self):
        paths = _paths(_repo({"app.py": APP +
            "@app.get('/a')\ndef a(): pass\n@app.post('/b')\ndef b(): pass\n"}))
        self.assertEqual(paths["/a"], "GET")
        self.assertEqual(paths["/b"], "POST")

    def test_a_converter_in_the_path_is_kept_verbatim(self):
        self.assertIn("/users/<int:id>", _paths(_repo({"app.py": APP +
            "@app.route('/users/<int:id>')\ndef user(id): pass\n"})))

    def test_add_url_rule_is_a_route_without_a_decorator(self):
        """Flask-RESTful and MethodView both produce these; decorators alone miss them."""
        paths = _paths(_repo({"app.py": APP +
            "app.add_url_rule('/thing', view_func=Thing.as_view('thing'), "
            "methods=['GET', 'DELETE'])\n"}))
        self.assertEqual(paths["/thing"], "DELETE/GET")

    def test_two_decorators_on_one_path_merge_into_one_route(self):
        paths = _paths(_repo({"app.py": APP +
            "@app.get('/x')\ndef a(): pass\n@app.post('/x')\ndef b(): pass\n"}))
        self.assertEqual(list(paths), ["/x"])
        self.assertEqual(paths["/x"], "GET/POST")


class Blueprints(unittest.TestCase):
    def test_a_blueprint_prefix_applies(self):
        self.assertIn("/api/items", _paths(_repo({"app.py":
            "from flask import Blueprint\nbp = Blueprint('a', __name__, url_prefix='/api')\n"
            "@bp.route('/items')\ndef items(): pass\n"
            "from flask import Flask\napp = Flask(__name__)\napp.register_blueprint(bp)\n"})))

    def test_the_registration_prefix_composes_with_the_blueprint_prefix(self):
        root = _repo({
            "app.py": APP + "from api.items import bp\napp.register_blueprint(bp, url_prefix='/v1')\n",
            "api/__init__.py": "",
            "api/items.py": "from flask import Blueprint\n"
                            "bp = Blueprint('items', __name__, url_prefix='/api')\n"
                            "@bp.route('/items')\ndef items(): pass\n",
        })
        self.assertIn("/v1/api/items", _paths(root))

    def test_a_blueprint_nothing_registers_is_uncertain(self):
        root = _repo({"api.py": "from flask import Blueprint\n"
                                "bp = Blueprint('a', __name__, url_prefix='/api')\n"
                                "@bp.route('/items')\ndef items(): pass\n"})
        scan = FlaskAdapter().scan(root, {}, null())
        route = next(s for s in scan.symbols if s.kind == "url")
        self.assertEqual(route.status, Status.UNCERTAIN)
        self.assertIn("runtime", route.note)

    def test_a_route_on_the_app_itself_is_connected(self):
        root = _repo({"app.py": APP + "@app.route('/health')\ndef health(): pass\n"})
        scan = FlaskAdapter().scan(root, {}, null())
        self.assertEqual(next(s for s in scan.symbols if s.kind == "url").status, Status.CONNECTED)


class RouteNames(unittest.TestCase):
    """Flask has url_for(), so unlike FastAPI and Express the name index is not empty."""

    def test_the_endpoint_function_names_the_route(self):
        root = _repo({"app.py": APP + "@app.route('/health')\ndef health(): pass\n"})
        self.assertEqual(FlaskAdapter().scan(root, {}, null()).route_names["health"], "/health")

    def test_a_blueprint_route_is_also_named_with_its_prefix(self):
        root = _repo({"api.py": "from flask import Blueprint\n"
                                "bp = Blueprint('items', __name__)\n"
                                "@bp.route('/items')\ndef items(): pass\n"})
        names = FlaskAdapter().scan(root, {}, null()).route_names
        self.assertIn("bp.items", names)


class Detection(unittest.TestCase):
    def test_an_app_with_routes_is_detected(self):
        root = _repo({"app.py": APP + "@app.route('/x')\ndef x(): pass\n"})
        self.assertGreater(FlaskAdapter().detect(root, {}), 0.5)

    def test_an_unrelated_repo_scores_zero(self):
        self.assertEqual(FlaskAdapter().detect(_repo({"a.py": "print(1)"}), {}), 0.0)

    def test_a_file_that_will_not_parse_does_not_stop_the_others(self):
        root = _repo({"broken.py": "def (:::\n",
                      "app.py": APP + "@app.route('/ok')\ndef ok(): pass\n"})
        self.assertIn("/ok", _paths(root))


class Helpers(unittest.TestCase):
    def test_join_collapses_slashes(self):
        self.assertEqual(_join("/v1/", "/api", "items/"), "/v1/api/items")
        self.assertEqual(_join("", ""), "/")


if __name__ == "__main__":
    unittest.main()
