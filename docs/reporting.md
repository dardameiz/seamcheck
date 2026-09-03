# Telling me it got something wrong

This is the one thing I would actually ask for. The scans worth learning from are the ones
that got something wrong, and those are almost always private repositories nobody can send.

A real example, and the reason this section exists: someone ran it on a Supabase project
and got **728 findings claiming their tables did not exist**. They all existed. Their schema
lives in the Supabase dashboard rather than in `supabase/migrations/`, so seamcheck found no
schema and read that absence as proof. It was fixed the same day. One aggregate line —
*Supabase detected, no schema present, 728 findings against it* — would have made it obvious
long before, and that line contains nothing of theirs.

```bash
seamcheck share
```

It prints a report of **counts and fixed words**: how many findings of each kind, in each
status, and why the uncertain ones are uncertain. No file paths. No symbol, table, column or
route names. No code. No repository name, no git remote, no SHA. Every value is a number or a
word seamcheck itself defines — which you can verify by reading one file,
[`seamcheck/share.py`](seamcheck/share.py), rather than taking my word for it.

**Nothing is sent.** Seamcheck makes no network calls at all, and never has. The report is
printed, written to `seamcheck-share.md`, and followed by a link that opens a pre-filled
GitHub issue in your browser — which submits nothing until you press the button. Or paste it
into an email. Or read it, decide it is too much, and delete it; that is a fine outcome too.

One thing worth saying plainly: **if the repository belongs to an employer or a client, that
is their call rather than yours.** Please do not send metrics about someone else's code
because a README asked nicely.

### The part that actually helps: tell it which findings were wrong

Counts say a scan produced three thousand findings. They cannot say which of them were
**wrong**, and wrongness is the only thing that improves the tool — hand-labelling eight
repositories is what took its precision from 28% to 42%.

You are already deciding this, one finding at a time, whenever you look at your backlog and
think *"that one's fine."* Say so and it stops being raised:

```bash
seamcheck triage '<symbol-id>' --wrong consumed-by-dependency
```

Or on the map: open a finding, press **This is wrong**, pick a reason. One tap puts the
command on your clipboard — the page cannot write to disk, so it hands you the thing that
can rather than pretending.

The reason is one of nine fixed words, and that is deliberate. The prose you type in
`--reason` stays on your machine forever; only the fixed word can travel, because free text
is exactly where a path or a table name would escape. The nine are not invented either —
each is a false-positive class measured on a real repository:

| | |
|---|---|
| `consumed-by-dependency` | a CDN bundle, a package, the framework's own code |
| `built-at-runtime` | the name is assembled, so no literal for it exists |
| `read-outside-repo` | a container, CI, a shell script, another app |
| `declared-elsewhere` | the schema or config it needs lives somewhere else |
| `generated` | build output, or a copy of code already read |
| `test-or-fixture` | a test, not the product |
| `framework-implicit` | the framework does this without being asked |
| `genuinely-dead` | nothing wrong with it — it really is dead |
| `other` | none of the above |

`genuinely-dead` matters as much as the rest. A finding confirmed **right** is evidence too.

What each word does for you, the author, once it is on the mark:

- `consumed-by-dependency` — the finding is filed as *someone else's code*, so it never
  counts against yours again.
- `built-at-runtime` — tells the next reader the name is assembled, before they go looking
  for a literal that does not exist.
- `read-outside-repo` — names the boundary: the consumer is real, it is just not here.
- `declared-elsewhere` — points at the other place, so the next scan of that place is where
  to look.
- `generated` — keeps build output out of every future count.
- `test-or-fixture` — keeps the tests out of the product's numbers.
- `framework-implicit` — records the framework rule once, so nobody rediscovers it.
- `genuinely-dead` — turns a finding into a confirmed one; the tool learns it was right.
- `other` — keeps the mark without claiming a reason it does not have.

### A mark is remembered

The mark stays on the symbol. When the code under it changes — the fingerprint no longer
matches — it is **kept**, stamped with the day, and the finding is raised again as
**returned**: `returned N` in the console counts, a pill in the map's findings list, and a
line on the card and in every report that says who marked it, when, why, when the evidence
moved, and what it is again. That line is the difference between judging a finding cold and
picking it up where the last person left it.

Two ways to answer a returned finding:

```bash
seamcheck triage '<symbol-id>' --wrong <reason>   # look again, mark it again
seamcheck triage '<symbol-id>' --undo             # take the mark off for good
```

The card has an **Undo the mark** button that puts the second command on your clipboard.
`--undo` reads the file and never scans, so it works on a symbol the scan no longer
produces. A mark whose finding has gone — the symbol is connected now — is not raised; it
is listed once, softly, as *a mark that outlived its finding*.

### Seeing it before you send it

The map has a **Send a report** view: the exact values, in a table, with a Copy button and a
pre-filled GitHub issue. Nothing leaves until you press a button, and the button is on
GitHub's page rather than this one.
