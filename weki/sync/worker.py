"""One sync run: list changes, record them in the backlog, then drain up to `cap` pages.

Listing commits in a single transaction together with the cursor, so a crash can't lose a change.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from weki.config import SyncConfig
from weki.htmlclean import clean
from weki.overlay import PRIORITY_NEW, PRIORITY_REST, BacklogItem, OverlayStore
from weki.sync.changes import Change, list_changes
from weki.sync.client import Offline, PageData, RateLimited, SyncError, WikiClient, WikiError

log = logging.getLogger(__name__)

TS_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
NEW_KINDS = {"new", "move", "merge", "restore"}


def format_ts(value: datetime) -> str:
    return value.astimezone(UTC).strftime(TS_FORMAT)


def parse_ts(value: str) -> datetime:
    return datetime.strptime(value, TS_FORMAT).replace(tzinfo=UTC)


def parse_zim_date(value: str | None) -> datetime | None:
    try:
        return datetime.strptime(value or "", "%Y-%m-%d").replace(tzinfo=UTC)
    except ValueError:
        return None


@dataclass
class SyncStatus:
    running: bool = False
    phase: str = "idle"
    done: int = 0
    total: int = 0
    failed: int = 0


class SyncWorker:
    def __init__(
        self,
        config: SyncConfig,
        overlay: OverlayStore,
        client: WikiClient,
        *,
        zim_date: str | None = None,
        nopic: bool = True,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.config = config
        self.overlay = overlay
        self.client = client
        self.zim_date = parse_zim_date(zim_date)
        self.nopic = nopic
        self.now = now
        self.status = SyncStatus()

    def start_ts(self) -> str:
        if last := self.overlay.get_state("last_ts"):
            return format_ts(parse_ts(last) - timedelta(minutes=self.config.overlap_minutes))
        base = self.zim_date or self.now()
        return format_ts(base - timedelta(days=self.config.first_sync_lookback_days))

    async def run(self) -> None:
        if self.status.running:
            return
        self.status = SyncStatus(running=True, phase="checking connection")
        try:
            try:
                result = await self._run()
            except asyncio.CancelledError:
                self._finish("cancelled")
                raise
            except Offline:
                result = "offline"
            except RateLimited as exc:
                result = f"paused by rate limit: {exc}"
            except SyncError as exc:
                result = f"error: {exc}"
            except sqlite3.Error as exc:
                log.exception("sync database error")
                result = f"database error: {exc}"
            except OSError as exc:
                log.exception("sync storage error")
                result = f"storage error: {exc}"
            except Exception as exc:
                log.exception("sync failed")
                result = f"error: {exc}"
            self._finish(result)
        except sqlite3.Error:
            log.exception("could not persist sync result")
            raise
        except OSError:
            log.exception("could not persist sync result")
            raise
        finally:
            self.status.running = False
            self.status.phase = "idle"

    def _finish(self, result: str) -> None:
        self.overlay.set_state("last_run", format_ts(self.now()))
        self.overlay.set_state("last_result", result)

    async def _run(self) -> str:
        if not await self.client.is_online():
            return "offline"

        self.status.phase = "listing changes"
        until = format_ts(self.now())
        changes = await list_changes(self.client, self.start_ts(), until)
        self._record(changes, until)

        self.status.phase = "fetching pages"
        batch = self.overlay.next_backlog(self.config.cap)
        self.status.total = len(batch)
        try:
            async with asyncio.TaskGroup() as group:
                for item in batch:
                    group.create_task(self._fetch(item))
        except* SyncError as failed:
            raise failed.exceptions[0] from None

        if self.status.failed:
            return f"ok, {self.status.failed} pages failed and stay queued"
        return "ok"

    def _record(self, changes: dict[str, Change], until: str) -> None:
        with self.overlay.transaction():
            for change in changes.values():
                if change.is_delete:
                    self.overlay.mark_deleted(change.path, change.title, change.ts)
                    self.overlay.dequeue(change.path)
                elif not self._up_to_date(change):
                    priority = PRIORITY_NEW if change.kind in NEW_KINDS else PRIORITY_REST
                    self.overlay.enqueue(
                        BacklogItem(
                            change.path,
                            change.title,
                            change.kind,
                            change.ts,
                            priority,
                            change.rev_id,
                        )
                    )
            self.overlay.set_state("last_ts", until)

    def _up_to_date(self, change: Change) -> bool:
        stored = self.overlay.rev_id(change.path)
        return stored is not None and change.rev_id is not None and stored >= change.rev_id

    async def _fetch(self, item: BacklogItem) -> None:
        try:
            page = await self.client.page(item.title)
        except WikiError as exc:
            log.warning("skipping %s: %s", item.title, exc)
            self.status.failed += 1
            return
        await asyncio.to_thread(self._store, item, page)
        self.status.done += 1

    def _store(self, item: BacklogItem, page: PageData | None) -> None:
        cleaned = clean(page.html, nopic=self.nopic) if page and not page.redirect_to else None
        with self.overlay.transaction():
            if page is None:
                self.overlay.mark_deleted(item.path, item.title, item.rc_ts)
            elif page.redirect_to:
                self.overlay.set_redirect(item.path, page.redirect_to.replace(" ", "_"))
            else:
                self.overlay.upsert_article(
                    item.path, page.title, cleaned.html, cleaned.text, page.rev_id, page.rev_ts
                )
            self.overlay.dequeue(item.path)

    def status_dict(self) -> dict:
        return {
            "running": self.status.running,
            "phase": self.status.phase,
            "done": self.status.done,
            "total": self.status.total,
            "failed": self.status.failed,
            "last_ts": self.overlay.get_state("last_ts"),
            "last_run": self.overlay.get_state("last_run"),
            "last_result": self.overlay.get_state("last_result"),
        }
