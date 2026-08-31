"""Turn `path:line` into something you can click.

Every finding seamcheck reports is a place in a file, and the distance between reading
"button_badges.css:3" and having the cursor on line 3 is a copy, a paste and a lost train
of thought. Editors all expose a URL scheme for exactly this; the only question is which
one is installed, which is a fact about the reader, not about the project - so it is
configuration, with a sane default.

The map builds the href in the browser, from the template and the repo's absolute path,
because the scan stores every file relative to the repo root and a `vscode://` URL needs
an absolute one.
"""

from __future__ import annotations

# name -> URL template. {path} is absolute and already begins with a slash, so
# "vscode://file{path}" produces the "vscode://file/Users/..." these schemes expect.
SCHEMES: dict[str, str] = {
    "vscode": "vscode://file{path}:{line}",
    "vscode-insiders": "vscode-insiders://file{path}:{line}",
    "cursor": "cursor://file{path}:{line}",
    "windsurf": "windsurf://file{path}:{line}",
    "zed": "zed://file{path}:{line}",
    "sublime": "subl://open?url=file://{path}&line={line}",
    "pycharm": "jetbrains://pycharm/navigate/reference?path={path}:{line}",
    "idea": "jetbrains://idea/navigate/reference?path={path}:{line}",
    "webstorm": "jetbrains://web-storm/navigate/reference?path={path}:{line}",
    # An explicit opt-out. The location still renders, and still copies on click; it
    # just stops pretending an editor will answer.
    "none": "",
}

DEFAULT = "vscode"


def scheme(name: str | None) -> str:
    """The URL template for `name`, or the default. Unknown names fall back to none.

    Deliberately forgiving: a typo in a config key should cost a reader their clickable
    links, not their whole report.
    """
    if not name:
        return SCHEMES[DEFAULT]
    return SCHEMES.get(str(name).strip().lower(), "")


def link(name: str | None, absolute_path: str, line: int | None) -> str:
    """One href, or "" when there is no scheme or no file to point at."""
    template = scheme(name)
    if not template or not absolute_path:
        return ""
    return template.format(path=absolute_path, line=line or 1)
