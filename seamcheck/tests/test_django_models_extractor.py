import json
from pathlib import Path

from django.test import SimpleTestCase

from seamcheck.extractors.django_models_extractor import parse_graph_models_json
from seamcheck.graph import Status

FIXTURE = Path(__file__).parent / "fixtures" / "fixture_graph_models_sample.json"


class GraphModelsParsingTests(SimpleTestCase):
    def setUp(self):
        self.symbols = parse_graph_models_json(json.loads(FIXTURE.read_text()))

    def test_parses_a_symbol_per_model_using_the_top_level_app_name(self):
        labels = {s.label for s in self.symbols}

        self.assertIn("SuperuserProxy", labels)
        self.assertIn("Profile", labels)
        self.assertTrue(all(s.kind == "model" for s in self.symbols))
        self.assertTrue(all(s.sub == "pointless" for s in self.symbols))

    def test_ignores_the_per_model_app_name_which_is_not_the_app_label(self):
        # graph_models reports app_name="pointless_models" on each model but "pointless" on
        # the graph. Only the graph-level value is the real app label; using the per-model
        # one would mint ids ("model:pointless_models.Profile") that match nothing later.
        self.assertNotIn("pointless_models", {s.sub for s in self.symbols})
        self.assertFalse(any("pointless_models" in s.id for s in self.symbols))

    def test_model_symbol_id_uses_app_label_and_model_name(self):
        profile = next(s for s in self.symbols if s.label == "Profile")

        self.assertEqual(profile.id, "model:pointless.Profile")

    def test_model_status_defaults_to_uncertain_with_an_explanatory_note(self):
        for symbol in self.symbols:
            self.assertEqual(symbol.status, Status.UNCERTAIN)
            self.assertNotEqual(symbol.note, "")


class OptionalDependencyTests(SimpleTestCase):
    def test_a_project_without_django_extensions_still_gets_a_scan(self):
        # graph_models belongs to django-extensions, an optional dependency. Raising here
        # took down every other extractor with it.
        from unittest import mock

        from seamcheck.extractors.django_models_extractor import extract_django_models

        failed = mock.Mock(returncode=1, stdout="", stderr="Unknown command: 'graph_models'")
        with mock.patch("subprocess.run", return_value=failed):
            self.assertEqual(extract_django_models(["app"]), [])

    def test_it_says_out_loud_that_model_symbols_are_missing(self):
        # Inside quiet(), because that is where the CLI runs the scan: a logger.warning
        # here was muted with the host project's noise, and a wheel install without
        # django-extensions scanned 66 symbols short while printing nothing.
        import contextlib
        import io
        from unittest import mock

        from seamcheck import nodetools
        from seamcheck.extractors.django_models_extractor import extract_django_models
        from seamcheck.quiet import quiet

        nodetools._reported.discard("models")
        failed = mock.Mock(returncode=1, stdout="", stderr="boom")
        stderr = io.StringIO()
        with (
            mock.patch("subprocess.run", return_value=failed),
            quiet(),
            contextlib.redirect_stderr(stderr),
        ):
            extract_django_models(["app"])

        self.assertIn("no model symbols", stderr.getvalue())
        self.assertIn("seamcheck[models]", stderr.getvalue())
