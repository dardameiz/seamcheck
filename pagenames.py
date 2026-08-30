"""Human names for the pages the map is rooted at.

A bundler calls an entry `push-arena-main`. The page a person opens is called Push Arena
and lives at `/push_arena/`. A map whose only labels are build artefacts tells a reader
which file was compiled, never where they are - which is the one thing a map is for.

Each root is traced along evidence the project already contains: the template that loads
the script, then the view that renders that template, then the URL that serves the view.
Nothing is invented. A root no template references keeps its filename, and says so.
"""

from __future__ import annotations

import ast
import pathlib
import re
from dataclasses import dataclass

from signal_map.graph import Graph
from signal_map.roots import static_js_by_template, vite_entry_map

_VITE_USE_RE = re.compile(r"\{%\s*vite_(?:asset|css)\s+['\"]([\w-]+)['\"]")
# A template name, as it appears in a view: "push_arena.html", "legal/terms.html".
_TEMPLATE_RE = re.compile(r"^[\w./-]+\.html$")


@dataclass(frozen=True)
class PageName:
    """What to show for one map root."""

    title: str          # "Push Arena"
    where: str          # "/push_arena/ - push_arena.html"
    entry: str          # "push-arena-main", the root's own filename


def _titleise(stem: str) -> str:
    return " ".join(word.capitalize() for word in re.split(r"[-_]+", stem) if word)


def vite_use_by_template(templates_root: str) -> dict[str, set[str]]:
    """Entry name -> the templates that load it, relative to the templates root."""
    root = pathlib.Path(templates_root)
    uses: dict[str, set[str]] = {}
    for template in root.rglob("*.html"):
        source = template.read_text(encoding="utf-8", errors="replace")
        for entry in _VITE_USE_RE.findall(source):
            uses.setdefault(entry, set()).add(str(template.relative_to(root)))
    return uses


def _templates_rendered_by(path: str, function: str, cache: dict[str, ast.Module | None]) -> set[str]:
    """Template names named inside one view function.

    A string literal ending in .html inside the view IS the render target in every shape
    this project uses - `render(request, "x.html")`, a `template_name`, a conditional
    pick between two. Matching the call rather than the literal would miss the last two.
    """
    if path not in cache:
        try:
            cache[path] = ast.parse(pathlib.Path(path).read_text(encoding="utf-8", errors="replace"))
        except (OSError, SyntaxError):
            cache[path] = None
    tree = cache[path]
    if tree is None:
        return set()
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == function:
            for inner in ast.walk(node):
                if (
                    isinstance(inner, ast.Constant)
                    and isinstance(inner.value, str)
                    and _TEMPLATE_RE.match(inner.value)
                ):
                    found.add(inner.value)
    return found


def urls_by_template(graph: Graph) -> dict[str, set[str]]:
    """Template -> the URL patterns that serve it, via the routing edges already scanned."""
    views = {symbol.id: symbol for symbol in graph.symbols if symbol.kind == "view"}
    urls = {symbol.id: symbol for symbol in graph.symbols if symbol.kind == "url"}
    cache: dict[str, ast.Module | None] = {}
    rendered = {
        view_id: _templates_rendered_by(view.file, view.label, cache)
        for view_id, view in views.items()
        if view.file
    }
    serving: dict[str, set[str]] = {}
    for edge in graph.edges:
        if edge.from_id in urls and edge.to_id in views:
            for template in rendered.get(edge.to_id, ()):
                serving.setdefault(template, set()).add(urls[edge.from_id].label)
    return serving


def _best_url(candidates: set[str], template: str) -> str:
    """The URL a person would recognise as this page's address.

    The site root wins outright - it is the most recognisable address a page can have.
    Otherwise prefer the route that shares the most words with the template name, so
    `leaderboard_arena.html` reads as /leaderboard/ rather than its /halloffame/ alias.
    """
    if "" in candidates:
        return "/"
    words = {word for word in re.split(r"[-_/.]+", template.removesuffix(".html")) if word}
    # Django's resolver reports patterns without their leading slash; a reader reads
    # addresses with one, and `push_arena/` does not look like somewhere you can go.
    best = min(candidates, key=lambda url: (-len({w for w in words if w in url}), len(url), url))
    return best if best.startswith("/") else f"/{best}"


def page_names(repo_root: str, config: dict, graph: Graph) -> dict[str, PageName]:
    """Map each JS root's filename stem to what to call it on screen."""
    root = pathlib.Path(repo_root)
    templates_root = str(root / config["templates_root"])
    by_entry = vite_use_by_template(templates_root)
    by_script = static_js_by_template(templates_root)
    serving = urls_by_template(graph)

    loaded_by: dict[str, tuple[set[str], str]] = {}
    for entry, path in vite_entry_map(str(root / "vite.config.js")).items():
        loaded_by[pathlib.Path(path).stem] = (by_entry.get(entry, set()), entry)
    for reference, templates in by_script.items():
        stem = pathlib.Path(reference).stem
        loaded_by.setdefault(stem, (templates, stem))

    names: dict[str, PageName] = {}
    for stem, (templates, entry) in loaded_by.items():
        if not templates:
            # No template asks for this bundle. Saying so beats inventing a page name.
            names[stem] = PageName(title=stem, where="no template loads this", entry=stem)
            continue
        if len(templates) == 1:
            template = next(iter(templates))
            title = _titleise(pathlib.Path(template).stem)
            urls = serving.get(template, set())
            where = f"{_best_url(urls, template)} - {template}" if urls else template
        else:
            title = _titleise(entry)
            where = f"{len(templates)} templates"
        names[stem] = PageName(title=title, where=where, entry=stem)
    return names
