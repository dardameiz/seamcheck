from django.urls import include, path

from signal_map.tests.fixtures import fixture_views

urlpatterns = [
    path("api/get-thing/", fixture_views.get_thing, name="get_thing"),
    path("api/orphan/", fixture_views.orphan_view, name="orphan_view"),
    # The same view behind a second URL — proves view symbols are deduplicated.
    path("api/get-thing-alias/", fixture_views.get_thing, name="get_thing_alias"),
    path("sub/", include("signal_map.tests.fixtures.fixture_included_urls")),
]
