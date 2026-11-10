#!/usr/bin/env python3
"""Create a verified SQLite online backup of the configured overlay."""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from weki.config import APP_ROOT, load
from weki.overlay import backup_database


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "destination",
        type=Path,
        help="new backup file; relative paths resolve from the application root",
    )
    args = parser.parse_args()
    destination = args.destination
    if not destination.is_absolute():
        destination = APP_ROOT / destination

    config = load()
    source_path = config.overlay_path.resolve()
    source = sqlite3.connect(source_path.as_uri() + "?mode=ro", uri=True)
    try:
        backup = backup_database(source, source_path, destination)
    finally:
        source.close()
    print(f"verified overlay backup: {backup}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
