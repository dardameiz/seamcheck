# Using it from an agent

This is where I have found it most useful, honestly. Asked *who writes this element?*, an
agent tends to grep, read a handful of files, and make a reasonable guess. On a big repo
that costs a lot of context and is still a guess.

And there is a failure I have run into more than once: asked to fix something on screen, an
agent will sometimes add a **second** place that writes the same element rather than finding
the one already there. The symptom moves, the next session adds another, and it slowly gets
worse.

So there is an MCP server. The agent asks, and gets the answer with the exact lines.

**And it is cheaper than asking the model.** A question like *"is any of this dead?"* has a
deterministic answer, and paying an LLM to re-derive it is paying for the same reasoning
every session, at the price of reading half the repository into context each time. Seamcheck
computes it once, from the source, with **no model and no tokens at all** — then hands the
agent a list of file-and-line answers to act on. The agent spends its context on the fix
rather than on rediscovering the problem.

That difference matters most exactly where the codebase is too big to hold at once, which
is the same place the bugs hide.

**There is a correctness argument too, and it is the more important one.** An agent reading
code to decide whether something is dead will produce an answer either way — it has no way
to say *"I could not tell."* Seamcheck does, and says it constantly: `uncertain` is a real
verdict here, and the whole design refuses to convert it into `unused` by assumption. An
agent that trusts a confident guess deletes working code; one that is handed `unresolved`
with the evidence attached, and `uncertain` where the evidence is missing, does not.

```bash
pip install 'seamcheck[mcp]'
claude mcp add seamcheck -- seamcheck-mcp
```

| tool | what the agent gets |
|---|---|
| `seamcheck_check` | every finding, with counts and what is new since the last scan |
| `seamcheck_explain` | one symbol: where it is, how it was reached, why it is classified so |
| `seamcheck_report` | the digest as markdown, to paste into a PR |
| `seamcheck_triage` | records "this one is fine, and here is why", so it stops being raised |
| `seamcheck_services` | which services this repository declares, and which are deployable |
| `seamcheck_share` | the code-free scan report, for an agent to show you before you send it |
| `seamcheck_why_wrong` | the nine fixed reasons, so an agent can pick one when it triages |

The server talks over stdin/stdout — no port, no daemon. Run it with the agent's working
directory set to the project root. **For a Django project it has to run inside that
project's virtualenv**, for the same reason the CLI does: it reads the project by importing
it. Every other backend is read from source, so anywhere works.

The tools are thin wrappers over the same functions the CLI runs. If the agent and your
terminal ever disagreed, neither would be worth trusting.
