"""What each seamcheck command COSTS AN AGENT: bytes and rough tokens of its answer.

An agent pays for every byte it reads back. This runs each command on two real projects -
one small, one the reference project - and records the size of stdout, the size of stderr,
and the wall time, so "is this surface efficient for an LLM" has numbers behind it rather
than an opinion. Tokens are bytes/4, the usual rough conversion; the ratios are what matter,
not the third digit.
"""
import csv
import pathlib
import subprocess
import time

PY = "/Users/balazssimon/dev/pointlessbutton/venv/bin/python"
SMALL = "/Users/balazssimon/dev/seamcheck-corpus/django-debug-toolbar"
BIG = "/Users/balazssimon/dev/pointlessbutton"
OUT = pathlib.Path(__file__).with_name("cli_output.csv")

# (label, argv). Read-only commands only; nothing that serves or writes into the repo.
COMMANDS = [
    ("help", ["--help"]),
    ("version", ["--version"]),
    ("config", ["config"]),
    ("scan", ["scan"]),
    ("check", ["check"]),
    ("report", ["report"]),
    ("json", ["json"]),
    ("explain (one symbol)", ["explain", "urls.py"]),
    ("map --no-serve", ["map", "--no-serve", "--out", "/tmp/seamcheck_cli_probe.html"]),
]

rows = []
for repo, size in ((SMALL, "small (12k lines)"), (BIG, "reference (500k lines)")):
    for label, args in COMMANDS:
        started = time.monotonic()
        try:
            done = subprocess.run([PY, "-m", "seamcheck.cli", *args], cwd=repo,
                                  capture_output=True, timeout=1200)
        except subprocess.TimeoutExpired:
            rows.append({"repo": size, "command": label, "stdout_bytes": None,
                         "stderr_bytes": None, "tokens": None, "wall_s": None,
                         "exit": "timeout"})
            continue
        wall = round(time.monotonic() - started, 1)
        out_bytes, err_bytes = len(done.stdout), len(done.stderr)
        rows.append({"repo": size, "command": label, "stdout_bytes": out_bytes,
                     "stderr_bytes": err_bytes, "tokens": out_bytes // 4,
                     "wall_s": wall, "exit": done.returncode})
        print(f"{size:22} {label:22} out {out_bytes:>10,}  ~{out_bytes // 4:>9,} tok  "
              f"err {err_bytes:>7,}  {wall:>6}s  exit {done.returncode}", flush=True)

with OUT.open("w", newline="") as handle:
    writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
print("CLI OUTPUT DONE")
