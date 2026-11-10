"""Read-only access to the Kiwix ZIM base."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path

from libzim.reader import Archive
from libzim.search import Query, Searcher
from libzim.suggestion import SuggestionSearcher


@dataclass(frozen=True)
class ZimItem:
    path: str
    title: str
    mimetype: str
    content: bytes


@dataclass(frozen=True)
class ZimHit:
    path: str
    title: str
    snippet: str = ""


class ZimReader:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._archive = Archive(self.path)
        self._lock = threading.Lock()

    @property
    def main_path(self) -> str | None:
        if not self._archive.has_main_entry:
            return None
        entry = self._archive.main_entry
        while entry.is_redirect:
            entry = entry.get_redirect_entry()
        return entry.path

    @property
    def article_count(self) -> int:
        return self._archive.article_count

    @property
    def has_fulltext_index(self) -> bool:
        return self._archive.has_fulltext_index

    def metadata(self, name: str) -> str | None:
        if name not in self._archive.metadata_keys:
            return None
        return bytes(self._archive.get_metadata(name)).decode("utf-8", "replace")

    def has(self, path: str) -> bool:
        return self._archive.has_entry_by_path(path)

    def redirect_target(self, path: str) -> str | None:
        """One hop only: the caller re-resolves so overlay rows can win on the target."""
        try:
            entry = self._archive.get_entry_by_path(path)
        except KeyError:
            return None
        return entry.get_redirect_entry().path if entry.is_redirect else None

    def get(self, path: str) -> ZimItem | None:
        try:
            entry = self._archive.get_entry_by_path(path)
        except KeyError:
            return None
        while entry.is_redirect:
            entry = entry.get_redirect_entry()
        item = entry.get_item()
        return ZimItem(item.path, item.title, item.mimetype, bytes(item.content))

    def search(self, query: str, limit: int = 30) -> list[ZimHit]:
        if not self._archive.has_fulltext_index or not query.strip():
            return self.suggest(query, limit)
        with self._lock:
            search = Searcher(self._archive).search(Query().set_query(query))
            results = list(search.get_results(0, limit))
        return self._dedupe(results)

    def suggest(self, query: str, limit: int = 10) -> list[ZimHit]:
        if not query.strip():
            return []
        with self._lock:
            search = SuggestionSearcher(self._archive).suggest(query)
            results = list(search.get_results(0, limit))
        return self._dedupe(results)

    def _dedupe(self, results: list) -> list[ZimHit]:
        """Results may be redirect paths: collapse them onto their targets."""
        hits: dict[str, ZimHit] = {}
        for result in results:
            raw_path = result if isinstance(result, str) else result.path
            snippet = "" if isinstance(result, str) else (result.snippet or "")
            try:
                entry = self._archive.get_entry_by_path(raw_path)
            except KeyError:
                continue
            while entry.is_redirect:
                entry = entry.get_redirect_entry()
            hits.setdefault(entry.path, ZimHit(entry.path, entry.title, snippet))
        return list(hits.values())
