"""The gate's exit code is the only thing CI reads, and it was wrong everywhere but Django.

`cli.py` asked `result.get("findings")` of a dict that has no `findings` key, so the
non-Django path returned 0 for every project that ever had a finding. Reproduced on redash:
47 unresolved, 53 unused, exit 0.
"""
from unittest import mock

from django.test import SimpleTestCase

from seamcheck import cli


class NonDjangoGateTests(SimpleTestCase):
    def test_a_project_with_findings_fails_the_gate(self):
        outcome = {"passed": False, "message": "2 new", "new_unresolved": [{"id": "url:x"}],
                   "new_unused": [], "triage_invalidated": [], "returned": [],
                   "counts": {"unresolved": 1}}
        with mock.patch("seamcheck.api.check", return_value=outcome), \
             mock.patch("seamcheck.api.report", return_value="digest"), \
             mock.patch("seamcheck.cli._worth_scanning", return_value=True):
            code = cli._run_without_django(["--check"], verbose=False)

        self.assertEqual(code, 1, "a project with findings must fail the gate")

    def test_a_clean_project_passes(self):
        outcome = {"passed": True, "message": "clean", "new_unresolved": [],
                   "new_unused": [], "triage_invalidated": [], "returned": [],
                   "counts": {}}
        with mock.patch("seamcheck.api.check", return_value=outcome), \
             mock.patch("seamcheck.api.report", return_value="digest"), \
             mock.patch("seamcheck.cli._worth_scanning", return_value=True):
            code = cli._run_without_django(["--check"], verbose=False)

        self.assertEqual(code, 0)
