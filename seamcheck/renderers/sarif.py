"""Findings as SARIF 2.1.0, so GitHub puts them on the line they are about.

A pull request is where a finding is cheapest to act on, and the only way into that view is
this format. Everything else this tool prints is for a terminal or a browser; this one is
for the review.
"""
from __future__ import annotations

import json

# unresolved is an error: something reaches for what is not there. unused is a warning: it
# may be reached from outside the repository, and the tool says so rather than guessing.
_LEVEL = {"unresolved": "error", "unused": "warning", "uncertain": "note"}


def render(findings: list[dict], *, sha: str = "", repo: str = ".") -> str:
    rules: dict[str, dict] = {}
    results = []
    for finding in findings:
        rule_id = f"seamcheck/{finding['kind']}/{finding['status']}"
        rules.setdefault(rule_id, {
            "id": rule_id,
            "shortDescription": {"text": f"{finding['kind']} {finding['status']}"},
            "defaultConfiguration": {"level": _LEVEL.get(finding["status"], "note")},
        })
        results.append({
            "ruleId": rule_id,
            "level": _LEVEL.get(finding["status"], "note"),
            "message": {"text": finding.get("note") or f"{finding['label']} is {finding['status']}."},
            "locations": [{"physicalLocation": {
                "artifactLocation": {"uri": finding["file"]},
                "region": {"startLine": finding.get("line") or 1},
            }}],
            "partialFingerprints": {"seamcheckId": finding["id"]},
        })
    return json.dumps({
        "version": "2.1.0",
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "runs": [{
            "tool": {"driver": {"name": "seamcheck",
                                "informationUri": "https://github.com/dardameiz/seamcheck",
                                "rules": list(rules.values())}},
            "versionControlProvenance": [{"revisionId": sha}] if sha else [],
            "results": results,
        }],
    }, indent=2)
