# Recovery and consistency

This describes what happens when a sync run is interrupted, how the cursor and overlay stay consistent, and how duplicate work is avoided. Each guarantee names the test that covers it in `tests/test_sync_worker.py` unless stated otherwise.

## State that must stay consistent

- `last_ts` in `sync_state`: the revision cursor, the timestamp sync has caught up to.
- `backlog`: pages discovered but not yet fetched.
- `articles` / `deleted` / `redirects`: the stored result of a fetch.

The invariant: the cursor never advances past changes that are not yet recorded as outstanding work.

## How interruptions are handled

A run has two phases: list-and-record, then drain.

1. **List and record.** Changes are written to the backlog and the cursor is advanced in a single transaction. If recording the changes or writing the cursor fails, the whole transaction rolls back and the cursor is untouched, so the next run lists the same window again (`test_listing_transaction_rolls_back_backlog_when_cursor_write_fails`, `test_listing_failure_leaves_cursor_untouched`).

2. **Drain.** Each page is fetched, then stored and dequeued in one transaction. Pages are independent.

Because the cursor advances only after the backlog write commits, anything that stops the drain phase leaves the outstanding pages queued:

- **Cancelled task** (app shutdown, `asyncio` cancel): unfetched pages stay in the backlog and are drained on the next run (`test_cancelled_fetch_recovers_from_persistent_backlog_after_restart`).
- **Killed process** (power loss, SIGKILL): the last committed transaction wins; a half-done fetch leaves its backlog row in place, so the page is refetched. State is consistent on restart (`test_killed_process_recovers_consistent_state`).
- **Rate limited or offline mid-run:** the run stops, keeps unfetched pages queued, and keeps the cursor so no window is skipped (`test_rate_limit_mid_run_keeps_unfetched_pages_queued`, `test_offline_returns_silently_and_keeps_cursor`).
- **One page fails:** it is counted as failed and left queued; the other pages in the batch still complete (`test_one_bad_page_does_not_stop_the_others`).
- **Database or storage error:** classified, written to `last_result`, and the worker returns to idle (`test_database_failure_is_classified_and_worker_returns_to_idle`).

An overlap window softens the boundary: the next run starts slightly before the stored cursor (`overlap_minutes`), so a change written exactly at the cursor instant is not missed (`test_next_sync_starts_from_cursor_minus_overlap`).

## How duplicate processing is prevented

- **Backlog merge.** Enqueuing a path that is already queued merges onto the existing row, keeping the strongest priority and newest revision instead of adding a second entry.
- **Revision check.** A change whose revision is already stored is not refetched (`test_unchanged_revision_is_not_refetched`).
- **Idempotent store.** Storing a page is an upsert keyed by path, and each stored page clears any tombstone or redirect for that path in the same transaction. Replaying the same change produces the same state (`test_replaying_same_revision_is_idempotent`).
- **Deleted-by-fetch-time.** A page listed as changed but gone by the time it is fetched is marked deleted rather than stored, and dequeued (`test_page_gone_by_fetch_time_is_marked_deleted`).

## Backups and restore

The overlay is the only mutable state. Back it up with the online-backup helper, which copies a consistent snapshot and runs `PRAGMA integrity_check` on the copy before accepting it:

```bash
uv run --locked python scripts/backup_overlay.py backups/overlay.db
```

The backup refuses to overwrite an existing file and refuses a destination equal to the source. A restored backup brings back articles, the FTS index, the cursor, and the backlog together (`tests/test_overlay.py::test_online_backup_restores_articles_fts_cursor_and_backlog`). The backup CLI reads through a read-only connection and refuses to overwrite (`tests/test_backup_overlay.py::test_backup_cli_uses_read_only_source_and_does_not_overwrite`).

To restore, stop WekI, put the backup at the `overlay_path` from `config.toml`, and start again. The ZIM base is never modified, so recovery is: the matching ZIM file, `config.toml`, and the overlay backup. A database backup alone is not a complete backup of the library.
