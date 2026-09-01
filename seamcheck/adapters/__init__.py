"""Backend adapters, and how one is chosen.

Selection is by artefact, never by asking the user: a project that has to be configured
before it can be scanned will not be scanned. `config["server_adapter"]` forces a choice
for the cases detection gets wrong, and saying so in `seamcheck config` is how the guess
stays inspectable rather than magic.
"""

from __future__ import annotations

from seamcheck.adapters.base import ServerAdapter, ServerScan
from seamcheck.adapters.express_adapter import ExpressAdapter
from seamcheck.adapters.fastapi_adapter import FastAPIAdapter
from seamcheck.adapters.flask_adapter import FlaskAdapter
from seamcheck.adapters.nestjs_adapter import NestJSAdapter
from seamcheck.adapters.nextjs_adapter import NextJSAdapter

# Ordered for stable tie-breaking: an exact tie picks the earlier entry, so the ordering
# here is a deliberate preference and not an accident of dictionary iteration.
#
# Django is loaded LAZILY, and that is the whole reason six of these are usable at all.
# `DjangoAdapter` imports django.urls at module scope, so importing this registry used to
# import Django - which meant scanning an Express repository required Django to be
# installed, and the CLI refused to start without a Django project. The other five never
# needed it.
_LAZY: tuple[tuple[str, str, str], ...] = (
    ("django", "seamcheck.adapters.django_adapter", "DjangoAdapter"),
)
_EAGER: tuple[ServerAdapter, ...] = (
    FastAPIAdapter(),
    FlaskAdapter(),
    NestJSAdapter(),
    NextJSAdapter(),
    ExpressAdapter(),
)
_loaded: dict[str, ServerAdapter] = {}


def _django() -> ServerAdapter | None:
    """The Django adapter, if Django is importable. None is a real answer here."""
    if "django" in _loaded:
        return _loaded["django"]
    try:
        from seamcheck.adapters.django_adapter import DjangoAdapter
    except ImportError:
        # No Django in this environment. That is not an error - it is an Express repo.
        _loaded["django"] = None
        return None
    _loaded["django"] = DjangoAdapter()
    return _loaded["django"]


def _adapters() -> tuple[ServerAdapter, ...]:
    django = _django()
    return ((django,) if django else ()) + _EAGER


def available() -> tuple[str, ...]:
    return tuple(adapter.name for adapter in _adapters())


def select(repo_root: str, config: dict) -> tuple[ServerAdapter, float]:
    """The adapter that fits this repository best, and how sure it is.

    Returns the highest-confidence adapter even when confidence is 0.0. That is deliberate:
    an adapter that finds nothing produces an empty ServerScan and the frontend half of the
    graph still gets built, which is more useful than refusing to run. What must never
    happen is a scan that silently reports every endpoint broken because it read no routes -
    the pipeline says so explicitly instead.
    """
    forced = (config or {}).get("server_adapter")
    if forced:
        for adapter in _adapters():
            if adapter.name == forced:
                return adapter, 1.0
        raise ValueError(
            f"Unknown server_adapter {forced!r}. Available: {', '.join(available())}"
        )
    return _ranked(repo_root, config)[0]


# Below this, a signal is incidental - a sample app, a stray dependency - and the adapter
# should not be run. Above it, the framework is really present.
_CONFIDENT = 0.5


def _ranked(repo_root: str, config: dict) -> list[tuple[ServerAdapter, float]]:
    return sorted(
        ((adapter, adapter.detect(repo_root, config or {})) for adapter in _adapters()),
        key=lambda pair: -pair[1],
    )


def select_all(repo_root: str, config: dict) -> list[tuple[ServerAdapter, float]]:
    """Every adapter that confidently fits, not just the best one.

    A large monorepo is not one application. cal.com serves a Next.js front end AND a
    NestJS API from the same repository; picking a single winner threw away every route
    of whichever one lost, and the loser was decided by registration order. Reading both
    is not a compromise - it is what the repository actually serves.

    Falls back to the single best adapter when nothing clears the bar, so a project the
    readers do not recognise still gets a scan rather than an empty graph.
    """
    forced = (config or {}).get("server_adapter")
    if forced:
        return [select(repo_root, config)]
    ranked = _ranked(repo_root, config)
    confident = [pair for pair in ranked if pair[1] >= _CONFIDENT]
    return confident or ranked[:1]


__all__ = ["ServerAdapter", "ServerScan", "available", "select", "select_all"]
