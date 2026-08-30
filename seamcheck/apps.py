from django.apps import AppConfig
from django.conf import settings

# Without these, a scan cannot start: they name the URLconf to walk, the templates and
# static roots to read, and which import prefixes count as this project's own.
REQUIRED_CONFIG_KEYS = frozenset({
    "urlconf_module",
    "templates_root",
    "first_party_prefixes",
})


def missing_config_keys() -> set[str]:
    """Required keys absent from SEAMCHECK_CONFIG. Empty when the app can run."""
    config = getattr(settings, "SEAMCHECK_CONFIG", {})
    return set(REQUIRED_CONFIG_KEYS) - set(config)


class SeamcheckConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "seamcheck"
