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

    def test_the_real_id_stays_available_for_explain_and_triage(self):
        run = json.loads(sarif.render(FINDINGS, sha="abc123", repo="."))["runs"][0]

        self.assertEqual(run["results"][0]["properties"]["seamcheckId"], "url:api/x/")


class FingerprintStabilityTests(SimpleTestCase):
    """GitHub tracks an alert across commits by `partialFingerprints`, so a fingerprint
    that changes when nothing about the FINDING changed reads as the old alert closing
    and a new one opening - churn on lines nobody touched, every time an edit above a
    finding shifts its line number.
    """

    def _fingerprint(self, finding):
        run = json.loads(sarif.render([finding]))["runs"][0]
        return run["results"][0]["partialFingerprints"]["seamcheckId"]

    def test_a_finding_that_only_moved_lines_keeps_the_same_fingerprint(self):
        moved_down = dict(FINDINGS[0], line=412)

        self.assertEqual(self._fingerprint(FINDINGS[0]), self._fingerprint(moved_down))

    def test_a_different_file_gets_a_different_fingerprint(self):
        elsewhere = dict(FINDINGS[0], file="app/other.py")

        self.assertNotEqual(self._fingerprint(FINDINGS[0]), self._fingerprint(elsewhere))

    def test_a_different_label_gets_a_different_fingerprint(self):
        renamed = dict(FINDINGS[0], label="api/y/")

        self.assertNotEqual(self._fingerprint(FINDINGS[0]), self._fingerprint(renamed))


class FingerprintCollisionTests(SimpleTestCase):
    """The redash-shaped case: the three-part (kind, label, file) fingerprint merged six
    real findings - same kind, same label, same file, different lines - into ONE SARIF
    alert, and five of the six vanished from review. Fixed by adding a discriminator only
    when a group of findings sharing (kind, label, file, owner) actually collides: the
    position within that group, ordered by line.
    """

    def _fingerprints(self, findings):
        run = json.loads(sarif.render(findings))["runs"][0]
        return [result["partialFingerprints"]["seamcheckId"] for result in run["results"]]

    def test_two_findings_identical_but_for_line_get_different_fingerprints(self):
        first = dict(FINDINGS[0], line=10)
        second = dict(FINDINGS[0], line=20)

        fingerprints = self._fingerprints([first, second])

        self.assertEqual(len(set(fingerprints)), 2, fingerprints)

    def test_each_keeps_its_fingerprint_when_unrelated_lines_above_them_shift(self):
        # An edit above a THIRD finding that shares none of the pair's identity (a
        # different kind/label/file) must not touch either of their fingerprints - only
        # the group each finding actually collides in can move its position.
        first = dict(FINDINGS[0], line=10)
        second = dict(FINDINGS[0], line=20)
        unrelated = {"id": "url:api/other/", "kind": "url", "label": "api/other/",
                    "status": "unresolved", "file": "app/other.py", "line": 1,
                    "owner": "", "note": ""}

        before = self._fingerprints([unrelated, first, second])
        after = self._fingerprints([dict(unrelated, line=999), first, second])

        self.assertEqual(before[1:], after[1:])

    def test_a_lone_finding_in_its_group_is_unaffected_by_the_discriminator(self):
        # No collision, no index suffix: a finding that shares its (kind, label, file,
        # owner) with nothing else hashes exactly as it did before this fix, so most
        # findings on a real project keep the fingerprint GitHub already tracks.
        alone = dict(FINDINGS[0], line=10)
        elsewhere = dict(FINDINGS[0], file="app/other.py", line=20)

        batched = self._fingerprints([alone, elsewhere])
        singly = [self._fingerprints([alone])[0], self._fingerprints([elsewhere])[0]]

        self.assertEqual(batched, singly)

    def test_a_different_owner_is_its_own_group_even_with_the_same_kind_label_file(self):
        first = dict(FINDINGS[0], line=10, owner="handler_a")
        second = dict(FINDINGS[0], line=10, owner="handler_b")

        fingerprints = self._fingerprints([first, second])

        self.assertEqual(len(set(fingerprints)), 2, fingerprints)
