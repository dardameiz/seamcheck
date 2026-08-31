"""Template elements against the JavaScript that touches them."""

from __future__ import annotations

import os
from collections import Counter, defaultdict

from seamcheck.graph import Edge, Status, Symbol

_MULTI_WRITER_NOTE = (
    "More than one file writes this element. Whichever runs last wins, which is how a "
    "display bug survives being 'fixed' in one of them. Pick one canonical owner and "
    "route the others through it."
)

# Above this many writers, and this concentrated in one directory, the writers are not
# competing - they are siblings implementing one interface against a shared container.
# Measured: the project this was tuned on has 62 files writing `main-push-area`, 61 of them
# in buttons/js, because it has ~60 button types and each renders into the same area. That
# is an architecture, and reporting it as the two-writers-overwrite-each-other bug is the
# tool crying wolf on a pattern the author chose on purpose.
#
# Both conditions are needed. Two writers in one directory is the classic bug; forty
# writers spread across the codebase is a genuine free-for-all worth flagging.
_FAMILY_MIN_WRITERS = 5
_FAMILY_CONCENTRATION = 0.8
_FAMILY_NOTE = (
    "{count} files write this element, {top} of them in {directory}. That concentration is "
    "a family of sibling implementations sharing one container - a plugin pattern - not two "
    "writers overwriting each other. Not claimed as a finding; worth knowing when you change "
    "who owns this element."
)


def _base_sub(symbol: Symbol) -> str:
    return symbol.sub.split(":", 1)[0]


def match_dom_selectors(dom_attrs: list[Symbol], dom_selectors: list[Symbol]) -> list[Edge]:
    """Match by exact id/data key or class-token membership.

    Not a CSS selector engine: a combinator like `.a .b` is matched on segment presence,
    a stated v1 limitation.
    """
    attrs_by_key: dict[tuple[str, str], Symbol] = {}
    for attr in dom_attrs:
        attrs_by_key.setdefault((attr.sub, attr.label), attr)

    edges: list[Edge] = []
    for selector in dom_selectors:
        # A runtime-built selector names nothing checkable, and a class JavaScript
        # applies needs no template attribute to match - the JS creates the element.
        if selector.label == "<dynamic>" or selector.sub.startswith(("class:apply", "class:stem")):
            continue
        matched = attrs_by_key.get((_base_sub(selector), selector.label))
        if matched:
            edges.append(Edge(from_id=selector.id, to_id=matched.id, status=Status.CONNECTED))
        else:
            edges.append(Edge(from_id=selector.id, to_id=selector.id, status=Status.UNRESOLVED))
    return edges


def detect_multi_writers(dom_selectors: list[Symbol]) -> list[Symbol]:
    writers: dict[str, set[str]] = defaultdict(set)
    # Whole paths, not basenames: the directory is what tells a family apart from a fight,
    # and two same-named files in different directories are two writers either way.
    paths: dict[str, set[str]] = defaultdict(set)
    evidence: dict[str, Symbol] = {}
    for selector in dom_selectors:
        if not selector.sub.endswith(":write") or selector.label == "<dynamic>":
            continue
        writers[selector.label].add(os.path.basename(selector.file))
        paths[selector.label].add(selector.file)
        evidence.setdefault(selector.label, selector)

    flagged: list[Symbol] = []
    for label, files in sorted(writers.items()):
        # Gated on distinct PATHS, not basenames. Counting basenames meant two different
        # files with the same name - push_arena/stats.js and js/stats.js - collapsed into
        # one writer and the element was never flagged at all. A false negative, and the
        # invisible kind: nothing in the output hints that a check was skipped.
        if len(paths[label]) < 2:
            continue
        sample = evidence[label]
        ordered = sorted(files)

        # One file counted once however many writes it makes.
        by_directory = Counter(os.path.dirname(path) for path in paths[label])
        top_directory, top_count = by_directory.most_common(1)[0]
        family = (
            len(paths[label]) >= _FAMILY_MIN_WRITERS
            and top_count / len(paths[label]) >= _FAMILY_CONCENTRATION
        )

        flagged.append(
            Symbol(
                id=f"multi_writer_element:{label}",
                kind="multi_writer_element",
                label=label,
                sub=_base_sub(sample),
                file=sample.file,
                line=sample.line,
                status=Status.UNCERTAIN if family else Status.UNRESOLVED,
                snippet=sample.snippet,
                chain=ordered,
                note=(
                    _FAMILY_NOTE.format(count=len(paths[label]), top=top_count,
                                       directory=top_directory or 'one directory')
                    if family
                    else f"{_MULTI_WRITER_NOTE} Writers: {', '.join(ordered)}."
                ),
            )
        )
    return flagged


def _assemblable(label: str, stems: set[str]) -> str | None:
    """The literal prefix a runtime-built class name could have come from.

    `'pb-badge-' + kind` produces `pb-badge-success`, and the only thing in the source is
    the stem. A rule for the full name is therefore live and unprovable at the same time,
    so it must not be called dead - measured at 537 of one project's 4,415 otherwise
    unreferenced selectors, 12%.

    Only stems ending in a separator count. Requiring that is what keeps this from matching
    every class that happens to share three opening letters with another.
    """
    for cut in range(len(label) - 1, 2, -1):
        stem = label[:cut]
        if stem.endswith(("-", "_")) and stem in stems:
            return stem
    return None


