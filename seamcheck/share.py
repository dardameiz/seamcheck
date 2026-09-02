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
import pathlib
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


def _triage_shapes(repo_root: str, graph: Graph) -> dict:
    """What a person marked wrong, as counts of (kind, status, reason) - never the prose.

    This is the part worth having. Counts alone say a scan produced 3,000 findings; they
    cannot say which of them were WRONG, and wrongness is the only thing that improves the
    tool. A person triaging their own backlog is already deciding exactly that, one finding
    at a time, and the decision was being thrown away.

    The prose reason they typed stays on their machine. Only the fixed word travels, and
    only alongside the kind it was about - `dom_attr|unresolved|consumed-by-dependency`
    tells us an extractor is missing a dependency's markup, and contains nothing of theirs.
    """
    from seamcheck.triage import load_triage

    by_id = {symbol.id: symbol for symbol in graph.symbols}
    shapes: collections.Counter = collections.Counter()
    marked = 0
    for entry in load_triage(repo_root):
        symbol = by_id.get(entry.symbol_id)
        if symbol is None:
            continue
        marked += 1
        shapes[f"{symbol.kind}|{symbol.status.value}|{entry.why or 'unspecified'}"] += 1
    return {"marked": marked, "shapes": dict(shapes.most_common(30))}


# Manifests that name PUBLIC packages. Read only when the reader asks for it - see
# _dependencies for why this is opt-in when everything else is not.
_MANIFESTS = (
    ("package.json", ("dependencies", "devDependencies")),
    ("pyproject.toml", None),
    ("requirements.txt", None),
)


def _dependencies(repo_root: str, limit: int = 60) -> list[str]:
    """The public packages this project depends on, with versions.

    THE EXCEPTION to the no-free-text rule, and the reason it exists: counts tell us an
    extractor is wrong and in what way - `dom_attr|unresolved|consumed-by-dependency, 47` -
    but not WHICH dependency, and you cannot write the handling for a library you cannot
    name. Told "the project uses tiptap, sonner and radix", the fix is an afternoon of
    reading three public repositories. Told only "a dependency", it is guesswork.

    These are public package names, not the reader's code. But a scoped name CAN be a
    private registry package, and this module cannot tell the difference offline - so
    unlike everything else in the payload this is **opt-in**, and the reader sees the list
    before it goes anywhere. The strict guarantee stays the default; this is a conscious
    upgrade to it.
    """
    import json as _json
    import re as _re

    root = pathlib.Path(repo_root)
    found: dict[str, str] = {}
    package = root / "package.json"
    if package.is_file():
        try:
            data = _json.loads(package.read_text(encoding="utf-8", errors="replace"))
            for section in ("dependencies", "devDependencies"):
                for name, version in (data.get(section) or {}).items():
                    if isinstance(name, str) and isinstance(version, str):
                        found[name] = version.lstrip("^~>=< ")
        except (OSError, ValueError):
            pass
    requirements = root / "requirements.txt"
    if requirements.is_file():
        try:
            for line in requirements.read_text(encoding="utf-8", errors="replace").splitlines():
                line = line.split("#")[0].strip()
                match = _re.match(r"^([A-Za-z0-9._-]+)\s*[=><~!]*\s*([0-9][\w.]*)?", line)
                if match and match.group(1):
                    found[match.group(1)] = match.group(2) or ""
        except OSError:
            pass
    return [f"{name}@{version}" if version else name
            for name, version in sorted(found.items())][:limit]


def _payload(graph: Graph, adapters: list[dict], services: list,
             triage: dict | None = None, dependencies: list[str] | None = None) -> dict:
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
        "triage": triage or {"marked": 0, "shapes": {}},
        # Absent unless asked for. See _dependencies.
        **({"dependencies": dependencies} if dependencies else {}),
    }


def _version() -> str:
    try:
        from importlib.metadata import version

        return version("seamcheck")
    except Exception:  # noqa: BLE001 - a source checkout has no installed metadata
        return "source"


def build(repo_root: str = ".", with_deps: bool = False) -> dict:
    """Scan and reduce to the shareable payload."""
    from seamcheck import api
    from seamcheck.pipeline import LAST_ADAPTERS
    from seamcheck.services import detect_services

    graph = api.scan(repo_root)
    return _payload(graph, list(LAST_ADAPTERS), detect_services(repo_root),
                    _triage_shapes(repo_root, graph),
                    _dependencies(repo_root) if with_deps else None)


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
    triage = payload.get("triage") or {}
    if triage.get("shapes"):
        lines += [
            f"**Findings a person marked** ({triage['marked']}) — the useful part. Each row "
            "is a kind, the status it was reported at, and why it was wrong.",
            "",
        ]
        lines += _table("Marked", triage["shapes"], limit=20)[1:]
    if payload.get("dependencies"):
        lines += [
            f"**Dependencies** ({len(payload['dependencies'])}) — included because you "
            "asked with `--with-deps`. Public package names, so a false positive blamed on "
            "\"a dependency\" can actually be chased.",
            "",
            "```", ", ".join(payload["dependencies"]), "```", "",
        ]
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


def report(repo_root: str = ".", with_deps: bool = False) -> tuple[str, dict]:
    """(markdown, payload) - the one function the CLI and the MCP server both call."""
    payload = build(repo_root, with_deps)
    return render(payload), payload


def as_json(payload: dict) -> str:
    return json.dumps(payload, indent=2, sort_keys=True)
