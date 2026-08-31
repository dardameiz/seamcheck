"""FastAPI route reading, and the four ways a real app composes a path.

Every case here came from running the adapter against three cloned repositories - the
official template, the RealWorld example, and Netflix's Dispatch (717 Python files). Each
one was a silent failure: the routes still appeared, at paths that do not exist, which would
mark every call to them unresolved.

  1. `from .users import router as users_router` renames the variable, so a mount keyed on
     the local name matched nothing.
  2. `from . import items` then `items.router` resolved the router to the mounting module
     instead of the one it lives in.
  3. A `src/` layout made every module `src.dispatch...` while every import said
     `dispatch...`, so no prefix matched at all.
  4. `prefix=settings.API_V1_STR` is not a literal, and it is how both the template and the
     RealWorld app mount their API root.

Plus two structural ones: prefixes must compose transitively through the whole router tree,
and `app.mount("/api/v1", app=api)` is a different mechanism from `include_router`.
"""

from __future__ import annotations

import pathlib
import tempfile
import unittest

from seamcheck.adapters.fastapi_adapter import FastAPIAdapter, _join, _module_name
from seamcheck.graph import Status
from seamcheck.progress import null


def _repo(**files: str) -> str:
    """Keys are paths with `__` for `/`; use `_repo_at` when a name contains dunders."""
    return _repo_at({name.replace("__", "/"): body for name, body in files.items()})


def _repo_at(files: dict[str, str]) -> str:
    root = pathlib.Path(tempfile.mkdtemp())
    for name, body in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    return str(root)


def _paths(root: str) -> dict[str, str]:
    scan = FastAPIAdapter().scan(root, {}, null())
    return {s.label: s.sub for s in scan.symbols if s.kind == "url"}


class PathComposition(unittest.TestCase):
    def test_a_bare_decorator_is_the_whole_path(self):
        root = _repo(**{"main.py": 'from fastapi import FastAPI\napp = FastAPI()\n'
                                   '@app.get("/health")\ndef health(): ...\n'})
        self.assertIn("/health", _paths(root))

    def test_methods_on_one_path_become_one_route(self):
        root = _repo(**{"main.py": 'from fastapi import FastAPI\napp = FastAPI()\n'
                                   '@app.get("/x")\ndef a(): ...\n'
                                   '@app.delete("/x")\ndef b(): ...\n'})
        paths = _paths(root)
        self.assertEqual(list(paths), ["/x"], "one path, not one per decorator")
        self.assertEqual(paths["/x"], "DELETE/GET")

    def test_router_own_prefix_applies(self):
        root = _repo(**{"main.py": 'from fastapi import APIRouter\n'
                                   'r = APIRouter(prefix="/users")\n'
                                   '@r.get("/{uid}")\ndef one(): ...\n'})
        self.assertIn("/users/{uid}", _paths(root))

    def test_aliased_import_still_matches_its_mount(self):
        root = _repo(**{
            "main.py": 'from fastapi import FastAPI\nfrom app.users import router as users_router\n'
                       'app = FastAPI()\napp.include_router(users_router, prefix="/api")\n',
            "app__users.py": 'from fastapi import APIRouter\nrouter = APIRouter(prefix="/users")\n'
                             '@router.get("/{uid}")\ndef one(): ...\n',
        })
        self.assertIn("/api/users/{uid}", _paths(root))

    def test_module_attribute_form_resolves_to_the_right_module(self):
        root = _repo(**{
            "main.py": 'from fastapi import FastAPI\nfrom app import items\napp = FastAPI()\n'
                       'app.include_router(items.router, prefix="/api")\n',
            "app__items.py": 'from fastapi import APIRouter\nrouter = APIRouter()\n'
                             '@router.get("/items")\ndef all_items(): ...\n',
        })
        self.assertIn("/api/items", _paths(root))

    def test_prefixes_compose_through_the_whole_tree(self):
        """A leaf mounted into a router that is itself mounted must inherit both."""
        root = _repo(**{
            "main.py": 'from fastapi import FastAPI\nfrom api import api_router\napp = FastAPI()\n'
                       'app.include_router(api_router, prefix="/api/v1")\n',
            "api.py": 'from fastapi import APIRouter\nfrom items import router\n'
                      'api_router = APIRouter()\napi_router.include_router(router)\n',
            "items.py": 'from fastapi import APIRouter\nrouter = APIRouter(prefix="/items")\n'
                        '@router.get("")\ndef all_items(): ...\n',
        })
        self.assertIn("/api/v1/items", _paths(root))

    def test_a_prefix_held_in_a_setting_is_resolved(self):
        root = _repo(**{
            "config.py": 'class Settings:\n    API_V1_STR: str = "/api/v1"\n',
            "main.py": 'from fastapi import FastAPI\nfrom api import api_router\n'
                       'from config import Settings\nsettings = Settings()\napp = FastAPI()\n'
                       'app.include_router(api_router, prefix=settings.API_V1_STR)\n',
            "api.py": 'from fastapi import APIRouter\napi_router = APIRouter()\n'
                      '@api_router.get("/ping")\ndef ping(): ...\n',
        })
        self.assertIn("/api/v1/ping", _paths(root))

    def test_an_ambiguous_constant_is_not_guessed(self):
        """Two files, two values, no evidence which applies - so neither is used."""
        root = _repo(**{
            "a.py": 'PREFIX = "/one"\n',
            "b.py": 'PREFIX = "/two"\n',
            "main.py": 'from fastapi import FastAPI\nfrom api import api_router\napp = FastAPI()\n'
                       'app.include_router(api_router, prefix=PREFIX)\n',
            "api.py": 'from fastapi import APIRouter\napi_router = APIRouter()\n'
                      '@api_router.get("/ping")\ndef ping(): ...\n',
        })
        self.assertIn("/ping", _paths(root))

    def test_sub_application_mount_applies(self):
        root = _repo(**{
            "main.py": 'from fastapi import FastAPI\nfrom inner import api\napp = FastAPI()\n'
                       'app.mount("/api/v1", app=api)\n',
            "inner.py": 'from fastapi import FastAPI\napi = FastAPI()\n'
                        '@api.get("/ping")\ndef ping(): ...\n',
        })
        self.assertIn("/api/v1/ping", _paths(root))


