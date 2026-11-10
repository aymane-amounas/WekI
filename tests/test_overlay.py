import shutil
import sqlite3

import pytest

from weki.overlay import (
    MIGRATIONS,
    PRIORITY_NEW,
    PRIORITY_REST,
    BacklogItem,
    OverlayStore,
    fts_query,
)


def add(store, path="Ulm", title="Ulm", text="city on the Danube", rev=1):
    store.upsert_article(path, title, f"<p>{text}</p>", text, rev, "2026-11-01T00:00:00Z")


def item(path, ts="2026-11-01T00:00:00Z", priority=PRIORITY_REST, rev=None, kind="edit"):
    return BacklogItem(path, path, kind, ts, priority, rev)


def test_upsert_and_read(store):
    add(store)
    article = store.article("Ulm")
    assert article.title == "Ulm"
    assert article.rev_id == 1
    assert store.rev_id("Ulm") == 1
    assert store.article("Nope") is None


def test_migrations_are_idempotent(tmp_path):
    db = tmp_path / "o.db"
    first = OverlayStore(db)
    add(first)
    first.close()
    second = OverlayStore(db)
    assert second.schema_version == len(MIGRATIONS)
    assert second.article("Ulm") is not None
    second.close()


def test_unicode_and_space_path_reopens_with_default_rollback_journal(tmp_path):
    db_path = tmp_path / "portable archive" / "WekI 数据.db"
    first = OverlayStore(db_path)
    add(first, path="Café/Δ", title="Café Δ")
    assert first._db.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    first.close()

    second = OverlayStore(db_path)
    try:
        assert second.article("Café/Δ").title == "Café Δ"
    finally:
        second.close()


def test_invalid_journal_mode_rejected(tmp_path):
    with pytest.raises(ValueError):
        OverlayStore(tmp_path / "o.db", journal_mode="DELETE; DROP TABLE articles")


def test_failed_transaction_rolls_back(store):
    with pytest.raises(sqlite3.OperationalError):
        with store.transaction() as db:
            db.execute(
                "INSERT INTO sync_state (key, value) VALUES ('a', 'b')",
            )
            db.execute("SELECT * FROM missing_table")
    assert store.get_state("a") is None


def test_failed_commit_rolls_back_and_connection_remains_usable(store):
    store._db.execute("PRAGMA foreign_keys = ON")
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY constraint failed"):
        with store.transaction() as db:
            db.execute("CREATE TABLE parent (id INTEGER PRIMARY KEY)")
            db.execute(
                """
                CREATE TABLE child (
                    parent_id INTEGER REFERENCES parent(id)
                    DEFERRABLE INITIALLY DEFERRED
                )
                """
            )
            db.execute("INSERT INTO child (parent_id) VALUES (1)")

    assert not store._db.in_transaction
    assert (
        store._one("SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'child'") is None
    )
    store.set_state("after_failed_commit", "usable")
    assert store.get_state("after_failed_commit") == "usable"


def test_online_backup_restores_articles_fts_cursor_and_backlog(tmp_path):
    source_path = tmp_path / "source" / "overlay.db"
    backup_path = tmp_path / "backup" / "overlay.db"
    restored_path = tmp_path / "restored" / "overlay.db"
    source = OverlayStore(source_path)
    add(source, text="restorable overlay article")
    source.set_state("last_ts", "2026-11-10T12:00:00Z")
    source.enqueue(item("Queued", rev=7))

    try:
        with pytest.raises(ValueError, match="must differ"):
            source.backup_to(source_path)
        backup = source.backup_to(backup_path)
        assert backup == backup_path.resolve()
        with pytest.raises(FileExistsError):
            source.backup_to(backup_path)
        restored_path.parent.mkdir()
        shutil.copy2(backup, restored_path)
    finally:
        source.close()

    restored = OverlayStore(restored_path)
    try:
        assert restored.article("Ulm").rev_id == 1
        assert restored.search("restorable")[0].path == "Ulm"
        assert restored.get_state("last_ts") == "2026-11-10T12:00:00Z"
        assert [queued.path for queued in restored.next_backlog(10)] == ["Queued"]
        assert restored._db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        restored.close()


