from __future__ import annotations

import pytest
from libzim.writer import Creator, Hint, Item, StringProvider

from weki.overlay import OverlayStore
from weki.zim import ZimReader


class Page(Item):
    def __init__(self, path: str, title: str, html: str, mimetype: str = "text/html") -> None:
        super().__init__()
        self.path, self.title, self.html, self.mimetype = path, title, html, mimetype

    def get_path(self) -> str:
        return self.path

    def get_title(self) -> str:
        return self.title

    def get_mimetype(self) -> str:
        return self.mimetype

    def get_contentprovider(self) -> StringProvider:
        return StringProvider(self.html)

    def get_hints(self) -> dict:
        return {Hint.FRONT_ARTICLE: self.mimetype == "text/html"}


def page(title: str, body: str) -> str:
    return (
        f'<html><head><title>{title}</title><link rel="stylesheet" href="-/style.css"></head>'
        f"<body><h1>{title}</h1><p>{body}</p></body></html>"
    )


@pytest.fixture(scope="session")
def zim_file(tmp_path_factory):
    path = tmp_path_factory.mktemp("zim") / "tiny.zim"
    creator = Creator(path).config_indexing(True, "eng")
    with creator:
        creator.set_mainpath("Main_Page")
        creator.add_metadata("Title", "Tiny")
        creator.add_metadata("Language", "eng")
        creator.add_metadata("Date", "2026-09-28")
        for slug, title, body in [
            ("Main_Page", "Main Page", "Welcome to the tiny wiki"),
            ("Albert_Einstein", "Albert Einstein", "A physicist who developed relativity"),
            ("Ulm", "Ulm", "A city in Germany where a physicist was born"),
            ("Removed_Page", "Removed Page", "This physicist page gets deleted upstream"),
            ("AC/DC", "AC/DC", "An Australian rock band"),
        ]:
            creator.add_item(Page(slug, title, page(title, body)))
        creator.add_item(Page("-/style.css", "", "body{color:black}", "text/css"))
        creator.add_redirection(
            "Einstein", "Einstein", "Albert_Einstein", {Hint.FRONT_ARTICLE: True}
        )
    return path


@pytest.fixture(scope="session")
def zim(zim_file):
    return ZimReader(zim_file)


@pytest.fixture
def store(tmp_path):
    overlay = OverlayStore(tmp_path / "overlay.db")
    yield overlay
    overlay.close()
