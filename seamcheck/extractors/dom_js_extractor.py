"""DOM elements JavaScript touches, and whether it writes them or only reads them."""

from __future__ import annotations

import itertools
import os
import re

from seamcheck.extractors.js_extractor import _stamp, _walk, iter_parsed, register_cache
from seamcheck.graph import Status, Symbol
from seamcheck.nodetools import node_line

_SELECTOR_CALLEES = frozenset({"getElementById", "querySelector", "querySelectorAll", "closest"})
# A string that is nothing but an attribute name. Two segments minimum, so `data-` alone
# and `data-x` from a truncated interpolation are not read as names.
_DATA_NAME_RE = re.compile(r"^data-[a-z][a-z0-9]*(?:-[a-z0-9]+)+$", re.IGNORECASE)
# The other two ways JavaScript reaches a data attribute. Only `[data-x]` selector syntax
# was read, so a template's `data-can-change` looked like an attribute nothing touches while
# the code read it as `dataset.canChange` or `getAttribute("data-can-change")` - 584 of them
# on the project measured, every one sitting in `uncertain` with no explanation at all.
_ATTRIBUTE_CALLEES = frozenset(
    {"getAttribute", "setAttribute", "hasAttribute", "removeAttribute"}
)
_CAMEL_BOUNDARY = re.compile(r"(?<!^)(?=[A-Z])")

_SELECTORS_CACHE: dict[tuple, tuple[dict | None, list]] = register_cache({})


def _dataset_name(camel: str) -> str:
    """`buttonType` -> `button-type`, the HTML mapping the browser itself applies."""
    return _CAMEL_BOUNDARY.sub("-", camel).lower()

# Assigning to one of these, or to anything under .style / .dataset, mutates the element.
_WRITE_PROPERTIES = frozenset(
    {"textContent", "innerHTML", "innerText", "value", "src", "href", "checked", "disabled"}
)
_WRITE_METHODS = frozenset(
    {"add", "remove", "toggle", "setAttribute", "append", "prepend", "replaceChildren", "insertAdjacentHTML"}
)
_WRITE_NAMESPACES = frozenset({"style", "dataset", "classList"})

_TOKEN_RE = re.compile(r"#([\w-]+)|\.([\w-]+)|\[data-([\w-]+)")


# `[data-tab="${x}"]` and `'[data-tab="' + x + '"]'` - the attribute name is static even
# when the value is not.
_PARTIAL_ATTR_RE = re.compile(r"\[\s*data-([\w-]+)")


def _partial_attr_names(node: dict) -> list[str]:
    """Attribute names readable from a selector that is otherwise built at runtime."""
    text = ""
    if node.get("type") == "TemplateLiteral":
        text = "".join(
            ((q.get("value") or {}).get("cooked") or "") for q in (node.get("quasis") or [])
        )
    elif node.get("type") == "BinaryExpression":
        text = "".join(_concat_shape(node))
    return sorted(set(_PARTIAL_ATTR_RE.findall(text)))


def _selector_tokens(callee_name: str, raw: str) -> list[tuple[str, str]]:
    """(sub, label) pairs a selector string pins down."""
    if callee_name == "getElementById":
        return [("id", raw)] if raw else []
    tokens: list[tuple[str, str]] = []
    for element_id, class_name, data_name in _TOKEN_RE.findall(raw):
        if element_id:
            tokens.append(("id", element_id))
        elif class_name:
            tokens.append(("class", class_name))
        elif data_name:
            tokens.append(("data", data_name))
    return tokens


def _property_names(node) -> set[str]:
    return {
        (inner.get("property") or {}).get("name")
        for inner, _ in _walk(node)
        if inner.get("type") == "MemberExpression" and (inner.get("property") or {}).get("name")
    }


def _selector_calls(node) -> list[dict]:
    return [
        inner
        for inner, _ in _walk(node)
        if inner.get("type") == "CallExpression"
        and (inner.get("callee") or {}).get("type") == "MemberExpression"
        and ((inner.get("callee") or {}).get("property") or {}).get("name") in _SELECTOR_CALLEES
    ]


def _base_object_name(node: dict) -> str | None:
    """The variable a member chain hangs off: `el.style.color` -> 'el', `this.box.x` -> 'this.box'."""
    current = node
    while isinstance(current, dict) and current.get("type") == "MemberExpression":
        obj = current.get("object") or {}
        if obj.get("type") == "Identifier":
            return obj["name"]
        if obj.get("type") == "MemberExpression" and (obj.get("object") or {}).get("type") == "ThisExpression":
            return f"this.{(obj.get('property') or {}).get('name')}"
        current = obj
    return None


