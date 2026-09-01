"""A report about a scan that contains none of the code it scanned.

Seamcheck learns most from the repositories it gets wrong, and those are private. A user
reported 728 false `unresolved` findings against a Supabase project whose schema lives in
the dashboard rather than in the repo; the fix took an afternoon, and the bug had shipped
because nothing like that project was in the corpus. One line - *Supabase detected, no
schema present, 728 uses reported unresolved* - would have made it obvious, and that line
contains nothing of theirs.

So: no telemetry, no endpoint, no daemon, no network call anywhere in this package. This
builds a report, prints it, and stops. The person reading it decides whether to send it,
and sends it themselves.

**The rule that makes it safe is structural rather than careful: every value is a number
or a word from a vocabulary this file defines.** No free text, ever - free text is where
paths, table names, route shapes and customer identifiers escape, and an intention to strip
them is not a mechanism. Anyone can audit the payload by reading `_payload` below, and a
reviewer at a company can be shown the file rather than a privacy policy.

What that leaves out, deliberately: file paths, symbol names, table and column names, route
paths, code snippets, the repository name, the git remote, the git SHA, commit messages,
environment variable names, and anything a person typed. What it keeps is the SHAPE of the
scan, which is the part that says whether the tool did its job.
"""

from __future__ import annotations

import collections
import json
import platform
import sys
import urllib.parse

from seamcheck.graph import Graph

# Where a person can send it. Not contacted by this code - printed, so they can click it.
ISSUES_URL = "https://github.com/dardameiz/seamcheck/issues/new"

# Size is reported as a bucket rather than a count: "how big" is what matters for
# understanding a scan, and an exact line count is a weak fingerprint of a private repo.
_SIZE_BUCKETS = ((1_000, "tiny"), (10_000, "small"), (100_000, "medium"),
                 (1_000_000, "large"), (10_000_000, "very large"))


def _bucket(count: int) -> str:
    for limit, name in _SIZE_BUCKETS:
        if count < limit:
            return name
    return "enormous"


def _note_key(note: str) -> str:
    """A stable label for a note, without sending the note.

    Notes are written in this repository and contain no user data, but they are long and
    they change. The first six words are enough to tell two causes apart in an aggregate
    and short enough to read in a table.
    """
    if not note:
        return "no note"
    return " ".join(note.split()[:6]).rstrip(".,:").lower()


def _payload(graph: Graph, adapters: list[dict], services: list) -> dict:
    """Everything sent, and nothing else. Numbers and fixed words only."""
    by_status = collections.Counter(symbol.status.value for symbol in graph.symbols)
    by_kind = collections.Counter(symbol.kind for symbol in graph.symbols)
    # kind x status, which is what actually locates a misfiring extractor.
    pairs = collections.Counter(
        f"{symbol.kind}|{symbol.status.value}" for symbol in graph.symbols
    )
    # Why things are uncertain, by cause rather than by instance.
    causes = collections.Counter(
        f"{symbol.kind}|{_note_key(symbol.note)}"
        for symbol in graph.symbols if symbol.status.value == "uncertain"
    )
    # Which extractors produced nothing. A kind absent from a repository that clearly has
    # that thing is the single most useful signal in the whole report.
    return {
        "seamcheck": _version(),
        "python": ".".join(platform.python_version_tuple()[:2]),
        "platform": sys.platform,
        "adapters": [
            {"name": a.get("name", ""), "confidence": a.get("confidence", 0)}
            for a in adapters
        ],
        "services": {
            "declared": len(services),
            "deployable": sum(1 for s in services if getattr(s, "deployable", False)),
            "languages": sorted({s.language for s in services if s.language}),
        },
        "size": _bucket(len(graph.symbols)),
        "symbols": len(graph.symbols),
        "edges": len(graph.edges),
        "by_status": dict(by_status),
        "by_kind": dict(by_kind.most_common()),
        "kind_status": dict(pairs.most_common()),
        "uncertain_causes": dict(causes.most_common(20)),
    }


