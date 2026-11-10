"""Merge the overlay and the ZIM: overlay > deleted > overlay redirect > ZIM."""

from __future__ import annotations

from dataclasses import dataclass
from html import escape
from typing import Literal

from weki.overlay import HIT_END, HIT_START, Article, DeletedPage, OverlayStore
from weki.zim import ZimItem, ZimReader

MAX_HOPS = 8
Kind = Literal["overlay", "zim", "deleted", "missing"]


@dataclass(frozen=True)
class Resolved:
    kind: Kind
    path: str
    article: Article | None = None
    item: ZimItem | None = None
    deleted: DeletedPage | None = None

    @property
    def is_html(self) -> bool:
        return self.item is not None and self.item.mimetype.startswith("text/html")

    @property
    def title(self) -> str:
        if self.article:
            return self.article.title
        if self.deleted:
            return self.deleted.title
        if self.item and self.item.title:
            return self.item.title
        return title_from_path(self.path)


@dataclass(frozen=True)
class SearchHit:
    path: str
    title: str
    snippet_html: str
    updated: bool


def normalize_path(path: str) -> str:
    return path.strip().replace(" ", "_")


def title_from_path(path: str) -> str:
    return path.replace("_", " ")


def overlay_snippet(snippet: str) -> str:
    return escape(snippet).replace(HIT_START, "<mark>").replace(HIT_END, "</mark>")


def zim_snippet(snippet: str) -> str:
    """Xapian marks hits with <b>; everything else in the snippet is treated as text."""
    return escape(snippet).replace("&lt;b&gt;", "<mark>").replace("&lt;/b&gt;", "</mark>")


class Resolver:
    def __init__(self, zim: ZimReader | None, overlay: OverlayStore) -> None:
        self.zim = zim
        self.overlay = overlay

    def resolve(self, path: str) -> Resolved:
        path = normalize_path(path)
        found = self._walk(path)
        if found.kind == "missing" and path and path[0] != path[0].upper():
            capitalized = self._walk(path[0].upper() + path[1:])
            if capitalized.kind != "missing":
                return capitalized
        return found

    def _walk(self, path: str) -> Resolved:
        seen: set[str] = set()
        for _ in range(MAX_HOPS):
            if path in seen:
                break
            seen.add(path)
            if article := self.overlay.article(path):
                return Resolved("overlay", path, article=article)
            if deleted := self.overlay.deleted(path):
                return Resolved("deleted", path, deleted=deleted)
            if target := self.overlay.redirect_target(path):
                path = target
                continue
            if self.zim is None:
                break
            if target := self.zim.redirect_target(path):
                path = target
                continue
            if item := self.zim.get(path):
                return Resolved("zim", item.path, item=item)
            break
        return Resolved("missing", path)

    def open(self, path: str) -> Resolved:
        """Resolve a page the reader is opening, and remember it for sync priority."""
        found = self.resolve(path)
        is_asset = found.item is not None and not found.is_html
        if found.kind in ("overlay", "zim") and not is_asset:
            self.overlay.record_open(found.path, found.title)
        return found

    def search(self, query: str, limit: int = 30) -> list[SearchHit]:
        overlay_hits = self.overlay.search(query, limit)
        zim_hits = self.zim.search(query, limit) if self.zim else []

        terms = query.casefold().split()
        title_hits, body_hits = [], []
        for hit in overlay_hits:
            matched = all(term in hit.title.casefold() for term in terms)
            (title_hits if matched else body_hits).append(hit)

        paths = [hit.path for hit in zim_hits]
        deleted = self.overlay.deleted_paths(paths)
        updated = self.overlay.article_paths(paths)

        merged: dict[str, SearchHit] = {}
        for hit in title_hits:
            merged[hit.path] = SearchHit(hit.path, hit.title, overlay_snippet(hit.snippet), True)
        for hit in zim_hits:
            if hit.path not in deleted and hit.path not in merged:
                is_updated = hit.path in updated
                snippet = zim_snippet(hit.snippet)
                merged[hit.path] = SearchHit(hit.path, hit.title, snippet, is_updated)
        for hit in body_hits:
            merged.setdefault(
                hit.path, SearchHit(hit.path, hit.title, overlay_snippet(hit.snippet), True)
            )
        return list(merged.values())

    def suggest(self, query: str, limit: int = 10) -> list[tuple[str, str]]:
        merged = dict(self.overlay.suggest(query, limit))
        if self.zim:
            zim_hits = self.zim.suggest(query, limit)
            deleted = self.overlay.deleted_paths(hit.path for hit in zim_hits)
            for hit in zim_hits:
                if hit.path not in deleted:
                    merged.setdefault(hit.path, hit.title)
        return list(merged.items())[:limit]
