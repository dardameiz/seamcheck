# The commands

```bash
seamcheck map        # scan, then open the canvas. Start here.
seamcheck check      # the CI gate. Exit 1 on findings, 0 clean; --since REF adds exit 2
                     # if REF has no stored snapshot to compare against.
seamcheck report     # the findings digest, as text or markdown
seamcheck explain    # why one symbol is classified the way it is
seamcheck triage     # record "this one is fine, and here is why"
seamcheck backfill   # scan the last N commits so the map has history
seamcheck observe    # drive your pages in a real browser and record what it saw
seamcheck config     # what was detected, and how it was worked out
seamcheck share      # a report about the scan containing none of your code
```

`seamcheck help <command>` explains any of them with examples.

Useful flags: `--format terminal|markdown|html|map|json|sarif|github` · `--out FILE` ·
`--serve` / `--no-serve` · `--tunnel` (a temporary public HTTPS link, for your phone) ·
`--local-only` · `--since REF` · `--open` · `--bundle`. `sarif` and `github` render the
current findings as SARIF 2.1.0 or GitHub Actions annotations - see `docs/ci.md` for the
pull-request workflow that consumes them.

A map is one HTML file by default. `seamcheck map --out map/` (a folder, or `--bundle`)
writes a small `index.html` plus `data/*.js`, and each page's rows - and each review
list and the file tree - are fetched only when looked at; the form a very large
repository needs. A map that would pass 50 MB as one
file is written this way on its own, and the command says so.
