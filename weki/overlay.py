"""SQLite overlay: articles changed since the ZIM snapshot, plus sync state.

A path lives in at most one of articles / deleted / redirects; each write clears the others.
"""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

PRIORITY_NEW = 1
PRIORITY_REST = 2

HIT_START, HIT_END = "\x02", "\x03"


MIGRATIONS = [
    """
    CREATE TABLE articles (
        id INTEGER PRIMARY KEY,
        path TEXT NOT NULL UNIQUE,
        title TEXT NOT NULL,
        html TEXT NOT NULL,
        text TEXT NOT NULL,
        rev_id INTEGER NOT NULL,
        rev_ts TEXT NOT NULL,
        fetched_at REAL NOT NULL
    );
    CREATE VIRTUAL TABLE articles_fts USING fts5(
        title, text, content='articles', content_rowid='id'
    );
    CREATE TRIGGER articles_ai AFTER INSERT ON articles BEGIN
        INSERT INTO articles_fts(rowid, title, text) VALUES (new.id, new.title, new.text);
    END;
    CREATE TRIGGER articles_ad AFTER DELETE ON articles BEGIN
        INSERT INTO articles_fts(articles_fts, rowid, title, text)
        VALUES ('delete', old.id, old.title, old.text);
    END;
    CREATE TRIGGER articles_au AFTER UPDATE ON articles BEGIN
        INSERT INTO articles_fts(articles_fts, rowid, title, text)
        VALUES ('delete', old.id, old.title, old.text);
        INSERT INTO articles_fts(rowid, title, text) VALUES (new.id, new.title, new.text);
    END;
    CREATE TABLE deleted (
        path TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        deleted_at TEXT NOT NULL
    );
    CREATE TABLE redirects (
        path TEXT PRIMARY KEY,
        target TEXT NOT NULL
    );
    CREATE TABLE history (
        path TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        last_opened REAL NOT NULL,
        open_count INTEGER NOT NULL DEFAULT 1
    );
    CREATE TABLE sync_state (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    CREATE TABLE backlog (
        path TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        kind TEXT NOT NULL,
        rc_ts TEXT NOT NULL,
        priority INTEGER NOT NULL,
        rev_id INTEGER
    );
    """,
]


@dataclass(frozen=True)
class Article:
    path: str
    title: str
    html: str
    rev_id: int
    rev_ts: str


@dataclass(frozen=True)
class DeletedPage:
    path: str
    title: str
    deleted_at: str


@dataclass(frozen=True)
class OverlayHit:
    path: str
    title: str
    snippet: str
    rev_ts: str


@dataclass(frozen=True)
class BacklogItem:
    path: str
    title: str
    kind: str
    rc_ts: str
    priority: int
    rev_id: int | None = None


def backup_database(source: sqlite3.Connection, source_path: Path, destination: Path) -> Path:
    """Create a new, integrity-checked backup without replacing an existing file."""
    source_path = Path(source_path).resolve()
    destination = Path(destination).expanduser().resolve()
    if destination == source_path:
        raise ValueError("backup destination must differ from the overlay database")
    if source.in_transaction:
        raise sqlite3.OperationalError("cannot back up an open SQLite transaction")
    destination.parent.mkdir(parents=True, exist_ok=True)

    descriptor = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(descriptor)
    backup = None
    try:
        backup = sqlite3.connect(destination)
        source.backup(backup)
        integrity = backup.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise sqlite3.DatabaseError(f"backup integrity check failed: {integrity}")
    except BaseException:
        if backup is not None:
            backup.close()
        destination.unlink(missing_ok=True)
        raise
    else:
        backup.close()
    return destination


def fts_query(query: str) -> str:
    """Quote every term so input like AC/DC or a stray quote can't be read as FTS5 syntax."""
    return " ".join('"' + term.replace('"', '""') + '"' for term in query.split())


