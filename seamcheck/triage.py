"""Human dispositions on findings, invalidated the moment the evidence changes."""

from __future__ import annotations

import dataclasses
import json
import pathlib
from dataclasses import dataclass
from enum import Enum

from seamcheck.graph import Graph, Status, Symbol

_TRIAGE_FILE = pathlib.Path(".seamcheck") / "triage.json"
# Where this lived before 2026-09-14. A bare `seamcheck/triage.json` collides with any
# project directory literally named `seamcheck` (this tool's own reference project keeps
# a gitignored clone at exactly that path) - imported as a namespace package, it shadows
# the real one, and every write silently landed in the WRONG repository's working tree.
# Read-only: `load_triage` falls back here so existing marks are not stranded, and the
# next `save_triage` (every caller loads before it saves - see api.py) naturally migrates
# them to the new path without an explicit migration step.
_LEGACY_TRIAGE_FILE = pathlib.Path("seamcheck") / "triage.json"

# Statuses a human can act on. APPROVED is the only one that silences a finding; a
# CONFIRMED finding is a real bug someone has acknowledged, and must keep blocking.
_BLOCKING_STATUSES = frozenset({Status.UNRESOLVED, Status.UNUSED})


class TriageStatus(str, Enum):
    UNTRIAGED = "untriaged"
    APPROVED = "approved"
    CONFIRMED = "confirmed"
    DEFERRED = "deferred"


class WhyWrong(str, Enum):
    """Why a finding was wrong, as a fixed word rather than a sentence.

    The reason people write is the most useful thing seamcheck could learn from - it is
    what took precision from 28% to 42% when eight repositories were hand-labelled. It is
    also free text, which is exactly where a path, a table name or a customer identifier
    would escape if the reason were ever shared.

    So the reason is captured as an ENUM and the prose is kept beside it, local and never
    sent. The categories are not invented: each is a false-positive class actually
    measured on a real repository, which is why the list is short and why "other" is last
    rather than first.
    """

    DEPENDENCY = "consumed-by-dependency"      # a CDN bundle, node_modules, the framework
    RUNTIME = "built-at-runtime"               # the name is assembled, not a literal
    OUTSIDE_REPO = "read-outside-repo"         # a container, CI, a shell script, mobile
    DECLARED_ELSEWHERE = "declared-elsewhere"  # schema or config lives in another repo
    GENERATED = "generated"                    # build output, a copy of source
    TEST_ONLY = "test-or-fixture"              # not the product
    FRAMEWORK = "framework-implicit"           # the framework does this unasked
    REALLY_DEAD = "genuinely-dead"             # a TRUE positive, and just as worth knowing
    OTHER = "other"


# What each one means, for `seamcheck help triage` and for the map's own picker. Kept
# beside the enum so the two can never drift.
WHY_HELP: dict[str, str] = {
    WhyWrong.DEPENDENCY.value: "Something outside the repo uses it - a CDN bundle, a "
                               "package, the framework's own code.",
    WhyWrong.RUNTIME.value: "The name is built at runtime, so no literal for it exists.",
    WhyWrong.OUTSIDE_REPO.value: "Read by a container, CI, a shell script or another app.",
    WhyWrong.DECLARED_ELSEWHERE.value: "The schema or config it needs lives somewhere else.",
    WhyWrong.GENERATED.value: "Build output or a copy of code already read.",
    WhyWrong.TEST_ONLY.value: "A test or fixture, not the product.",
    WhyWrong.FRAMEWORK.value: "The framework does this without being asked.",
    WhyWrong.REALLY_DEAD.value: "Nothing wrong with the finding - it really is dead.",
    WhyWrong.OTHER.value: "None of the above.",
}


@dataclass
class TriageEntry:
    symbol_id: str
    fingerprint: str
    status: TriageStatus
    who: str
    when: str
    reason: str
    # The shareable half of `reason`: a fixed word, defaulted so every stored entry
    # written before this existed still loads.
    why: str = ""
    # The day a scan first found the evidence changed out from under this mark. Empty
    # while the mark holds. The entry is KEPT rather than dropped, because "someone
    # looked at this on the 1st and said consumed-by-dependency" is the context a reader
    # needs when the finding comes back on the 3rd - and it is somebody's work.
    expired: str = ""


