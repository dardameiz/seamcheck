"""Findings as SARIF 2.1.0, so GitHub puts them on the line they are about.

A pull request is where a finding is cheapest to act on, and the only way into that view is
this format. Everything else this tool prints is for a terminal or a browser; this one is
for the review.
"""
from __future__ import annotations

import hashlib
import json

# unresolved is an error: something reaches for what is not there. unused is a warning: it
# may be reached from outside the repository, and the tool says so rather than guessing.
_LEVEL = {"unresolved": "error", "unused": "warning", "uncertain": "note"}


def _stable_fingerprint(finding: dict) -> str:
    """What makes a finding the SAME finding across commits: its kind, label and file -
    never its line.

    Several kinds' own ids embed the line (`dom_attr:templates/base.html:412:class:navbar`
    is a real one), so using `finding["id"]` as the fingerprint meant any edit ABOVE a
    finding shifted its line, changed the id, and read to GitHub as a brand new alert -
    closing the old one and opening a duplicate for a line nobody touched. A finding that
    moves down a file because someone edited above it is still the same finding.

    Hashed rather than a plain join so the three parts can never collide with each other
    (a label containing the join character, say) and so the fingerprint stays one opaque
    token either way - not a security use of the hash, just a fixed-width one.
    """
    raw = "\0".join([finding["kind"], finding["label"], finding["file"]])
    return hashlib.sha256(raw.encode()).hexdigest()


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
            "partialFingerprints": {"seamcheckId": _stable_fingerprint(finding)},
            # The real id, kept for a human or an agent: `seamcheck explain <id>` and
            # `seamcheck triage <id>` both still take this, not the hash above.
            "properties": {"seamcheckId": finding["id"]},
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
