"""A fictional project, so the screenshots can show the real UI.

The README needs pictures of Seamcheck working on something with enough in it to look
like a real codebase. The only project it has actually been run against is private, and
its file paths and symbol names are exactly the kind of thing a public README should not
carry - so this builds a plausible Django + JS bookshop instead and feeds it through the
same renderer the tool ships.

Everything here is invented. The point is that the CHROME is real: every screenshot in the
README is the actual map_html output, not a drawing of it.
"""

from __future__ import annotations

from seamcheck.graph import Edge, Graph, Status, Symbol

C, U, X, Q = Status.CONNECTED, Status.UNUSED, Status.UNRESOLVED, Status.UNCERTAIN

# (module file, [(js call label, line, fetch path, status, note)])
_CALLS: list[tuple[str, list[tuple[str, int, str, Status, str]]] ] = [
    ("bookshop/static/js/catalogue.js", [
        ("loadBooks", 34, "/api/books/", C, ""),
        ("loadFacets", 61, "/api/books/facets/", C, ""),
        ("toggleWishlist", 88, "/api/wishlist/toggle/", X,
         "No URL pattern matches this path. Renamed to /api/wishlist/ in urls.py?"),
    ]),
    ("bookshop/static/js/cart.js", [
        ("addToCart", 22, "/api/cart/add/", C, ""),
        ("refreshCart", 47, "/api/cart/", C, ""),
        ("removeLine", 71, "/api/cart/remove/", C, ""),
    ]),
    ("bookshop/static/js/search.js", [
        ("suggest", 18, "/api/search/suggest/", C, ""),
        ("recordQuery", 55, "/api/search/log/", X,
         "Nothing in the URLconf serves this path."),
    ]),
    ("bookshop/static/js/checkout.js", [
        ("createSession", 40, "/api/checkout/session/", C, ""),
        ("applyCoupon", 77, "/api/checkout/coupon/", C, ""),
    ]),
    ("bookshop/static/js/reviews.js", [
        ("postReview", 29, "/api/reviews/", C, ""),
        ("loadReviews", 52, "/api/reviews/", C, ""),
    ]),
    ("bookshop/static/js/account.js", [
        ("loadOrders", 26, "/api/orders/", C, ""),
        ("updateProfile", 63, "/api/profile/", C, ""),
    ]),
]

# (url path, view name, view line, status)
_ROUTES = [
    ("api/books/", "book_list", 41, C),
    ("api/books/facets/", "book_facets", 88, C),
    ("api/cart/add/", "cart_add", 24, C),
    ("api/cart/", "cart_detail", 12, C),
    ("api/cart/remove/", "cart_remove", 39, C),
    ("api/search/suggest/", "search_suggest", 17, C),
    ("api/checkout/session/", "checkout_session", 55, C),
    ("api/checkout/coupon/", "apply_coupon", 91, C),
    ("api/reviews/", "review_list", 30, C),
    ("api/orders/", "order_list", 14, C),
    ("api/profile/", "profile_update", 47, C),
    ("api/wishlist/", "wishlist_toggle", 66, U),
    ("api/export/csv/", "export_csv", 120, U),
    ("webhooks/stripe/", "stripe_webhook", 8, U),
]

_FIELDS = {
    "book_list": ["id", "title", "author", "price_cents", "cover_url"],
    "cart_detail": ["lines", "subtotal_cents", "currency"],
    "review_list": ["id", "body", "rating", "created_at"],
    "order_list": ["id", "placed_at", "total_cents", "status"],
}

_TEMPLATES = {
    "bookshop/templates/catalogue.html": [
        ("book-grid", "id", C), ("book-card", "class", C), ("facet-panel", "id", C),
        ("wishlist-btn", "id", C), ("price-tag", "class", C), ("cover-img", "class", C),
        ("sort-select", "id", C), ("empty-state", "class", U),
    ],
    "bookshop/templates/cart.html": [
        ("cart-lines", "id", C), ("cart-total", "id", C), ("checkout-btn", "id", C),
        ("promo-input", "id", U),
    ],
    "bookshop/templates/checkout.html": [
        ("card-element", "id", C), ("pay-btn", "id", C), ("order-summary", "class", C),
    ],
    "bookshop/templates/account.html": [
        ("order-table", "id", C), ("profile-form", "id", C), ("avatar-upload", "id", U),
    ],
}

