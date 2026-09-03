# The commands

```bash
seamcheck map        # scan, then open the canvas. Start here.
seamcheck check      # the CI gate. Exit 1 on new findings, 2 with no baseline, 0 clean.
seamcheck report     # the findings digest, as text or markdown
seamcheck explain    # why one symbol is classified the way it is
seamcheck triage     # record "this one is fine, and here is why"
seamcheck backfill   # scan the last N commits so the map has history
seamcheck observe    # drive your pages in a real browser and record what it saw
seamcheck config     # what was detected, and how it was worked out
seamcheck share      # a report about the scan containing none of your code
```

`seamcheck help <command>` explains any of them with examples.

Useful flags: `--format terminal|markdown|html|map|json` · `--out FILE` · `--serve` /
`--no-serve` · `--tunnel` (a temporary public HTTPS link, for your phone) · `--local-only` ·
`--since REF` · `--open`.
