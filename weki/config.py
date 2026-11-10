"""Load config.toml. Every path resolves against the app root so the disk stays portable."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class SyncConfig:
    cap: int = 300
    concurrency: int = 2
    delay_seconds: float = 0.5
    overlap_minutes: int = 5
    first_sync_lookback_days: int = 5
    user_agent: str = "WekI/0.1.0-alpha.1 (https://github.com/aymane-amounas/WekI; bot)"


@dataclass(frozen=True)
class Config:
    zim_path: Path | None
    overlay_path: Path
    wiki_domain: str = "simple.wikipedia.org"
    mode: str = "nopic"
    journal_mode: str = "DELETE"
    host: str = "127.0.0.1"
    port: int = 8787
    sync: SyncConfig = SyncConfig()


def find_zim(root: Path) -> Path | None:
    zims = sorted(p for p in (root / "data").glob("*.zim") if not p.name.startswith("."))
    return zims[-1] if zims else None


def load(root: Path = APP_ROOT) -> Config:
    with (root / "config.toml").open("rb") as f:
        raw = tomllib.load(f)
    server = raw.get("server", {})
    configured = raw.get("zim_path", "")
    return Config(
        zim_path=root / configured if configured else find_zim(root),
        overlay_path=root / raw.get("overlay_path", "data/overlay.db"),
        wiki_domain=raw.get("wiki_domain", "simple.wikipedia.org"),
        mode=raw.get("mode", "nopic"),
        journal_mode=raw.get("journal_mode", "DELETE"),
        host=server.get("host", "127.0.0.1"),
        port=server.get("port", 8787),
        sync=SyncConfig(**raw.get("sync", {})),
    )
