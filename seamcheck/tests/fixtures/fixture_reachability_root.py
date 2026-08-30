from django.conf import settings  # noqa: F401  third-party: must NOT be followed

from seamcheck.tests.fixtures import fixture_reachability_a


def root_function():
    return fixture_reachability_a.a_function()
