#!/usr/bin/env python3
"""Measure SQLite overlay insert and search behavior with synthetic articles."""

from __future__ import annotations

import argparse
import asyncio
import gc
import math
import os
import platform
import sqlite3
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

try:
    import resource
except ImportError:
    resource = None

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from weki.config import SyncConfig
from weki.overlay import OverlayStore
from weki.sync.client import PageData
from weki.sync.worker import SyncWorker


def percentile(samples: list[float], percentage: int) -> float:
    ordered = sorted(samples)
    return ordered[max(math.ceil(percentage * len(ordered) / 100) - 1, 0)]


def benchmark(path: Path, article_count: int, batch_size: int) -> None:
    initial_bytes = path.stat().st_size if path.exists() else 0
    store = OverlayStore(path)
    started = time.perf_counter()
    for batch_start in range(0, article_count, batch_size):
        batch_end = min(batch_start + batch_size, article_count)
        with store.transaction():
            for index in range(batch_start, batch_end):
                text = f"benchmarktoken sharedtopic topic{index % 1000}"
                store.upsert_article(
                    f"Benchmark_{index}",
                    f"Benchmark article {index}",
                    f"<p>{text}</p>",
                    text,
                    index + 1,
                    "2026-11-01T00:00:00Z",
                )
    insert_seconds = time.perf_counter() - started

    with store.transaction() as db:
        db.execute("DELETE FROM articles")
        db.execute("DROP TRIGGER articles_ai")

    started = time.perf_counter()
    for batch_start in range(0, article_count, batch_size):
        batch_end = min(batch_start + batch_size, article_count)
        with store.transaction():
            for index in range(batch_start, batch_end):
                text = f"benchmarktoken sharedtopic topic{index % 1000}"
                store.upsert_article(
                    f"Benchmark_{index}",
                    f"Benchmark article {index}",
                    f"<p>{text}</p>",
                    text,
                    index + 1,
                    "2026-11-01T00:00:00Z",
                )
    insert_without_fts_seconds = time.perf_counter() - started

    started = time.perf_counter()
    with store.transaction() as db:
        db.execute("INSERT INTO articles_fts(articles_fts) VALUES ('rebuild')")
    fts_rebuild_seconds = time.perf_counter() - started
    with store.transaction() as db:
        db.execute(
            """
            CREATE TRIGGER articles_ai AFTER INSERT ON articles BEGIN
                INSERT INTO articles_fts(rowid, title, text)
                VALUES (new.id, new.title, new.text);
            END
            """
        )
    if len(store.search("benchmarktoken")) != min(article_count, 30):
        raise RuntimeError("FTS bulk rebuild verification failed")

    update_count = min(article_count, 1_000)
    started = time.perf_counter()
    for index in range(update_count):
        text = f"updatedtoken sharedtopic topic{index % 1000}"
        store.upsert_article(
            f"Benchmark_{index}",
            f"Benchmark article {index}",
            f"<p>{text}</p>",
            text,
            article_count + index + 1,
            "2026-11-02T00:00:00Z",
        )
    update_seconds = time.perf_counter() - started

    lookup_times = []
    duplicate_checks = 10_000
    duplicate_skips = 0
    for index in range(duplicate_checks):
        article_index = index % article_count
        lookup_started = time.perf_counter()
        stored_revision = store.rev_id(f"Benchmark_{article_index}")
        lookup_times.append(time.perf_counter() - lookup_started)
        if stored_revision is None:
            raise RuntimeError(f"missing synthetic article Benchmark_{article_index}")
        incoming_revision = (
            article_count + article_index + 1 if article_index < update_count else article_index + 1
        )
        duplicate_skips += stored_revision >= incoming_revision
    if duplicate_skips != duplicate_checks:
        raise RuntimeError(
            f"duplicate revision checks skipped {duplicate_skips}/{duplicate_checks}"
        )

    search_times = []
    for _ in range(30):
        search_started = time.perf_counter()
        hits = store.search("updatedtoken")
        search_times.append(time.perf_counter() - search_started)
    counts = store.counts()
    store.close()
    with sqlite3.connect(path) as db:
        integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
    peak_rss = None
    if resource is not None:
        peak_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        if platform.system() == "Darwin":
            peak_rss /= 1024 * 1024
        else:
            peak_rss /= 1024

    if counts["articles"] != article_count or len(hits) != min(update_count, 30):
        raise RuntimeError(
            f"benchmark verification failed: articles={counts['articles']}, hits={len(hits)}"
        )
    if integrity != "ok":
        raise RuntimeError(f"SQLite integrity check failed: {integrity}")

    print("overlay:")
    print(f"  platform: {platform.platform()}")
    print(f"  python: {platform.python_version()}")
    print(f"  sqlite: {sqlite3.sqlite_version}")
    print(f"  articles: {counts['articles']}")
    print(f"  insert_and_fts_seconds: {insert_seconds:.3f}")
    print(f"  insert_and_fts_articles_per_second: {article_count / insert_seconds:.0f}")
    print(f"  bulk_insert_without_fts_seconds: {insert_without_fts_seconds:.3f}")
    print(f"  fts_bulk_rebuild_seconds: {fts_rebuild_seconds:.3f}")
    print(f"  article_updates: {update_count}")
    print(f"  article_updates_seconds: {update_seconds:.3f}")
    print(f"  duplicate_revision_checks: {duplicate_checks}")
    print(f"  duplicate_revision_check_p50_ms: {percentile(lookup_times, 50) * 1000:.4f}")
    print(f"  duplicate_revision_checks_skipped: {duplicate_skips}/{duplicate_checks}")
    print(f"  article_search_p50_ms: {percentile(search_times, 50) * 1000:.3f}")
    print(f"  article_search_p95_ms: {percentile(search_times, 95) * 1000:.3f}")
    print(f"  article_search_hits: {len(hits)} (limited to 30)")
    print(f"  database_growth_bytes: {path.stat().st_size - initial_bytes}")
    print(f"  database_final_bytes: {path.stat().st_size}")
    if peak_rss is None:
        print("  peak_rss_mb: unavailable on this platform")
    else:
        print(f"  peak_rss_mb: {peak_rss:.1f}")
    print(f"  sqlite_integrity: {integrity}")


