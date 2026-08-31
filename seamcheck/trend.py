"""The trend across scans: is this codebase getting better?

Distinct from `history.py`, which attributes findings to the commit that introduced them.
That answers "who did this"; this answers "which way is it going", and needs only a small
row per scan rather than a snapshot per commit.

`Changes` compares this scan against one baseline and is empty until a baseline exists,
which answers "what moved since that commit" and nothing else. The question a codebase
actually raises is the one no tool in this category answers:

    is this getting better?

So every scan appends one small row - commit, timestamp, counts by status, findings by
kind - to a file that only ever grows. A few hundred bytes each; a year of daily scans is
under a megabyte. The payoff is the sentence that needs a series and cannot be faked:
*"unused CSS fell from 4,340 to 1,802 over six weeks."*

Two rules keep the series honest.

**A commit appears once.** Re-scanning the same commit replaces its row rather than adding
a second, so a rebuild does not look like progress. **And nothing is ever rewritten.** A
row records what that scan found, including when it found more than the one before; a
history that only goes down is a history nobody should believe.
"""

from __future__ import annotations

import dataclasses
import datetime
import json
import pathlib

from seamcheck.graph import Graph, Status

_TREND_PATH = pathlib.Path("OTHER") / "seamcheck" / "trend.jsonl"

# Kinds worth tracking separately, because they are the ones a team acts on and the ones
# whose movement means something. Everything else lands in the totals.
TRACKED_KINDS = (
    "css_selector", "css_token_def", "css_token_use", "dom_selector", "dom_attr",
    "fetch_target", "url", "view", "js_call",
)


@dataclasses.dataclass(frozen=True)
class Entry:
    """One scan, reduced to what a trend needs."""

    sha: str
    at: str
    symbols: int
    findings: int
    by_status: dict[str, int]
    by_kind: dict[str, int]

    @property
    def short(self) -> str:
        return self.sha[:12]


def path(repo_root: str) -> pathlib.Path:
    return pathlib.Path(repo_root) / _TREND_PATH


def summarise(graph: Graph, sha: str, at: str | None = None) -> Entry:
    """Reduce a scan to one row."""
    by_status: dict[str, int] = {}
    by_kind: dict[str, int] = {}
    findings = 0
    for symbol in graph.symbols:
        status = symbol.status.value
        by_status[status] = by_status.get(status, 0) + 1
        if symbol.status in (Status.UNRESOLVED, Status.UNUSED):
            findings += 1
            if symbol.kind in TRACKED_KINDS:
                by_kind[symbol.kind] = by_kind.get(symbol.kind, 0) + 1
    return Entry(
        sha=sha,
        at=at or datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds"),
        symbols=len(graph.symbols),
        findings=findings,
        by_status=by_status,
        by_kind=by_kind,
    )


def load(repo_root: str) -> list[Entry]:
    """Every scan recorded, oldest first. A corrupt line is skipped, never fatal."""
    file = path(repo_root)
    if not file.is_file():
        return []
    entries: list[Entry] = []
    try:
        text = file.read_text(encoding="utf-8")
    except OSError:
        return []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            entries.append(Entry(
                sha=row["sha"], at=row["at"], symbols=row["symbols"],
                findings=row["findings"], by_status=row.get("by_status", {}),
                by_kind=row.get("by_kind", {}),
            ))
        except (ValueError, KeyError, TypeError):
            # One unreadable row must not cost the series.
            continue
    return entries


def record(graph: Graph, sha: str, repo_root: str, at: str | None = None) -> Entry:
    """Append this scan, replacing any earlier row for the same commit."""
    entry = summarise(graph, sha, at)
    entries = [e for e in load(repo_root) if e.sha != sha]
    entries.append(entry)
    file = path(repo_root)
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(
        "".join(json.dumps(dataclasses.asdict(e), separators=(",", ":")) + "\n" for e in entries),
        encoding="utf-8",
    )
    return entry


def trend(entries: list[Entry]) -> dict:
    """What the series says, in the terms a reader asks it in.

    `delta` is last minus first, so negative is fewer findings - the direction a reader
    wants. `movers` are the kinds that changed most, because "findings went down 300" is
    less useful than "unused CSS went down 300 and unresolved selectors went up 12".
    """
    if not entries:
        return {"entries": [], "span": 0, "delta": 0, "movers": []}
    first, last = entries[0], entries[-1]
    movers = []
    for kind in sorted(set(first.by_kind) | set(last.by_kind)):
        change = last.by_kind.get(kind, 0) - first.by_kind.get(kind, 0)
        if change:
            movers.append({"kind": kind, "change": change,
                           "from": first.by_kind.get(kind, 0), "to": last.by_kind.get(kind, 0)})
    movers.sort(key=lambda m: -abs(m["change"]))
    return {
        "entries": [dataclasses.asdict(e) for e in entries],
        "span": len(entries),
        "delta": last.findings - first.findings,
        "first": dataclasses.asdict(first),
        "last": dataclasses.asdict(last),
        "movers": movers[:8],
    }
