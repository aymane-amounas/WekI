import asyncio
import multiprocessing
import sqlite3
import threading
from contextlib import contextmanager
from datetime import UTC, datetime

import httpx
import pytest
import respx

from weki.config import SyncConfig
from weki.overlay import PRIORITY_REST, BacklogItem, OverlayStore
from weki.sync.changes import Change
from weki.sync.client import PageData, WikiClient
from weki.sync.worker import SyncWorker

BASE = "https://simple.wikipedia.org"
API = f"{BASE}/w/api.php"
NOW = datetime(2026, 11, 7, 12, 0, 0, tzinfo=UTC)
T1 = "2026-11-01T00:00:00Z"
T2 = "2026-11-02T00:00:00Z"


@pytest.fixture
def anyio_backend():
    return "asyncio"


class Wiki:
    """Minimal fake of the two Wikimedia APIs."""

    def __init__(self, revisions=(), logs=None, pages=None):
        self.revisions = list(revisions)
        self.logs = logs or {}
        self.pages = pages or {}
        self.fetched = []
        self.queries = []

    def api(self, request):
        params = dict(request.url.params)
        self.queries.append(params)
        if params.get("meta") == "siteinfo":
            return httpx.Response(200, json={})
        entries = self.revisions if params["list"] == "allrevisions" else []
        if params["list"] == "logevents":
            entries = self.logs.get(params["letype"], [])
        return httpx.Response(200, json={"query": {params["list"]: entries}})

    def page(self, request):
        key = request.url.raw_path.decode().split("/page/")[1].split("/with_html")[0]
        self.fetched.append(key)
        return self.pages.get(key) or httpx.Response(404, json={})

    def install(self):
        respx.get(API).mock(side_effect=self.api)
        respx.get(url__regex=rf"{BASE}/w/rest\.php/v1/page/.*").mock(side_effect=self.page)


def html_page(title, rev=10, body="fresh"):
    return httpx.Response(
        200,
        json={
            "title": title,
            "latest": {"id": rev, "timestamp": T2},
            "html": f"<html><body><p>{body}</p><img src='x'></body></html>",
        },
    )


def rev(title, revid=10, parent=9, ts=T2):
    return {"title": title, "revisions": [{"revid": revid, "parentid": parent, "timestamp": ts}]}


def _process_exit_at_sync_boundary(database: str, crash_point: str, stopping_point) -> None:
    class FakeClient:
        async def is_online(self):
            return True

        async def paged(self, params, list_key):
            if params.get("list") == "allrevisions":
                yield rev("Ulm")

        async def page(self, title):
            if crash_point == "during_fetch":
                stopping_point.set()
                await asyncio.Future()
            page = PageData(title, 10, T2, "<p>recovered</p>")
            if crash_point == "after_response":
                stopping_point.set()
                await asyncio.Future()
            return page

    overlay = OverlayStore(database)
    overlay.set_state("last_ts", T1)

    if crash_point in {"cursor_commit", "article_commit"}:
        original_transaction = overlay.transaction

        @contextmanager
        def blocked_transaction():
            outermost = not overlay._db.in_transaction
            with original_transaction() as db:
                yield db
                if outermost:
                    cursor = db.execute(
                        "SELECT value FROM sync_state WHERE key = 'last_ts'"
                    ).fetchone()
                    article = db.execute("SELECT path FROM articles WHERE path = 'Ulm'").fetchone()
                    should_block = (
                        crash_point == "cursor_commit"
                        and cursor is not None
                        and cursor["value"] == "2026-11-07T12:00:00Z"
                        or crash_point == "article_commit"
                        and article is not None
                    )
                    if should_block and not stopping_point.is_set():
                        stopping_point.set()
                        threading.Event().wait(120)

        overlay.transaction = blocked_transaction

    worker = SyncWorker(
        SyncConfig(),
        overlay,
        FakeClient(),
        zim_date="2026-09-28",
        now=lambda: NOW,
    )
    if crash_point == "after_response":

        async def stop_before_persist(function, /, *args, **kwargs):
            stopping_point.set()
            await asyncio.Future()

        asyncio.to_thread = stop_before_persist
    asyncio.run(worker.run())


@pytest.fixture
async def make(store):
    clients = []

    def build(cap=300, zim_date="2026-09-28"):
        async def sleep(_):
            pass

        client = WikiClient("simple.wikipedia.org", "ua", delay=0, sleep=sleep, max_retries=1)
        clients.append(client)
        config = SyncConfig(cap=cap)
        return SyncWorker(config, store, client, zim_date=zim_date, now=lambda: NOW)

    yield build
    for client in clients:
        await client.aclose()


