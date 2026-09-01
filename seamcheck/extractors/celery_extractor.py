"""Celery: code reached with no HTTP request at all, and the outage class that follows.

A task is defined in one file, scheduled by a STRING in another, and called by a third.
Nothing checks that the string matches anything - and when it does not, the scheduler
raises where nobody is looking and the job simply never runs. The reference project lost
challenges and season rollover for fourteen days that way, which is why the beat health
dashboard exists.

Three shapes, and the mismatch between them is the finding:

    @shared_task                                    the task
    def send_receipt(...): ...
    send_receipt.delay(order.id)                    a caller
    CELERY_BEAT_SCHEDULE = {                        a schedule, by dotted NAME
        "nightly": {"task": "billing.tasks.send_receipt", "schedule": 3600},
    }

**A task with no caller is not dead.** It may be sent by name from another service, or
triggered by an operator. That is `uncertain`. **A beat entry naming a task that does not
exist IS a finding** - it is a scheduled job that cannot run, and no test catches it.
"""

from __future__ import annotations

import ast
import os
import pathlib

from seamcheck.graph import Edge, Status, Symbol

_SKIP = {
    "node_modules", ".git", "dist", "build", "__pycache__", "venv", ".venv",
    "site-packages", "migrations", "corpus", ".tox",
}

# The decorators that make a function a task, in every form projects write them.
_TASK_DECORATORS = ("shared_task", "task", "periodic_task")

_NO_CALLER_NOTE = (
    "Defined as a Celery task and nothing in this repository calls it or schedules it. "
    "That is NOT evidence it is dead: a task can be sent by name from another service, or "
    "triggered by an operator. Read it as 'no caller here'."
)
_MISSING_TASK_NOTE = (
    "Scheduled by name and no task with that name is defined in this repository. If the "
    "worker does not register it either, the beat scheduler raises when the entry fires "
    "and the job silently never runs - a failure that shows up as missing data days later, "
    "not as an error at deploy time."
)


def _is_test(path: str) -> bool:
    """A test's beat entry names a fixture, not a task the project ships.

    `{"task": "a.b.c"}` in a test of the health dashboard is a made-up name on purpose,
    and reporting it as a scheduled job that cannot run is the tool failing to tell a
    fixture from an application.
    """
    name = os.path.basename(path)
    parts = pathlib.Path(path).parts
    return (name.startswith("test_") or name.endswith("_test.py") or name == "conftest.py"
            or "tests" in parts or "test" in parts)


def _files(root: str) -> list[str]:
    found: list[str] = []
    for directory, subdirectories, names in os.walk(root):
        subdirectories[:] = [d for d in subdirectories
                             if d not in _SKIP and not d.startswith(".")]
        for name in sorted(names):
            path = os.path.join(directory, name)
            if name.endswith(".py") and not _is_test(path):
                found.append(path)
    return found


def _decorator_names(node) -> list[tuple[str, ast.AST]]:
    found = []
    for decorator in node.decorator_list:
        call = decorator if isinstance(decorator, ast.Call) else None
        target = call.func if call else decorator
        name = getattr(target, "attr", None) or getattr(target, "id", None)
        if name:
            found.append((name, call))
    return found


def _string(node) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _module_name(path: str) -> str:
    file = pathlib.Path(path).resolve()
    parts = [file.stem]
    directory = file.parent
    while (directory / "__init__.py").is_file():
        parts.append(directory.name)
        directory = directory.parent
    return ".".join(reversed(parts))