def _selector_bindings(ast_root: dict) -> dict[str, dict]:
    """Variables (and `this.x` properties) holding the result of a selector call.

    Real code almost never writes in the same statement it queries in: it binds the
    element once and mutates it later. Matching only same-statement writes found 70 of
    them across this project's 2,300 selectors.
    """
    bindings: dict[str, dict] = {}
    for node, _ in _walk(ast_root):
        node_type = node.get("type")
        if node_type == "VariableDeclarator":
            name = (node.get("id") or {}).get("name")
            calls = _selector_calls(node.get("init") or {})
            if name and calls:
                bindings[name] = calls[0]
        elif node_type == "AssignmentExpression":
            target = node.get("left") or {}
            if (target.get("object") or {}).get("type") == "ThisExpression":
                calls = _selector_calls(node.get("right") or {})
                if calls:
                    bindings[f"this.{(target.get('property') or {}).get('name')}"] = calls[0]
    return bindings


def _writing_call_ids(ast_root: dict) -> set[int]:
    """Selector calls whose element is mutated, directly or through a binding."""
    bindings = _selector_bindings(ast_root)
    writing: set[int] = set()
    for node, _ in _walk(ast_root):
        node_type = node.get("type")
        if node_type == "AssignmentExpression":
            target = node.get("left") or {}
            names = _property_names(target)
            final = (target.get("property") or {}).get("name")
            if final in _WRITE_PROPERTIES or names & _WRITE_NAMESPACES:
                writing.update(id(call) for call in _selector_calls(target))
                bound = bindings.get(_base_object_name(target) or "")
                if bound is not None:
                    writing.add(id(bound))
        elif node_type == "CallExpression":
            callee = node.get("callee") or {}
            if (callee.get("property") or {}).get("name") in _WRITE_METHODS:
                receiver = callee.get("object") or {}
                writing.update(id(call) for call in _selector_calls(receiver))
                bound = bindings.get(_base_object_name(receiver) or "")
                if bound is not None:
                    writing.add(id(bound))
    return writing


def _dom_selectors_in(path: str, ast_root: dict, line_offset: int = 0) -> list[Symbol]:
    """Every DOM query in one parsed unit, whether that unit was a file or a <script>.

    Memoised per parsed unit: the pipeline asks three times - entry files for claims,
    the rest of the tree for evidence, the whole tree again for multi-writer detection -
    and the answer for a given AST does not change between them.

    Keyed by the file's stamp, not by the tree: this memo used to hold a reference to
    every tree it had answered for, which made it a second, unbudgeted copy of the whole
    parse cache - 1.8 GB on a 21,000-file monorepo, after the parse cache itself had
    stopped at its budget. An inline <script> has no stamp of its own; its tree lives in
    the inline cache for the scan, so its id is stable and the tree is kept to prove it.
    """
    stamp = _stamp(path)
    if stamp is not None:
        key, guard = (path, stamp, line_offset), None
    else:
        key, guard = (path, id(ast_root), line_offset), ast_root
    hit = _SELECTORS_CACHE.get(key)
    if hit is not None and hit[0] is guard:
        return list(hit[1])
    symbols = _dom_selectors_in_uncached(path, ast_root, line_offset)
    _SELECTORS_CACHE[key] = (guard, symbols)
    return list(symbols)


