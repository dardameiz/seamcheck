"""Template elements against the JavaScript that touches them."""

from __future__ import annotations

import os
import re
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
    # EVERY element with the key, not the first one. This was a dict keyed by
    # (sub, label) built with setdefault, so one selector reached one element - and a class
    # written six times in a template came out as one connected and five "nothing reaches
    # this", with the single `querySelectorAll` for it sitting in plain sight. The `All`
    # is the entire point of that method, and even `querySelector` REACHES FOR the class;
    # which element it happens to return is a runtime detail, not a fact about the markup.
    # 92 false claims on one project.
    attrs_by_key: dict[tuple[str, str], list[Symbol]] = {}
    for attr in dom_attrs:
        attrs_by_key.setdefault((attr.sub, attr.label), []).append(attr)

    edges: list[Edge] = []
    reached: set[str] = set()
    for selector in dom_selectors:
        # A runtime-built selector names nothing checkable, and a class JavaScript
        # applies needs no template attribute to match - the JS creates the element.
        if selector.label == "<dynamic>" or selector.sub.startswith(("class:apply", "class:stem")):
            continue
        matched = attrs_by_key.get((_base_sub(selector), selector.label)) or []
        # `id_<field>` is rendered by a Django form widget, never written in the template.
        # The declaration exists - in a ModelForm, in Python - and nothing in the HTML
        # layer can see it, so a template search for it looks like a search for nothing.
        # Uncertain rather than connected: the field may genuinely not exist, and this
        # cannot tell which without the form.
        if not matched and _base_sub(selector) == "id" and _FORM_ID_RE.match(selector.label):
            edges.append(Edge(from_id=selector.id, to_id=selector.id,
                              status=Status.UNCERTAIN, note=_UNCERTAIN_FORM_ID))
            continue
        if matched:
            for attr in matched:
                reached.add(attr.id)
                edges.append(Edge(from_id=selector.id, to_id=attr.id, status=Status.CONNECTED))
        elif not selector.sub.endswith(":evidence"):
            edges.append(Edge(from_id=selector.id, to_id=selector.id, status=Status.UNRESOLVED))

    # A data attribute is reached exactly four ways, and all four are read now: dataset.x,
    # getAttribute("data-x"), a [data-x] selector in JavaScript, and a [data-x] selector in
    # a stylesheet. So "nothing reads this" is a measurement here, where for an id or a
    # class it would not be - those are also reached by CSS cascade and by markup this scan
    # does not model. 516 of these sat in `uncertain` with no explanation at all, which is
    # the one thing a status word must never do.
    for attr in dom_attrs:
        if _base_sub(attr) == "data" and attr.id not in reached:
            edges.append(Edge(from_id=attr.id, to_id=attr.id, status=Status.UNUSED))
    return edges


_SAME_FILE_NOTE = (
    "Two places in {file} write this element - {who}. One file, so the gate for "
    "\"more than one file writes this\" never fired, and this is the shape that hides "
    "best: whichever runs last wins, and a fix applied to one of them survives review "
    "because the other is a screen away rather than a file away."
)


def _same_file_writers(
    label: str,
    file_paths: set[str],
    inside: dict[str, set[tuple[str, str]]],
    evidence: dict[str, Symbol],
) -> Symbol | None:
    """The same element written from two functions in ONE file.

    A different and weaker claim than two files fighting, so it gets its own status:
    a module legitimately writes an element from a setup path and an update path. What
    makes it worth saying at all is that it is the exact shape of the display bugs that
    come back after being fixed - two functions, one element, last-write-wins - and the
    file-count gate above can never see it.
    """
    sites = inside.get(label) or set()
    if len(sites) < 2:
        return None
    sample = evidence[label]
    who = ", ".join(sorted(name for _, name in sites)[:4])
    return Symbol(
        id=f"multi_writer_element:{label}",
        kind="multi_writer_element",
        label=label,
        sub=_base_sub(sample),
        file=sample.file,
        line=sample.line,
        # Uncertain, not unresolved: one module writing an element twice is ordinary
        # until a person looks. It is a lead, and it is the only lead there is.
        status=Status.UNCERTAIN,
        snippet=sample.snippet,
        chain=sorted(name for _, name in sites),
        note=_SAME_FILE_NOTE.format(
            file=os.path.basename(next(iter(file_paths))) if file_paths else "one file",
            who=who,
        ),
    )


# Class families shipped by a stylesheet a project LINKS rather than contains. Every one
# of these is a library whose CSS normally arrives from a CDN, so by construction no file
# in the repo defines `fa-home` or `bi-check` - and reporting them is reporting the
# absence of a file again.
# Django renders every form widget with `id="id_<field>"`. So does its admin, for every
# field on every ModelForm - and none of it appears in any template.
_UNCERTAIN_ASSEMBLABLE = (
    "Nothing references this rule, but its name could be assembled at runtime from a "
    "prefix that does appear in the JavaScript - so it is live and unprovable at once, "
    "and calling it dead would be a guess."
)

