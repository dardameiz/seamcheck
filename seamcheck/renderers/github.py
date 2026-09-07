"""Findings as workflow commands, for a job that has no SARIF upload.

One line each, on stdout, in the format Actions turns into an annotation on the diff.
"""
from __future__ import annotations

_KIND = {"unresolved": "error", "unused": "warning", "uncertain": "notice"}


def render(findings: list[dict]) -> str:
    lines = []
    for finding in findings:
        level = _KIND.get(finding["status"], "notice")
        message = (finding.get("note") or f"{finding['label']} is {finding['status']}").replace("\n", " ")
        lines.append(f"::{level} file={finding['file']},line={finding.get('line') or 1}"
                     f",title=seamcheck {finding['kind']}::{message}")
    return "\n".join(lines)
