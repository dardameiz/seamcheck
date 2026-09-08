# Security Policy

## Scope

Seamcheck is a local, offline development tool. It reads source files, shells out to
`node` for the bundled JavaScript and CSS parsers, and writes JSON to disk. It opens no
ports and is not intended to run in production.

It makes exactly one kind of network request on its own: an HTTPS GET to PyPI's public
JSON API, at most once a day, to check whether a newer version exists
(`seamcheck/updatecheck.py`; off with `SEAMCHECK_NO_UPDATE_CHECK=1`, and skipped
automatically whenever `CI` is set). Nothing about the scanned project is sent in that
request, and the version string that comes back is only compared and printed — never
executed, never interpolated into a path or a shell command. `seamcheck config --tunnel
always` is the one *opt-in* exception that can carry an actual scan report off the
machine; see the README for what that sends and to whom.

The one place it executes project code is the URLconf module, which Django's own
`include()` resolution requires importing. Every other extractor parses source text into
an AST and never imports it.

## Supported versions

The latest released version receives security fixes.

## Reporting a vulnerability

Please report privately through GitHub's
[private vulnerability reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability)
on this repository rather than opening a public issue.

Include what an attacker would need to control (a source file, a config value, a filename)
and what they would gain. A reproducer is far more useful than a description.

You can expect an acknowledgement within a week.

## What counts

In scope:

- Anything that makes Seamcheck execute code it was only supposed to parse.
- Path traversal or arbitrary file write via a config value or a scanned filename.
- Command injection through the `node` subprocess boundary.

Out of scope:

- Incorrect classifications. A wrong `unused` is a serious bug — open a normal issue.
- Denial of service from pointing the scanner at a pathologically large or malformed file.