def fingerprint_for_symbol(symbol: Symbol) -> str:
    """Content hash of the evidence a disposition was made against.

    Keyed to evidence, never to a symbol id or line number: if the snippet or the status
    changes, the mark someone made no longer describes what is on screen and must expire.
    """
    if symbol.kind == "multi_writer_element":
        # The finding IS the set of writers, so the disposition must expire when that
        # set changes -- a third writer appearing is a new problem, not the old one.
        return f"multi_writer_element:{symbol.label}:{'|'.join(sorted(symbol.chain))}"
    return f"{symbol.kind}:{symbol.snippet}:{symbol.status.value}"


_ENTRY_FIELDS = frozenset(f.name for f in dataclasses.fields(TriageEntry))


def load_triage(repo_root: str) -> list[TriageEntry]:
    path = pathlib.Path(repo_root) / _TRIAGE_FILE
    if not path.is_file():
        legacy = pathlib.Path(repo_root) / _LEGACY_TRIAGE_FILE
        if not legacy.is_file():
            return []
        path = legacy
    data = json.loads(path.read_text(encoding="utf-8"))
    return [
        # Unknown keys dropped rather than raising: a file written by a newer seamcheck
        # must still load in an older one, and a triage file is somebody's work.
        TriageEntry(**{
            **{k: v for k, v in entry.items() if k in _ENTRY_FIELDS},
            "status": TriageStatus(entry["status"]),
        })
        for entry in data.get("entries", [])
    ]


def save_triage(entries: list[TriageEntry], repo_root: str) -> None:
    path = pathlib.Path(repo_root) / _TRIAGE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "entries": [
            {**dataclasses.asdict(entry), "status": entry.status.value} for entry in entries
        ]
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def valid_triage_entries(graph: Graph, entries: list[TriageEntry]) -> list[TriageEntry]:
    """Entries whose stored fingerprint still matches the symbol as it is now, in order.

    The one place the fingerprint-validity predicate lives -- valid_triage_ids() and
    _valid_entries() below, plus report.py, all derive from this list instead of
    re-deriving the predicate, so "still valid" can't silently drift between callers.
    Filtering entry-by-entry (not id-by-id) matters: triage.json is a checked-in file a
    human can hand-edit, so nothing here may assume at most one entry per symbol_id --
    a set of "ids with SOME valid entry" would let a stale entry for an id ride along
    just because another entry for that same id happened to validate.
    """
    by_id = {symbol.id: symbol for symbol in graph.symbols}
    return [
        entry
        for entry in entries
        if entry.symbol_id in by_id
        and fingerprint_for_symbol(by_id[entry.symbol_id]) == entry.fingerprint
    ]


def stale_entries(graph: Graph, entries: list[TriageEntry]) -> list[TriageEntry]:
    """The other half: entries whose symbol is still in the scan but whose evidence moved.

    A symbol that vanished altogether is neither valid nor stale - there is nothing on
    screen for the mark to describe or contradict - so it is in neither list, and the
    entry waits in the file for the day the symbol returns.
    """
    by_id = {symbol.id: symbol for symbol in graph.symbols}
    return [
        entry
        for entry in entries
        if entry.symbol_id in by_id
        and fingerprint_for_symbol(by_id[entry.symbol_id]) != entry.fingerprint
    ]


def note_expired(entries: list[TriageEntry], stale: list[TriageEntry], today: str) -> bool:
    """Stamp the day on every stale mark that has none yet. True if any was stamped.

    In place, so the caller can save the same list it loaded and the date survives to
    the next scan - "expired 2026-09-01" is a fact about when the code moved, and a
    scan a week later must not reset it to its own date.
    """
    changed = False
    for entry in stale:
        if not entry.expired:
            entry.expired = today
            changed = True
    return changed


