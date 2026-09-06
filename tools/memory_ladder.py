"""Peak RSS and wall time for seamcheck, across a ladder of real repositories.

Each run is its own process, so ru_maxrss of the child is that run's own peak and
nothing leaks between rows. Two phases per repo:

  scan  - read the project and build the graph, no map, no document
  map   - the same plus the map document and writing it to disk

The difference between them is what rendering costs on top of holding the graph.
"""
import csv
import json
import pathlib
import re
import subprocess
import time

PY = "/Users/balazssimon/dev/pointlessbutton/venv/bin/python"
CORPUS = pathlib.Path("/Users/balazssimon/dev/seamcheck-corpus")
OUT = pathlib.Path(__file__).with_name("ram.csv")

# (label, path, lines) - lines from the corpus results table, smallest first.
LADDER = [
    ("fastapi-realworld", CORPUS / "fastapi-realworld", 3_841),
    ("django-debug-toolbar", CORPUS / "django-debug-toolbar", 12_347),
    ("redash", CORPUS / "redash", 72_112),
    ("bookwyrm", CORPUS / "bookwyrm", 81_955),
    ("open-webui", CORPUS / "open-webui", 113_970),
    ("immich", CORPUS / "immich", 169_524),
    ("documenso", CORPUS / "documenso", 268_456),
    ("pointlessbutton", pathlib.Path("/Users/balazssimon/dev/pointlessbutton"), 500_000),
    ("cal.com", CORPUS / "cal.com", 549_591),
    ("ghost", CORPUS / "ghost", 765_172),
    ("saleor", CORPUS / "saleor", 847_043),
    ("sentry", CORPUS / "sentry", 1_714_762),
    ("n8n", CORPUS / "n8n", 3_829_574),
]

PHASES = {
    "scan": ["scan"],
    "map": ["map", "--no-serve", "--out", "/tmp/seamcheck_measure_map.html"],
}


def peak_mb_of(args, cwd):
    """Peak resident bytes of ONE child run, in MB, plus wall seconds.

    Measured with /usr/bin/time -l, which reports that child's own maximum resident set
    size in bytes. getrusage(RUSAGE_CHILDREN) was the obvious alternative and is wrong
    here: it is a running maximum over every child this process has ever reaped, so once
    one big repository has been measured every later row reports that repository's peak.
    """
    started = time.monotonic()
    try:
        done = subprocess.run(["/usr/bin/time", "-l", PY, "-m", "seamcheck.cli", *args],
                              cwd=cwd, stdout=subprocess.DEVNULL,
                              stderr=subprocess.PIPE, timeout=2400)
    except subprocess.TimeoutExpired:
        return None, None, "timed out after 2400s"
    wall = round(time.monotonic() - started, 1)
    text = done.stderr.decode("utf-8", "replace")
    found = re.search(r"(\d+)\s+maximum resident set size", text)
    peak = round(int(found.group(1)) / 1024 / 1024, 1) if found else None
    note = ""
    if done.returncode != 0:
        said = [row for row in text.splitlines()
                if row.strip() and "maximum" not in row]
        note = f"exit {done.returncode}: {said[0][:100] if said else ''}"
    return peak, wall, note


rows = []
for label, path, lines in LADDER:
    if not path.exists():
        print(f"{label}: not cloned", flush=True)
        continue
    for phase, args in PHASES.items():
        peak, wall, note = peak_mb_of(args, path)
        rows.append({"repo": label, "lines": lines, "phase": phase,
                     "peak_mb": peak, "wall_s": wall,
                     "mb_per_100k_lines": round(peak / (lines / 100_000), 1)
                     if peak else None, "note": note})
        print(f"{label:22} {phase:5} {peak}MB  {wall}s  {note}", flush=True)

with OUT.open("w", newline="") as handle:
    writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
print(json.dumps(rows, indent=1))
print("MEASURE DONE")