_UNCERTAIN_FORM_ID = (
    "Django renders this id from a form widget, so it is declared in Python and never "
    "written in a template. Searching the HTML for it is searching where it was never "
    "going to be; confirming it needs the form, which this scan does not read."
)

_FORM_ID_RE = re.compile(r"\Aid_[a-z][\w]*\Z")

# Utility-first class shapes, for when the Tailwind build output is not available to name
# them exactly. Deliberately narrow: these are prefixes no hand-written component class
# starts with, followed by a value.
_UTILITY_RE = re.compile(
    r"\A(?:text|bg|border|ring|fill|stroke|from|via|to|shadow|opacity|rounded)-"
    r"(?:\[.*\]|[a-z]+(?:-\d{2,3})?|\d+|full|none|sm|md|lg|xl|\dxl)\Z"
)


def _is_utility(label: str, utility_classes: frozenset[str] | None) -> bool:
    """Whether this class is a framework utility rather than a name someone chose."""
    if utility_classes and label in utility_classes:
        return True
    return bool(_UTILITY_RE.match(label))


_CDN_CLASS_PREFIXES = (
    "fa-", "fas", "far", "fab", "fal", "fad", "fa ",       # Font Awesome
    "bi-", "bi ",                                          # Bootstrap Icons
    "mdi-", "mdi ",                                        # Material Design Icons
    "glyphicon",                                           # Bootstrap 3
    "material-icons", "material-symbols",                  # Material
    "ti-", "ti ",                                          # Tabler
    "icon-",                                               # the common generic
    "swiper-", "leaflet-", "flatpickr-", "select2-",        # widgets that ship their own CSS
    "tox-", "cke_", "ql-", "fc-",                          # TinyMCE, CKEditor, Quill, FullCalendar
)


def _from_a_cdn(label: str, extra: tuple[str, ...]) -> bool:
    """Whether this class belongs to a library whose stylesheet is not in the repo.

    The oracle is per FAMILY, not per project. `styles_are_local` asks "is there a
    stylesheet here at all", which is the right question for a project whose CSS is
    entirely a CDN link - and the wrong one for the ordinary mixed case: 199 local
    stylesheets AND a Font Awesome tag. There the oracle says local, and 453 icon classes
    get judged against a file that was never going to define them.
    """
    return label.startswith(_CDN_CLASS_PREFIXES) or (bool(extra) and label.startswith(extra))


def _one_spelling(path: str, repo_root: str = "") -> str:
    """`/abs/pointless/x.js`, `./pointless/x.js` and `pointless/x.js` are one file.

    graph.shorten() already knows this, but it runs at EXPORT - long after this detector
    has counted the two spellings as two writers and flagged every element the file
    touches. discover_js_files() joins against a project root of ".", the evidence walk
    does not, and both feed this function. Measured on the reference project: 65 of 122
    findings, from 7 files, were one file counted twice - and every one of them said
    "more than one file writes this element" and then named exactly one file, which is
    the self-contradiction that gives the class away.
    """
    cleaned = os.path.normpath(path)
    # An ABSOLUTE path and a repo-relative one are the same file too, and that half was
    # missed: the entry-graph reader returns absolute paths and the tree walk returns
    # relative ones, both feed this, and normpath alone leaves them different strings.
    # Measured on the reference project after the `./x` fix shipped: 299 of 370
    # multi-writer findings - 81% - were one file wearing both spellings.
    if repo_root and os.path.isabs(cleaned):
        try:
            relative = os.path.relpath(cleaned, os.path.abspath(repo_root))
            if not relative.startswith(".."):
                cleaned = relative
        except ValueError:
            pass
    return cleaned.replace(os.sep, "/")


