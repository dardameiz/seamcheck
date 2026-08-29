from django.apps import apps
from django.conf import settings
from django.test import SimpleTestCase


class SignalMapAppConfigTests(SimpleTestCase):
    def test_signal_map_app_is_registered(self):
        self.assertTrue(apps.is_installed("signal_map"))

    def test_signal_map_config_has_every_required_key(self):
        required = {
            "urlconf_module", "app_configs", "celery_app_module", "management_commands_dirs",
            "asgi_module", "js_vite_manifest", "js_source_root", "templates_root",
            "first_party_prefixes",
        }
        self.assertTrue(required.issubset(settings.SIGNAL_MAP_CONFIG.keys()))
