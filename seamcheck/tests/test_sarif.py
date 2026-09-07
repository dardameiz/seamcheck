"""GitHub reads SARIF, and an agent writing a pipeline knows that.

Without it the only way to surface a finding in a pull request is to paste text into a
comment, so the tool's output lives outside the review rather than on the line it is about.
"""
import json

from django.test import SimpleTestCase

from seamcheck.renderers import sarif

FINDINGS = [{"id": "url:api/x/", "kind": "url", "label": "api/x/", "status": "unresolved",
             "file": "app/urls.py", "line": 12, "owner": "", "note": "nothing calls it"}]


class SarifTests(SimpleTestCase):
    def test_it_is_valid_sarif_with_one_result_per_finding(self):
        out = json.loads(sarif.render(FINDINGS, sha="abc123", repo="."))

        self.assertEqual(out["version"], "2.1.0")
        run = out["runs"][0]
        self.assertEqual(run["tool"]["driver"]["name"], "seamcheck")
        self.assertEqual(len(run["results"]), 1)

    def test_a_result_points_at_the_exact_line(self):
        run = json.loads(sarif.render(FINDINGS, sha="abc123", repo="."))["runs"][0]
        location = run["results"][0]["locations"][0]["physicalLocation"]

        self.assertEqual(location["artifactLocation"]["uri"], "app/urls.py")
        self.assertEqual(location["region"]["startLine"], 12)

    def test_the_rule_id_is_the_kind_so_findings_group_in_the_ui(self):
        run = json.loads(sarif.render(FINDINGS, sha="abc123", repo="."))["runs"][0]

        self.assertEqual(run["results"][0]["ruleId"], "seamcheck/url/unresolved")
        self.assertIn("seamcheck/url/unresolved",
                      [rule["id"] for rule in run["tool"]["driver"]["rules"]])