def detect_multi_writers(
    dom_selectors: list[Symbol],
    repo_root: str = "",
    utility_classes: frozenset[str] | None = None,
) -> list[Symbol]:
    # Whole paths, not basenames: the directory is what tells a family apart from a fight,
    # and two same-named files in different directories are two writers either way. The
    # parallel basename map this used to keep is gone - the gate read one collection and
    # the note read the other, which is exactly how a finding could contradict itself.
    paths: dict[str, set[str]] = defaultdict(set)
    # Writers WITHIN one file, keyed (file, enclosing function). Two functions in the same
    # module writing the same element is the same disease and was invisible: the gate asks
    # for two distinct FILES, so `_reorderWindow` and `_syncPinnedRow` both writing
    # `.lr-row-rank` in one file could never be flagged.
    inside: dict[str, set[tuple[str, str]]] = defaultdict(set)
    evidence: dict[str, Symbol] = {}
    for selector in dom_selectors:
        if not selector.sub.endswith(":write") or selector.label == "<dynamic>":
            continue
        # A utility class has no owner. `text-yellow-400` is touched by every file that
        # colours something, which is what a utility IS - reporting three files as fighting
        # over it describes Tailwind, not a defect.
        if _is_utility(selector.label, utility_classes):
            continue
        canonical = _one_spelling(selector.file, repo_root)
        paths[selector.label].add(canonical)
        enclosing = selector.chain[-1] if len(selector.chain) > 1 else ""
        if enclosing:
            inside[selector.label].add((canonical, enclosing))
        evidence.setdefault(selector.label, selector)

    flagged: list[Symbol] = []
    for label, file_paths in sorted(paths.items()):
        if len(file_paths) < 2:
            # One file, but possibly two functions in it. A separate, weaker claim.
            single = _same_file_writers(label, file_paths, inside, evidence)
            if single is not None:
                flagged.append(single)
            continue
        sample = evidence[label]
        # Basenames read better, but only while they stay distinct. Two files called
        # stats.js in different directories are the case the path gate exists for, and
        # naming both "stats.js" would describe it as one writer.
        ordered = sorted(file_paths)
        names = [os.path.basename(path) for path in ordered]
        if len(set(names)) == len(names):
            ordered = names

        # One file counted once however many writes it makes.
        by_directory = Counter(os.path.dirname(path) for path in file_paths)
        top_directory, top_count = by_directory.most_common(1)[0]
        family = (
            len(file_paths) >= _FAMILY_MIN_WRITERS
            and top_count / len(file_paths) >= _FAMILY_CONCENTRATION
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
                    _FAMILY_NOTE.format(count=len(file_paths), top=top_count,
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
    styles_are_local: bool = True,
    vendor_prefixes: tuple[str, ...] = (),
) -> list[Edge]:
    """Three-way: what the DOM uses, what CSS defines, and what Tailwind generates.

    A class with no hand-written rule is not dead if the utility CSS build emits it -
    without that set, every Tailwind utility in every template reads as unresolved.

    `styles_are_local` is the same question the Supabase reader had to learn to ask: is
    there an oracle here at all? A project whose entire stylesheet is a CDN `<link>` has no
    CSS in the repository, so "nothing styles this class" is drawn from the absence of a
    file rather than from evidence. Measured on a Flask project that loads Bootstrap from
    jsdelivr and ships no CSS of its own: 102 of its 167 findings were `nav-link`, `badge`,
    `page-item` and friends - every one of them styled perfectly well, by a stylesheet the
    scan was never shown.
    """
    # A project's own rule outranks a vendor rule of the same name, so project rules are
    # written last. The vendor prefix is stripped from the key: `("class", "form-row")` is
    # what the DOM side asks for, whoever defined it.
    css_by_key: dict[tuple[str, str], Symbol] = {}
    for symbol in sorted(css_selectors, key=lambda s: not s.sub.startswith("vendor:")):
        css_by_key[(symbol.sub.removeprefix("vendor:"), symbol.label)] = symbol
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
        # A Django form widget's id is declared in Python, not in any stylesheet or
        # template, so it can be judged by neither. The exemption belongs here as well as
        # in the DOM matcher: this loop reaches the same selectors and would overrule it.
        if _base_sub(symbol) == "id" and _FORM_ID_RE.match(symbol.label):
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
            from_cdn = _from_a_cdn(symbol.label, vendor_prefixes)
            if not styles_are_local:
                edges.append(Edge(
                    from_id=symbol.id, to_id=symbol.id, status=Status.UNCERTAIN,
                    note=("No stylesheet in this repository defines this class, and no "
                          "local CSS was found at all - so there is nothing here to check "
                          "it against. Styles served from a CDN or built elsewhere are "
                          "invisible to a source scan."),
                ))
            elif from_cdn:
                edges.append(Edge(
                    from_id=symbol.id, to_id=symbol.id, status=Status.UNCERTAIN,
                    note=("This class belongs to a family loaded from a CDN (an icon font "
                          "or a vendor stylesheet), whose rules are not in the repository. "
                          "The class is almost certainly real; nothing here can confirm it."),
                ))
            else:
                edges.append(Edge(
                    from_id=symbol.id, to_id=symbol.id, status=Status.UNRESOLVED,
                ))

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
        # A framework's own stylesheet - Django admin's base.css, shipped in site-packages
        # - is read so the classes it defines resolve, and is never judged: a vendor rule
        # this project does not happen to use is not this project's dead code.
        if symbol.sub.startswith("vendor:"):
            edges.append(Edge(
                from_id=symbol.id, to_id=symbol.id, status=Status.UNCERTAIN,
                note=("A rule from a framework's own stylesheet, read so that classes it "
                      "defines resolve. Whether this project uses it is not this "
                      "project's business, so it is never judged."),
            ))
            continue
        # A rule nothing references, where the name could still be built at runtime, is not
        # evidence of anything. Marked UNCERTAIN here rather than downgraded later, because
        # this is where the evidence is: the classifier cannot see class-name stems.
        if key[0] == "class" and _assemblable(symbol.label, stems):
            edges.append(Edge(
                from_id=symbol.id, to_id=symbol.id, status=Status.UNCERTAIN,
                note=_UNCERTAIN_ASSEMBLABLE,
            ))
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
