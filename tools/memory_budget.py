"""What the AST cache budget actually buys, and what it costs.

The cache admits parsed JavaScript until its parser output reaches a budget that defaults
to a QUARTER OF PHYSICAL MEMORY - so the same project takes 6 GB on this 24 GB machine and
2 GB on an 8 GB one. This measures the trade the budget makes: peak RSS against wall time,
on one project, with everything else identical.
"""
import csv
import os
import pathlib
import re
import subprocess
import time

PY = "/Users/balazssimon/dev/pointlessbutton/venv/bin/python"
REPO = "/Users/balazssimon/dev/pointlessbutton"
OUT = pathlib.Path(__file__).with_name("budget.csv")
# None = the default (a quarter of RAM). The rest are explicit ceilings in MB.
BUDGETS = [None, 4096, 1024, 512, 256, 128]

rows = []
for budget in BUDGETS:
    env = dict(os.environ)
    if budget is not None:
        env["SEAMCHECK_AST_CACHE_MB"] = str(budget)
    else:
        env.pop("SEAMCHECK_AST_CACHE_MB", None)
    started = time.monotonic()
    done = subprocess.run(["/usr/bin/time", "-l", PY, "-m", "seamcheck.cli", "scan"],
                          cwd=REPO, env=env, stdout=subprocess.DEVNULL,
                          stderr=subprocess.PIPE, timeout=3600)
    wall = round(time.monotonic() - started, 1)
    text = done.stderr.decode("utf-8", "replace")
    found = re.search(r"(\d+)\s+maximum resident set size", text)
    peak = round(int(found.group(1)) / 1024 / 1024, 1) if found else None
    label = "default (RAM/4)" if budget is None else f"{budget} MB"
    rows.append({"budget": label, "peak_mb": peak, "wall_s": wall,
                 "exit": done.returncode})
    print(f"{label:16} peak {peak} MB   {wall}s   exit {done.returncode}", flush=True)

with OUT.open("w", newline="") as handle:
    writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
print("BUDGET DONE")
