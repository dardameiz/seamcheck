"""Background jobs outside Celery: the same seam, spelled seven other ways.

A job is named by a string in one file and registered by a string in another, and nothing
in any of these libraries checks that the two agree. Rename the producer and the work stops
happening - no exception, no failed build, no test failure. The queue simply fills with a
name nothing is listening for, which is the purest form of the bug this tool exists to
find, and the most expensive: it is discovered when someone notices the invoices never went
out.

Read here, because a 32-repository corpus showed every one of them in real use:

    BullMQ      queue.add('send-invoice')          ⇄  new Worker('send-invoice', …)
    Agenda      agenda.every('5 min', 'reindex')   ⇄  agenda.define('reindex', …)
    Inngest     inngest.send({name:'app/x'})       ⇄  createFunction({event:'app/x'})
    Temporal    startWorkflow('SyncOrders')        ⇄  export async function SyncOrders
    node-cron   cron.schedule('* * * * *', fn)     -  the expression itself
    Nest        @Cron('0 3 * * *')                 -  the expression itself
    Python      RQ · Dramatiq · arq                ⇄  the same shape as Celery

Two kinds come out. A **job** is a registered handler: connected when something enqueues
it, uncertain when nothing here does - a job can always be enqueued by another service or
by a console, so silence is not death. An **enqueue** naming a job no handler registers is
`unresolved`, and that one is a real defect: the message goes nowhere.

A cron expression is checked for being a valid expression, and nothing more. Whether the
schedule is the RIGHT one is a question about intent, which no scanner can answer.
"""

from __future__ import annotations

import ast
import os
import pathlib
import re

from seamcheck.adapters.discovery import SKIP_DIRS
from seamcheck.graph import Edge, Status, Symbol
from seamcheck.nodetools import node_line

_JS_EXTENSIONS = (".js", ".mjs", ".cjs", ".jsx", ".ts", ".mts", ".cts", ".tsx")
_MAX_BYTES = 400_000

# Cheap text gate: parsing every file in a monorepo to find three queues is not a scan.
_NEEDLES = (
    "bullmq", "new Queue", "new Worker", "agenda", "Agenda", "inngest", "Inngest",
    "node-cron", "cron.schedule", "@Cron", "Cron(", "@temporalio", "startWorkflow",
    "executeWorkflow", "trigger.dev", "@trigger.dev", "boss.send", "boss.work",
    "from rq import", "import dramatiq", "from arq", "APScheduler", "add_job",
)

_PY_EXTENSIONS = (".py",)

# The producer side: a call that puts work on a queue. Value is which argument holds the
# job name, because the libraries disagree and getting it wrong invents findings.
_JS_ENQUEUE = {
    "add": 0,           # BullMQ:  queue.add('name', data)
    "every": 1,         # Agenda:  agenda.every('5 minutes', 'name')
    "schedule": 1,      # Agenda:  agenda.schedule('in 2 hours', 'name')
    "now": 0,           # Agenda:  agenda.now('name')
    "send": 0,          # pg-boss: boss.send('name', data)
    "startWorkflow": 0,
    "executeWorkflow": 0,
}
# The consumer side: a call that registers a handler.
_JS_REGISTER = {
    "define": 0,        # Agenda:  agenda.define('name', handler)
    "work": 0,          # pg-boss: boss.work('name', handler)
    "process": 0,       # Bull:    queue.process('name', handler)
}
# Python, where the shape is Celery's and the decorators differ.
_PY_TASK_DECORATORS = frozenset({"task", "actor", "job", "cron", "scheduled_job"})
_PY_ENQUEUE_METHODS = frozenset({"enqueue", "enqueue_call", "send", "send_with_options",
                                 "enqueue_job", "add_job", "delay", "spawn"})

