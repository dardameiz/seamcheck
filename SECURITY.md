# Security Policy

## Scope

Seamcheck is a local, offline development tool. It reads source files, shells out to
`node` for the bundled JavaScript and CSS parsers, and writes JSON to disk. It makes no
network requests, opens no ports, and is not intended to run in production.

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