def _dom_selectors_in_uncached(path: str, ast_root: dict, line_offset: int = 0) -> list[Symbol]:
    symbols: list[Symbol] = []
    writing = _writing_call_ids(ast_root)
    basename = os.path.basename(path)

    def _data_access(name: str, line, enclosing: str, snippet: str) -> None:
        # One per (name, line): `getAttribute('data-x')` is now read twice - once as an
        # attribute call, once as a plain string - and two symbols under one id is two
        # rows in every list for one line of source.
        key = f"{name}:{line}"
        if key in _data_seen:
            return
        _data_seen.add(key)
        symbols.append(
            Symbol(
                id=f"dom_selector:data:{name}:{path}:{line}", kind="dom_selector",
                label=name, sub="data:read", file=path, line=line,
                status=Status.UNCERTAIN, snippet=snippet,
                chain=[basename, enclosing] if enclosing else [basename], owner=enclosing,
                note="Reads a data attribute. Evidence that the attribute is used; the "
                     "verdict belongs to the attribute, not to this read.",
            )
        )

    # Nodes that are the TARGET of an assignment. `el.dataset.sectionIndex = si` is not a
    # read of a data attribute, it is the line that CREATES it - and recording it as a read
    # made the write into a search for an element that has the attribute, which nothing
    # declares, so the very line that defines it was reported as reaching nothing. Four of
    # one project's remaining false claims were exactly this, each pointing at its own
    # definition. The definitions extractor already reads these; only the read side was
    # double-counting them.
    _data_seen: set[str] = set()
    assigned = set()
    for node, _enclosing in _walk(ast_root):
        node_type = node.get("type")
        if node_type in ("AssignmentExpression", "UpdateExpression"):
            target = node.get("left") or node.get("argument")
            if isinstance(target, dict):
                assigned.add(id(target))

    for node, enclosing in _walk(ast_root):
        raw = node_line(node)
        at = (raw + line_offset) if raw else raw

        # A bare string that spells an attribute name IS a reference to it. Mapping
        # tables are written this way all the time -
        # `[['daily_hours_active', 'data-modal-daily-hours'], ...]`, then a loop that
        # reads each pair - and the attribute is never named by `dataset.x` or
        # `getAttribute` anywhere. Read only as EVIDENCE: it connects an attribute the
        # markup already declares, and can never make a claim of its own, so a string
        # that happens to look like one costs nothing.
        if node.get("type") == "Literal" and isinstance(node.get("value"), str):
            text = node["value"]
            if _DATA_NAME_RE.match(text):
                _data_access(text[5:], at, enclosing, f"'{text}'")

        # el.dataset.buttonType -> the data-button-type attribute
        if node.get("type") == "MemberExpression" and id(node) not in assigned:
            owner = node.get("object") or {}
            if (owner.get("property") or {}).get("name") == "dataset":
                accessed = (node.get("property") or {}).get("name")
                if accessed:
                    _data_access(_dataset_name(accessed), at, enclosing,
                                 f"dataset.{accessed}")

        if node.get("type") != "CallExpression":
            continue
        callee = node.get("callee") or {}
        callee_name = (callee.get("property") or {}).get("name")

        # getAttribute("data-can-change") and friends
        if callee_name in _ATTRIBUTE_CALLEES:
            first = (node.get("arguments") or [{}])[0]
            if first.get("type") == "Literal" and isinstance(first.get("value"), str):
                raw_name = first["value"]
                if raw_name.startswith("data-") and len(raw_name) > 5:
                    _data_access(raw_name[5:], at, enclosing,
                                 f"{callee_name}('{raw_name}')")

        if callee_name not in _SELECTOR_CALLEES:
            continue

        arguments = node.get("arguments") or []
        first = arguments[0] if arguments else {}
        raw_line = node_line(node)
        line = (raw_line + line_offset) if raw_line else raw_line
        access = "write" if id(node) in writing else "read"
        chain = [basename, enclosing] if enclosing else [basename]

        if first.get("type") != "Literal" or not isinstance(first.get("value"), str):
            # The LITERAL HALF of a built selector is still evidence. In
            # `[data-tab="${tabName}"]` the value is unknown and the attribute NAME is
            # right there in the source - so crediting nothing reported every live tab
            # control on a dashboard as markup nothing reads: 638 of them on one project,
            # each with its reader sixty lines further down the same file.
            #
            # The same split the class stems already make: a name that is partly static is
            # partly evidence.
            for name in _partial_attr_names(first):
                _data_access(name, line, enclosing,
                             f'{callee_name}("[data-{name}=...]")')
            symbols.append(
                Symbol(
                    id=f"dom_selector:dynamic:{path}:{line}", kind="dom_selector",
                    label="<dynamic>", sub=f"dynamic:{access}", file=path, line=line,
                    status=Status.UNCERTAIN, snippet=f"{callee_name}(<built at runtime>)",
                    chain=chain,
                    note="Selector built at runtime -- cannot be tied to a template element.",
                )
            )
            continue

        raw = first["value"]
        for sub, label in _selector_tokens(callee_name, raw):
            symbols.append(
                Symbol(
                    id=f"dom_selector:{sub}:{label}:{path}:{line}", kind="dom_selector",
                    label=label, sub=f"{sub}:{access}", file=path, line=line,
                    status=Status.UNCERTAIN, snippet=f"{callee_name}('{raw}')",
                    chain=chain, note="",
                )
            )
    return symbols


def extract_dom_selectors(
    js_files: list[str],
    template_files: list[str] | None = None,
    declared: dict[str, str] | None = None,
) -> list[Symbol]:
    """DOM queries, from .js files AND from the JavaScript templates write inline.

    Inline <script> used to be invisible here. It is not a rounding error: the project this
    was measured on keeps 202 KB of it, and every getElementById inside it was unread - so
    148 template elements that JavaScript demonstrably uses were reported as elements
    nothing reaches, 17% of everything the scan claimed.

    `declared` maps each name the markup renders to what it is - `id`, `class` or `data`.
    When given, a bare string literal that spells one of them is recorded as evidence -
    see `_named_in_a_constant`.
    """
    from seamcheck.extractors.js_extractor import parse_inline_blocks

    symbols: list[Symbol] = []
    for path, ast_root in iter_parsed([f for f in js_files if os.path.isfile(f)]):
        symbols += _dom_selectors_in(path, ast_root)
        symbols += _named_in_a_constant(path, ast_root, declared)
    for template, ast_root, offset in parse_inline_blocks(template_files or []):
        symbols += _dom_selectors_in(template, ast_root, line_offset=offset)
        symbols += _named_in_a_constant(template, ast_root, declared, line_offset=offset)
    return symbols


