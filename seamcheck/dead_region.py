"""Code that cannot run, because the guard above it always fires.

The finding this exists for, from the reference project: `push_arena.js` looked up
`#leaderboard-list`, `#pioneers-btn` and `#paradises-btn`, returned early if any was
missing, and all three had been deleted from the markup long ago. So the guard fired on
every load and **292 lines below it were unreachable** - a fetch, two click listeners, a
setTimeout, three functions. The scan reported that as **fourteen separate findings**:
each selector in the region, each element it wanted, all at the same severity as a
one-line typo.

They were one problem. And the two halves are not the same KIND of problem either:
"nothing references this name" was worth zero deletions on that surface, while "this
region never runs" was worth 329 lines. Presenting them at one severity is what makes a
list of 712 get skimmed.

Deliberately narrow. The guard has to be a direct statement of the function body, its
condition has to be readable, and every element it depends on has to be one the scan has
already decided is missing. A return inside a branch ends the function too, but knowing
whether that branch always runs is analysis this tool does not do - so it says nothing.
"""
from __future__ import annotations

import os
from dataclasses import replace

from seamcheck.extractors.js_extractor import iter_parsed
from seamcheck.graph import Edge, Status, Symbol
from seamcheck.nodetools import node_end_line, node_line

_LOOKUPS = frozenset({"getElementById", "querySelector"})
_FUNCTIONS = frozenset({"FunctionDeclaration", "FunctionExpression", "ArrowFunctionExpression"})


def _lookup_name(node: dict) -> str | None:
    """The element a `getElementById('x')` / `querySelector('.x')` call names."""
    if not isinstance(node, dict) or node.get("type") != "CallExpression":
        return None
    callee = node.get("callee") or {}
    if (callee.get("property") or {}).get("name") not in _LOOKUPS:
        return None
    arguments = node.get("arguments") or []
    first = arguments[0] if arguments else {}
    if first.get("type") != "Literal" or not isinstance(first.get("value"), str):
        return None
    text = first["value"].strip()
    # `#id`, `.class` and a bare id all name one element; anything with a combinator or
    # an attribute in it is a selector this pass will not reason about.
    if text.startswith(("#", ".")):
        text = text[1:]
    if not text or any(c in text for c in " >+~[]:,*"):
        return None
    return text


def _looked_up_in(body: list, until: int) -> dict[str, str]:
    """variable name -> the element it holds, for declarations before `until`."""
    found: dict[str, str] = {}
    for statement in body[:until]:
        if not isinstance(statement, dict) or statement.get("type") != "VariableDeclaration":
            continue
        for declarator in statement.get("declarations") or []:
            name = (declarator.get("id") or {}).get("name")
            element = _lookup_name(declarator.get("init") or {})
            if name and element:
                found[name] = element
    return found


def _guard_names(test: dict, known: dict[str, str]) -> list[str] | None:
    """The elements an `if (...) return` depends on, when every branch of it is a miss.

    `!a`, `!a || !b`, `!(a && b)`. Returns None for a condition this cannot read - which
    includes `!a && !b`, where BOTH have to be absent for the guard to fire, so one
    missing element proves nothing.
    """
    if not isinstance(test, dict):
        return None
    kind = test.get("type")
    if kind == "UnaryExpression" and test.get("operator") == "!":
        argument = test.get("argument") or {}
        if argument.get("type") == "Identifier":
            name = known.get(argument.get("name") or "")
            return [name] if name else None
        # !(a && b) is the same guard written the other way round.
        if argument.get("type") == "LogicalExpression" and argument.get("operator") == "&&":
            names = []
            for side in (argument.get("left"), argument.get("right")):
                if not isinstance(side, dict) or side.get("type") != "Identifier":
                    return None
                name = known.get(side.get("name") or "")
                if not name:
                    return None
                names.append(name)
            return names
        return None
    if kind == "LogicalExpression" and test.get("operator") == "||":
        names = []
        for side in (test.get("left"), test.get("right")):
            part = _guard_names(side or {}, known)
            if part is None:
                return None
            names += part
        return names
    return None


def _returns(statement: dict) -> bool:
    """Whether this `if`'s consequent leaves the function immediately."""
    consequent = statement.get("consequent") or {}
    if consequent.get("type") == "ReturnStatement":
        return True
    if consequent.get("type") == "BlockStatement":
        body = consequent.get("body") or []
        return bool(body) and body[-1].get("type") in ("ReturnStatement", "ThrowStatement")
    return consequent.get("type") == "ThrowStatement"


def _function_name(node: dict, holder: dict | None) -> str:
    name = (node.get("id") or {}).get("name")
    if name:
        return name
    if holder:
        return ((holder.get("id") or {}).get("name")
                or (holder.get("key") or {}).get("name") or "")
    return ""