def benchmark_zim(path: Path, article_path: str | None) -> None:
    from weki.zim import ZimReader

    started = time.perf_counter()
    zim = ZimReader(path)
    open_seconds = time.perf_counter() - started

    metadata_started = time.perf_counter()
    metadata = {key: zim.metadata(key) for key in ("Title", "Date", "Language")}
    metadata_seconds = time.perf_counter() - metadata_started
    selected_path = article_path or zim.main_path
    if selected_path is None:
        raise RuntimeError("archive does not have a main article entry")

    lookup_times = []
    article = None
    for _ in range(20):
        lookup_started = time.perf_counter()
        article = zim.get(selected_path)
        lookup_times.append(time.perf_counter() - lookup_started)
    if article is None:
        raise RuntimeError(f"article path not found in ZIM: {selected_path}")

    redirect_path = None
    redirect_target = None
    redirect_entries_examined = 0
    main_entry = zim._archive.main_entry if zim._archive.has_main_entry else None
    if main_entry is not None and main_entry.is_redirect:
        redirect_path = main_entry.path
        redirect_target = zim.redirect_target(redirect_path)
        if redirect_target is None:
            redirect_path = None
    for entry in zim._archive.iter_by_path():
        if redirect_path is not None:
            break
        redirect_entries_examined += 1
        if entry.is_redirect:
            target = zim.redirect_target(entry.path)
            if target is not None:
                redirect_path = entry.path
                redirect_target = target
                break
        if redirect_entries_examined == 10_000:
            break

    redirect_times = []
    redirect_resolved_path = None
    if redirect_path is not None:
        for _ in range(20):
            redirect_started = time.perf_counter()
            resolved = zim.get(redirect_path)
            redirect_times.append(time.perf_counter() - redirect_started)
            if resolved is None:
                raise RuntimeError(f"ZIM redirect resolution failed: {redirect_path}")
            redirect_resolved_path = resolved.path

    search_times = []
    search_result_count = 0
    for _ in range(30):
        search_started = time.perf_counter()
        results = zim.search("Wikimedia", limit=10)
        search_times.append(time.perf_counter() - search_started)
        search_result_count = len(results)
    suggestion_times = []
    suggestion_result_count = 0
    for _ in range(30):
        suggestion_started = time.perf_counter()
        suggestions = zim.suggest("Wik", limit=10)
        suggestion_times.append(time.perf_counter() - suggestion_started)
        suggestion_result_count = len(suggestions)
    missing_is_none = zim.get("__WekI_benchmark_missing_path__") is None

    def open_file_descriptors() -> int | None:
        descriptor_dir = Path("/proc/self/fd")
        if not descriptor_dir.exists() and platform.system() == "Darwin":
            descriptor_dir = Path("/dev/fd")
        if not descriptor_dir.exists():
            return None
        return len(os.listdir(descriptor_dir))

    descriptors_before_cycles = open_file_descriptors()
    open_release_times = []
    for _ in range(5):
        cycle_started = time.perf_counter()
        reopened = ZimReader(path)
        reopened.metadata("Title")
        del reopened
        gc.collect()
        open_release_times.append(time.perf_counter() - cycle_started)
    descriptors_after_cycles = open_file_descriptors()

    peak_rss = None
    if resource is not None:
        peak_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        peak_rss /= 1024 * 1024 if platform.system() == "Darwin" else 1024

    print("zim:")
    print(f"  platform: {platform.platform()}")
    print(f"  python: {platform.python_version()}")
    print(f"  sqlite: {sqlite3.sqlite_version}")
    print(f"  path: {path}")
    print(f"  file_bytes: {path.stat().st_size}")
    print(f"  articles: {zim.article_count}")
    print(f"  fulltext_index: {zim.has_fulltext_index}")
    print(f"  open_seconds: {open_seconds:.4f}")
    print(f"  metadata_seconds: {metadata_seconds:.4f}")
    print(f"  metadata: {metadata}")
    print(f"  selected_article_path: {selected_path}")
    print(f"  selected_article_bytes: {len(article.content)}")
    print(f"  article_lookup_p50_ms: {percentile(lookup_times, 50) * 1000:.3f}")
    print(f"  article_lookup_p95_ms: {percentile(lookup_times, 95) * 1000:.3f}")
    print(f"  redirect_path: {redirect_path}")
    print(f"  redirect_target: {redirect_target}")
    print(f"  redirect_resolved_path: {redirect_resolved_path}")
    print(f"  redirect_entries_examined: {redirect_entries_examined}")
    if redirect_times:
        print(f"  redirect_resolution_p50_ms: {percentile(redirect_times, 50) * 1000:.3f}")
        print(f"  redirect_resolution_p95_ms: {percentile(redirect_times, 95) * 1000:.3f}")
    else:
        print("  redirect_resolution: no redirect found in main entry or first 10000 entries")
    print(f"  search_mode: {'fulltext' if zim.has_fulltext_index else 'suggestion'}")
    print(f"  search_query: Wikimedia; results: {search_result_count}")
    print(f"  search_p50_ms: {percentile(search_times, 50) * 1000:.3f}")
    print(f"  search_p95_ms: {percentile(search_times, 95) * 1000:.3f}")
    print(f"  suggestion_query: Wik; results: {suggestion_result_count}")
    print(f"  suggestion_p50_ms: {percentile(suggestion_times, 50) * 1000:.3f}")
    print(f"  suggestion_p95_ms: {percentile(suggestion_times, 95) * 1000:.3f}")
    print(f"  missing_article_returns_none: {missing_is_none}")
    print("  repeated_open_release_cycles: 5")
    print(f"  open_release_cycle_p50_ms: {percentile(open_release_times, 50) * 1000:.3f}")
    print(f"  file_descriptors_before_cycles: {descriptors_before_cycles}")
    print(f"  file_descriptors_after_cycles: {descriptors_after_cycles}")
    if peak_rss is None:
        print("  process_peak_rss_mb: unavailable on this platform")
    else:
        print(f"  process_peak_rss_mb: {peak_rss:.1f}")