def _named_in_a_constant(
    path: str, ast_root: dict, declared: dict[str, str] | None, line_offset: int = 0
) -> list[Symbol]:
    """A string that spells an element the markup renders, held in a variable.

        var COUNTDOWN_ID = 'arena-next-season-countdown';
        ...
        document.getElementById(COUNTDOWN_ID);

    The lookup names a variable, so the reader saw `getElementById(<runtime value>)` and
    the element - plainly rendered, plainly used - was reported as one nothing reaches.
    Following the variable is data-flow analysis this tool does not do; recognising the
    string is not.

    Bounded on purpose, and in two ways. Only strings that match a name the markup
    ALREADY declares are recorded, so this can never invent an element and the output is
    at most one symbol per declared name per file rather than one per string literal -
    the reference project has 48,000 symbols and hundreds of thousands of strings.
    And it is emitted as `:evidence`, so it satisfies the element it names and can never
    become a claim of its own: a string is not proof that a lookup happened, only that
    the name is live in this file.
    """
    if not declared:
        return []
    symbols: list[Symbol] = []
    seen: set[str] = set()
    basename = os.path.basename(path)
    for node, enclosing in _walk(ast_root):
        if node.get("type") != "Literal":
            continue
        text = node.get("value")
        kind = declared.get(text) if isinstance(text, str) else None
        if not kind:
            continue
        raw = node_line(node)
        line = (raw + line_offset) if raw else raw
        symbol_id = f"dom_selector:{kind}:string:{text}:{path}"
        if symbol_id in seen:
            continue
        seen.add(symbol_id)
        symbols.append(
            Symbol(
                id=symbol_id, kind="dom_selector", label=text,
                sub=f"{kind}:string:evidence",
                file=path, line=line, status=Status.UNCERTAIN, snippet=f"'{text}'",
                chain=[basename, enclosing] if enclosing else [basename], owner=enclosing,
                note="A string in this file spells the name of an element the markup "
                     "renders - held in a variable, most likely, and used through it. "
                     "Evidence that the name is live here; not a claim that this line "
                     "looks it up.",
            )
        )
    return symbols


_SET_PROPERTY = "setProperty"
# The other half of the CSS-OM. setProperty WRITES a token; getPropertyValue READS one, and
# reading is a use - so a property defined in CSS and consumed only from JavaScript was
# reported as a token nothing reads. Four of them on the project this was measured against,
# every one working correctly.
_GET_PROPERTY = "getPropertyValue"
# CSS-in-JS: a stylesheet built as a string and injected still consumes tokens, and a
# CSS-only scan cannot see it. Without this, a token set by JS and read by JS-embedded
# CSS is reported unused while working perfectly.
# The comma decides whether this is a question or a statement: `var(--x)` asks for a
# definition, `var(--x, 50)` supplies its own answer. Injected CSS uses both forms, and
# reading only the name reported the second as a broken reference.
_VAR_USE_RE = re.compile(r"var\(\s*(--[\w-]+)\s*(,)?")


def extract_js_css_tokens(
    js_files: list[str], template_files: list[str] | None = None
) -> list[Symbol]:
    """CSS custom properties JavaScript reaches through the CSS-OM, either way.

    `setProperty` defines one at runtime; a token defined only that way looks undefined to
    a CSS-only scan - 64 of 128 `var(--x)` references with no CSS definition were set this
    way on the project measured. `getPropertyValue` reads one, which is a use, and without
    it a property defined in CSS and consumed only from JavaScript reads as a token nothing
    reads. Both are false claims about working code.
    """
    from seamcheck.extractors.js_extractor import parse_inline_blocks

    symbols: list[Symbol] = []
    seen: set[str] = set()

    # A generator, not a list: a list here held every tree the budget had refused to
    # cache, 1.1 GB on a 21,000-file monorepo, for the length of the loop.
    units = itertools.chain(
        ((path, ast_root, 0)
         for path, ast_root in iter_parsed([f for f in js_files if os.path.isfile(f)])),
        parse_inline_blocks(template_files or []),
    )

    for path, ast_root, line_offset in units:
        def _line(node, _offset=line_offset):
            raw = node_line(node)
            return (raw + _offset) if raw else raw

        for node, enclosing in _walk(ast_root):
            if node.get("type") != "CallExpression":
                continue
            callee_name = ((node.get("callee") or {}).get("property") or {}).get("name")
            if callee_name not in (_SET_PROPERTY, _GET_PROPERTY):
                continue

            arguments = node.get("arguments") or []
            first = arguments[0] if arguments else {}
            # A template literal or variable name is built at runtime; guessing which
            # token it produces would trade one false claim for another.
            if first.get("type") != "Literal" or not isinstance(first.get("value"), str):
                continue
            name = first["value"]
            if not name.startswith("--"):
                continue

            reading = callee_name == _GET_PROPERTY
            kind = "css_token_use" if reading else "css_token_def"
            symbol_id = f"{kind}:token:{name}"
            if symbol_id in seen:
                continue
            seen.add(symbol_id)
            basename = os.path.basename(path)
            symbols.append(
                Symbol(
                    id=symbol_id, kind=kind, label=name, sub="token", file=path,
                    line=_line(node),
                    status=Status.UNCERTAIN,
                    snippet=f"{callee_name}('{name}'{'' if reading else ', ...'})",
                    chain=[basename, enclosing] if enclosing else [basename], owner=enclosing,
                    note="Read from JavaScript through the CSS-OM." if reading
                    else "Defined at runtime by JavaScript, not in any stylesheet.",
                )
            )

        # Token *uses* inside any string this module holds - injected stylesheets,
        # template literals, inline style text.
        for node, _enclosing in _walk(ast_root):
            for text in _literal_strings(node):
                for name, comma in _VAR_USE_RE.findall(text):
                    sub = "token-fallback" if comma else "token"
                    symbol_id = f"css_token_use:{sub}:{name}"
                    if symbol_id in seen:
                        continue
                    seen.add(symbol_id)
                    symbols.append(
                        Symbol(
                            id=symbol_id, kind="css_token_use", label=name, sub=sub,
                            file=path, line=_line(node),
                            status=Status.UNCERTAIN,
                            snippet=f"var({name}, ...)" if comma else f"var({name})",
                            chain=[os.path.basename(path), name],
                            note="Consumed by CSS that JavaScript injects."
                            + (" Resolves to its own fallback." if comma else ""),
                        )
                    )
    return symbols