def _walk_functions(node, holder=None):
    """Every function, paired with the declarator or property that names it."""
    if isinstance(node, dict):
        if node.get("type") in _FUNCTIONS:
            yield node, holder
        nested_holder = node if node.get("type") in ("VariableDeclarator", "Property",
                                                     "MethodDefinition") else None
        for key, value in node.items():
            if key in ("loc", "type"):
                continue
            if isinstance(value, (dict, list)):
                yield from _walk_functions(value, nested_holder or holder)
    elif isinstance(node, list):
        for item in node:
            yield from _walk_functions(item, holder)


_NOTE = (
    "This function returns before anything below line {line} runs, and it always will: "
    "{which} {missing} that no markup renders. So {lines} lines here are unreachable - "
    "not unreferenced, unreachable, which is a different and larger thing. Everything "
    "the scan reports inside this region is a symptom of this one guard."
)


_CONDITIONAL_NOTE = (
    "The guard returns on {missing}, which no template renders - but {others} other "
    "module(s) reach for the same element, so it is more likely rendered only in some "
    "state than never rendered at all. Check the state that draws it before touching "
    "anything here: a feature that appears for one player in a hundred looks exactly "
    "like a dead one from the outside."
)


def find_dead_regions(
    js_files: list[str],
    missing: set[str],
    line_offsets: dict[str, int] | None = None,
    readers: dict[str, set[str]] | None = None,
) -> tuple[list[Symbol], list[tuple[str, int, int, str]]]:
    """One finding per guard that always fires, instead of one per symbol below it.

    Returns the region symbols and the spans they cover - `(file, first line, last line,
    region id)` - so the caller can point everything inside a region at it instead of
    raising each one separately. That is the whole purpose: fourteen rows, each looking
    like a small chore, in place of one line that explains all fourteen.

    `missing` is the set of element names the scan has already decided nothing renders -
    so this runs after matching, and can never disagree with it.

    `readers` maps each element name to the files that reach for it. A guard element only
    one file touches is a dead branch; one that two unrelated modules touch is a
    conditional element, and the region under it is reported as something to CHECK rather
    than something to delete.
    """
    symbols: list[Symbol] = []
    spans: list[tuple[str, int, int, str]] = []
    # One region per guard, however many times the caller hands us the same file: the
    # evidence file list carries a file under more than one spelling, and every region
    # was reported twice.
    seen: set[str] = set()
    if not missing:
        return symbols, spans
    for path, ast_root in iter_parsed([f for f in js_files if os.path.isfile(f)]):
        offset = (line_offsets or {}).get(path, 0)
        for function, holder in _walk_functions(ast_root):
            body = ((function.get("body") or {}).get("body")) or []
            if not isinstance(body, list):
                continue
            known: dict[str, str] = {}
            for index, statement in enumerate(body):
                if not isinstance(statement, dict):
                    continue
                if statement.get("type") == "VariableDeclaration":
                    known = _looked_up_in(body, index + 1)
                    continue
                if statement.get("type") != "IfStatement" or not _returns(statement):
                    continue
                names = _guard_names(statement.get("test") or {}, known)
                if not names:
                    continue
                # Every shape `_guard_names` reads fires if ANY of its elements is
                # absent - `!a`, `!a || !b`, `!(a && b)` are all that guard. So one
                # missing element is enough, and the note names the ones that are.
                gone = [name for name in names if name in missing]
                if not gone:
                    continue
                after = body[index + 1:]
                if not after:
                    continue          # nothing below it to be dead
                guard_line = (node_line(statement) or 0) + offset
                last = max((node_end_line(s) or node_line(s) or 0) for s in after) + offset
                first = min((node_line(s) or 0) for s in after) + offset
                dead = max(1, last - first + 1)
                symbol_id = f"dead_region:{path}:{guard_line}"
                # Deduped on the file the OS thinks it is, not on the string: the same
                # file arrives here as an absolute path and as a repo-relative one, and
                # every region was reported twice. `relativise` fixes the spelling later,
                # by which point there are two symbols under one id.
                fingerprint = f"{os.path.realpath(path)}:{guard_line}"
                if fingerprint in seen:
                    break
                seen.add(fingerprint)
                name = _function_name(function, holder) or os.path.basename(path)
                # Who else reaches for the same element. Its own file does not count -
                # the whole region is in it.
                elsewhere = {
                    where
                    for element in gone
                    for where in (readers or {}).get(element, ())
                    if os.path.realpath(where) != os.path.realpath(path)
                }
                which = "it looks up an element" if len(gone) == 1 else \
                        "it looks up elements"
                symbols.append(Symbol(
                    id=symbol_id, kind="dead_region", label=name,
                    sub=(f"{dead} lines, check first" if elsewhere else f"{dead} lines"),
                    file=path, line=guard_line,
                    # Uncertain, not a claim: the evidence is the same either way, and the
                    # difference between the two readings is a feature nobody can see from
                    # here. Said out loud rather than guessed at.
                    status=Status.UNCERTAIN if elsewhere else Status.UNRESOLVED,
                    snippet=f"if (!{gone[0]}...) return;",
                    chain=[os.path.basename(path), name], owner=name,
                    note=(_CONDITIONAL_NOTE.format(missing=", ".join(sorted(set(gone))),
                                                   others=len(elsewhere))
                          if elsewhere
                          else _NOTE.format(line=guard_line, which=which,
                                            missing=", ".join(sorted(set(gone))),
                                            lines=dead)),
                ))
                if not elsewhere:
                    spans.append((path, first, last, symbols[-1].id))
                break   # the first always-firing guard kills everything after it
    return symbols, spans


