# Architecture

WekI is a single-user, local-first reader. One process serves a web UI on loopback, reads an immutable ZIM base, and keeps newer content in a SQLite overlay that a background worker fills from the Wikimedia APIs.

The three design decisions behind this are recorded as ADRs:

- [0001 ZIM as the immutable base](adr/0001-zim-as-immutable-source.md)
- [0002 SQLite overlay for changes](adr/0002-sqlite-overlay.md)
- [0003 Persistent sync backlog](adr/0003-sync-backlog.md)

## Components

```text
browser (127.0.0.1)
    |
    v
FastAPI app  (weki/server.py)
    |-- Resolver (weki/resolver.py): merges overlay + ZIM per request
    |     |-- OverlayStore (weki/overlay.py): SQLite, FTS5, backlog, sync state
    |     '-- ZimReader   (weki/zim.py): read-only libzim access
    '-- SyncWorker (weki/sync/worker.py): triggered by POST /sync
          |-- list_changes (weki/sync/changes.py): action API, allrevisions + logevents
          |-- WikiClient   (weki/sync/client.py): REST page fetch, polite + rate-limited
          '-- clean        (weki/htmlclean.py): Parsoid HTML -> safe overlay HTML + FTS text
```

## Read path

`GET /wiki/{path}` resolves a page by walking, in order: overlay article, overlay tombstone, overlay redirect, ZIM redirect, ZIM entry. Redirects are followed up to a fixed hop limit with loop detection; a missing lowercase path is retried capitalized. The resolver records the open for read-history and sync priority.

Overlay HTML is rendered into a template as trusted markup only because it was sanitized at store time (see the write path). Those responses carry a strict Content-Security-Policy (`default-src 'none'`, `script-src 'self'`). ZIM pages are served near as-authored with WekI chrome injected and no CSP, which places the trust boundary at the ZIM the operator chose to download. Every response also carries `X-Content-Type-Options: nosniff` and `Referrer-Policy: no-referrer`, the latter so a click through to the live wiki cannot leak the local path being read.

## Write path (sync)

A run is: check connectivity, list changes since the cursor, record them in the backlog while advancing the cursor in one transaction, then drain the backlog up to `cap`. Each page is fetched over the REST API with a bot User-Agent, bounded concurrency, `maxlag`, and backoff that honors `Retry-After`. Fetched Parsoid HTML is sanitized by `clean()` before it is stored: active-content tags are dropped, event-handler and unsafe-URL attributes removed, and links rewritten to local or allow-listed schemes.

## Trust boundaries

- Network input (Wikimedia responses) is untrusted HTML and is sanitized before storage.
- The ZIM base is operator-trusted content.
- The HTTP surface is loopback-only, protected by `TrustedHostMiddleware` against DNS rebinding, with an Origin check on the one state-changing endpoint (`POST /sync`). The sync domain is operator config, not user input, and redirects are not auto-followed.

## Concurrency

The web app is async; blocking SQLite work runs in a thread. A single SQLite connection is shared and serialized by an `RLock`, with explicit `BEGIN IMMEDIATE` transactions. The sync worker runs as one `asyncio` task; a second trigger while running is a no-op.