# (selector, module that queries it, line, status, note)
_QUERIES = [
    ("#book-grid", "bookshop/static/js/catalogue.js", 12, C, ""),
    (".book-card", "bookshop/static/js/catalogue.js", 19, C, ""),
    ("#facet-panel", "bookshop/static/js/catalogue.js", 44, C, ""),
    ("#wishlist-btn", "bookshop/static/js/catalogue.js", 82, C, ""),
    ("#sort-select", "bookshop/static/js/catalogue.js", 96, C, ""),
    ("#book-carousel", "bookshop/static/js/catalogue.js", 104, X,
     "No template renders a matching element. Removed in the grid redesign?"),
    ("#cart-lines", "bookshop/static/js/cart.js", 14, C, ""),
    ("#cart-total", "bookshop/static/js/cart.js", 31, C, ""),
    ("#cart-total", "bookshop/static/js/checkout.js", 18, C, ""),
    ("#checkout-btn", "bookshop/static/js/cart.js", 58, C, ""),
    ("#card-element", "bookshop/static/js/checkout.js", 25, C, ""),
    ("#pay-btn", "bookshop/static/js/checkout.js", 33, C, ""),
    ("#order-table", "bookshop/static/js/account.js", 15, C, ""),
    ("#profile-form", "bookshop/static/js/account.js", 40, C, ""),
    ("#promo-code", "bookshop/static/js/checkout.js", 61, X,
     "Nothing in the templates matches. The field is called #promo-input."),
]

_CSS = [
    (".book-card", "bookshop/static/css/catalogue.css", 12, C),
    (".price-tag", "bookshop/static/css/catalogue.css", 44, C),
    (".cover-img", "bookshop/static/css/catalogue.css", 61, C),
    (".order-summary", "bookshop/static/css/checkout.css", 20, C),
    (".book-carousel", "bookshop/static/css/catalogue.css", 88, U),
    (".legacy-banner", "bookshop/static/css/catalogue.css", 140, U),
    (".promo-box", "bookshop/static/css/checkout.css", 77, U),
]

_TOKENS = [
    ("--ink", "bookshop/static/css/tokens.css", 4, "def", C),
    ("--paper", "bookshop/static/css/tokens.css", 5, "def", C),
    ("--accent", "bookshop/static/css/tokens.css", 6, "def", C),
    ("--rule", "bookshop/static/css/tokens.css", 7, "def", U),
    ("--ink", "bookshop/static/css/catalogue.css", 14, "use", C),
    ("--accent", "bookshop/static/css/checkout.css", 22, "use", C),
    ("--surface-2", "bookshop/static/css/catalogue.css", 51, "use", X),
    ("--radius-lg", "bookshop/static/css/checkout.css", 30, "use", X),
]

_MODELS = ["Book", "Author", "Cart", "CartLine", "Order", "Review", "Coupon", "Profile"]


def _sym(id_, kind, label, sub, file, line, status, snippet="", note="", chain=None):
    return Symbol(id=id_, kind=kind, label=label, sub=sub, file=file, line=line,
                  status=status, snippet=snippet, chain=chain or [], note=note)