# A five- or six-field cron expression. Deliberately permissive about the field grammar and
# strict about the field COUNT, which is the mistake people actually make - a five-field
# expression pasted into a library that wants six shifts every run by a factor of sixty.
_CRON_FIELD = r"(?:[\d*/,\-]+|[A-Za-z]{3}(?:-[A-Za-z]{3})?|\*)"
_CRON_RE = re.compile(rf"^\s*{_CRON_FIELD}(?:\s+{_CRON_FIELD}){{4,5}}\s*$")
_CRON_ALIASES = frozenset({
    "@yearly", "@annually", "@monthly", "@weekly", "@daily", "@midnight", "@hourly",
    "@reboot", "@every_minute", "@every_second",
})

_NO_PRODUCER_NOTE = (
    "Nothing in this repository enqueues this job. Not a claim that it is dead - another "
    "service, a console or a scheduled trigger can enqueue by name, and none of those "
    "appear in any source file here."
)
_NO_HANDLER_NOTE = (
    "No handler registers this job name anywhere in this repository. If the worker lives "
    "here, this message is written to a queue nothing reads and the work silently never "
    "happens."
)
_BAD_CRON_NOTE = (
    "This is not a schedule any cron parser will accept, so the job it guards never runs."
)


def _relativise(path: str, root: str) -> str:
    try:
        return os.path.relpath(path, root).replace(os.sep, "/")
    except ValueError:
        return path


# A job defined in a test is a fixture. Reporting it says something about the test suite
# and nothing about the product, and the first run did exactly that.
_TEST_DIRS = frozenset({"test", "tests", "__tests__", "e2e", "spec", "specs", "testing"})
_TEST_FILE_RE = re.compile(r"\.(test|spec)\.[jt]sx?$|^test_|_test\.py$")


def _files(root: str, extensions: tuple[str, ...]) -> list[str]:
    found: list[str] = []
    for current, directories, names in os.walk(root):
        directories[:] = [
            d for d in directories
            if d not in SKIP_DIRS and not d.startswith(".") and d not in _TEST_DIRS
        ]
        for name in names:
            if not name.endswith(extensions) or name.endswith((".min.js", ".d.ts")):
                continue
            if _TEST_FILE_RE.search(name):
                continue
            found.append(os.path.join(current, name))
    return found


