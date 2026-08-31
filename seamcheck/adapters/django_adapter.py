"""Django: the first adapter, and the hardest one in the plan.

A Django URLconf is a tree - `include()`, namespaces, path converters, `urlpatterns +=` -
which is why this reader is the largest of the set. Laravel's `routes/web.php` is a flat
list, FastAPI's routes are decorators on functions, and Next.js does not have a routes file
at all because its routes ARE the filesystem. Every one of those is cheaper than what is
already working here.

Two ways to read the URLconf, and the choice is about what the machine can do rather than
about accuracy. Asking Django is exact and needs the project to RUN: settings importable,
every app installed, every dependency present. Reading the source needs none of that, and
on the reference project it recovers 95% of the routes actually declared in a `urls.py`.
What it cannot see is what no reader of text could - routes Django generates at runtime
(the admin's 116) and patterns built by a loop.
"""

from __future__ import annotations

import os
import pathlib

from seamcheck.adapters.base import ServerScan
from seamcheck.extractors.asgi_extractor import extract_asgi_routes
from seamcheck.extractors.django_extractor import extract_django_urls_views, route_name_index
from seamcheck.extractors.django_models_extractor import extract_django_models
from seamcheck.extractors.django_static_extractor import extract_urls_views_static


class DjangoAdapter:
    name = "django"

    def detect(self, repo_root: str, config: dict) -> float:
        """Confidence by artefact. `manage.py` beside a settings module is unambiguous.

        A supplied `urlconf_module` outranks everything found on disk, because it does not
        come from a guess: autoconfig reads it from `settings.ROOT_URLCONF`, and a project
        that HAS a ROOT_URLCONF is a Django project by definition. Weighting it as one
        signal among several let a directory of vendored FastAPI code outscore the real
        answer - which is exactly the failure mode a monorepo produces.
        """
        if config.get("urlconf_module"):
            return 0.95
        root = pathlib.Path(repo_root)
        score = 0.0
        if (root / "manage.py").is_file():
            score += 0.6
        if any(root.glob("*/settings.py")) or (root / "settings.py").is_file():
            score += 0.2
        if not score and any(root.glob("*/urls.py")):
            score = 0.4
        return round(min(score, 1.0), 3)

    def scan(self, repo_root: str, config: dict, progress) -> ServerScan:
        urlconf = config.get("urlconf_module")
        if not urlconf:
            # No URLconf means no routes, and no routes means every fetch would be reported
            # unresolved. An empty scan says nothing rather than saying something false.
            return ServerScan()

        first_party = config.get("first_party_prefixes")
        progress.step("URLs and views")
        if config.get("static_urls"):
            symbols, edges, names = extract_urls_views_static(repo_root, urlconf, first_party)
        else:
            symbols, edges = extract_django_urls_views(urlconf, first_party)
            names = route_name_index(urlconf)

        progress.step("ASGI routes")
        asgi_file = config.get("asgi_file")
        if asgi_file and os.path.isfile(asgi_file):
            symbols = symbols + extract_asgi_routes(asgi_file)

        progress.step("models")
        app_labels = config.get("app_labels")
        if app_labels:
            symbols = symbols + extract_django_models(app_labels)

        return ServerScan(symbols=symbols, edges=edges, route_names=names)
