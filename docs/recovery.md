# Recovery and consistency

This is what happens when a sync run gets interrupted: how the cursor and the overlay stay in agreement, and how WekI avoids redoing work it already did. Each guarantee below names the test that covers it in `tests/test_sync_worker.py` unless it says otherwise, so you can check I'm not just asserting things.

## State that has to stay consistent

Three things move during a sync and have to agree with each other:

- `last_ts` in `sync_state` is the cursor: the timestamp sync has caught up to.
- `backlog` is the set of pages discovered but not yet fetched.
- `articles` / `deleted` / `redirects` hold the stored result of a fetch.

The one invariant everything hangs on: the cursor never advances past a change that isn't already recorded as outstanding work.

## How interruptions are handled

A run has two phases, list-and-record and then drain.

In the list-and-record phase, the changes are written to the backlog and the cursor is advanced in a single transaction. If either the backlog write or the cursor write fails, the whole thing rolls back and the cursor is left where it was, so the next run lists the same window over again (`test_listing_transaction_rolls_back_backlog_when_cursor_write_fails`, `test_listing_failure_leaves_cursor_untouched`).

In the drain phase, each page is fetched, then stored and dequeued in one transaction. Pages are independent of each other.

Because the cursor only moves after the backlog write commits, anything that stops the drain phase leaves the outstanding pages sitting in the queue. The specific cases:

- A cancelled task (app shutdown, `asyncio` cancel): unfetched pages stay in the backlog and get drained next run (`test_cancelled_fetch_recovers_from_persistent_backlog_after_restart`).
- A killed process (power loss, SIGKILL): the last committed transaction wins, so a half-done fetch leaves its backlog row in place and the page is refetched. State is consistent on restart (`test_killed_process_recovers_consistent_state`).
- Rate-limited or offline mid-run: the run stops, keeps the unfetched pages queued, and keeps the cursor so no window is skipped (`test_rate_limit_mid_run_keeps_unfetched_pages_queued`, `test_offline_returns_silently_and_keeps_cursor`).
- One page fails: it's counted as failed and left queued; the rest of the batch still completes (`test_one_bad_page_does_not_stop_the_others`).
- A database or storage error: it's classified, written to `last_result`, and the worker goes back to idle (`test_database_failure_is_classified_and_worker_returns_to_idle`).

There's also a small overlap window to soften the boundary. The next run starts slightly before the stored cursor (`overlap_minutes`), so a change written at the exact cursor instant isn't missed (`test_next_sync_starts_from_cursor_minus_overlap`).

## How duplicate work is prevented

A few mechanisms, each pulling in the same direction:

- Backlog merge: enqueuing a path that's already queued merges onto the existing row, keeping the strongest priority and newest revision instead of adding a second entry.
- Revision check: a change whose revision is already stored isn't refetched (`test_unchanged_revision_is_not_refetched`).
- Idempotent store: storing a page is an upsert keyed by path, and it clears any tombstone or redirect for that path in the same transaction. Replay the same change and you get the same state (`test_replaying_same_revision_is_idempotent`).
- Deleted by fetch time: a page that was listed as changed but is gone by the time it's fetched gets marked deleted rather than stored, and dequeued (`test_page_gone_by_fetch_time_is_marked_deleted`).

## Backups and restore

The overlay is the only mutable state, so it's the only thing you need to back up. Use the online-backup helper, which copies a consistent snapshot and runs `PRAGMA integrity_check` on the copy before it accepts it:

```bash
uv run --locked python scripts/backup_overlay.py backups/overlay.db
```

The backup refuses to overwrite an existing file and refuses a destination equal to the source. A restored backup brings back the articles, the FTS index, the cursor, and the backlog together (`tests/test_overlay.py::test_online_backup_restores_articles_fts_cursor_and_backlog`). The backup CLI reads through a read-only connection and refuses to overwrite (`tests/test_backup_overlay.py::test_backup_cli_uses_read_only_source_and_does_not_overwrite`).

To restore: stop WekI, put the backup at the `overlay_path` from `config.toml`, and start it again. The ZIM base is never modified, so a full recovery is three things together: the matching ZIM file, `config.toml`, and the overlay backup. A database backup on its own is not a complete backup of the library.
