# Benchmark methodology

How the numbers in the README and in `benchmarks/` are produced, so you can reproduce them and read them with the right amount of skepticism.

Every result here is hardware, filesystem, and version dependent. The absolute numbers only mean something next to the environment that produced them, which each report records. Read them as evidence that the design isn't accidentally slow, not as a spec you can hold me to on your own machine.

## Harness

One script drives all of it, `scripts/benchmark_overlay.py`, and it has two halves.

The synthetic overlay and sync half (`--articles`, `--sync-pages`) builds a SQLite overlay of N synthetic articles through WekI's real `OverlayStore` API and its FTS triggers, then runs a synthetic sync against an in-memory client with no network at all. It reports insert throughput, update timing, duplicate-revision-check timing, and updated-article search latency, and finishes with `PRAGMA integrity_check`.

The real-ZIM half (`--zim <path>`) opens an actual Kiwix archive through `ZimReader` and measures open time, article lookup, redirect resolution, full-text search, suggestion, and repeated open/release cycles with a file-descriptor check to catch leaks.

The script prints the environment it ran in: `platform.platform()`, the Python version, and `sqlite3.sqlite_version`. Those fields are copied verbatim into each report.

## What's measured, and how

- Timing primitive: `time.perf_counter()` around each operation.
- Iteration counts: article lookup, redirect resolution, full-text search, and suggestion are each timed over 30 iterations; the open/release cycle over 5; duplicate-revision checks over 10,000. Percentiles come from those samples.
- Percentiles: `p50` and `p95` by nearest-rank on the sorted samples. With 30 samples, `p95` is a coarse tail estimate, not a production-load latency.
- Warm-up: there isn't a separate warm-up phase yet. The first access to a cold mapping happens inside the measured loop, so the first sample can carry OS page-cache and `libzim` warm-up cost. That's a known limitation of the harness. It means `p50` is the figure to trust and the single-digit-millisecond tails should be read loosely. A fixed warm-up pass is on the list to add.
- Memory: peak RSS from `resource.getrusage(RUSAGE_SELF).ru_maxrss`, normalized per platform (bytes on macOS, KiB on Linux).
- Verification: every run asserts the expected row counts and `PRAGMA integrity_check == ok` and raises on mismatch, so any benchmark that printed numbers also checked its own correctness.

## Reproducing

Synthetic, no network and no archive needed:

```bash
uv run --locked python scripts/benchmark_overlay.py --articles 100000 --sync-pages 1000
```

Real ZIM: download an archive, verify its SHA-256, then point the script at it. The exact commands, where each archive came from, and the checksums are in the reports under [`benchmarks/`](../benchmarks/).

## Reports

| Report | What it covers |
|---|---|
| [overlay-macos-arm64](../benchmarks/overlay-macos-arm64.md) | Synthetic overlay and sync on Apple Silicon |
| [real-zim-openzim](../benchmarks/real-zim-openzim.md) | Real Kiwix archives (OpenZIM wiki and a larger Wikipedia snapshot) |
| [live-sync-2026-11-10](../benchmarks/live-sync-2026-11-10.md) | Live sync against simple.wikipedia.org, short-window smoke test |
| [filesystem-compatibility](../benchmarks/filesystem-compatibility.md) | Storage and filesystem behavior |

## Limits

A few things these numbers deliberately don't tell you:

- The synthetic overlay uses generated tokens, so the FTS term distribution is uniform and friendlier than real prose. For search on real text, use the real-ZIM reports instead.
- The live-sync report is a correctness and politeness smoke test over short time windows, not a throughput benchmark.
- Nothing here represents full English Wikipedia at scale, which is still an open problem (see the README limitations).
