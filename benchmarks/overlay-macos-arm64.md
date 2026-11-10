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

The normal upsert measurement uses WekI's article API and its FTS triggers. To isolate bulk insertion and FTS costs, the benchmark then clears the rows, temporarily disables the article-insert FTS trigger, times the bulk insert, rebuilds the FTS index, and restores the trigger. Those staged timings are diagnostic and are not the application's normal per-article sync path.

## Synthetic synchronization

The same command ran a separate synthetic synchronization using an in-memory client (no network or Wikimedia):

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

This sync benchmark excludes HTTP latency, API pagination cost, rate limits, response-size variation, and real Wikimedia behavior. It is not a full synchronization throughput estimate. The generated articles are intentionally small and uniform; results depend on machine and filesystem and do not predict production performance, exFAT, or Windows behavior.
