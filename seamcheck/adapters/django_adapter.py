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

import contextlib
import os
import pathlib

from seamcheck.adapters.base import ServerScan
from seamcheck.adapters.discovery import root_urlconf
from seamcheck.extractors.asgi_extractor import extract_asgi_routes
from seamcheck.extractors.django_extractor import extract_django_urls_views, route_name_index
from seamcheck.extractors.django_models_extractor import extract_django_models
from seamcheck.extractors.django_static_extractor import extract_urls_views_static
from seamcheck.nodetools import report


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
        # A ROOT_URLCONF assignment IS a Django project, and reading it needs no import -
        # which matters because the largest Django repositories keep it several levels
        # down (sentry: src/sentry/conf/server.py) and cannot be imported anyway.
        if root_urlconf(repo_root):
            return 0.9
        score = 0.0
        # `manage.py` is not a Django artefact, it is a CONVENTION - Flask-Script, Flask
        # CLI wrappers and countless project scripts use the name. CTFd's imports
        # flask.cli, and treating the filename as evidence made a Flask app read as
        # Django. The file has to actually mention Django.
        manage = root / "manage.py"
        if manage.is_file():
            try:
                text = manage.read_text(encoding="utf-8", errors="replace")
            except OSError:
                text = ""
            if "DJANGO_SETTINGS_MODULE" in text or "django" in text.lower():
                score += 0.6
        if any(root.glob("*/settings.py")) or (root / "settings.py").is_file():
            score += 0.2
        if not score and any(root.glob("*/urls.py")):
            score = 0.4
        return round(min(score, 1.0), 3)

    def scan(self, repo_root: str, config: dict, progress) -> ServerScan:
        urlconf = config.get("urlconf_module") or root_urlconf(repo_root)
        if not urlconf:
            # No URLconf means no routes, and no routes means every fetch would be reported
            # unresolved. An empty scan says nothing rather than saying something false.
            return ServerScan()

        first_party = config.get("first_party_prefixes")
        progress.step("URLs and views")
        static_used = bool(config.get("static_urls"))
        if config.get("static_urls"):
            symbols, edges, names = extract_urls_views_static(repo_root, urlconf, first_party)
        else:
            try:
                symbols, edges = extract_django_urls_views(urlconf, first_party)
                names = route_name_index(urlconf)
            except Exception as error:  # noqa: BLE001 - see below
                # Import mode needs the project to RUN: its settings importable, its apps
                # installed, its dependencies present. A global `pip install seamcheck`
                # pointed at somebody else's checkout has none of that, and this used to
                # propagate - so `seamcheck map` CRASHED on four of the largest open Django
                # codebases (sentry, wagtail, saleor, django-oscar) with a bare
                # ModuleNotFoundError naming a package the reader never asked for.
                #
                # The source reader needs none of it and recovers ~95% of the routes
                # declared in a urls.py, so falling back is strictly better than an
                # exception. Broad by intent: a project's own import side effects can raise
                # anything at all, and none of it is this tool's business.
                report(
                    "django-import-fallback",
                    f"could not import this project ({type(error).__name__}: "
                    f"{str(error)[:80]}), so its routes were read from source instead. "
                    "That misses routes Django builds at runtime, such as the admin's. "
                    "Run seamcheck from the project's own virtualenv for the exact list.",
                )
                symbols, edges, names = extract_urls_views_static(
                    repo_root, urlconf, first_party
                )
                static_used = True

        progress.step("ASGI routes")
        asgi_file = config.get("asgi_file")
        if asgi_file and os.path.isfile(asgi_file):
            symbols = symbols + extract_asgi_routes(asgi_file)

        progress.step("models")
        app_labels = config.get("app_labels")
        if app_labels:
            # Same reason as the URLconf above: reading models asks Django's app
            # registry, which only exists in a project that could be imported.
            with contextlib.suppress(Exception):
                symbols = symbols + extract_django_models(app_labels)

        from seamcheck.extractors.django_static_extractor import LAST_COVERAGE

        # Source reading - by request or by fallback - never sees the routes Django builds
        # at runtime (the admin's `<model>_changelist` and friends), and may not reach
        # every urls.py either. Either way the table is incomplete and must say so.
        complete = not static_used and not LAST_COVERAGE["partial"]
        note = ""
        if static_used:
            note = ("routes were read from source, which cannot see the ones Django builds "
                    "at runtime such as the admin's")
        if LAST_COVERAGE["partial"]:
            note = (f"only {LAST_COVERAGE['read']} of {LAST_COVERAGE['total']} urls.py "
                    "files were reachable from ROOT_URLCONF by reading text")
        return ServerScan(symbols=symbols, edges=edges, route_names=names,
                          complete=complete, coverage_note=note)
