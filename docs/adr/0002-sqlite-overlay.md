# 2. SQLite overlay for changes

Status: accepted

## Context

The ZIM base is immutable (ADR 0001), but Wikipedia keeps moving: articles are edited, created, redirected, and deleted after the snapshot date. WekI has to store those changes, index them for search, and track how far sync has progressed, all for a single local user with no server to run.

## Decision

Keep every change in one SQLite database next to the ZIM. One connection, `isolation_level=None` so transactions are explicit, guarded by a process-level `RLock` for the mix of worker threads and request handlers. Tables:

- `articles` plus an FTS5 virtual table kept in sync by triggers, for newer article bodies and local full-text search.
- `deleted` and `redirects` for tombstones and redirect overrides.
- `history` for read tracking, which also drives sync priority.
- `sync_state` for the cursor and last-run result.
- `backlog` for pages discovered but not yet fetched (ADR 0003).

A path lives in at most one of `articles` / `deleted` / `redirects`; every write to one clears the others in the same transaction.

## Decision details that matter for safety

- All queries are parameterized. The only identifiers interpolated into SQL are hard-coded table names and a `journal_mode` value validated against an allowlist before it reaches a PRAGMA.
- Full-text queries quote every term so FTS5 operators in user input are treated as literals. Prefix suggestions escape LIKE wildcards.
- Schema changes run as numbered migrations inside `BEGIN IMMEDIATE ... COMMIT`, tracked by `PRAGMA user_version`, so a crash mid-migration rolls back.

## Consequences

- No separate search engine, cache, or index to operate. SQLite is the store, the index, and the queue.
- Single-writer by design. Throughput is bounded by one connection, which is adequate for a single-user reader and keeps the consistency model simple.
- The overlay is the only mutable state, so backup and recovery reduce to backing up one file (see the recovery doc). The online-backup helper integrity-checks the copy.
- Default journal mode is `DELETE` for maximum portability across filesystems; WAL is available via config where the filesystem supports it.