# `contains` reads rather than writes, and it is still evidence: code asking whether an
# element carries a class is code that knows the class exists. Excluding it reported
# `watermelon-particle--eat` as a dead rule while watermelon.js tests for it by name.
_CLASS_LIST_METHODS = frozenset({"add", "remove", "toggle", "replace", "contains"})
# The closing quote is optional: a template literal splits at every ${...}, so a
# quasi routinely ends mid-attribute (`<div class="row ` before the hole).
# `\b` is not enough on the left: between the `-` of `data-test-id` and its `id` there IS
# a word boundary, so `[data-test-id="swatch-preview"]` - a SELECTOR, sitting in a string -
# was read as markup declaring `id="swatch-preview"`. Inventing an element is worse than
# missing one: a real finding about a missing element goes quiet, because the scan now
# believes something declares it.
_CLASS_ATTR_RE = re.compile(r"""(?<![\w-])class\s*=\s*["']([^"']*)(?:["']|$)""")
# The id half of the same idea. Only classes were ever read out of generated markup, so an
# element JavaScript builds with an id was invisible - and then every querySelector looking
# for it reported an element no template renders.
_ID_ATTR_RE = re.compile(r"""(?<![\w-])id\s*=\s*["']([^"'\s]+)["']""")
_ID_PROPERTIES = frozenset({"id", "className"})
# A class token is written by a human or a utility framework; anything carrying JS
# punctuation is an interpolation fragment, not a name.
_NOT_A_CLASS_RE = re.compile(r"""[${}()'"^,;<>=]|^\W+$""")


def _class_tokens(raw: str) -> list[str]:
    return [token for token in raw.split() if token and not _NOT_A_CLASS_RE.search(token)]


# A name whose last piece is cut off by an interpolation: the stem of a family assembled
# at runtime. `pb-badge-${kind}` and `'pb-badge-' + kind` both leave `pb-badge-` in the
# source and nothing else, so a rule for the whole name is live and unprovable at once.
_STEM_RE = re.compile(r"(?:^|\s)([A-Za-z_][\w-]*[-_])(?:\$\{\}|$)")


def _literal_strings(node: dict) -> list[str]:
    """String content a node contributes statically.

    A template literal's quasis are static text; its `${...}` holes are not, so the
    static neighbours are kept and the holes contribute nothing rather than a guess.

    A `+` concatenation is treated the same way, with the same "${}" stand-in for whatever
    is not a literal. It used to contribute NOTHING, so `'badge ' + kind` gave up the whole
    token `badge` as well as the dynamic part - and `'pb-badge-' + kind` left no trace that
    a pb-badge- family exists at all.
    """
    node_type = node.get("type")
    if node_type == "Literal" and isinstance(node.get("value"), str):
        return [node["value"]]
    if node_type == "TemplateLiteral":
        # Join the static chunks with a literal "${}" so any token touching an
        # interpolation keeps the marker and is rejected as a name. `bb-spark-${i}`
        # must not yield the fragment "bb-spark-"; `ab-star ${x}` must still yield
        # "ab-star", because that token is whole.
        return ["${}".join((quasi.get("value") or {}).get("raw", "") for quasi in node.get("quasis") or [])]
    if node_type == "BinaryExpression" and node.get("operator") == "+":
        return ["".join(_concat_shape(node))]
    return []


def _concat_shape(node: dict) -> list[str]:
    """A `+` chain flattened to its static text, with "${}" where an operand is dynamic."""
    if node.get("type") == "BinaryExpression" and node.get("operator") == "+":
        return _concat_shape(node.get("left") or {}) + _concat_shape(node.get("right") or {})
    static = _literal_strings(node)
    return [static[0]] if static else ["${}"]


def _class_stems(node: dict) -> list[str]:
    """Class-name prefixes this node leaves behind, e.g. `pb-badge-` from `pb-badge-${k}`."""
    return [
        stem
        for text in _literal_strings(node)
        for stem in _STEM_RE.findall(text)
    ]