def _version() -> str:
    try:
        from importlib.metadata import version

        return version("seamcheck")
    except Exception:  # noqa: BLE001 - a source checkout has no installed metadata
        return "source"


def build(repo_root: str = ".") -> dict:
    """Scan and reduce to the shareable payload."""
    from seamcheck import api
    from seamcheck.pipeline import LAST_ADAPTERS
    from seamcheck.services import detect_services

    graph = api.scan(repo_root)
    return _payload(graph, list(LAST_ADAPTERS), detect_services(repo_root))


def _table(title: str, rows: dict, limit: int = 14) -> list[str]:
    if not rows:
        return []
    out = [f"**{title}**", "", "| | count |", "|---|---:|"]
    for key, value in list(rows.items())[:limit]:
        out.append(f"| `{key}` | {value} |")
    out.append("")
    return out


def render(payload: dict) -> str:
    """The report, as markdown a person can paste anywhere."""
    status = payload["by_status"]
    total = sum(status.values()) or 1
    adapters = ", ".join(
        f"{a['name']} ({a['confidence']})" for a in payload["adapters"]
    ) or "none detected"
    lines = [
        "### Seamcheck scan report",
        "",
        "_Shape of a scan only. No file paths, names, routes, snippets or repository "
        "identity - see `seamcheck/share.py` for exactly what is collected._",
        "",
        f"- seamcheck **{payload['seamcheck']}** · Python {payload['python']} · {payload['platform']}",
        f"- adapters: **{adapters}**",
        f"- services: {payload['services']['deployable']} deployable of "
        f"{payload['services']['declared']} declared"
        + (f" ({', '.join(payload['services']['languages'])})"
           if payload["services"]["languages"] else ""),
        f"- graph: **{payload['symbols']:,} symbols**, {payload['edges']:,} edges "
        f"({payload['size']})",
        "",
        "**Result**",
        "",
        "| status | count | share |",
        "|---|---:|---:|",
    ]
    for name in ("connected", "unresolved", "unused", "uncertain"):
        count = status.get(name, 0)
        lines.append(f"| {name} | {count:,} | {count * 100 // total}% |")
    lines.append("")
    lines += _table("Uncertain, by cause", payload["uncertain_causes"])
    lines += _table("Findings by kind and status", payload["kind_status"], limit=20)
    lines += [
        "**Anything wrong here?** If a number looks false for your project - findings "
        "against things that do exist, or nothing found where there is plainly something "
        "- that is the useful part. Say which, in your own words.",
        "",
    ]
    return "\n".join(lines)


def issue_url(payload: dict) -> str:
    """A pre-filled 'new issue' link, so sending it is one click and no typing.

    The URL is printed, never opened and never fetched: what leaves this machine is
    whatever the person chooses to submit, in a browser, after reading it.
    """
    status = payload["by_status"]
    title = (
        f"scan report: {'+'.join(a['name'] for a in payload['adapters']) or 'no adapter'}"
        f" - {status.get('uncertain', 0)} uncertain, {status.get('unresolved', 0)} unresolved"
    )
    body = render(payload)
    # GitHub truncates a very long query string, and browsers differ on the limit. The
    # tables are the first thing to go, because the file on disk has them anyway.
    if len(body) > 5000:
        body = body[:5000] + "\n\n_(truncated - the full report is in seamcheck-share.md)_"
    query = urllib.parse.urlencode({"title": title, "body": body, "labels": "scan-report"})
    return f"{ISSUES_URL}?{query}"


def report(repo_root: str = ".") -> tuple[str, dict]:
    """(markdown, payload) - the one function the CLI and the MCP server both call."""
    payload = build(repo_root)
    return render(payload), payload


def as_json(payload: dict) -> str:
    return json.dumps(payload, indent=2, sort_keys=True)