def extract_celery(root: str) -> tuple[list[Symbol], list[Edge]]:
    """Tasks, the things that call or schedule them, and the names that match nothing."""
    tasks: dict[str, tuple[str, int, str]] = {}      # short name -> (file, line, dotted)
    dotted_tasks: dict[str, str] = {}                # dotted name -> short name
    called: set[str] = set()                         # short names with a .delay() caller
    scheduled: list[tuple[str, str, int]] = []       # (name, file, line) from beat

    for path in _files(root):
        try:
            text = pathlib.Path(path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        # Case-insensitive, and every marker: the settings module that holds the schedule
        # spells it CELERY_BEAT_SCHEDULE, and a caller file may contain nothing but
        # `.apply_async(` or `.send_task(`. A case-sensitive check for "celery" skipped
        # the one file in a project most likely to hold a beat entry.
        lowered = text.lower()
        if not any(marker in lowered for marker in
                   ("celery", "shared_task", ".delay(", "apply_async", "send_task",
                    "periodic_task", "beat_schedule")):
            continue
        try:
            tree = ast.parse(text, filename=path)
        except (SyntaxError, ValueError):
            continue
        relative = os.path.relpath(path, root)
        module = _module_name(path)

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for name, call in _decorator_names(node):
                    if name not in _TASK_DECORATORS:
                        continue
                    explicit = None
                    if call is not None:
                        for keyword in call.keywords:
                            if keyword.arg == "name":
                                explicit = _string(keyword.value)
                    tasks[node.name] = (relative, node.lineno, explicit or f"{module}.{node.name}")
                    dotted_tasks[explicit or f"{module}.{node.name}"] = node.name
                    if explicit:
                        dotted_tasks[explicit] = node.name
                    break
            elif isinstance(node, ast.Call):
                attribute = node.func
                if not isinstance(attribute, ast.Attribute):
                    continue
                if attribute.attr in ("delay", "apply_async"):
                    owner = getattr(attribute.value, "id", None) or getattr(
                        attribute.value, "attr", None)
                    if owner:
                        called.add(owner)
                elif attribute.attr in ("send_task", "signature"):
                    named = _string(node.args[0]) if node.args else None
                    if named:
                        called.add(named)
                        scheduled.append((named, relative, node.lineno))
            elif isinstance(node, ast.Dict):
                # A beat entry needs BOTH a task and a schedule. A dict with a "task" key
                # alone is not one: the reference project writes an audit record shaped
                # {"item_id": ..., "task": "cascade_redis_cleanup"} into Redis, and reading
                # that as a scheduled job reported a perfectly healthy log line as an
                # outage waiting to happen.
                keys = {_string(key) for key in node.keys}
                if "task" not in keys or not keys & {"schedule", "crontab", "options",
                                                     "args", "kwargs", "relative"}:
                    continue
                for key, value in zip(node.keys, node.values, strict=False):
                    if _string(key) == "task":
                        named = _string(value)
                        if named:
                            scheduled.append((named, relative, getattr(value, "lineno", 1)))

    if not tasks and not scheduled:
        return [], []

    symbols: list[Symbol] = []
    edges: list[Edge] = []
    for name, (file, line, dotted) in sorted(tasks.items()):
        reached = name in called or dotted in {s for s, _, _ in scheduled}
        symbols.append(Symbol(
            id=f"celery_task:{dotted}", kind="celery_task", label=dotted, sub="task",
            file=file, line=line,
            status=Status.CONNECTED if reached else Status.UNCERTAIN,
            snippet=f"@shared_task\\ndef {name}(...): ...", chain=[dotted],
            note="" if reached else _NO_CALLER_NOTE,
        ))

    seen: set[str] = set()
    for named, file, line in scheduled:
        if named in seen:
            continue
        seen.add(named)
        known = named in dotted_tasks or named.rsplit(".", 1)[-1] in tasks
        symbol_id = f"celery_schedule:{named}"
        symbols.append(Symbol(
            id=symbol_id, kind="celery_schedule", label=named, sub="scheduled",
            file=file, line=line,
            status=Status.CONNECTED if known else Status.UNRESOLVED,
            snippet=f'"task": "{named}"', chain=[named],
            note="" if known else _MISSING_TASK_NOTE,
        ))
        if known:
            short = dotted_tasks.get(named) or named.rsplit(".", 1)[-1]
            dotted = tasks[short][2] if short in tasks else named
            edges.append(Edge(from_id=symbol_id, to_id=f"celery_task:{dotted}",
                              status=Status.CONNECTED))
    return symbols, edges
