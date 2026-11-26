# Benchmark methodology

How the numbers in the README and in `benchmarks/` are produced, so they can be reproduced and read with the right caveats.

All results are hardware, filesystem, and version dependent. The absolute numbers below are only meaningful next to the environment that produced them, which each report records. Treat them as evidence that the design is not accidentally slow, not as a spec.

## Harness

One script drives everything: `scripts/benchmark_overlay.py`. It has two parts.

- **Synthetic overlay and sync** (`--articles`, `--sync-pages`): builds a SQLite overlay of N synthetic articles through WekI's real `OverlayStore` API and its FTS triggers, then runs a synthetic sync against an in-memory client (no network). It reports insert throughput, update timing, duplicate-revision-check timing, and updated-article search latency, and ends with `PRAGMA integrity_check`.
- **Real ZIM** (`--zim <path>`): opens a real Kiwix archive through `ZimReader` and measures open time, article lookup, redirect resolution, full-text search, suggestion, and repeated open/release cycles with a file-descriptor check for leaks.

The script prints the environment it ran in: `platform.platform()`, Python version, and `sqlite3.sqlite_version`. Those fields are copied verbatim into each report.

## What is measured, and how

- **Timing primitive:** `time.perf_counter()` around each operation.
- **Iteration counts:** article lookup, redirect resolution, full-text search, and suggestion are each timed over 30 iterations; the open/release cycle over 5; duplicate-revision checks over 10,000. Percentiles are computed over those samples.
- **Percentiles:** `p50` and `p95` via nearest-rank on the sorted samples. With 30 samples `p95` is a coarse tail estimate, not a production-load latency.
- **Warm-up:** there is currently no separate warm-up phase. The first access to a cold mapping happens inside the measured loop, so the first sample can include OS page-cache and `libzim` warm-up cost. This is a known limitation of the harness; it means `p50` is the more trustworthy figure and single-digit-millisecond tails should be read loosely. A fixed warm-up pass is a planned improvement.
- **Memory:** peak RSS from `resource.getrusage(RUSAGE_SELF).ru_maxrss`, unit-normalized per platform (bytes on macOS, KiB on Linux).
- **Verification:** every run asserts expected row counts and `PRAGMA integrity_check == ok` and raises on mismatch, so a benchmark that printed numbers also verified correctness.

## Reproducing

Synthetic (no network, no archive):

```bash
uv run --locked python scripts/benchmark_overlay.py --articles 100000 --sync-pages 1000
```

Real ZIM: download an archive, verify its SHA-256, then point the script at it. The exact commands, archive provenance, and checksums are in the reports under [`benchmarks/`](../benchmarks/).

## Reports

| Report | What it covers |
|---|---|
| [overlay-macos-arm64](../benchmarks/overlay-macos-arm64.md) | Synthetic overlay and sync on Apple Silicon |
| [real-zim-openzim](../benchmarks/real-zim-openzim.md) | Real Kiwix archives (OpenZIM wiki and a larger Wikipedia snapshot) |
| [live-sync-2026-11-10](../benchmarks/live-sync-2026-11-10.md) | Live sync against simple.wikipedia.org, short-window smoke test |
| [filesystem-compatibility](../benchmarks/filesystem-compatibility.md) | Storage and filesystem behavior |

## Limits

- The synthetic overlay uses generated tokens, so FTS term distribution is uniform and friendlier than real prose. Real-archive search numbers come from the real-ZIM reports instead.
- The live-sync report is a correctness and politeness smoke test over short time windows, not a throughput benchmark.
- No result here represents full English Wikipedia at scale, which remains an open problem (see the README limitations).