class HonestyAboutUnknownPaths(unittest.TestCase):
    def test_a_router_nothing_mounts_is_uncertain_not_asserted(self):
        """Dispatch loads plugin routers through a runtime registry; the prefix is unknowable."""
        root = _repo(**{"plugin.py": 'from fastapi import APIRouter\nrouter = APIRouter()\n'
                                     '@router.post("/slack/command")\ndef command(): ...\n'})
        scan = FastAPIAdapter().scan(root, {}, null())
        route = next(s for s in scan.symbols if s.kind == "url")
        self.assertEqual(route.status, Status.UNCERTAIN)
        self.assertIn("runtime", route.note)

    def test_an_app_root_is_not_treated_as_unmounted(self):
        root = _repo(**{"main.py": 'from fastapi import FastAPI\napp = FastAPI()\n'
                                   '@app.get("/health")\ndef health(): ...\n'})
        scan = FastAPIAdapter().scan(root, {}, null())
        self.assertEqual(next(s for s in scan.symbols if s.kind == "url").status, Status.CONNECTED)

    def test_a_file_that_will_not_parse_does_not_stop_the_others(self):
        root = _repo(**{
            "broken.py": "def (:::\n",
            "main.py": 'from fastapi import FastAPI\napp = FastAPI()\n'
                       '@app.get("/ok")\ndef ok(): ...\n',
        })
        self.assertIn("/ok", _paths(root))

    def test_fastapi_has_no_route_names(self):
        """No reverse(), no {% url %} - the index is empty by nature, not by omission."""
        root = _repo(**{"main.py": 'from fastapi import FastAPI\napp = FastAPI()\n'
                                   '@app.get("/x")\ndef x(): ...\n'})
        self.assertEqual(FastAPIAdapter().scan(root, {}, null()).route_names, {})


class Detection(unittest.TestCase):
    def test_a_fastapi_app_is_detected(self):
        root = _repo(**{"main.py": 'from fastapi import FastAPI\napp = FastAPI()\n'
                                   '@app.get("/x")\ndef x(): ...\n'})
        self.assertGreater(FastAPIAdapter().detect(root, {}), 0.5)

    def test_an_unrelated_repo_scores_zero(self):
        self.assertEqual(FastAPIAdapter().detect(_repo(**{"a.py": "print(1)"}), {}), 0.0)


class Helpers(unittest.TestCase):
    def test_join_collapses_slashes(self):
        self.assertEqual(_join("/api/", "/v1", "items/"), "/api/v1/items")
        self.assertEqual(_join("", "", ""), "/")

    def test_module_name_ignores_a_src_layout(self):
        root = pathlib.Path(_repo_at({
            "src/dispatch/__init__.py": "", "src/dispatch/case/__init__.py": "",
            "src/dispatch/case/views.py": "",
        }))
        name = _module_name(str(root / "src" / "dispatch" / "case" / "views.py"))
        self.assertEqual(name, "dispatch.case.views")


if __name__ == "__main__":
    unittest.main()
