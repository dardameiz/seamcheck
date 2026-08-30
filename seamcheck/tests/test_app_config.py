from django.apps import apps
from django.conf import settings
from django.test import SimpleTestCase, override_settings

from seamcheck.apps import REQUIRED_CONFIG_KEYS, missing_config_keys


class SeamcheckAppConfigTests(SimpleTestCase):
    def test_the_app_is_registered(self):
        self.assertTrue(apps.is_installed("seamcheck"))


class RequiredConfigTests(SimpleTestCase):
    """The old version asserted the HOST project's settings were complete, which is a
    statement about whichever project the package is installed in rather than about the
    package - and it failed the moment anyone cloned the repo to contribute."""

    @override_settings(SEAMCHECK_CONFIG=dict.fromkeys(REQUIRED_CONFIG_KEYS, "x"))
    def test_a_complete_config_reports_nothing_missing(self):
        self.assertEqual(missing_config_keys(), set())

    @override_settings(SEAMCHECK_CONFIG={"urlconf_module": "myproject.urls"})
    def test_it_names_exactly_what_is_missing(self):
        self.assertEqual(missing_config_keys(), {"templates_root", "first_party_prefixes"})

    @override_settings()
    def test_no_config_at_all_is_reported_as_every_key_missing(self):
        # A project that installed the app and never configured it should be told all of
        # it is missing, not crash on an attribute that was never set.
        del settings.SEAMCHECK_CONFIG

        self.assertEqual(missing_config_keys(), set(REQUIRED_CONFIG_KEYS))