@pytest.mark.anyio
@respx.mock
async def test_first_sync_starts_before_zim_date_and_stores_cleaned_article(store, make):
    wiki = Wiki([rev("Ulm")], pages={"Ulm": html_page("Ulm")})
    wiki.install()
    await make().run()

    listing = next(q for q in wiki.queries if q.get("list") == "allrevisions")
    assert listing["arvstart"] == "2026-09-23T00:00:00Z"
    assert listing["arvend"] == "2026-11-07T12:00:00Z"
    article = store.article("Ulm")
    assert article.rev_id == 10
    assert "<img" not in article.html
    assert [h.path for h in store.search("fresh")] == ["Ulm"]
    assert store.get_state("last_ts") == "2026-11-07T12:00:00Z"
    assert store.get_state("last_result") == "ok"
    assert store.next_backlog(10) == []


@pytest.mark.anyio
@respx.mock
async def test_next_sync_starts_from_cursor_minus_overlap(store, make):
    store.set_state("last_ts", "2026-11-07T10:00:00Z")
    wiki = Wiki()
    wiki.install()
    await make().run()
    listing = next(q for q in wiki.queries if q.get("list") == "allrevisions")
    assert listing["arvstart"] == "2026-11-07T09:55:00Z"


@pytest.mark.anyio
@respx.mock
async def test_cancelled_fetch_recovers_from_persistent_backlog_after_restart(tmp_path):
    async def sleep(_):
        pass

    overlay_path = tmp_path / "overlay.db"
    overlay = OverlayStore(overlay_path)
    worker = SyncWorker(
        SyncConfig(),
        overlay,
        WikiClient("simple.wikipedia.org", "ua", delay=0, sleep=sleep, max_retries=0),
        zim_date="2026-09-28",
        now=lambda: NOW,
    )
    wiki = Wiki([rev("Ulm")], pages={"Ulm": html_page("Ulm")})
    started = asyncio.Event()
    interrupted = True
    overlay_open = True

    async def page_request(request):
        if interrupted:
            started.set()
            await asyncio.Future()
        return wiki.page(request)

    respx.get(API).mock(side_effect=wiki.api)
    respx.get(url__regex=rf"{BASE}/w/rest\.php/v1/page/.*").mock(side_effect=page_request)

    try:
        task = asyncio.create_task(worker.run())
        await asyncio.wait_for(started.wait(), timeout=2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert not worker.status.running
        assert overlay.get_state("last_ts") == "2026-11-07T12:00:00Z"
        assert overlay.get_state("last_result") == "cancelled"
        assert [item.path for item in overlay.next_backlog(10)] == ["Ulm"]

        overlay.close()
        overlay_open = False
        overlay = OverlayStore(overlay_path)
        overlay_open = True
        interrupted = False
        restarted = SyncWorker(SyncConfig(), overlay, worker.client, now=lambda: NOW)
        await restarted.run()

        assert overlay.article("Ulm").rev_id == 10
        assert overlay.next_backlog(10) == []
        assert overlay.get_state("last_result") == "ok"
        assert not restarted.status.running
    finally:
        if overlay_open:
            overlay.close()
        await worker.client.aclose()


@pytest.mark.parametrize(
    ("crash_point", "expected_cursor", "expected_backlog"),
    [
        ("during_fetch", "2026-11-07T12:00:00Z", 1),
        ("after_response", "2026-11-07T12:00:00Z", 1),
        ("article_commit", "2026-11-07T12:00:00Z", 1),
        ("cursor_commit", T1, 0),
    ],
)
def test_killed_process_recovers_consistent_state(
    tmp_path, crash_point, expected_cursor, expected_backlog
):
    database = tmp_path / "overlay.db"
    context = multiprocessing.get_context("spawn")
    stopping_point = context.Event()
    process = context.Process(
        target=_process_exit_at_sync_boundary,
        args=(str(database), crash_point, stopping_point),
    )
    process.start()
    try:
        assert stopping_point.wait(timeout=20), f"worker did not reach {crash_point}"
        process.kill()
        process.join(timeout=10)
        assert not process.is_alive()
    finally:
        if process.is_alive():
            process.kill()
            process.join(timeout=10)

    overlay = OverlayStore(database)
    integrity = overlay._db.execute("PRAGMA integrity_check").fetchone()[0]

    class RecoveryClient:
        async def is_online(self):
            return True

        async def paged(self, params, list_key):
            if params.get("list") == "allrevisions":
                yield rev("Ulm")

        async def page(self, title):
            return PageData(title, 10, T2, "<p>recovered</p>")

    try:
        assert integrity == "ok"
        assert overlay.get_state("last_ts") == expected_cursor
        assert len(overlay.next_backlog(10)) == expected_backlog
        assert overlay.article("Ulm") is None
        assert overlay.search("recovered") == []
        restarted = SyncWorker(SyncConfig(), overlay, RecoveryClient(), now=lambda: NOW)
        asyncio.run(restarted.run())
        assert overlay.article("Ulm").rev_id == 10
        assert overlay.counts()["articles"] == 1
        assert overlay.search("recovered")[0].path == "Ulm"
        assert overlay.next_backlog(10) == []
        assert overlay.get_state("last_result") == "ok"
    finally:
        overlay.close()


@pytest.mark.anyio
@respx.mock
async def test_gap_older_than_thirty_days_uses_the_same_path(store, make):
    store.set_state("last_ts", "2026-01-01T00:00:00Z")
    wiki = Wiki([rev("Ulm")], pages={"Ulm": html_page("Ulm")})
    wiki.install()
    await make().run()
    assert store.article("Ulm") is not None


@pytest.mark.anyio
async def test_offline_returns_silently_and_keeps_cursor(store, make):
    with respx.mock:
        respx.get(API).mock(side_effect=httpx.ConnectError("no network"))
        store.set_state("last_ts", T1)
        worker = make()
        await worker.run()
    assert store.get_state("last_ts") == T1
    assert store.get_state("last_result") == "offline"
    assert not worker.status.running


@pytest.mark.anyio
@respx.mock
async def test_deletion_applies_without_fetching_and_clears_queue(store, make):
    store.upsert_article("Ulm", "Ulm", "<p>x</p>", "x", 1, T1)
    store.enqueue(BacklogItem("Ulm", "Ulm", "edit", T1, PRIORITY_REST, 5))
    event = {"type": "delete", "action": "delete", "title": "Ulm", "ns": 0, "timestamp": T2}
    wiki = Wiki(logs={"delete": [event]})
    wiki.install()
    await make().run()
    assert store.deleted("Ulm").deleted_at == T2
    assert store.article("Ulm") is None
    assert wiki.fetched == []


@pytest.mark.anyio
@respx.mock
async def test_restore_refetches_and_clears_tombstone(store, make):
    store.mark_deleted("Ulm", "Ulm", T1)
    event = {"type": "delete", "action": "restore", "title": "Ulm", "ns": 0, "timestamp": T2}
    wiki = Wiki(logs={"delete": [event]}, pages={"Ulm": html_page("Ulm")})
    wiki.install()
    await make().run()
    assert store.deleted("Ulm") is None
    assert store.article("Ulm") is not None


@pytest.mark.anyio
@respx.mock
async def test_move_leaves_redirect_at_source_and_article_at_target(store, make):
    event = {
        "type": "move",
        "action": "move",
        "title": "Old",
        "ns": 0,
        "timestamp": T2,
        "params": {"target_ns": 0, "target_title": "New"},
    }
    redirect = httpx.Response(
        200,
        json={
            "title": "Old",
            "latest": {"id": 11, "timestamp": T2},
            "html": "",
            "redirect_target": "/w/rest.php/v1/page/New/with_html?redirect=no",
        },
    )
    wiki = Wiki(logs={"move": [event]}, pages={"Old": redirect, "New": html_page("New")})
    wiki.install()
    await make().run()
    assert store.redirect_target("Old") == "New"
    assert store.article("New") is not None


@pytest.mark.anyio
@respx.mock
async def test_move_without_redirect_marks_source_deleted(store, make):
    event = {
        "type": "move",
        "action": "move",
        "title": "Old",
        "ns": 0,
        "timestamp": T2,
        "params": {"target_ns": 0, "target_title": "New", "suppressredirect": True},
    }
    wiki = Wiki(logs={"move": [event]}, pages={"New": html_page("New")})
    wiki.install()
    await make().run()
    assert store.deleted("Old").title == "Old"


@pytest.mark.anyio
@respx.mock
async def test_page_gone_by_fetch_time_is_marked_deleted(store, make):
    wiki = Wiki([rev("Ulm")])
    wiki.install()
    await make().run()
    assert store.deleted("Ulm") is not None


@pytest.mark.anyio
@respx.mock
async def test_unchanged_revision_is_not_refetched(store, make):
    store.upsert_article("Ulm", "Ulm", "<p>x</p>", "x", 10, T1)
    wiki = Wiki([rev("Ulm", revid=10)])
    wiki.install()
    await make().run()
    assert wiki.fetched == []


@pytest.mark.anyio
@respx.mock
async def test_replaying_same_revision_is_idempotent(store, make):
    wiki = Wiki([rev("Ulm")], pages={"Ulm": html_page("Ulm")})
    wiki.install()
    worker = make()

    await worker.run()
    await worker.run()

    assert wiki.fetched == ["Ulm"]
    assert store.counts()["articles"] == 1
    assert store.search("fresh")[0].path == "Ulm"
    assert store.next_backlog(10) == []


@pytest.mark.anyio
async def test_listing_transaction_rolls_back_backlog_when_cursor_write_fails(store, make):
    store.set_state("last_ts", T1)
    with store.transaction() as db:
        db.execute(
            """
            CREATE TRIGGER reject_cursor BEFORE INSERT ON sync_state
            WHEN NEW.key = 'last_ts'
            BEGIN SELECT RAISE(ABORT, 'cursor write failed'); END
            """
        )
    worker = make()

    with pytest.raises(sqlite3.IntegrityError, match="cursor write failed"):
        worker._record({"Ulm": Change("Ulm", "Ulm", "edit", T2, 10)}, T2)

    assert store.get_state("last_ts") == T1
    assert store.next_backlog(10) == []


@pytest.mark.anyio
@respx.mock
async def test_cap_orders_by_priority_and_backlog_carries_over(store, make):
    store.record_open("Read", "Read")
    revisions = [
        rev("Old", ts="2026-11-01T00:00:00Z"),
        rev("Recent", ts="2026-11-03T00:00:00Z"),
        rev("Fresh", parent=0, ts="2026-11-02T00:00:00Z"),
        rev("Read", ts="2026-10-01T00:00:00Z"),
    ]
    pages = {t: html_page(t) for t in ("Old", "Recent", "Fresh", "Read")}
    wiki = Wiki(revisions, pages=pages)
    wiki.install()

    await make(cap=2).run()
    assert sorted(wiki.fetched) == ["Fresh", "Read"]
    assert [b.path for b in store.next_backlog(10)] == ["Recent", "Old"]

    wiki.revisions = []
    await make(cap=2).run()
    assert sorted(wiki.fetched) == ["Fresh", "Old", "Read", "Recent"]
    assert store.next_backlog(10) == []


@pytest.mark.anyio
@respx.mock
async def test_rate_limit_mid_run_keeps_unfetched_pages_queued(store, make):
    pages = {"Ulm": httpx.Response(429, headers={"Retry-After": "9999"})}
    wiki = Wiki([rev("Ulm")], pages=pages)
    wiki.install()
    await make().run()
    assert "rate limit" in store.get_state("last_result")
    assert [b.path for b in store.next_backlog(10)] == ["Ulm"]
    assert store.get_state("last_ts") == "2026-11-07T12:00:00Z"


@pytest.mark.anyio
@respx.mock
async def test_one_bad_page_does_not_stop_the_others(store, make):
    pages = {"Bad": httpx.Response(500), "Good": html_page("Good")}
    wiki = Wiki([rev("Bad"), rev("Good")], pages=pages)
    wiki.install()
    worker = make()
    await worker.run()
    assert store.article("Good") is not None
    assert [b.path for b in store.next_backlog(10)] == ["Bad"]
    assert "1 pages failed" in store.get_state("last_result")
    assert worker.status_dict()["failed"] == 1


@pytest.mark.anyio
@respx.mock
async def test_listing_failure_leaves_cursor_untouched(store, make):
    def api(request):
        if request.url.params.get("meta"):
            return httpx.Response(200, json={})
        return httpx.Response(200, json={"error": {"code": "internal_api_error"}})

    respx.get(API).mock(side_effect=api)
    store.set_state("last_ts", T1)
    await make().run()
    assert store.get_state("last_ts") == T1
    assert store.get_state("last_result").startswith("error")


@pytest.mark.anyio
async def test_database_failure_is_classified_and_worker_returns_to_idle(store, make, caplog):
    worker = make()

    async def fail():
        raise sqlite3.OperationalError("database or disk is full")

    worker._run = fail
    await worker.run()

    assert store.get_state("last_result") == "database error: database or disk is full"
    assert "sync database error" in caplog.text
    assert not worker.status.running
    assert worker.status.phase == "idle"


@pytest.mark.anyio
@respx.mock
async def test_second_run_while_running_is_a_noop(store, make):
    wiki = Wiki()
    wiki.install()
    worker = make()
    worker.status.running = True
    await worker.run()
    assert wiki.queries == []


def test_first_sync_without_zim_uses_lookback_from_now(store):
    worker = SyncWorker(SyncConfig(), store, None, now=lambda: NOW)
    assert worker.start_ts() == "2026-11-02T12:00:00Z"
