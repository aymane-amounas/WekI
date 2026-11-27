# Synthetic SQLite and synchronization benchmark

Measured 2026-11-10 on macOS 27.2, ARM64, Python 3.12.13, SQLite 3.53.1.

Command:

```bash
uv run --locked python scripts/benchmark_overlay.py --articles 100000 --sync-pages 1000
```

## Synthetic overlay

| Metric | Result |
| --- | ---: |
| Synthetic articles inserted | 100,000 |
| Normal article upsert + trigger-maintained FTS | 1.467 s |
| Normal upsert throughput | 68,151 articles/s |
| Bulk article insertion with FTS trigger deferred | 0.458 s |
| FTS bulk rebuild | 0.105 s |
| Article updates | 1,000 in 0.427 s |
| Duplicate revision checks skipped | 10,000 / 10,000 |
| Duplicate revision check p50 | 0.0043 ms |
| Updated-article FTS search p50 / p95 | 0.418 / 0.480 ms |
| Search hits returned | 30 (query result limit) |
| SQLite database growth | 24,956,928 bytes |
| Peak process RSS | 37.7 MiB |
| `PRAGMA integrity_check` | `ok` |

The "normal upsert" row is the one that matters for real use: it goes through WekI's article API and its FTS triggers, which is exactly what a sync does. The rows after it exist to pull apart where the time goes. The benchmark clears the table, temporarily turns off the article-insert FTS trigger, times the bulk insert, rebuilds the FTS index, and puts the trigger back. Those staged timings are diagnostic only. They are not the path a normal per-article sync takes.

## Synthetic synchronization

The same command also ran a separate synthetic sync against an in-memory client, so no network or Wikimedia involved:

| Metric | Result |
| --- | ---: |
| Initial synchronization, 1,000 pages | 0.433 s |
| Backlog after listing | 1,000 |
| Pages fetched | 1,000 |
| Incremental synchronization, 100 updates | 0.050 s |
| Incremental backlog after listing | 100 |
| Replayed duplicate revisions, refetches | 0.001 s, 0 |
| Final backlog | 0 |
| Cursor consistent | yes |
| SQLite integrity | `ok` |

This one leaves out everything that actually makes sync slow in the wild: HTTP latency, API pagination, rate limits, response-size variation, the real behavior of Wikimedia. So don't read it as a throughput estimate. The generated articles are deliberately small and uniform, the numbers depend on the machine and filesystem, and none of this predicts production performance, exFAT, or Windows.
