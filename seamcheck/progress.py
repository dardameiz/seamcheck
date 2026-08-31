"""A one-line progress bar for a scan that takes half a minute.

A scan of a real project reads every URL, every template, every stylesheet and every JS
module, and says nothing for forty seconds. That is indistinguishable from a hang, and
the first thing anyone does about a hang is press Ctrl-C.

Three rules keep it from becoming noise of its own:

* It writes to **stderr**, so `seamcheck json > graph.json` still produces clean JSON.
* It draws **only on a terminal**. Redirected, piped or in CI it emits nothing at all -
  a progress bar in a build log is 400 lines of carriage returns.
* It **redraws one line**, never appends. `finish()` erases it, so whatever the command
  prints next starts on a clean line.

`total` is a hint, not a promise. If a scan turns out to have more phases than the caller
declared, the bar widens to fit rather than reporting 19/14 or sitting at 100% - a bar
that lies about where it is teaches you to stop reading it.
"""

from __future__ import annotations

import shutil
import sys
import time

_FILLED = "█"  # █
_EMPTY = "░"   # ░
_CLEAR_LINE = "\r\x1b[K"


class Progress:
    """Draw `n/total  label  elapsed` on one line, in place."""

    def __init__(self, total: int = 0, stream=None, enabled: bool | None = None, width: int = 18):
        self.total = max(int(total), 0)
        self.stream = stream if stream is not None else sys.stderr
        self.width = width
        self.count = 0
        self.started = time.monotonic()
        self._drawn = False
        if enabled is None:
            # isatty can be absent on a stub stream, and a stream that cannot answer the
            # question is treated as "not a terminal" - the quiet choice.
            enabled = bool(getattr(self.stream, "isatty", lambda: False)())
        self.enabled = enabled

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self.started

    def step(self, label: str = "") -> None:
        """Advance one phase and redraw."""
        self.count += 1
        if self.count > self.total:
            self.total = self.count
        self._draw(label)

    def finish(self, message: str = "") -> None:
        """Erase the bar. Anything printed after this starts on a clean line."""
        if not self.enabled or not self._drawn:
            self._drawn = False
            return
        self.stream.write(_CLEAR_LINE)
        if message:
            self.stream.write(message.rstrip("\n") + "\n")
        self._flush()
        self._drawn = False

    # -- internals ------------------------------------------------------------------

    def _draw(self, label: str) -> None:
        if not self.enabled:
            return
        done = self.count / self.total if self.total else 0.0
        filled = int(round(done * self.width))
        bar = _FILLED * filled + _EMPTY * (self.width - filled)
        head = f"[{bar}] {self.count:>2}/{self.total:<2} "
        tail = f" {self.elapsed:5.1f}s"
        # Truncate the label rather than wrap: a wrapped line cannot be overwritten by
        # the next carriage return, and the bar starts crawling down the terminal.
        room = max(self._columns() - len(head) - len(tail) - 1, 8)
        self.stream.write(_CLEAR_LINE + head + label[:room].ljust(min(room, 34)) + tail)
        self._flush()
        self._drawn = True

    def _columns(self) -> int:
        try:
            return shutil.get_terminal_size((80, 24)).columns
        except OSError:
            return 80

    def _flush(self) -> None:
        flush = getattr(self.stream, "flush", None)
        if flush:
            flush()


def null() -> Progress:
    """A Progress that draws nothing - the default for every library caller."""
    return Progress(0, enabled=False)
