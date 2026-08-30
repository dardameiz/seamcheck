"""Parsed by the ASGI extractor, never executed. Handlers are stubs so it lints clean."""


async def raw_handler(scope, receive, send):
    return None


async def health(scope, receive, send):
    return None


async def django_app(scope, receive, send):
    return None


async def application(scope, receive, send):
    path = scope["path"]
    if path in ("/submit_push/", "/submit_push"):
        return await raw_handler(scope, receive, send)
    if scope["path"] == "/health/":
        return await health(scope, receive, send)
    label = "not a path"  # noqa: F841  a plain string must not be picked up as a route
    return await django_app(scope, receive, send)
