# Contributing to Baldur

Thank you for your interest in Baldur. Please read this before opening a pull
request — the contribution model here is deliberately narrow.

## This repository is a read-only mirror

This public repository is a one-way, path-filtered,
history-rewritten projection of a private canonical source. It is re-published
by force-push. **A pull request merged here would be erased on the next sync**,
so we cannot accept code contributions through pull requests.

This is an intentional, solo-maintainer model — not an oversight. It keeps the
published tree exactly in step with the internal architecture, test gates, and
OSS/PRO boundary that live in the private source.

## How to contribute instead

Open an issue. The issue tracker accepts:

- **Bug reports** — incorrect or unexpected behavior, with a minimal reproduction.
- **Feature requests** — describe the use case and what the current gap costs you.
- **Documentation problems** — typos, unclear sections, broken links.
- **Compatibility reports** — breakage on a specific Python / framework / dependency version.

Routed elsewhere (not the issue tracker):

- **Usage / how-to questions** → email `support@baldur.sh`.
- **Security vulnerabilities** → see [SECURITY.md](SECURITY.md). Do not open a public issue.
- **PRO / pricing / licensing** → email `support@baldur.sh`.

## What happens to pull requests

A pull request opened against this mirror will be closed with a pointer to this
document. If it contains a genuinely useful change, the idea may be
re-implemented in the private source and land in a later mirror sync, with
credit in the changelog — but the pull request itself will not be merged.