class BenchmarkSyncClient:
    def __init__(self) -> None:
        self.revisions: list[dict] = []
        self.revision_by_title: dict[str, int] = {}
        self.fetched = 0

    def set_revisions(self, revisions: list[dict]) -> None:
        self.revisions = revisions
        self.revision_by_title = {
            entry["title"]: entry["revisions"][0]["revid"] for entry in revisions
        }

    async def is_online(self) -> bool:
        return True

    async def paged(self, params: dict, list_key: str):
        if params.get("list") == "allrevisions":
            for revision in self.revisions:
                yield revision

    async def page(self, title: str) -> PageData:
        self.fetched += 1
        revision = self.revision_by_title[title]
        return PageData(
            title,
            revision,
            "2026-11-10T00:00:00Z",
            f"<html><body><p>syncbenchmark article {revision}</p></body></html>",
        )


class MeasuredSyncWorker(SyncWorker):
    backlog_after_listing = 0

    def _record(self, changes, until: str) -> None:
        super()._record(changes, until)
        self.backlog_after_listing = self.overlay.counts()["backlog"]


def benchmark_sync(page_count: int) -> None:
    client = BenchmarkSyncClient()
    initial = [
        {
            "title": f"Sync_{index}",
            "revisions": [
                {
                    "revid": index + 1,
                    "parentid": 0,
                    "timestamp": "2026-11-09T00:00:00Z",
                }
            ],
        }
        for index in range(page_count)
    ]
    incremental_count = max(1, page_count // 10)
    incremental = [
        {
            "title": f"Sync_{index}",
            "revisions": [
                {
                    "revid": page_count + index + 1,
                    "parentid": page_count + index,
                    "timestamp": "2026-11-10T00:00:00Z",
                }
            ],
        }
        for index in range(incremental_count)
    ]

    def now() -> datetime:
        return datetime(2026, 11, 10, 12, 0, tzinfo=UTC)

    with tempfile.TemporaryDirectory(prefix="weki-sync-benchmark-") as directory:
        path = Path(directory) / "overlay.db"
        store = OverlayStore(path)
        worker = MeasuredSyncWorker(
            SyncConfig(cap=page_count, delay_seconds=0),
            store,
            client,
            zim_date="2026-11-01",
            now=now,
        )

        client.set_revisions(initial)
        initial_started = time.perf_counter()
        asyncio.run(worker.run())
        initial_seconds = time.perf_counter() - initial_started
        initial_backlog = worker.backlog_after_listing
        initial_fetched = worker.status.done
        initial_cursor = store.get_state("last_ts")

        client.set_revisions(incremental)
        before_incremental_fetch = client.fetched
        incremental_started = time.perf_counter()
        asyncio.run(worker.run())
        incremental_seconds = time.perf_counter() - incremental_started
        incremental_backlog = worker.backlog_after_listing
        incremental_fetched = client.fetched - before_incremental_fetch
        incremental_cursor = store.get_state("last_ts")

        client.set_revisions(incremental)
        before_duplicate_fetch = client.fetched
        duplicate_started = time.perf_counter()
        asyncio.run(worker.run())
        duplicate_seconds = time.perf_counter() - duplicate_started
        duplicate_fetches = client.fetched - before_duplicate_fetch
        counts = store.counts()
        integrity = store._db.execute("PRAGMA integrity_check").fetchone()[0]
        store.close()

    expected_articles = page_count
    if (
        initial_fetched != page_count
        or initial_backlog != page_count
        or incremental_fetched != incremental_count
        or incremental_backlog != incremental_count
        or duplicate_fetches != 0
        or counts["articles"] != expected_articles
        or counts["backlog"] != 0
        or initial_cursor != "2026-11-10T12:00:00Z"
        or incremental_cursor != initial_cursor
        or integrity != "ok"
    ):
        raise RuntimeError("synthetic synchronization benchmark invariants failed")

    print("synthetic_sync:")
    print(f"  initial_pages: {page_count}")
    print(f"  initial_seconds: {initial_seconds:.3f}")
    print(f"  initial_listing_backlog: {initial_backlog}")
    print(f"  initial_fetched: {initial_fetched}")
    print(f"  incremental_pages: {incremental_count}")
    print(f"  incremental_seconds: {incremental_seconds:.3f}")
    print(f"  incremental_listing_backlog: {incremental_backlog}")
    print(f"  incremental_fetched: {incremental_fetched}")
    print(f"  duplicate_replay_seconds: {duplicate_seconds:.3f}")
    print(f"  duplicate_replay_refetches: {duplicate_fetches}")
    print(f"  final_articles: {counts['articles']}")
    print(f"  final_backlog: {counts['backlog']}")
    print(f"  cursor_consistent: {initial_cursor == incremental_cursor}")
    print(f"  sqlite_integrity: {integrity}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--articles", type=int, default=100_000)
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument(
        "--database",
        type=Path,
        help="write the benchmark database here; the path must not already exist",
    )
    parser.add_argument("--zim", type=Path, help="measure operations on this real ZIM archive")
    parser.add_argument(
        "--zim-article-path", help="article path to retrieve; default is main entry"
    )
    parser.add_argument("--skip-overlay", action="store_true", help="run only the ZIM benchmark")
    parser.add_argument(
        "--sync-pages",
        type=int,
        default=0,
        help="also benchmark synthetic initial/incremental sync for this many pages",
    )
    args = parser.parse_args()
    if args.articles <= 0 or args.batch_size <= 0 or args.sync_pages < 0:
        parser.error(
            "--articles and --batch-size must be positive; --sync-pages cannot be negative"
        )

    if args.zim:
        if not args.zim.is_file():
            parser.error(f"ZIM file does not exist: {args.zim}")
        benchmark_zim(args.zim, args.zim_article_path)

    if args.zim_article_path and not args.zim:
        parser.error("--zim-article-path requires --zim")
    if args.skip_overlay:
        if not args.zim:
            parser.error("--skip-overlay requires --zim")
        if args.sync_pages:
            parser.error("--skip-overlay cannot be combined with --sync-pages")
        return 0

    if args.database:
        path = args.database.resolve()
        if path.exists():
            parser.error(f"database already exists: {path}")
        path.parent.mkdir(parents=True, exist_ok=True)
        benchmark(path, args.articles, args.batch_size)
    else:
        with tempfile.TemporaryDirectory(prefix="weki-overlay-benchmark-") as directory:
            benchmark(Path(directory) / "overlay.db", args.articles, args.batch_size)
    if args.sync_pages:
        benchmark_sync(args.sync_pages)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