def test_path_lives_in_one_table(store):
    add(store)
    store.mark_deleted("Ulm", "Ulm", "2026-11-02T00:00:00Z")
    assert store.article("Ulm") is None
    assert store.deleted("Ulm").title == "Ulm"

    store.set_redirect("Ulm", "Neu-Ulm")
    assert store.deleted("Ulm") is None
    assert store.redirect_target("Ulm") == "Neu-Ulm"

    add(store)
    assert store.redirect_target("Ulm") is None
    assert store.deleted("Ulm") is None
    assert store.article("Ulm") is not None


def test_fts_tracks_update_and_delete(store):
    add(store, text="danube river")
    assert [h.path for h in store.search("danube")] == ["Ulm"]

    add(store, text="rhine river", rev=2)
    assert store.search("danube") == []
    assert [h.path for h in store.search("rhine")] == ["Ulm"]

    store.mark_deleted("Ulm", "Ulm", "2026-11-02T00:00:00Z")
    assert store.search("rhine") == []


def test_search_ranks_title_above_body(store):
    add(store, "Other", "Other", "ulm ulm ulm")
    add(store, "Ulm", "Ulm", "a city")
    assert store.search("ulm")[0].path == "Ulm"


@pytest.mark.parametrize("query", ["AC/DC", '"', "NOT", "a AND", "foo*", "col:val", "(x"])
def test_search_survives_fts_syntax_in_input(store, query):
    add(store)
    assert store.search(query) == []


def test_fts_query_quotes_terms():
    assert fts_query('say "hi"') == '"say" """hi"""'
    assert fts_query("   ") == ""


def test_history_counts_opens(store):
    store.record_open("Ulm", "Ulm")
    store.record_open("Ulm", "Ulm")
    store.record_open("Bern", "Bern")
    assert store.recent_history() == [("Bern", "Bern"), ("Ulm", "Ulm")]
    assert store.counts()["history"] == 2


def test_backlog_orders_history_then_new_then_rest(store):
    store.enqueue(item("Old", "2026-11-01T00:00:00Z"))
    store.enqueue(item("Recent", "2026-11-03T00:00:00Z"))
    store.enqueue(item("Fresh", "2026-11-02T00:00:00Z", PRIORITY_NEW, kind="new"))
    store.enqueue(item("Read", "2026-10-01T00:00:00Z"))
    store.record_open("Read", "Read")
    assert [b.path for b in store.next_backlog(10)] == ["Read", "Fresh", "Recent", "Old"]
    assert [b.path for b in store.next_backlog(2)] == ["Read", "Fresh"]


def test_enqueue_merges_keeping_strongest_priority_and_newest_rev(store):
    store.enqueue(item("Ulm", "2026-11-01T00:00:00Z", PRIORITY_NEW, rev=5, kind="new"))
    store.enqueue(item("Ulm", "2026-11-03T00:00:00Z", PRIORITY_REST, rev=9))
    [merged] = store.next_backlog(10)
    assert (merged.kind, merged.priority) == ("new", PRIORITY_NEW)
    assert (merged.rc_ts, merged.rev_id) == ("2026-11-03T00:00:00Z", 9)


def test_dequeue(store):
    store.enqueue(item("Ulm"))
    store.dequeue("Ulm")
    assert store.next_backlog(10) == []


def test_state_roundtrip(store):
    assert store.get_state("last_ts") is None
    store.set_state("last_ts", "2026-11-01T00:00:00Z")
    store.set_state("last_ts", "2026-11-02T00:00:00Z")
    assert store.get_state("last_ts") == "2026-11-02T00:00:00Z"