def build() -> tuple[Graph, dict[str, set[str]]]:
    """(graph, page -> files) for the fictional bookshop."""
    symbols: list[Symbol] = []
    edges: list[Edge] = []

    for path, calls in _CALLS:
        for label, line, target, status, note in calls:
            call_id = f"js_call:{label}@{path}:{line}"
            fetch_id = f"fetch:{target}@{path}:{line}"
            symbols.append(_sym(call_id, "js_call", label, "function", path, line, C,
                                f"async function {label}() {{"))
            symbols.append(_sym(fetch_id, "fetch_target", target, "fetch", path, line,
                                status, f"await fetch('{target}')", note))
            edges.append(Edge(call_id, fetch_id, C))
            if status is C:
                edges.append(Edge(fetch_id, f"url:{target.lstrip('/')}", C))

    views = "bookshop/views.py"
    for path, view, line, status in _ROUTES:
        url_id, view_id = f"url:{path}", f"view:{view}"
        symbols.append(_sym(url_id, "url", path, "GET/POST", "bookshop/urls.py",
                            _ROUTES.index((path, view, line, status)) + 9, status,
                            f"path('{path}', views.{view})",
                            "No fetch call, template link or reverse() reaches this route."
                            if status is U else ""))
        symbols.append(_sym(view_id, "view", view, "async", views, line, status,
                            f"async def {view}(request):"))
        edges.append(Edge(url_id, view_id, status))
        for field in _FIELDS.get(view, []):
            field_id = f"field:{field}@{view_id}"
            symbols.append(_sym(field_id, "json_field", field, view, views, line, C,
                                f"'{field}': ...", chain=[view, field]))
            edges.append(Edge(view_id, field_id, C))

    for template, elements in _TEMPLATES.items():
        for label, sub, status in elements:
            attr_id = f"dom_attr:{label}@{template}"
            symbols.append(_sym(attr_id, "dom_attr", label, sub, template, 20, status,
                                f'<div {"id" if sub == "id" else "class"}="{label}">',
                                "No JavaScript selects this element." if status is U else ""))

    writers: dict[str, list[str]] = {}
    for selector, path, line, status, note in _QUERIES:
        sel_id = f"dom_selector:{selector}@{path}:{line}"
        symbols.append(_sym(sel_id, "dom_selector", selector, "query", path, line, status,
                            f"document.querySelector('{selector}')", note))
        writers.setdefault(selector, []).append(path)
        if status is C:
            plain = selector.lstrip("#.")
            for template, elements in _TEMPLATES.items():
                if any(label == plain for label, _, _ in elements):
                    edges.append(Edge(sel_id, f"dom_attr:{plain}@{template}", C))

    for selector, paths in writers.items():
        if len(paths) > 1:
            symbols.append(_sym(
                f"multi_writer:{selector}", "multi_writer_element", selector, "2 writers",
                paths[0], 1, Q, "",
                f"Written by {len(paths)} files: {', '.join(p.split('/')[-1] for p in paths)}. "
                "Two writers on one element is the usual cause of a value that reverts.",
            ))

    for selector, path, line, status in _CSS:
        symbols.append(_sym(f"css_selector:{selector}@{path}:{line}", "css_selector",
                            selector, "class", path, line, status, f"{selector} {{",
                            "No element and no class applied at runtime matches this rule."
                            if status is U else ""))
    for token, path, line, kind, status in _TOKENS:
        symbols.append(_sym(
            f"css_token_{kind}:{token}@{path}:{line}", f"css_token_{kind}", token,
            kind, path, line, status,
            f"{token}: ..." if kind == "def" else f"var({token})",
            "Nothing in the scanned CSS defines this custom property."
            if status is X else ("No var() reads it." if status is U else ""),
        ))

    for index, model in enumerate(_MODELS):
        symbols.append(_sym(f"model:{model}", "model", model, "bookshop",
                            "bookshop/models.py", 12 + index * 24, Q,
                            f"class {model}(models.Model):"))
    for index, receiver in enumerate(["send_receipt", "reindex_book", "warm_cart_cache"]):
        symbols.append(_sym(f"signal:{receiver}", "signal_receiver", receiver, "post_save",
                            "bookshop/signals.py", 8 + index * 16, Q,
                            f"def {receiver}(sender, instance, **kwargs):"))

    pages = {
        "catalogue-main": {"bookshop/static/js/catalogue.js", "bookshop/static/js/search.js",
                           "bookshop/static/js/recommend.js", "bookshop/static/js/filters.js"},
        "cart-main": {"bookshop/static/js/cart.js", "bookshop/static/js/promo.js"},
        "checkout-main": {"bookshop/static/js/checkout.js", "bookshop/static/js/address.js"},
        "account-main": {"bookshop/static/js/account.js", "bookshop/static/js/reviews.js",
                         "bookshop/static/js/orders.js"},
    }
    _bulk(symbols, edges, pages)
    return Graph(symbols=symbols, edges=edges), pages


# A hand-written core gives realistic names; this gives realistic VOLUME, and the volume
# is the point of the picture. A codebase two people have shipped to for a year does not
# have six problems in it - it has a wall of them, which is the thing worth seeing before
# you open the tool. Roughly a quarter of what follows is a finding, which is close to
# what the real project measured.
_BULK_MODULES = {
    "bookshop/static/js/recommend.js": ["alsoBought", "trending", "personalise", "seedFromHistory"],
    "bookshop/static/js/filters.js": ["applyFacet", "clearFacets", "priceRange", "syncQuery"],
    "bookshop/static/js/promo.js": ["validateCode", "showBanner", "clearPromo"],
    "bookshop/static/js/address.js": ["lookupPostcode", "saveAddress", "listSaved"],
    "bookshop/static/js/orders.js": ["reorder", "trackParcel", "requestReturn", "downloadInvoice"],
}
_BULK_ENDPOINTS = [
    ("/api/recommend/also-bought/", C), ("/api/recommend/trending/", C),
    ("/api/recommend/personalise/", X), ("/api/history/seed/", X),
    ("/api/facets/apply/", C), ("/api/facets/clear/", C),
    ("/api/facets/price/", C), ("/api/search/query/", X),
    ("/api/promo/validate/", C), ("/api/promo/banner/", X), ("/api/promo/clear/", C),
    ("/api/address/postcode/", C), ("/api/address/", C), ("/api/address/saved/", C),
    ("/api/orders/reorder/", C), ("/api/orders/track/", X),
    ("/api/orders/return/", C), ("/api/orders/invoice/", C),
]
_BULK_SELECTORS = [
    ("#recommend-rail", C), (".rec-card", C), ("#trending-strip", X), (".rec-price", C),
    ("#facet-price", C), (".facet-chip", C), ("#facet-clear", C), ("#facet-legacy", X),
    ("#promo-banner", X), ("#promo-apply", C), (".promo-error", U),
    ("#address-list", C), ("#postcode-input", C), ("#address-save", C),
    ("#order-rows", C), ("#track-panel", X), (".order-chip", C), ("#invoice-link", C),
    (".returns-form", U), ("#reorder-btn", C), (".parcel-eta", X), ("#saved-cards", U),
]
_BULK_CSS = [
    (".rec-card", C), (".rec-price", C), (".trending-strip", U), (".facet-chip", C),
    (".facet-legacy", U), (".promo-banner", U), (".promo-error", U), (".address-row", C),
    (".order-chip", C), (".track-panel", U), (".parcel-eta", U), (".invoice-link", C),
    (".returns-form", U), (".saved-card", U), (".rail-arrow", U), (".skeleton-row", U),
]
_BULK_TOKENS = [
    ("--space-4", "def", C), ("--space-6", "def", C), ("--shadow-1", "def", U),
    ("--space-4", "use", C), ("--space-6", "use", C), ("--surface-3", "use", X),
    ("--radius-sm", "use", X), ("--font-display", "use", X), ("--z-modal", "use", X),
]


