# 1. ZIM as the immutable base

Status: accepted

## Context

WekI needs a complete offline Wikipedia corpus on disk. The corpus is large (hundreds of thousands of articles), ships as a single file, and must stay readable with no network. It also needs to survive being copied to a removable drive and read on another machine.

Kiwix ZIM is the format the Wikipedia offline ecosystem already produces and distributes. It bundles articles, assets, redirects, and a full-text index in one content-addressed, compressed file, and `libzim` reads entries by path without unpacking.

## Decision

Treat the ZIM archive as read-only. WekI opens it through `libzim` and never writes to it. Every newer article, redirect, deletion, and piece of sync state goes into a separate SQLite overlay (ADR 0002). Lookups check the overlay first and fall back to the ZIM.

## Consequences

- The base snapshot is tamper-evident by construction: a corrupt or swapped ZIM is the operator's file, and WekI holds no write path to it.
- No migration or re-indexing of the base is ever needed. Upgrading the snapshot means dropping in a newer ZIM file.
- The ZIM and the overlay are two files with a logical dependency but no referential integrity between them. A backup of one without the other is incomplete, which the recovery doc states explicitly.
- ZIM pages are served close to as-authored (chrome injected, see the architecture doc). Their HTML is operator-trusted, not sanitized like overlay HTML, so the trust boundary sits at "the ZIM you chose to download".