def returned(graph: Graph, entries: list[TriageEntry]) -> list[tuple[Symbol, TriageEntry]]:
    """Findings that came back: a finding-status symbol wearing a mark that no longer holds.

    Not every stale mark is a return. A mark whose symbol is now CONNECTED outlived a
    finding that went away, which is the good outcome and nothing to raise. The later
    entry per symbol wins, as everywhere else, so a hand-edited duplicate cannot
    resurrect a mark that was replaced.
    """
    by_id = {symbol.id: symbol for symbol in graph.symbols}
    latest = {entry.symbol_id: entry for entry in stale_entries(graph, entries)}
    valid = valid_triage_ids(graph, entries)
    return [
        (by_id[symbol_id], entry)
        for symbol_id, entry in latest.items()
        if symbol_id not in valid and by_id[symbol_id].status in _BLOCKING_STATUSES
    ]


def remove_mark(entries: list[TriageEntry], symbol_id: str) -> tuple[list[TriageEntry], int]:
    """Every entry on the symbol gone - the undo. Returns what is left and how many went."""
    kept = [entry for entry in entries if entry.symbol_id != symbol_id]
    return kept, len(entries) - len(kept)


def valid_triage_ids(graph: Graph, entries: list[TriageEntry]) -> set[str]:
    """Symbol ids carrying a still-valid triage entry."""
    return {entry.symbol_id for entry in valid_triage_entries(graph, entries)}


def judged_ids(entries: list[TriageEntry]) -> set[str]:
    """Every symbol id carrying ANY mark at all, valid or not.

    Deliberately looser than `valid_triage_ids`: this answers "has a person recorded an
    opinion about this symbol", which stays true even after the code moved out from under
    the mark, not "and does that opinion still match the evidence". `unverified`'s queue
    wants exactly that - a claim someone already looked at must not resurface in the queue
    just because a line shifted - and `findings()` reuses the same predicate so "what is
    wrong" answers the same question everywhere it is asked, no graph required.
    """
    return {entry.symbol_id for entry in entries}


def _valid_entries(graph: Graph, entries: list[TriageEntry]) -> dict[str, TriageEntry]:
    """Entries whose stored fingerprint still matches the symbol as it is now.

    Built only from already-valid entries, so when more than one entry names the same
    symbol_id, the later VALID one wins -- never a later entry that merely shares an id
    with a valid one, which would let a stale disposition decide has_blocking_findings().
    """
    return {entry.symbol_id: entry for entry in valid_triage_entries(graph, entries)}


def apply_triage(graph: Graph, entries: list[TriageEntry]) -> Graph:
    valid = _valid_entries(graph, entries)
    symbols = [
        dataclasses.replace(symbol, note=f"[triage:{valid[symbol.id].status.value}] {symbol.note}".strip())
        if symbol.id in valid
        else symbol
        for symbol in graph.symbols
    ]
    return Graph(symbols=symbols, edges=graph.edges, schema_version=graph.schema_version)


def blocking_ids(graph: Graph, entries: list[TriageEntry]) -> set[str]:
    """Symbol ids that are still blocking the build, right now.

    A symbol is blocking when its status is UNRESOLVED/UNUSED and either nothing has
    judged it, or the mark that HAS is CONFIRMED - a real bug someone has already
    acknowledged, which must keep blocking (see `TriageStatus`'s own comment: CONFIRMED
    is the only status that does not silence a finding). This is the ONE predicate
    "still blocking" is decided by - `has_blocking_findings` (the CI gate) and
    `queries.findings(only_blocking=True)` (what feeds SARIF and the GitHub-annotation
    output) both derive from it, so a CONFIRMED finding can no longer fail the build
    while being invisible in the very report meant to say why it failed.
    """
    valid = _valid_entries(graph, entries)
    return {
        symbol.id
        for symbol in graph.symbols
        if symbol.status in _BLOCKING_STATUSES
        and (symbol.id not in valid or valid[symbol.id].status is TriageStatus.CONFIRMED)
    }


def has_blocking_findings(graph: Graph, entries: list[TriageEntry]) -> bool:
    return bool(blocking_ids(graph, entries))
