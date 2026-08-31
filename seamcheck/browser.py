"""Drive the running application and record what it actually did.

Playwright is imported inside the function, never at module load, because it must stay an
optional dependency: the scan is the product and it must install and run on a machine that
will never open a browser. `pip install seamcheck[observe]` adds it.

This is the only part of Seamcheck that needs the application to RUN. Everything else reads
files. That difference is why observation is enrichment and never a prerequisite - a cloned
repository, which is the whole point of static mode, can never be observed.
"""

from __future__ import annotations

import pathlib

from seamcheck.observe import PROBE, Observation

# Long enough for a page's own boot to finish and its first requests to go out; short enough
# that thirty pages is not an afternoon. Anything slower than this wants a real wait
# condition rather than a bigger number.
_SETTLE_MS = 1500


class BrowserUnavailable(RuntimeError):
    """Playwright is not installed, or has no browser downloaded."""


def _launch(playwright, browser: str):
    try:
        return getattr(playwright, browser).launch()
    except Exception as error:  # noqa: BLE001 - surfaced verbatim, never swallowed
        raise BrowserUnavailable(
            f"could not start {browser}: {error}\n"
            "Install the browser with:  python -m playwright install chromium"
        ) from error


def observe_pages(
    urls: list[str],
    *,
    shots_dir: str | None = None,
    viewport: tuple[int, int] = (1440, 900),
    browser: str = "chromium",
    settle_ms: int = _SETTLE_MS,
) -> list[Observation]:
    """Visit each URL with the probe installed, and bring back what happened."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as error:
        raise BrowserUnavailable(
            "Playwright is not installed. It is optional, and only the observe step needs "
            "it:\n    pip install 'seamcheck[observe]'\n"
            "    python -m playwright install chromium"
        ) from error

    directory = pathlib.Path(shots_dir) if shots_dir else None
    if directory:
        directory.mkdir(parents=True, exist_ok=True)

    results: list[Observation] = []
    with sync_playwright() as playwright:
        engine = _launch(playwright, browser)
        context = engine.new_context(
            viewport={"width": viewport[0], "height": viewport[1]}
        )
        # Before any page script: the patches have to be in place when the app boots, or the
        # calls made during startup - which is most of them - are missed entirely.
        context.add_init_script(PROBE)
        for url in urls:
            page = context.new_page()
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=30_000)
                page.wait_for_timeout(settle_ms)
                recorded = page.evaluate(
                    "() => ({o: (window.__seamcheck||{}).observed || {},"
                    " b: (window.__seamcheck||{}).boxes ? window.__seamcheck.boxes() : []})"
                )
                shot = ""
                if directory:
                    name = _filename(url)
                    page.screenshot(path=str(directory / name), full_page=True)
                    shot = name
                observed = recorded.get("o") or {}
                results.append(Observation(
                    page=url,
                    selectors=observed.get("selectors", {}),
                    fetches=observed.get("fetches", {}),
                    classes=observed.get("classes", {}),
                    boxes=recorded.get("b") or [],
                    screenshot=shot,
                ))
            except Exception as error:  # noqa: BLE001
                # One page that will not load must not lose the twenty that did. The failure
                # is recorded as a page with no observations rather than dropped, so a
                # reader can tell "nothing happened here" from "never visited".
                results.append(Observation(
                    page=url, selectors={}, fetches={}, classes={},
                    boxes=[], screenshot=f"error: {error}"[:200],
                ))
            finally:
                page.close()
        engine.close()
    return results


def _filename(url: str) -> str:
    """A stable, filesystem-safe name for a URL's screenshot."""
    trimmed = url.split("://", 1)[-1].strip("/") or "index"
    safe = "".join(c if c.isalnum() or c in "-_." else "-" for c in trimmed)
    return f"{safe[:120]}.png"
