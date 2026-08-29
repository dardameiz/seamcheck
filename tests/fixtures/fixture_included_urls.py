from django.urls import path

from signal_map.tests.fixtures import fixture_views

urlpatterns = [
    path("nested/", fixture_views.nested_thing, name="nested_thing"),
]