def _candidates(paths: list[str]) -> list[tuple[str, str]]:
    """(path, text) for files that mention any job library at all."""
    out: list[tuple[str, str]] = []
    for path in paths:
        try:
            if os.path.getsize(path) > _MAX_BYTES:
                continue
            text = pathlib.Path(path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if any(needle in text for needle in _NEEDLES):
            out.append((path, text))
    return out


def _literal(node: dict | None) -> str:
    """A plain string argument, or "" when it is anything else."""
    if not node:
        return ""
    if node.get("type") == "Literal":
        value = node.get("value")
        return value if isinstance(value, str) else ""
    if node.get("type") == "TemplateLiteral" and not node.get("expressions"):
        quasis = node.get("quasis") or []
        return ((quasis[0].get("value") or {}).get("cooked") or "") if quasis else ""
    return ""


def _object_key(node: dict | None, key: str) -> str:
    """The string value of one key in an object literal argument."""
    if not node or node.get("type") != "ObjectExpression":
        return ""
    for prop in node.get("properties") or []:
        name = (prop.get("key") or {}).get("name") or (prop.get("key") or {}).get("value")
        if name == key:
            return _literal(prop.get("value"))
    return ""


def _valid_cron(expression: str) -> bool:
    text = expression.strip()
    if not text:
        return False
    if text.lower() in _CRON_ALIASES:
        return True
    # A named constant (`CronExpression.EVERY_DAY_AT_3AM`) is resolved by the library, not
    # by us, and claiming it invalid would be inventing a finding out of not looking.
    if not any(character.isdigit() or character in "*/,-" for character in text):
        return False
    return bool(_CRON_RE.match(text))


def _const_strings(parsed: dict) -> dict[str, str]:
    """Module-level `const NAME = 'literal'`, so a queue named by a constant is readable.

    Real projects do not write `new Queue('emails')`; they write `new Queue(QUEUE_NAME)`
    with the constant one import away. A literal-only reader finds almost nothing in a
    codebase anyone maintains, which is what the first run of this extractor proved.
    """
    from seamcheck.extractors.js_extractor import _walk

    found: dict[str, str] = {}
    for node, _ in _walk(parsed):
        if node.get("type") != "VariableDeclarator":
            continue
        name = (node.get("id") or {}).get("name")
        value = _literal(node.get("init"))
        if name and value:
            found[name] = value
    return found


def _named(node: dict | None, constants: dict[str, str]) -> str:
    """A job name, whether written inline or held in a constant."""
    direct = _literal(node)
    if direct:
        return direct
    if node and node.get("type") == "Identifier":
        return constants.get(node.get("name") or "", "")
    return ""


def _scan_js(root: str) -> tuple[dict, list, list]:
    """(registered handlers, enqueue sites, cron expressions) across the JavaScript tree."""
    from seamcheck.extractors.js_extractor import _walk, iter_parsed

    candidates = _candidates(_files(root, _JS_EXTENSIONS))
    uses_inngest = {
        path for path, text in candidates if "inngest" in text.lower()
    }
    registered: dict[str, tuple[str, int, str]] = {}
    enqueued: list[tuple[str, str, int, str]] = []
    crons: list[tuple[str, str, int, bool]] = []

    for path, parsed in iter_parsed([p for p, _ in candidates]):
        relative = _relativise(path, root)
        constants = _const_strings(parsed)
        for node, _enclosing in _walk(parsed):
            if node.get("type") == "NewExpression":
                callee = (node.get("callee") or {}).get("name") or ""
                name = _named((node.get("arguments") or [None])[0], constants)
                if not name:
                    continue
                if callee in ("Worker", "QueueScheduler"):
                    registered.setdefault(name, (relative, _line(node), f"new {callee}('{name}')"))
                elif callee == "Queue":
                    # Declaring a queue is not enqueueing to it, but it does prove the name
                    # is a queue rather than an arbitrary string.
                    registered.setdefault(name, (relative, _line(node), f"new Queue('{name}')"))
                continue

            if node.get("type") != "CallExpression":
                continue
            callee = node.get("callee") or {}
            arguments = node.get("arguments") or []
            method = (callee.get("property") or {}).get("name") or callee.get("name") or ""

            # Inngest: both halves live in object literals rather than in arguments.
            if method == "createFunction":
                event = ""
                for argument in arguments:
                    event = event or _object_key(argument, "event")
                if event:
                    registered.setdefault(event, (relative, _line(node), f"createFunction({event})"))
                continue
            # Only where the file actually imports Inngest. `.send({name: …})` is also a
            # supertest request body, a mail payload and an event-emitter frame - reading
            # it unconditionally reported nine test fixtures as broken queues.
            if (method == "send" and path in uses_inngest and arguments
                    and (name := _object_key(arguments[0], "name"))):
                enqueued.append((name, relative, _line(node), f"send({{name:'{name}'}})"))
                continue

            if method in _JS_REGISTER:
                index = _JS_REGISTER[method]
                name = _named(arguments[index], constants) if len(arguments) > index else ""
                if name:
                    registered.setdefault(name, (relative, _line(node), f"{method}('{name}')"))
                continue

            if method in _JS_ENQUEUE:
                index = _JS_ENQUEUE[method]
                name = _named(arguments[index], constants) if len(arguments) > index else ""
                if name:
                    enqueued.append((name, relative, _line(node), f"{method}('{name}')"))
                # Agenda's every()/schedule() carry the cron in argument 0.
                if method in ("every", "schedule") and arguments:
                    expression = _literal(arguments[0])
                    if expression and any(c.isdigit() or c == "*" for c in expression):
                        crons.append((expression, relative, _line(node), _valid_cron(expression)))
                continue

            # node-cron: cron.schedule('* * * * *', handler) - handled above for the
            # expression, but node-cron has no job name at all, so nothing else to pair.
    return registered, enqueued, crons


def _line(node: dict) -> int:
    return node_line(node) or 1


def _decorator_name(node: ast.AST) -> str:
    if isinstance(node, ast.Call):
        node = node.func
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return ""


def _scan_python(root: str) -> tuple[dict, list]:
    """RQ, Dramatiq, arq and APScheduler - Celery's shape without Celery's decorator."""
    registered: dict[str, tuple[str, int, str]] = {}
    enqueued: list[tuple[str, str, int, str]] = []
    # Callable references, kept apart: they count as evidence when they match a known job
    # and are dropped when they do not. See the comment at the call site.
    resolved_only: list[tuple[str, str, int, str]] = []

    for path, text in _candidates(_files(root, _PY_EXTENSIONS)):
        try:
            tree = ast.parse(text)
        except (SyntaxError, ValueError):
            continue
        relative = _relativise(path, root)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                for decorator in node.decorator_list:
                    if _decorator_name(decorator) in _PY_TASK_DECORATORS:
                        registered.setdefault(
                            node.name, (relative, node.lineno, f"@{_decorator_name(decorator)}")
                        )
                        break
                continue
            if not isinstance(node, ast.Call):
                continue
            method = getattr(node.func, "attr", "") or getattr(node.func, "id", "")
            if method not in _PY_ENQUEUE_METHODS or not node.args:
                continue
            first = node.args[0]
            # `queue.enqueue(my_function)` names the callable directly; `.enqueue("name")`
            # names it by string. Both are the producer half.
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                # A STRING is the only unchecked form, and therefore the only one worth a
                # verdict: nothing validates it until the message is already on the queue.
                name = first.value.rsplit(".", 1)[-1]
                enqueued.append((name, relative, node.lineno, f'{method}("{name}")'))
            elif isinstance(first, ast.Name | ast.Attribute):
                # `queue.enqueue(send_email, …)` passes the callable itself, so Python has
                # already checked it exists - there is no seam here and no finding to make.
                # It is still recorded when it matches a job we know about, because that is
                # real evidence the job is reached; when it does not match, it is far more
                # likely to be a plain argument (`enqueue_job(query_id)`) than a missing
                # handler, and reporting those produced exactly that false positive.
                name = getattr(first, "id", "") or getattr(first, "attr", "")
                if name:
                    resolved_only.append((name, relative, node.lineno, f"{method}({name})"))
            else:
                continue
    return registered, enqueued


def extract_jobs(root: str) -> tuple[list[Symbol], list[Edge]]:
    """Registered jobs, the sites that enqueue them, and the verdicts between."""
    js_registered, js_enqueued, crons = _scan_js(root)
    py_registered, py_enqueued = _scan_python(root)

    registered = {**js_registered, **py_registered}
    enqueued = js_enqueued + py_enqueued
    if not registered and not enqueued:
        return [], []

    symbols: list[Symbol] = []
    edges: list[Edge] = []
    produced = {name for name, _, _, _ in enqueued}

    for name, (file, line, snippet) in sorted(registered.items()):
        reached = name in produced
        symbols.append(Symbol(
            id=f"job:{name}", kind="job", label=name, sub="background job",
            file=file, line=line,
            status=Status.CONNECTED if reached else Status.UNCERTAIN,
            snippet=snippet, chain=[name],
            note="" if reached else _NO_PRODUCER_NOTE,
        ))

    for name, file, line, snippet in enqueued:
        known = name in registered
        symbol_id = f"job_enqueue:{name}:{file}:{line}"
        symbols.append(Symbol(
            id=symbol_id, kind="job_enqueue", label=name, sub="enqueues a job",
            file=file, line=line,
            status=Status.CONNECTED if known else Status.UNRESOLVED,
            snippet=snippet, chain=[name],
            note="" if known else _NO_HANDLER_NOTE,
        ))
        if known:
            edges.append(Edge(from_id=symbol_id, to_id=f"job:{name}", status=Status.CONNECTED))

    for expression, file, line, valid in crons:
        symbols.append(Symbol(
            id=f"job_schedule:{file}:{line}", kind="job_schedule",
            label=expression, sub="schedule", file=file, line=line,
            status=Status.CONNECTED if valid else Status.UNRESOLVED,
            snippet=f"'{expression}'", chain=[expression],
            note="" if valid else _BAD_CRON_NOTE,
        ))
    return symbols, edges
