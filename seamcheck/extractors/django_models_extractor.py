"""Model symbols, read from django-extensions' graph_models JSON output."""

from __future__ import annotations

import json
import subprocess
import sys

from seamcheck.graph import Status, Symbol
from seamcheck.nodetools import report

_NO_ORM_USAGE_NOTE = (
    "No ORM-usage extractor yet - status is a known Core Pipeline limitation until a later "
    "task detects Model.objects access. Never claim CONNECTED/UNUSED without evidence."
)


def parse_graph_models_json(data: dict) -> list[Symbol]:
    """Build one model Symbol per entry.

    The app label comes from the graph, not from each model's own `app_name` - that field
    reads "pointless_models" and is not a real app label.
    """
    where = _model_files()
    symbols: list[Symbol] = []
    for graph in data.get("graphs", []):
        app_label = graph["app_name"]
        for model in graph.get("models", []):
            model_name = model["name"]
            symbols.append(
                Symbol(
                    id=f"model:{app_label}.{model_name}",
                    kind="model",
                    label=model_name,
                    sub=app_label,
                    file=where.get(model_name, ""),
                    line=None,
                    status=Status.UNCERTAIN,
                    snippet=f"class {model_name}(models.Model): ...",
                    chain=[app_label, model_name],
                    note=_NO_ORM_USAGE_NOTE,
                )
            )
    return symbols


def _model_files() -> dict[str, str]:
    """model name -> the file it is declared in, from Django's own registry.

    A symbol with no file cannot be opened, cannot be attributed to a page, and cannot be
    told apart from another of the same name: 65 of them on the reference project, every
    one a row a reader can do nothing with. Django knows where each model lives; nothing
    was asking.
    """
    try:
        import inspect
        import os

        from django.apps import apps
    except Exception:  # noqa: BLE001 - a file is a nicety, never a reason to fail a scan
        return {}
    found: dict[str, str] = {}
    for model in apps.get_models():
        try:
            path = inspect.getfile(model)
        except (TypeError, OSError):
            continue
        found.setdefault(model.__name__, os.path.abspath(path))
    return found


def extract_django_models(app_labels: list[str]) -> list[Symbol]:
    """Model symbols, or none when django-extensions is not installed.

    `graph_models` belongs to django-extensions, which is an optional dependency: a
    project that does not have it, or a commit from before it was added, must still get a
    scan. Raising here took the whole scan down over one extractor - the graph loses its
    model symbols, which is a smaller loss than losing every other kind as well.
    """
    # sys.executable, not "python": the interpreter running Seamcheck is the one that has
    # Django installed. A bare "python" resolves against PATH and silently differs in CI.
    result = subprocess.run(
        [sys.executable, "manage.py", "graph_models", "--json", *app_labels],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0 or not result.stdout.strip():
        # Say it, through `report`: a plain logger.warning is muted by quiet(), which
        # is how a wheel install without django-extensions scanned the reference project
        # 66 symbols short and printed nothing. A scan that silently drops a whole symbol
        # kind is indistinguishable from a project that has none of them, which is the
        # exact ambiguity this tool exists to refuse.
        report(
            "models",
            "no model symbols - `manage.py graph_models` did not run (django-extensions "
            "installed? `pip install 'seamcheck[models]'`). Every other extractor still ran.",
        )
        return []
    try:
        return parse_graph_models_json(json.loads(result.stdout))
    except json.JSONDecodeError:
        report("models", "no model symbols - graph_models returned unreadable JSON.")
        return []
