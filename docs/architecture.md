# Architecture

WekI is a single-user, local-first reader. One process serves a web UI on loopback, reads an immutable ZIM base, and keeps newer content in a SQLite overlay that a background worker fills from the Wikimedia APIs. That's the whole shape of it.

The three decisions that shaped it are written up as ADRs, and they're the place to go for the "why":

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

When a `GET /wiki/{path}` comes in, the resolver walks a fixed order until something answers: overlay article, overlay tombstone, overlay redirect, ZIM redirect, then the ZIM entry itself. Redirects are followed up to a fixed hop limit with loop detection, and if a lowercase path misses, it retries capitalized. Every open is recorded, which feeds both read-history and sync priority.

Overlay HTML is dropped into the template as trusted markup, but only because it was sanitized when it was stored (see the write path). Those responses also carry a strict Content-Security-Policy (`default-src 'none'`, `script-src 'self'`). ZIM pages are a different story: they're served close to as-authored, with WekI's chrome injected and no CSP. That puts the trust boundary at the ZIM the operator chose to download, which is the honest place for it. Every response also sends `X-Content-Type-Options: nosniff` and `Referrer-Policy: no-referrer`. The referrer header matters because a click through to the live wiki shouldn't leak the local path you were reading.

## Write path (sync)

A sync run goes: check connectivity, list what changed since the cursor, record those changes in the backlog while advancing the cursor in one transaction, then drain the backlog up to `cap`. Each page is fetched over the REST API with a bot User-Agent, bounded concurrency, `maxlag`, and backoff that honors `Retry-After`. Before any fetched Parsoid HTML is stored, `clean()` runs over it: active-content tags are dropped, event-handler and unsafe-URL attributes are removed, and links are rewritten to local or allow-listed schemes.

## Trust boundaries

Three things worth being explicit about:

- Network input (Wikimedia responses) is untrusted HTML, and it's sanitized before it's stored.
- The ZIM base is operator-trusted content. WekI serves it largely as-is.
- The HTTP surface is loopback-only, protected by `TrustedHostMiddleware` against DNS rebinding, with an Origin check on the one state-changing endpoint, `POST /sync`. The sync domain is operator config, not user input, and redirects are not auto-followed.

## Concurrency

The web app is async, and the blocking SQLite work runs in a thread. A single SQLite connection is shared and serialized by an `RLock`, with explicit `BEGIN IMMEDIATE` transactions. The sync worker runs as one `asyncio` task; if a second trigger arrives while it's running, that trigger does nothing.