def extract_js_class_usages(
    js_files: list[str], template_files: list[str] | None = None
) -> list[Symbol]:
    """CSS classes JavaScript puts on elements: className, classList, setAttribute, markup.

    A stylesheet rule referenced only this way looks unreferenced to a scan that reads
    only querySelector() and template class= attributes. Measured on this project, that
    was 3,205 unread sites and 5,318 selectors with no evidence either way.

    Emitted with sub "class:apply" rather than ":write" deliberately: applying a class is
    evidence the rule is live, but twenty modules adding `.active` is normal, so these
    must not reach multi-writer detection.
    """
    from seamcheck.extractors.js_extractor import parse_inline_blocks

    symbols: list[Symbol] = []
    seen: set[str] = set()

    def _record(name: str, path: str, line, enclosing: str, snippet: str) -> None:
        # One per class PER FILE, not per site. These symbols are evidence that a rule is
        # live - they are uncertain by design and can never become a finding - so a second
        # copy of `.btn` from line 40 of the same file adds nothing and costs a node. On a
        # React monorepo the per-site keying produced 20,041 of them where the whole rest
        # of the graph was 1,100, which is a slower map and a misleading uncertain count
        # for no extra evidence at all.
        symbol_id = f"dom_selector:class:{name}:{path}"
        if symbol_id in seen:
            return
        seen.add(symbol_id)
        basename = os.path.basename(path)
        symbols.append(
            Symbol(
                id=symbol_id, kind="dom_selector", label=name, sub="class:apply", file=path,
                line=line, status=Status.UNCERTAIN, snippet=snippet,
                chain=[basename, enclosing] if enclosing else [basename], owner=enclosing,
                note="JavaScript puts this class on an element. Carried as evidence that a "
                     "rule of the same name is live, not as a claim about this line - so it "
                     "is uncertain by design, and never a finding.",
            )
        )

    # Inline <script> applies classes like any other JavaScript. Reading only .js files
    # left 46 rules that a template's own script demonstrably applies looking unreferenced.
    # Chained lazily for the same reason as in extract_js_css_tokens.
    units = itertools.chain(
        ((path, ast_root, 0)
         for path, ast_root in iter_parsed([f for f in js_files if os.path.isfile(f)])),
        parse_inline_blocks(template_files or []),
    )

    for path, ast_root, line_offset in units:
        # CSS Modules: `import styles from "./X.module.css"` makes every `styles.foo` and
        # `styles["foo"]` an application of the class `foo`. Nothing here read that, and
        # on a React codebase that is nearly every class there is: one project scored 0 of
        # 12 sampled "unused CSS" claims true, with 729 rules reported dead because the
        # only thing that referenced them was `className={styles.sectionLink}`.
        module_bindings = _css_module_bindings(ast_root)

        for node, enclosing in _walk(ast_root):
            raw_line = node_line(node)
            line = (raw_line + line_offset) if raw_line else raw_line
            node_type = node.get("type")
            sources: list[tuple[str, str]] = []

            # JSX: <div className="a b"> and <div className={cond ? "a" : "b"}>. The
            # attribute form, which is how React applies classes, as opposed to the
            # `el.className = ...` assignment the branch below already reads.
            if node_type == "JSXAttribute":
                attr_name = (node.get("name") or {}).get("name")
                if attr_name in ("className", "class"):
                    value = node.get("value") or {}
                    if value.get("type") == "JSXExpressionContainer":
                        value = value.get("expression") or {}
                    for text in _literal_strings(value):
                        sources += [(token, f'{attr_name}="..."') for token in _class_tokens(text)]

            elif node_type == "MemberExpression" and module_bindings:
                obj = node.get("object") or {}
                if obj.get("type") == "Identifier" and obj.get("name") in module_bindings:
                    prop = node.get("property") or {}
                    name = None
                    if not node.get("computed") and prop.get("type") == "Identifier":
                        name = prop.get("name")
                    elif prop.get("type") == "Literal" and isinstance(prop.get("value"), str):
                        name = prop.get("value")
                    if name:
                        sources.append((name, f"{obj['name']}.{name} (CSS module)"))

            if node_type == "AssignmentExpression":
                target = node.get("left") or {}
                if (target.get("property") or {}).get("name") == "className":
                    for text in _literal_strings(node.get("right") or {}):
                        sources += [(token, "className = ...") for token in _class_tokens(text)]

            elif node_type == "CallExpression":
                callee = node.get("callee") or {}
                method = (callee.get("property") or {}).get("name")
                arguments = node.get("arguments") or []
                if method in _CLASS_LIST_METHODS and (
                    ((callee.get("object") or {}).get("property") or {}).get("name") == "classList"
                ):
                    for argument in arguments:
                        for text in _literal_strings(argument):
                            sources += [(token, f"classList.{method}(...)") for token in _class_tokens(text)]
                elif method == "setAttribute" and len(arguments) >= 2:
                    first = arguments[0]
                    if first.get("type") == "Literal" and first.get("value") == "class":
                        for text in _literal_strings(arguments[1]):
                            sources += [(token, 'setAttribute("class", ...)') for token in _class_tokens(text)]

            # Markup built as a string: class="..." inside any literal or template chunk.
            stems_from: list[str] = []
            for text in _literal_strings(node):
                for match in _CLASS_ATTR_RE.findall(text):
                    sources += [(token, 'class="..." in generated markup') for token in _class_tokens(match)]
                    stems_from += _STEM_RE.findall(match)
            if node_type == "AssignmentExpression" and (
                ((node.get("left") or {}).get("property") or {}).get("name") == "className"
            ):
                stems_from += _class_stems(node.get("right") or {})
            elif node_type == "CallExpression":
                for argument in node.get("arguments") or []:
                    stems_from += _class_stems(argument)

            for name, snippet in sources:
                _record(name, path, line, enclosing, snippet)

            # Stems, recorded separately. A stem is not a class anyone applied, so it must
            # not become evidence that a rule for the STEM is live; it is evidence that a
            # family of rules sharing that prefix may be assembled at runtime.
            for stem in stems_from:
                symbol_id = f"dom_selector:class:{stem}:{path}:{line}"
                if symbol_id in seen:
                    continue
                seen.add(symbol_id)
                symbols.append(
                    Symbol(
                        id=symbol_id, kind="dom_selector", label=stem, sub="class:stem",
                        file=path, line=line, status=Status.UNCERTAIN,
                        snippet=f"a '{stem}...' class name assembled at runtime",
                        chain=[os.path.basename(path)],
                        note="A class-name PREFIX, not a class. It is the only trace in the "
                             "source that a family of names is assembled here, which is what "
                             "stops rules for the whole names being called dead.",
                    )
                )

    return symbols


