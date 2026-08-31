import pathlib
import tempfile

from django.test import SimpleTestCase, override_settings

from seamcheck.autoconfig import EXCLUDED_DIRS, detect, effective, excluded


class ExclusionTests(SimpleTestCase):
    def test_built_output_is_never_scanned(self):
        # A bundler's dist/ is a COPY of the source, so scanning it doubles every symbol
        # and then reports the copies as unreferenced.
        root = pathlib.Path("/repo")

        self.assertTrue(excluded(root / "static" / "dist" / "app.css", root))
        self.assertTrue(excluded(root / "node_modules" / "x" / "y.css", root))
        self.assertTrue(excluded(root / "venv" / "lib" / "z.css", root))
        self.assertFalse(excluded(root / "app" / "static" / "app.css", root))

    def test_collectstatic_output_is_never_scanned(self):
        # Same problem as dist/, by a different name.
        root = pathlib.Path("/repo")

        self.assertTrue(excluded(root / "staticfiles" / "a.css", root))
        self.assertTrue(excluded(root / "static_collected" / "a.css", root))

    def test_a_path_outside_the_repo_is_still_judged(self):
        # site-packages paths arrive absolute and unrelated to the repo root.
        self.assertTrue(excluded(pathlib.Path("/usr/lib/site-packages/x/a.css"),
                                 pathlib.Path("/repo")))

    def test_the_list_covers_the_ones_that_actually_bite(self):
        for name in ("node_modules", "dist", "venv", ".venv", "site-packages", "migrations"):
            self.assertIn(name, EXCLUDED_DIRS)


class DetectionTests(SimpleTestCase):
    """Django knows most of this exactly; only three keys need a glob."""

    def test_the_urlconf_comes_from_django_not_a_guess(self):
        with override_settings(ROOT_URLCONF="proj.urls"):
            config, why = detect(".")

        self.assertEqual(config["urlconf_module"], "proj.urls")
        self.assertEqual(why["urlconf_module"], "settings.ROOT_URLCONF")

    def test_the_asgi_module_is_the_application_minus_its_attribute(self):
        with override_settings(ASGI_APPLICATION="proj.asgi.application"):
            config, _ = detect(".")

        self.assertEqual(config["asgi_module"], "proj.asgi")

    def test_a_template_dir_outside_the_repo_is_ignored(self):
        # A dependency's templates are not this project's dead code, and reporting them
        # gives the reader findings they cannot act on.
        with tempfile.TemporaryDirectory() as repo, tempfile.TemporaryDirectory() as elsewhere:
            (pathlib.Path(elsewhere) / "t.html").write_text("<div id=x>")
            with override_settings(TEMPLATES=[{"DIRS": [elsewhere], "APP_DIRS": False}]):
                config, _ = detect(repo)

            self.assertNotIn("templates_root", config)

    def test_the_dominant_template_dir_wins_and_the_rest_are_named(self):
        # templates_root is one path where Django allows many; their common ancestor is
        # usually the repo root, which is too broad to be useful.
        with tempfile.TemporaryDirectory() as repo:
            big, small = pathlib.Path(repo, "big"), pathlib.Path(repo, "small")
            big.mkdir()
            small.mkdir()
            for i in range(5):
                (big / f"{i}.html").write_text("<p>")
            (small / "only.html").write_text("<p>")
            with override_settings(TEMPLATES=[{"DIRS": [str(big), str(small)], "APP_DIRS": False}]):
                config, why = detect(repo)

            self.assertEqual(config["templates_root"], "big")
            self.assertIn("5 templates", why["templates_root"])
            self.assertIn("1 other dir", why["templates_root"])

    def test_a_project_django_cannot_describe_yields_nothing_rather_than_junk(self):
        with tempfile.TemporaryDirectory() as repo, override_settings(
            ROOT_URLCONF=None, ASGI_APPLICATION=None, TEMPLATES=[], STATICFILES_DIRS=[]
        ):
            config, _ = detect(repo)

            self.assertNotIn("urlconf_module", config)
            self.assertNotIn("templates_root", config)

    def test_every_detected_value_says_where_it_came_from(self):
        # Detection must not be a black box: a wrong path is the difference between a real
        # report and an invented one, so a reader has to be able to see it is wrong.
        config, why = detect(".")

        for key in config:
            self.assertIn(key, why)
            self.assertTrue(why[key])


class PrecedenceTests(SimpleTestCase):
    def test_written_config_wins_key_by_key(self):
        # Adding detection must not be able to change the answer for a project that had
        # already configured itself.
        with override_settings(
            ROOT_URLCONF="detected.urls",
            SEAMCHECK_CONFIG={"urlconf_module": "declared.urls"},
        ):
            config, why = effective(".")

        self.assertEqual(config["urlconf_module"], "declared.urls")
        self.assertEqual(why["urlconf_module"], "SEAMCHECK_CONFIG")

    def test_detection_fills_only_the_gaps(self):
        with override_settings(
            ROOT_URLCONF="detected.urls",
            SEAMCHECK_CONFIG={"editor": "cursor"},
        ):
            config, why = effective(".")

        self.assertEqual(config["editor"], "cursor")
        self.assertEqual(config["urlconf_module"], "detected.urls")
        self.assertEqual(why["urlconf_module"], "settings.ROOT_URLCONF")

    def test_a_project_with_no_config_at_all_still_gets_one(self):
        # The whole point: `pip install seamcheck && seamcheck map` has to work.
        with override_settings(ROOT_URLCONF="proj.urls", SEAMCHECK_CONFIG={}):
            config, _ = effective(".")

        self.assertEqual(config["urlconf_module"], "proj.urls")
