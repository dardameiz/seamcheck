"""What the browser did, recorded — the evidence static reading can never reach.

1,353 of one real project's remaining `uncertain` symbols are runtime-built: a selector
assembled from a variable, a fetch target concatenated at call time, a class name built from
a prefix. That is not a gap in the extractors. It is the floor of what any reader of text
can know, and no amount of further parsing moves it.

The browser knows. Patching `querySelector`, `fetch` and friends before a page's own scripts
run turns "a selector is built here, contents unknown" into "this selector was built here,
and it was `#combo-42`". A few dozen lines of JavaScript closes the category.

**Observed evidence has a failure mode static evidence does not, and it is not symmetric.**
It is true of the path that was exercised and silent about every path that was not. A route
nobody clicked looks exactly like a route that does not work. So every observation carries
its provenance, and `observed` never quietly becomes `connected` without saying which it is —
turning a coverage gap into a false all-clear is the one mistake this tool exists to avoid.
"""

from __future__ import annotations

import dataclasses
import json
import pathlib

_STORE_DIR = pathlib.Path("OTHER") / "seamcheck" / "observed"

# Injected before any page script runs, so the patches are in place when the app boots.
# Deliberately additive: every original is called and its value returned untouched. A probe
# that changed behaviour would make the thing it is measuring untrue.
PROBE = r"""
(() => {
  const seen = {selectors: {}, fetches: {}, classes: {}, dataset: {}};
  const note = (bucket, key, hit) => {
    if (typeof key !== "string" || !key) return;
    const at = seen[bucket];
    const row = at[key] || (at[key] = {count: 0, hits: 0});
    row.count += 1;
    if (hit) row.hits += 1;
  };

  // --- what was actually queried, and whether it was there -------------------------
  for (const [proto, method] of [
    [Document.prototype, "querySelector"], [Document.prototype, "querySelectorAll"],
    [Element.prototype, "querySelector"],  [Element.prototype, "querySelectorAll"],
    [Element.prototype, "closest"],
  ]) {
    const original = proto[method];
    if (!original) continue;
    proto[method] = function (selector) {
      const found = original.apply(this, arguments);
      // A NodeList is empty rather than null, so both shapes have to be asked.
      note("selectors", selector, found && (found.length === undefined || found.length > 0));
      return found;
    };
  }
  const byId = Document.prototype.getElementById;
  Document.prototype.getElementById = function (id) {
    const found = byId.apply(this, arguments);
    note("selectors", "#" + id, Boolean(found));
    return found;
  };

  // --- what was actually requested ---------------------------------------------------
  const fetched = window.fetch;
  if (fetched) {
    window.fetch = function (input) {
      const url = typeof input === "string" ? input : (input && input.url);
      note("fetches", url, true);
      return fetched.apply(this, arguments);
    };
  }
  const open = XMLHttpRequest.prototype.open;
  XMLHttpRequest.prototype.open = function (verb, url) {
    note("fetches", url, true);
    return open.apply(this, arguments);
  };

  // --- what classes were actually put on something -----------------------------------
  const add = DOMTokenList.prototype.add;
  DOMTokenList.prototype.add = function () {
    for (const name of arguments) note("classes", name, true);
    return add.apply(this, arguments);
  };
  const className = Object.getOwnPropertyDescriptor(Element.prototype, "className");
  if (className && className.set) {
    Object.defineProperty(Element.prototype, "className", {
      ...className,
      set(value) {
        String(value || "").split(/\s+/).forEach((n) => note("classes", n, true));
        return className.set.call(this, value);
      },
    });
  }

  window.__seamcheck = { observed: seen };
})();
"""


@dataclasses.dataclass
class Observation:
    """One page, exercised once."""

    page: str
    # selector or url -> {"count": times called, "hits": times it found something}
    selectors: dict[str, dict]
    fetches: dict[str, dict]
    classes: dict[str, dict]
    screenshot: str = ""


def store_path(repo_root: str, sha: str) -> pathlib.Path:
    return pathlib.Path(repo_root) / _STORE_DIR / f"{sha}.json"


def save(observations: list[Observation], repo_root: str, sha: str) -> pathlib.Path:
    path = store_path(repo_root, sha)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps([dataclasses.asdict(o) for o in observations], indent=1), encoding="utf-8"
    )
    return path


def load(repo_root: str, sha: str) -> list[Observation]:
    """Every observation recorded for this commit, or nothing.

    Keyed by commit, like snapshots: evidence gathered from a different version of the code
    describes a different program, and silently merging it would be the worst kind of wrong.
    """
    path = store_path(repo_root, sha)
    if not path.is_file():
        return []
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [
        Observation(
            page=row.get("page", ""),
            selectors=row.get("selectors", {}),
            fetches=row.get("fetches", {}),
            classes=row.get("classes", {}),
            # Older files carry `boxes` - the geometry a view that no longer exists drew.
            # Read past it.
            screenshot=row.get("screenshot", ""),
        )
        for row in rows
    ]


def merge(observations: list[Observation]) -> dict[str, dict]:
    """All pages folded into one view, because a symbol is used if ANY page used it."""
    out: dict[str, dict] = {"selectors": {}, "fetches": {}, "classes": {}}
    for observation in observations:
        for bucket in out:
            for key, row in getattr(observation, bucket).items():
                into = out[bucket].setdefault(key, {"count": 0, "hits": 0, "pages": []})
                into["count"] += row.get("count", 0)
                into["hits"] += row.get("hits", 0)
                if observation.page not in into["pages"]:
                    into["pages"].append(observation.page)
    return out