_CSS_MODULE_SUFFIXES = (".module.css", ".module.scss", ".module.sass", ".module.less",
                        ".module.styl")


def _css_module_bindings(ast_root: dict) -> set[str]:
    """Local names bound to a CSS-module import: `styles` in `import styles from "./x.module.css"`."""
    names: set[str] = set()
    for node in (ast_root.get("body") or []) if isinstance(ast_root, dict) else []:
        if not isinstance(node, dict) or node.get("type") != "ImportDeclaration":
            continue
        source = ((node.get("source") or {}).get("value") or "")
        if not isinstance(source, str) or not source.endswith(_CSS_MODULE_SUFFIXES):
            continue
        for spec in node.get("specifiers") or []:
            if spec.get("type") in ("ImportDefaultSpecifier", "ImportNamespaceSpecifier"):
                local = (spec.get("local") or {}).get("name")
                if local:
                    names.add(local)
    return names


def _definition(kind_sub: str, name: str, path: str, line, enclosing: str, snippet: str) -> Symbol:
    return Symbol(
        id=f"dom_attr:{kind_sub}:{name}:{path}:{line}",
        kind="dom_attr",
        label=name,
        sub=kind_sub,
        file=path,
        line=line,
        status=Status.UNCERTAIN,
        snippet=snippet,
        chain=[os.path.basename(path), enclosing] if enclosing else [os.path.basename(path)],
        owner=enclosing,
        note="Created by JavaScript at runtime. No template renders it, which is why a "
             "template-only scan reported everything querying it as reaching for nothing.",
    )


