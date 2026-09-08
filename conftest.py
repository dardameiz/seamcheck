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
import pytest
from django.conf import settings


@pytest.fixture(autouse=True)
def _no_real_network_in_tests(monkeypatch, tmp_path):
    """The suite must never depend on network access, or touch this machine's real
    cache, just because a test happened to call `main()` or start the MCP server - a
    laptop on a plane, a sandboxed CI runner and PyPI's own uptime all have to be
    irrelevant to whether tests pass, the same reason ScanCacheTests isolates
    XDG_CACHE_HOME for the scan cache.

    `seamcheck.updatecheck.notice()` is the one thing in this package that makes a real
    HTTP request, and it now runs on every `seamcheck.cli.main()` call (see cli.py's
    `_maybe_print_update_notice`) and on `seamcheck-mcp` startup - so without this, every
    test that exercises either would quietly hit PyPI and read/write
    ~/.cache/seamcheck/update_check.json on the machine running the tests. Patched at
    `_fetch_latest`, the one function that actually calls `urllib.request.urlopen`, so a
    test that exercises `updatecheck.notice()`/`latest_version()` itself - which passes
    its own `fetch=` stub - is unaffected; this only removes the call a test that never
    asked for one would otherwise make by omission. XDG_CACHE_HOME moves to a per-test
    tmp_path for the same reason: omission should touch nothing real.
    """
    monkeypatch.setattr("seamcheck.updatecheck._fetch_latest", lambda *a, **k: None)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))


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
