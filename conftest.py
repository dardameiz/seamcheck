"""Make the test suite runnable from a clone.

Seamcheck is a Django app, so its tests need settings — and until now the only settings
that existed were the host project's. Cloning the repo and running pytest failed on
`ImproperlyConfigured` before a single assertion ran, which is a poor welcome for anyone
who wants to contribute.

These settings are deliberately minimal: no database is touched, and SEAMCHECK_CONFIG
points at the fixtures under `seamcheck/tests/fixtures/`, so the suite describes the
package rather than any project it happens to be installed in.
"""

import django
from django.conf import settings


def pytest_configure():
    if settings.configured:
        return
    settings.configure(
        INSTALLED_APPS=["seamcheck"],
        DATABASES={},
        USE_TZ=True,
        # Every test that needs project paths overrides this with @override_settings;
        # what matters here is that the key exists and points at nothing real.
        SEAMCHECK_CONFIG={
            "urlconf_module": "seamcheck.tests.fixtures.fixture_urls",
            "js_entry_files": [],
            "js_project_root": "seamcheck/tests/fixtures",
            "entry_point_files": [],
        },
    )
    django.setup()
