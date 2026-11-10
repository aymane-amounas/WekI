import sqlite3
import sys

import pytest

from scripts import backup_overlay
from weki.config import Config
from weki.overlay import OverlayStore


def test_backup_cli_uses_read_only_source_and_does_not_overwrite(tmp_path, monkeypatch, capsys):
    source_path = tmp_path / "source.db"
    source = OverlayStore(source_path)
    source.upsert_article("Ulm", "Ulm", "<p>city</p>", "city", 4, "2026-11-10T00:00:00Z")
    source.close()

    monkeypatch.setattr(backup_overlay, "APP_ROOT", tmp_path)
    monkeypatch.setattr(
        backup_overlay, "load", lambda: Config(zim_path=None, overlay_path=source_path)
    )
    monkeypatch.setattr(sys, "argv", ["backup_overlay.py", "backup.db"])

    assert backup_overlay.main() == 0
    backup_path = tmp_path / "backup.db"
    with sqlite3.connect(backup_path) as db:
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert db.execute("SELECT rev_id FROM articles WHERE path = 'Ulm'").fetchone()[0] == 4
    assert "verified overlay backup" in capsys.readouterr().out

    with pytest.raises(FileExistsError):
        backup_overlay.main()
