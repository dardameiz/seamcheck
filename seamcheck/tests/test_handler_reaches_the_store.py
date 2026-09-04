"""A handler's store touches include the ones its helpers make.

The map drew the Index page as browser -> seam -> server and stopped there, on a page
whose handler exists to increment a Redis counter. The counter was found, the handler was
found, and no edge joined them: the reads sit in `read_hero_push_count`, a helper one call
away from `hero_push_counter`, and the page walk follows edges.

"The database, the server, and everything still going out to the client and coming back"
is the thing a map of a Django project is FOR, and it stopped at the handler.
"""
import pathlib
import tempfile
import textwrap

from django.test import SimpleTestCase

from seamcheck.graph import Graph, Status, Symbol
from seamcheck.storelink import link_handlers_to_stores


def _project(files: dict[str, str]) -> str:
    root = pathlib.Path(tempfile.mkdtemp())
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text), encoding="utf-8")
    return str(root)


def _graph(*symbols: Symbol) -> Graph:
    return Graph(symbols=list(symbols), edges=[])


def _view(name: str, file: str, line: int = 1) -> Symbol:
    return Symbol(id=f"view:app.views.{name}", kind="view", label=name, sub="handler",
                  file=file, line=line, status=Status.CONNECTED, snippet="", chain=[],
                  note="")


def _use(kind: str, label: str, file: str, line: int, owner: str) -> Symbol:
    return Symbol(id=f"{kind}:{label}:{file}:{line}", kind=kind, label=label,
                  sub="reads", file=file, line=line, status=Status.CONNECTED,
                  snippet="", chain=[label], note="", owner=owner)


SOURCE = {
    "app/views.py": """
        async def hero_push_counter(request):
            return await read_hero_push_count()

        async def read_hero_push_count():
            ar = get_async_redis_client()
            return await ar.get(HERO_PUSH_KEY)

        def untouched(request):
            return 1
    """,
}


class HandlerReachesTheStoreTests(SimpleTestCase):
    def test_a_handler_reaches_a_key_its_helper_touches(self):
        root = _project(SOURCE)
        graph = _graph(
            _view("hero_push_counter", "app/views.py", 2),
            _use("redis_key_use", "index:hero_push_count", "app/views.py", 7,
                 "read_hero_push_count"),
        )

        edges = link_handlers_to_stores(graph, root)

        self.assertEqual(
            [(e.from_id, e.to_id) for e in edges],
            [("view:app.views.hero_push_counter",
              "redis_key_use:index:hero_push_count:app/views.py:7")])

    def test_a_handler_reaches_a_table_its_helper_queries(self):
        root = _project(SOURCE)
        graph = _graph(
            _view("hero_push_counter", "app/views.py", 2),
            _use("db_table_use", "pointless_season", "app/views.py", 7,
                 "read_hero_push_count"),
        )

        self.assertEqual(len(link_handlers_to_stores(graph, root)), 1)

    def test_a_handler_that_touches_it_directly_is_joined_too(self):
        root = _project(SOURCE)
        graph = _graph(
            _view("hero_push_counter", "app/views.py", 2),
            _use("redis_key_use", "k:one", "app/views.py", 3, "hero_push_counter"),
        )

        self.assertEqual(len(link_handlers_to_stores(graph, root)), 1)

    def test_a_handler_that_calls_nothing_gets_nothing(self):
        root = _project(SOURCE)
        graph = _graph(
            _view("untouched", "app/views.py", 9),
            _use("redis_key_use", "index:hero_push_count", "app/views.py", 7,
                 "read_hero_push_count"),
        )

        self.assertEqual(link_handlers_to_stores(graph, root), [])

    def test_the_edge_is_evidence_and_never_a_verdict(self):
        # It says "this handler's work reaches this key", which is not a claim about
        # either end. Both keep whatever status they already had.
        root = _project(SOURCE)
        graph = _graph(
            _view("hero_push_counter", "app/views.py", 2),
            _use("redis_key_use", "index:hero_push_count", "app/views.py", 7,
                 "read_hero_push_count"),
        )

        edges = link_handlers_to_stores(graph, root)

        self.assertEqual([e.status for e in edges], [Status.CONNECTED])

    def test_a_helper_three_calls_deep_still_counts(self):
        root = _project({"app/views.py": """
            def handler(request):
                return middle()

            def middle():
                return deeper()

            def deeper():
                return read_it()

            def read_it():
                return r.get("k:deep")
        """})
        graph = _graph(
            _view("handler", "app/views.py", 2),
            _use("redis_key_use", "k:deep", "app/views.py", 11, "read_it"),
        )

        self.assertEqual(len(link_handlers_to_stores(graph, root)), 1)


class MethodNamesTests(SimpleTestCase):
    """The call graph names a method `Class.method`; a symbol's owner is looked up bare.

    On the reference project `submit_push` - the hottest path in the game - reached
    exactly ONE store row. The walk was fine: 74 functions deep. The lookup was not, so
    only the four undotted names among them matched and every method was dropped.
    """

    def test_a_store_row_owned_by_a_method_is_reached(self):
        # The reference project's shape exactly: a view delegating to a module-level
        # helper, which calls a service method, which calls another method that does
        # the Redis work.
        root = _project({
            "app/views.py": """
                from app.service import PushService

                def submit_push(request):
                    return _unified(request)

                def _unified(request):
                    service = PushService()
                    return service.process_batch(request)
            """,
            "app/service.py": """
                class PushService:
                    def process_batch(self, request):
                        return self._write(request)

                    def _write(self, request):
                        r.set("user:1:stats", 1)
            """,
        })
        graph = _graph(
            _view("submit_push", "app/views.py", 4),
            _use("redis_key_use", "user:*:stats", "app/service.py", 6,
                 "PushService._write"),
        )

        edges = link_handlers_to_stores(graph, root)

        self.assertEqual([e.to_id for e in edges],
                         ["redis_key_use:user:*:stats:app/service.py:6"])