def _definitions_in(path: str, ast_root: dict, line_offset: int = 0) -> list[Symbol]:
    """Elements this unit of JavaScript brings into existence."""
    symbols: list[Symbol] = []
    seen: set[str] = set()

    def _emit(sub, name, line, enclosing, snippet):
        symbol = _definition(sub, name, path, line, enclosing, snippet)
        if symbol.id not in seen:
            seen.add(symbol.id)
            symbols.append(symbol)

    for node, enclosing in _walk(ast_root):
        raw = node_line(node)
        line = (raw + line_offset) if raw else raw
        node_type = node.get("type")

        # el.dataset.buttonType = "x" - the same assertion in the other spelling. Read
        # as neither a definition nor a read before this: dropped entirely.
        if node_type == "AssignmentExpression":
            target = node.get("left") or {}
            if ((target.get("object") or {}).get("property") or {}).get("name") == "dataset":
                accessed = (target.get("property") or {}).get("name")
                if accessed:
                    _emit("data", _dataset_name(accessed), line, enclosing,
                          f"dataset.{accessed} = ...")

        # el.id = "x"  /  el.className = "a b"
        if node_type == "AssignmentExpression":
            name = ((node.get("left") or {}).get("property") or {}).get("name")
            if name in _ID_PROPERTIES:
                for text in _literal_strings(node.get("right") or {}):
                    if name == "id":
                        _emit("id", text.strip(), line, enclosing, f"{name} = '{text}'")
                    else:
                        for token in _class_tokens(text):
                            _emit("class", token, line, enclosing, f"{name} = '{text}'")

        # { id: "x", className: "a b" } - the shape a create-element helper takes
        elif node_type == "Property":
            key = (node.get("key") or {}).get("name") or (node.get("key") or {}).get("value")
            if key in ("id", "className", "class"):
                for text in _literal_strings(node.get("value") or {}):
                    if key == "id":
                        _emit("id", text.strip(), line, enclosing, f"{{ {key}: '{text}' }}")
                    else:
                        for token in _class_tokens(text):
                            _emit("class", token, line, enclosing, f"{{ {key}: '{text}' }}")

        # A JSX attribute IS markup. In a React, Preact or Solid codebase the component
        # file is where every element is written, and this reader only ever read Django
        # templates as markup - so on those projects the whole "does this element exist"
        # side of the DOM lens was blind. saleor-dashboard's CSS modules query
        # `[data-test-id="swatch-preview"]`, `[data-state]` and `[data-highlighted]`, and
        # every one of those attributes is written in a sibling .tsx: twelve findings,
        # not one of them true.
        elif node_type == "JSXAttribute":
            attribute = ((node.get("name") or {}).get("name") or "")
            value = node.get("value") or {}
            if attribute == "id":
                for text in _literal_strings(value):
                    _emit("id", text.strip(), line, enclosing, f'id="{text}"')
            elif attribute.startswith("data-") and len(attribute) > 5:
                # The NAME is the declaration; the value is often a variable, and
                # `data-x={undefined}` renders nothing - but a selector for that
                # attribute is still not reaching for something nobody writes.
                _emit("data", attribute[5:], line, enclosing, f"{attribute}=… in JSX")
            elif attribute in ("className", "class"):
                for text in _literal_strings(value):
                    for token in _class_tokens(text):
                        _emit("class", token, line, enclosing, f'{attribute}="{text}"')

        elif node_type == "CallExpression":
            callee = node.get("callee") or {}
            arguments = node.get("arguments") or []
            if ((callee.get("property") or {}).get("name") == "setAttribute"
                    and len(arguments) >= 2
                    and arguments[0].get("type") == "Literal"
                    and arguments[0].get("value") in ("id", "class")):
                which = arguments[0]["value"]
                for text in _literal_strings(arguments[1]):
                    if which == "id":
                        _emit("id", text.strip(), line, enclosing,
                              f"setAttribute('id', '{text}')")
                    else:
                        for token in _class_tokens(text):
                            _emit("class", token, line, enclosing,
                                  f"setAttribute('class', '{text}')")
            # setAttribute('data-x', v) - the attribute now EXISTS, and this was read as
            # a read of it. So an attribute JavaScript creates and JavaScript reads had
            # no definition anywhere and every reader was reported as reaching for
            # nothing: `data-incremented-today` is set in stats_manager.js line 1225 and
            # read in push_arena.js line 929, and both files were findings. Same
            # reasoning the tool already applies to `el.id = 'x'`: code that WRITES an
            # attribute asserts the element has it.
            elif ((callee.get("property") or {}).get("name") == "setAttribute"
                    and arguments
                    and arguments[0].get("type") == "Literal"
                    and isinstance(arguments[0].get("value"), str)
                    and _DATA_NAME_RE.match(arguments[0]["value"])):
                name = arguments[0]["value"]
                _emit("data", name[5:], line, enclosing, f"setAttribute('{name}', ...)")

        # Markup built as a string. This is how most of them arrive: 136 of 164 on the
        # project measured came from an id= or class= inside an innerHTML template literal.
        for text in _literal_strings(node):
            for found in _ID_ATTR_RE.findall(text):
                if not _NOT_A_CLASS_RE.search(found):
                    _emit("id", found, line, enclosing, 'id="..." in generated markup')
            for found in _CLASS_ATTR_RE.findall(text):
                for token in _class_tokens(found):
                    _emit("class", token, line, enclosing, 'class="..." in generated markup')

    return symbols


def extract_js_dom_definitions(
    js_files: list[str], template_files: list[str] | None = None
) -> list[Symbol]:
    """Elements JavaScript creates, as element DEFINITIONS rather than class usages.

    Half of what a modern page renders never appears in a template: it is built by
    innerHTML, by a create-element helper taking `{id, className}`, or by assigning
    `el.id`. Seamcheck read element definitions from templates alone, so every
    `getElementById` for one of those reported an element nothing renders - 164 of 426 such
    findings on the project this was measured against, 38% of the category.

    The class half of this information was already being read, but emitted as a `class:apply`
    USAGE - evidence that a CSS rule is live, which is a different question and deliberately
    excluded from element matching. The ids were not read at all.

    Deliberately kept out of CSS matching by the caller. An element JavaScript builds is
    often styled inline or by an injected stylesheet, so requiring a hand-written rule for
    it would trade one kind of false finding for another.
    """
    from seamcheck.extractors.js_extractor import parse_inline_blocks

    symbols: list[Symbol] = []
    for path, ast_root in iter_parsed([f for f in js_files if os.path.isfile(f)]):
        symbols += _definitions_in(path, ast_root)
    for template, ast_root, offset in parse_inline_blocks(template_files or []):
        symbols += _definitions_in(template, ast_root, line_offset=offset)
    return symbols
