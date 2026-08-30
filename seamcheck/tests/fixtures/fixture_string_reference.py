def include(module_path):
    return module_path


urlpatterns = [include("seamcheck.tests.fixtures.fixture_reachability_b")]
