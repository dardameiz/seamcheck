"""DOM elements JavaScript touches, and whether it writes them or only reads them."""

from __future__ import annotations

import os
import re

from seamcheck.extractors.js_extractor import _parse_files, _walk
from seamcheck.graph import Status, Symbol

_SELECTOR_CALLEES = frozenset({"getElementById", "querySelector", "querySelectorAll", "closest"})
# The other two ways JavaScript reaches a data attribute. Only `[data-x]` selector syntax
# was read, so a template's `data-can-change` looked like an attribute nothing touches while
# the code read it as `dataset.canChange` or `getAttribute("data-can-change")` - 584 of them
# on the project measured, every one sitting in `uncertain` with no explanation at all.
_ATTRIBUTE_CALLEES = frozenset(
    {"getAttribute", "setAttribute", "hasAttribute", "removeAttribute"}
)
_CAMEL_BOUNDARY = re.compile(r"(?<!^)(?=[A-Z])")


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
    """Every DOM query in one parsed unit, whether that unit was a file or a <script>."""
    symbols: list[Symbol] = []
    writing = _writing_call_ids(ast_root)
    basename = os.path.basename(path)

    def _data_access(name: str, line, enclosing: str, snippet: str) -> None:
        symbols.append(
            Symbol(
                id=f"dom_selector:data:{name}:{path}:{line}", kind="dom_selector",
                label=name, sub="data:read", file=path, line=line,
                status=Status.UNCERTAIN, snippet=snippet,
                chain=[basename, enclosing] if enclosing else [basename],
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
    assigned = set()
    for node, _enclosing in _walk(ast_root):
        node_type = node.get("type")
        if node_type in ("AssignmentExpression", "UpdateExpression"):
            target = node.get("left") or node.get("argument")
            if isinstance(target, dict):
                assigned.add(id(target))

    for node, enclosing in _walk(ast_root):
        raw = ((node.get("loc") or {}).get("start") or {}).get("line")
        at = (raw + line_offset) if raw else raw

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
        raw_line = ((node.get("loc") or {}).get("start") or {}).get("line")
        line = (raw_line + line_offset) if raw_line else raw_line
        access = "write" if id(node) in writing else "read"
        chain = [basename, enclosing] if enclosing else [basename]

        if first.get("type") != "Literal" or not isinstance(first.get("value"), str):
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


def extract_dom_selectors(js_files: list[str], template_files: list[str] | None = None) -> list[Symbol]:
    """DOM queries, from .js files AND from the JavaScript templates write inline.

    Inline <script> used to be invisible here. It is not a rounding error: the project this
    was measured on keeps 202 KB of it, and every getElementById inside it was unread - so
    148 template elements that JavaScript demonstrably uses were reported as elements
    nothing reaches, 17% of everything the scan claimed.
    """
    from seamcheck.extractors.js_extractor import parse_inline_blocks

    symbols: list[Symbol] = []
    for path, ast_root in _parse_files([f for f in js_files if os.path.isfile(f)]).items():
        symbols += _dom_selectors_in(path, ast_root)
    for template, ast_root, offset in parse_inline_blocks(template_files or []):
        symbols += _dom_selectors_in(template, ast_root, line_offset=offset)
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

    units = [
        (path, ast_root, 0)
        for path, ast_root in _parse_files([f for f in js_files if os.path.isfile(f)]).items()
    ]
    units += list(parse_inline_blocks(template_files or []))

    for path, ast_root, line_offset in units:
        def _line(node, _offset=line_offset):
            raw = ((node.get("loc") or {}).get("start") or {}).get("line")
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
                    chain=[basename, enclosing] if enclosing else [basename],
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
_CLASS_ATTR_RE = re.compile(r"""\bclass\s*=\s*["']([^"']*)(?:["']|$)""")
# The id half of the same idea. Only classes were ever read out of generated markup, so an
# element JavaScript builds with an id was invisible - and then every querySelector looking
# for it reported an element no template renders.
_ID_ATTR_RE = re.compile(r"""\bid\s*=\s*["']([^"'\s]+)["']""")
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
        symbol_id = f"dom_selector:class:{name}:{path}:{line}"
        if symbol_id in seen:
            return
        seen.add(symbol_id)
        basename = os.path.basename(path)
        symbols.append(
            Symbol(
                id=symbol_id, kind="dom_selector", label=name, sub="class:apply", file=path,
                line=line, status=Status.UNCERTAIN, snippet=snippet,
                chain=[basename, enclosing] if enclosing else [basename],
                note="JavaScript puts this class on an element. Carried as evidence that a "
                     "rule of the same name is live, not as a claim about this line - so it "
                     "is uncertain by design, and never a finding.",
            )
        )

    units = [
        (path, ast_root, 0)
        for path, ast_root in _parse_files([f for f in js_files if os.path.isfile(f)]).items()
    ]
    # Inline <script> applies classes like any other JavaScript. Reading only .js files
    # left 46 rules that a template's own script demonstrably applies looking unreferenced.
    units += list(parse_inline_blocks(template_files or []))

    for path, ast_root, line_offset in units:
        # CSS Modules: `import styles from "./X.module.css"` makes every `styles.foo` and
        # `styles["foo"]` an application of the class `foo`. Nothing here read that, and
        # on a React codebase that is nearly every class there is: one project scored 0 of
        # 12 sampled "unused CSS" claims true, with 729 rules reported dead because the
        # only thing that referenced them was `className={styles.sectionLink}`.
        module_bindings = _css_module_bindings(ast_root)

        for node, enclosing in _walk(ast_root):
            raw_line = ((node.get("loc") or {}).get("start") or {}).get("line")
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
        raw = ((node.get("loc") or {}).get("start") or {}).get("line")
        line = (raw + line_offset) if raw else raw
        node_type = node.get("type")

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
    for path, ast_root in _parse_files([f for f in js_files if os.path.isfile(f)]).items():
        symbols += _definitions_in(path, ast_root)
    for template, ast_root, offset in parse_inline_blocks(template_files or []):
        symbols += _definitions_in(template, ast_root, line_offset=offset)
    return symbols