class OverlayStore:
    def __init__(self, path: Path, journal_mode: str = "DELETE") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._db.execute(f"PRAGMA journal_mode={_journal_mode(journal_mode)}")
        self._migrate()

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def backup_to(self, destination: Path) -> Path:
        with self._lock:
            return backup_database(self._db, self.path, destination)

    def _migrate(self) -> None:
        for number, sql in enumerate(
            MIGRATIONS[self.schema_version :], start=self.schema_version + 1
        ):
            try:
                self._db.executescript(
                    f"BEGIN IMMEDIATE; {sql}; PRAGMA user_version={number}; COMMIT;"
                )
            except sqlite3.Error:
                if self._db.in_transaction:
                    self._db.execute("ROLLBACK")
                raise

    @property
    def schema_version(self) -> int:
        with self._lock:
            return self._db.execute("PRAGMA user_version").fetchone()[0]

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            if self._db.in_transaction:
                yield self._db
                return
            self._db.execute("BEGIN IMMEDIATE")
            try:
                yield self._db
                self._db.execute("COMMIT")
            except BaseException:
                if self._db.in_transaction:
                    self._db.execute("ROLLBACK")
                raise

    def _query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._db.execute(sql, params).fetchall()

    def _one(self, sql: str, params: tuple = ()) -> sqlite3.Row | None:
        rows = self._query(sql, params)
        return rows[0] if rows else None

    def article(self, path: str) -> Article | None:
        row = self._one(
            "SELECT path, title, html, rev_id, rev_ts FROM articles WHERE path = ?", (path,)
        )
        return Article(**row) if row else None

    def rev_id(self, path: str) -> int | None:
        row = self._one("SELECT rev_id FROM articles WHERE path = ?", (path,))
        return row["rev_id"] if row else None

    def upsert_article(
        self, path: str, title: str, html: str, text: str, rev_id: int, rev_ts: str
    ) -> None:
        with self.transaction() as db:
            db.execute("DELETE FROM deleted WHERE path = ?", (path,))
            db.execute("DELETE FROM redirects WHERE path = ?", (path,))
            db.execute(
                """
                INSERT INTO articles (path, title, html, text, rev_id, rev_ts, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(path) DO UPDATE SET
                    title = excluded.title, html = excluded.html, text = excluded.text,
                    rev_id = excluded.rev_id, rev_ts = excluded.rev_ts,
                    fetched_at = excluded.fetched_at
                """,
                (path, title, html, text, rev_id, rev_ts, time.time()),
            )

    def deleted(self, path: str) -> DeletedPage | None:
        row = self._one("SELECT path, title, deleted_at FROM deleted WHERE path = ?", (path,))
        return DeletedPage(**row) if row else None

    def deleted_paths(self, paths: Iterable[str]) -> set[str]:
        return self._present("deleted", paths)

    def article_paths(self, paths: Iterable[str]) -> set[str]:
        return self._present("articles", paths)

    def _present(self, table: str, paths: Iterable[str]) -> set[str]:
        paths = tuple(paths)
        marks = ",".join("?" * len(paths))
        rows = self._query(f"SELECT path FROM {table} WHERE path IN ({marks})", paths)
        return {row["path"] for row in rows}

    def mark_deleted(self, path: str, title: str, deleted_at: str) -> None:
        with self.transaction() as db:
            db.execute("DELETE FROM articles WHERE path = ?", (path,))
            db.execute("DELETE FROM redirects WHERE path = ?", (path,))
            db.execute(
                "INSERT OR REPLACE INTO deleted (path, title, deleted_at) VALUES (?, ?, ?)",
                (path, title, deleted_at),
            )

    def redirect_target(self, path: str) -> str | None:
        row = self._one("SELECT target FROM redirects WHERE path = ?", (path,))
        return row["target"] if row else None

    def set_redirect(self, path: str, target: str) -> None:
        with self.transaction() as db:
            db.execute("DELETE FROM articles WHERE path = ?", (path,))
            db.execute("DELETE FROM deleted WHERE path = ?", (path,))
            db.execute(
                "INSERT OR REPLACE INTO redirects (path, target) VALUES (?, ?)", (path, target)
            )

    def search(self, query: str, limit: int = 30) -> list[OverlayHit]:
        match = fts_query(query)
        if not match:
            return []
        rows = self._query(
            """
            SELECT a.path, a.title, a.rev_ts,
                   snippet(articles_fts, 1, ?, ?, '…', 24) AS snippet
            FROM articles_fts
            JOIN articles a ON a.id = articles_fts.rowid
            WHERE articles_fts MATCH ?
            ORDER BY bm25(articles_fts, 10.0, 1.0)
            LIMIT ?
            """,
            (HIT_START, HIT_END, match, limit),
        )
        return [OverlayHit(**row) for row in rows]

    def suggest(self, query: str, limit: int = 10) -> list[tuple[str, str]]:
        prefix = query.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        if not prefix:
            return []
        rows = self._query(
            "SELECT path, title FROM articles WHERE title LIKE ? ESCAPE '\\' "
            "ORDER BY title LIMIT ?",
            (prefix + "%", limit),
        )
        return [(row["path"], row["title"]) for row in rows]

    def record_open(self, path: str, title: str) -> None:
        with self.transaction() as db:
            db.execute(
                """
                INSERT INTO history (path, title, last_opened) VALUES (?, ?, ?)
                ON CONFLICT(path) DO UPDATE SET
                    title = excluded.title, last_opened = excluded.last_opened,
                    open_count = open_count + 1
                """,
                (path, title, time.time()),
            )

    def recent_history(self, limit: int = 12) -> list[tuple[str, str]]:
        rows = self._query(
            "SELECT path, title FROM history ORDER BY last_opened DESC LIMIT ?", (limit,)
        )
        return [(row["path"], row["title"]) for row in rows]

    def get_state(self, key: str) -> str | None:
        row = self._one("SELECT value FROM sync_state WHERE key = ?", (key,))
        return row["value"] if row else None

    def set_state(self, key: str, value: str) -> None:
        with self.transaction() as db:
            db.execute("INSERT OR REPLACE INTO sync_state (key, value) VALUES (?, ?)", (key, value))

    def enqueue(self, item: BacklogItem) -> None:
        with self.transaction() as db:
            db.execute(
                """
                INSERT INTO backlog (path, title, kind, rc_ts, priority, rev_id)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(path) DO UPDATE SET
                    title = excluded.title,
                    kind = CASE WHEN excluded.priority < backlog.priority
                                THEN excluded.kind ELSE backlog.kind END,
                    priority = min(excluded.priority, backlog.priority),
                    rc_ts = max(excluded.rc_ts, backlog.rc_ts),
                    rev_id = max(excluded.rev_id, backlog.rev_id)
                """,
                (item.path, item.title, item.kind, item.rc_ts, item.priority, item.rev_id),
            )

    def dequeue(self, path: str) -> None:
        with self.transaction() as db:
            db.execute("DELETE FROM backlog WHERE path = ?", (path,))

    def next_backlog(self, limit: int) -> list[BacklogItem]:
        """Pages you have read first, then new pages, then the rest; newest first in each."""
        rows = self._query(
            """
            SELECT b.path, b.title, b.kind, b.rc_ts, b.priority, b.rev_id
            FROM backlog b
            ORDER BY EXISTS (SELECT 1 FROM history h WHERE h.path = b.path) DESC,
                     b.priority, b.rc_ts DESC, b.path
            LIMIT ?
            """,
            (limit,),
        )
        return [BacklogItem(**row) for row in rows]

    def counts(self) -> dict[str, int]:
        return {
            table: self._one(f"SELECT count(*) AS n FROM {table}")["n"]
            for table in ("articles", "deleted", "redirects", "backlog", "history")
        }


def _journal_mode(mode: str) -> str:
    allowed = {"DELETE", "TRUNCATE", "PERSIST", "MEMORY", "WAL", "OFF"}
    if mode.upper() not in allowed:
        raise ValueError(f"unsupported journal_mode: {mode}")
    return mode.upper()
