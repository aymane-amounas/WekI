#!/usr/bin/env python3
"""Dump a ZIM's metadata, main entry and sample paths."""

from __future__ import annotations

import sys
from itertools import islice
from pathlib import Path

from libzim.reader import Archive

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from weki.config import find_zim, load


def main() -> int:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else load().zim_path or find_zim(Path("."))
    if path is None:
        print("no ZIM found: pass a path or run scripts/download_zim.py", file=sys.stderr)
        return 1
    archive = Archive(path)
    print(f"file:       {path} ({archive.filesize / 1e9:.2f} GB)")
    print(f"articles:   {archive.article_count}, entries: {archive.entry_count}")
    print(f"new scheme: {archive.has_new_namespace_scheme}")
    print(f"fulltext:   {archive.has_fulltext_index}, titles: {archive.has_title_index}")
    print(f"main entry: {archive.main_entry.path} -> {archive.main_entry.get_item().path}")
    for key in archive.metadata_keys:
        if key.startswith("Illustration"):
            continue
        print(f"M/{key}: {bytes(archive.get_metadata(key))[:100].decode('utf-8', 'replace')}")
    for entry in islice(archive.iter_by_path(), 40):
        print(f"  {entry.path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