def match_css_selectors(
    dom_selectors: list[Symbol],
    dom_attrs: list[Symbol],
    css_selectors: list[Symbol],
    tailwind_build_classes: set[str],
    usage_only: list[Symbol] | None = None,
) -> list[Edge]:
    """Three-way: what the DOM uses, what CSS defines, and what Tailwind generates.

    A class with no hand-written rule is not dead if the utility CSS build emits it -
    without that set, every Tailwind utility in every template reads as unresolved.
    """
    css_by_key = {(symbol.sub, symbol.label): symbol for symbol in css_selectors}
    used_keys: set[tuple[str, str]] = set()

    edges: list[Edge] = []
    # Elements JavaScript creates answer one of this function's two questions and not the
    # other. "Is this CSS rule used?" - yes, a rule matching a JS-built element is used, and
    # excluding them reported `#floating-combo-container` dead while combo_floating_text.js
    # assigns exactly that id. "Does this element have a rule?" - not a fair question of an
    # element usually styled inline or by an injected stylesheet, so they never get a
    # finding of their own.
    for symbol in list(usage_only or []):
        if symbol.label != "<dynamic>" and _base_sub(symbol) in ("id", "class"):
            used_keys.add((_base_sub(symbol), symbol.label))
            defined = css_by_key.get((_base_sub(symbol), symbol.label))
            if defined is not None:
                edges.append(Edge(from_id=symbol.id, to_id=defined.id, status=Status.CONNECTED))

    for symbol in list(dom_attrs) + list(dom_selectors):
        if symbol.label == "<dynamic>" or _base_sub(symbol) not in ("id", "class"):
            continue
        key = (_base_sub(symbol), symbol.label)
        used_keys.add(key)
        defined = css_by_key.get(key)
        if defined is not None:
            edges.append(Edge(from_id=symbol.id, to_id=defined.id, status=Status.CONNECTED))
        elif key[0] == "class" and symbol.label in tailwind_build_classes:
            edges.append(Edge(from_id=symbol.id, to_id=symbol.id, status=Status.CONNECTED))
        elif symbol.sub.startswith(("class:apply", "class:stem")):
            # A class JavaScript applies exists to be styled OR to be a hook the code
            # later queries. Having no stylesheet rule is therefore not a defect, so
            # these contribute evidence and never become findings themselves.
            continue
        else:
            edges.append(Edge(from_id=symbol.id, to_id=symbol.id, status=Status.UNRESOLVED))

    # Class-name stems seen in the source, so a rule whose name could be ASSEMBLED at
    # runtime is separated from one that could not. `extract_js_class_usages` already emits
    # the literal part of a concatenation as a class token, so the evidence is to hand.
    stems = {
        symbol.label for symbol in dom_selectors if symbol.sub.startswith("class:stem")
    } | {
        symbol.label for symbol in dom_attrs if symbol.label.endswith(("-", "_"))
    }

    for key, symbol in css_by_key.items():
        if key in used_keys:
            continue
        # A rule nothing references, where the name could still be built at runtime, is not
        # evidence of anything. Marked UNCERTAIN here rather than downgraded later, because
        # this is where the evidence is: the classifier cannot see class-name stems.
        if key[0] == "class" and _assemblable(symbol.label, stems):
            edges.append(Edge(from_id=symbol.id, to_id=symbol.id, status=Status.UNCERTAIN))
        else:
            edges.append(Edge(from_id=symbol.id, to_id=symbol.id, status=Status.UNUSED))
    return edges


def match_css_tokens(token_defs: list[Symbol], token_uses: list[Symbol]) -> list[Edge]:
    """Definitions against uses.

    Iterated, not keyed by label: a token used both bare and with a fallback is two
    symbols sharing one name, and a dict keyed by name silently dropped one of them.
    """
    defined = {symbol.label: symbol for symbol in token_defs}
    edges: list[Edge] = []
    used_labels: set[str] = set()

    for use in sorted(token_uses, key=lambda symbol: symbol.id):
        used_labels.add(use.label)
        target = defined.get(use.label)
        if target is not None:
            edges.append(Edge(from_id=use.id, to_id=target.id, status=Status.CONNECTED))
        elif use.sub == "token-fallback":
            # `var(--x, .08em)` carries its own value. It is not waiting on a definition,
            # and calling it unresolved reported 53 of this project's 63 "undefined
            # token" findings as bugs while the CSS was correct - Font Awesome alone
            # ships hundreds of them deliberately.
            edges.append(Edge(from_id=use.id, to_id=use.id, status=Status.CONNECTED))
        else:
            edges.append(Edge(from_id=use.id, to_id=use.id, status=Status.UNRESOLVED))

    edges += [
        Edge(from_id=symbol.id, to_id=symbol.id, status=Status.UNUSED)
        for name, symbol in sorted(defined.items())
        if name not in used_labels
    ]
    return edges