def _bulk(symbols, edges, pages):
    endpoints = iter(_BULK_ENDPOINTS)
    for path, calls in _BULK_MODULES.items():
        for offset, label in enumerate(calls):
            try:
                target, status = next(endpoints)
            except StopIteration:
                break
            line = 20 + offset * 27
            call_id = f"js_call:{label}@{path}:{line}"
            fetch_id = f"fetch:{target}@{path}:{line}"
            symbols.append(_sym(call_id, "js_call", label, "function", path, line, C,
                                f"async function {label}() {{"))
            symbols.append(_sym(
                fetch_id, "fetch_target", target, "fetch", path, line, status,
                f"await fetch('{target}')",
                "" if status is C else
                "No URL pattern matches this path - it 404s at runtime.",
            ))
            edges.append(Edge(call_id, fetch_id, C))
            if status is C:
                url_id, view = f"url:{target.lstrip('/')}", label
                symbols.append(_sym(url_id, "url", target.lstrip("/"), "GET/POST",
                                    "bookshop/urls.py", 40 + len(symbols) % 60, C,
                                    f"path('{target.lstrip('/')}', views.{view})"))
                symbols.append(_sym(f"view:{view}", "view", view, "async",
                                    "bookshop/views.py", 140 + len(symbols) % 400, C,
                                    f"async def {view}(request):"))
                edges.append(Edge(fetch_id, url_id, C))
                edges.append(Edge(url_id, f"view:{view}", C))

    module_paths = list(_BULK_MODULES)
    for index, (selector, status) in enumerate(_BULK_SELECTORS):
        path = module_paths[index % len(module_paths)]
        line = 12 + index * 9
        symbols.append(_sym(
            f"dom_selector:{selector}@{path}:{line}", "dom_selector", selector, "query",
            path, line, status, f"document.querySelector('{selector}')",
            "" if status is C else
            ("No template renders a matching element." if status is X
             else "Nothing applies this class at runtime."),
        ))
        if status is C:
            template = f"bookshop/templates/{path.split('/')[-1].replace('.js', '.html')}"
            attr_id = f"dom_attr:{selector.lstrip('#.')}@{template}"
            symbols.append(_sym(attr_id, "dom_attr", selector.lstrip("#."),
                                "id" if selector.startswith("#") else "class",
                                template, 14 + index * 6, C,
                                f'<div id="{selector.lstrip("#.")}">'))
            edges.append(Edge(f"dom_selector:{selector}@{path}:{line}", attr_id, C))

    for index, (selector, status) in enumerate(_BULK_CSS):
        sheet = f"bookshop/static/css/{'catalogue' if index % 2 else 'checkout'}.css"
        symbols.append(_sym(
            f"css_selector:{selector}@{sheet}:{160 + index * 11}", "css_selector",
            selector, "class", sheet, 160 + index * 11, status, f"{selector} {{",
            "" if status is C else
            "No element and no class applied at runtime matches this rule.",
        ))
    for index, (token, kind, status) in enumerate(_BULK_TOKENS):
        sheet = "bookshop/static/css/tokens.css" if kind == "def" \
            else f"bookshop/static/css/{'catalogue' if index % 2 else 'checkout'}.css"
        symbols.append(_sym(
            f"css_token_{kind}:{token}@{sheet}:{90 + index * 7}", f"css_token_{kind}",
            token, kind, sheet, 90 + index * 7, status,
            f"{token}: ..." if kind == "def" else f"var({token})",
            "" if status is C else
            ("Nothing in the scanned CSS defines this custom property." if status is X
             else "No var() reads it."),
        ))