_SYMPTOM_NOTE = (
    "Inside a region that never runs: the guard at {file}:{line} returns every time, "
    "because an element it looks up is not rendered. Nothing here is a finding of its "
    "own - fix or delete the region and this goes with it."
)


_CORPSE_NOTE = (
    "One of the writers this named is inside a region that never runs ({where}), so what "
    "is left is {left}. A multi-writer report is a risk about which of two writers wins; "
    "when one of them cannot run, the risk is not real and the dead one is the finding. "
    "Deleting it retires this report too."
)


def demote_dead_writers(
    spans: list[tuple[str, int, int, str]],
    symbols: list[Symbol],
    dom_selectors: list[Symbol],
) -> list[Symbol]:
    """A multi-writer whose second writer never runs is not a fight - it is a corpse.

    Found by the person acting on these findings: deleting `resetProgressBars()` retired
    two multi-writer reports, because one of the two writers had been in the dead region
    all along. Reporting "two files fight over this element" when one of them cannot run
    sends someone to reconcile a conflict that does not exist.
    """
    if not spans or not symbols:
        return symbols
    def _dead(symbol: Symbol) -> bool:
        if not symbol.file or not symbol.line:
            return False
        return any(symbol.file == path and first <= symbol.line <= last
                   for path, first, last, _ in spans)

    live: dict[str, set[str]] = {}
    dead_sites: dict[str, str] = {}
    for selector in dom_selectors:
        if not selector.sub.endswith(":write") or selector.label == "<dynamic>":
            continue
        if _dead(selector):
            dead_sites.setdefault(selector.label,
                                  f"{os.path.basename(selector.file)}:{selector.line}")
            continue
        live.setdefault(selector.label, set()).add(selector.file)

    out: list[Symbol] = []
    for symbol in symbols:
        if symbol.kind != "multi_writer_element" or symbol.label not in dead_sites:
            out.append(symbol)
            continue
        remaining = len(live.get(symbol.label, ()))
        if remaining >= 2:
            out.append(symbol)      # still a genuine fight between live writers
            continue
        left = "one live writer" if remaining == 1 else "no live writer at all"
        out.append(replace(
            symbol, status=Status.UNCERTAIN,
            note=_CORPSE_NOTE.format(where=dead_sites[symbol.label], left=left),
        ))
    return out


def fold_into_regions(
    spans: list[tuple[str, int, int, str]], symbols: list[Symbol], edges: list[Edge]
) -> list[Edge]:
    """Point every finding inside a dead region at the region, and stop claiming it.

    A symbol in unreachable code is not a broken reference - it is a line that does not
    execute, and the reference it makes is neither right nor wrong until the region does.
    So the self-edge that carried its `unresolved` verdict is replaced by an edge to the
    region carrying `uncertain` and a sentence saying where to look.
    """
    if not spans:
        return edges
    inside: dict[str, str] = {}
    for symbol in symbols:
        if not symbol.file or not symbol.line:
            continue
        for path, first, last, region_id in spans:
            if symbol.file == path and first <= symbol.line <= last:
                inside[symbol.id] = region_id
                break
    if not inside:
        return edges
    where = {region_id: (path, first) for path, first, _, region_id in spans}
    out: list[Edge] = []
    for edge in edges:
        region_id = inside.get(edge.from_id)
        if (region_id and edge.from_id == edge.to_id
                and edge.status in (Status.UNRESOLVED, Status.UNUSED)):
            path, line = where[region_id]
            out.append(Edge(from_id=edge.from_id, to_id=region_id, status=Status.UNCERTAIN,
                            note=_SYMPTOM_NOTE.format(file=os.path.basename(path),
                                                      line=line)))
            continue
        out.append(edge)
    return out
