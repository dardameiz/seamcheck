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

### Seeing it before you send it

The map has a **Send a report** view: the exact values, in a table, with a Copy button and a
pre-filled GitHub issue. Nothing leaves until you press a button, and the button is on
GitHub's page rather than this one.
