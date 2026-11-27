# 1. ZIM as the immutable base

Status: accepted

## Context

WekI needs a complete offline Wikipedia corpus sitting on disk. That corpus is large, hundreds of thousands of articles, it ships as a single file, and it has to stay readable with no network. It also has to survive being copied onto a removable drive and read on a different machine.

Kiwix ZIM is the format the Wikipedia offline world already produces and hands out. It packs articles, assets, redirects, and a full-text index into one content-addressed, compressed file, and `libzim` reads entries by path without unpacking the whole thing. There was no good reason to invent a container when this one already exists and is maintained.

## Decision

Treat the ZIM archive as read-only. WekI opens it through `libzim` and never writes to it. Everything newer, articles, redirects, deletions, and sync state, goes into a separate SQLite overlay (ADR 0002). A lookup checks the overlay first and falls back to the ZIM.

## Consequences

- The base snapshot is tamper-evident by construction. A corrupt or swapped ZIM is the operator's own file, and WekI holds no write path to it, so it can't quietly mangle the base.
- No migration or re-indexing of the base, ever. Upgrading the snapshot is just dropping in a newer ZIM file.
- The ZIM and the overlay are two files with a logical dependency but no referential integrity between them. Backing up one without the other gives you an incomplete backup, which the recovery doc states outright.
- ZIM pages are served close to as-authored, with chrome injected (see the architecture doc). Their HTML is operator-trusted rather than sanitized the way overlay HTML is, so the trust boundary sits at "the ZIM you chose to download".
