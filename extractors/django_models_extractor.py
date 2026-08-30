"""Model symbols, read from django-extensions' graph_models JSON output."""

from __future__ import annotations

import json
import logging
import subprocess
import sys

from signal_map.graph import Status, Symbol

logger = logging.getLogger(__name__)

_NO_ORM_USAGE_NOTE = (
    "No ORM-usage extractor yet - status is a known Core Pipeline limitation until a later "
    "task detects Model.objects access. Never claim CONNECTED/UNUSED without evidence."
)


def parse_graph_models_json(data: dict) -> list[Symbol]:
    """Build one model Symbol per entry.

    The app label comes from the graph, not from each model's own `app_name` - that field
    reads "pointless_models" and is not a real app label.
    """
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
                    file="",
                    line=None,
                    status=Status.UNCERTAIN,
                    snippet=f"class {model_name}(models.Model): ...",
                    chain=[app_label, model_name],
                    note=_NO_ORM_USAGE_NOTE,
                )
            )
    return symbols


def extract_django_models(app_labels: list[str]) -> list[Symbol]:
    """Model symbols, or none when django-extensions is not installed.

    `graph_models` belongs to django-extensions, which is an optional dependency: a
    project that does not have it, or a commit from before it was added, must still get a
    scan. Raising here took the whole scan down over one extractor - the graph loses its
    model symbols, which is a smaller loss than losing every other kind as well.
    """
    # sys.executable, not "python": the interpreter running Signal Map is the one that has
    # Django installed. A bare "python" resolves against PATH and silently differs in CI.
    result = subprocess.run(
        [sys.executable, "manage.py", "graph_models", "--json", *app_labels],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0 or not result.stdout.strip():
        # Say it. A scan that silently drops a whole symbol kind is indistinguishable
        # from a project that has none of them, which is the exact ambiguity this tool
        # exists to refuse.
        logger.warning(
            "signal-map: no model symbols - `manage.py graph_models` did not run "
            "(django-extensions installed?). Every other extractor still ran."
        )
        return []
    try:
        return parse_graph_models_json(json.loads(result.stdout))
    except json.JSONDecodeError:
        logger.warning("signal-map: no model symbols - graph_models returned unreadable JSON.")
        return []
