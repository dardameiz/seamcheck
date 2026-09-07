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


def _group_key(finding: dict) -> tuple[str, str, str, str]:
    """The identity a fingerprint is built from: never the line - see `_fingerprints`."""
    return (finding["kind"], finding["label"], finding["file"], finding.get("owner") or "")


def _fingerprints(findings: list[dict]) -> list[str]:
    """One stable fingerprint per finding, in the same order as `findings`.

    What makes a finding the SAME finding across commits: its kind, label, file and owning
    function - never its line. Several kinds' own ids embed the line
    (`dom_attr:templates/base.html:412:class:navbar` is a real one), so using
    `finding["id"]` as the fingerprint meant any edit ABOVE a finding shifted its line,
    changed the id, and read to GitHub as a brand new alert - closing the old one and
    opening a duplicate for a line nobody touched. A finding that moves down a file
    because someone edited above it is still the same finding.

    `owner` is folded into the identity, not just kind/label/file: on the redash corpus, six
    real findings - same kind, same label, same file, six different owning functions - all
    hashed to one fingerprint under the three-part key, so GitHub showed ONE alert and five
    of the six findings vanished from review.

    Best effort past that: two findings that are ALSO identical in owner (or both ownerless)
    cannot be told apart by anything this tool knows except which one comes first, so the
    position within the group - ordered by line - is the last resort, added only when a
    group actually collides (a lone finding's fingerprint is unaffected, so most findings
    hash exactly as before). This is genuinely fragile: inserting, deleting or reordering
    one member of such a pair changes every position after it, which reads identically to
    the findings having traded places - there is no way to do better without a stable
    per-finding identity the graph does not carry.

    Hashed rather than a plain join so the parts can never collide with each other (a label
    containing the join character, say) and so the fingerprint stays one opaque token
    either way - not a security use of the hash, just a fixed-width one.
    """
    groups: dict[tuple[str, str, str, str], list[int]] = {}
    for index, finding in enumerate(findings):
        groups.setdefault(_group_key(finding), []).append(index)

    fingerprints: list[str] = [""] * len(findings)
    for (kind, label, file, owner), indices in groups.items():
        collides = len(indices) > 1
        ordered = sorted(indices, key=lambda i: findings[i].get("line") or 0)
        for position, index in enumerate(ordered):
            parts = [kind, label, file]
            if owner:
                parts.append(owner)
            if collides:
                parts.append(str(position))
            fingerprints[index] = hashlib.sha256("\0".join(parts).encode()).hexdigest()
    return fingerprints


def render(findings: list[dict], *, sha: str = "", repo: str = ".") -> str:
    rules: dict[str, dict] = {}
    results = []
    for finding, fingerprint in zip(findings, _fingerprints(findings), strict=True):
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
            "partialFingerprints": {"seamcheckId": fingerprint},
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
