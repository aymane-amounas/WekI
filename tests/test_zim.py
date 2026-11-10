import pytest
from libzim.writer import Creator, Hint, Item, StringProvider

from weki.zim import ZimReader


class ContentItem(Item):
    def __init__(self, path: str, content: str) -> None:
        super().__init__()
        self.path = path
        self.content = content

    def get_path(self) -> str:
        return self.path

    def get_title(self) -> str:
        return self.path

    def get_mimetype(self) -> str:
        return "text/html"

    def get_contentprovider(self) -> StringProvider:
        return StringProvider(self.content)

    def get_hints(self) -> dict:
        return {Hint.FRONT_ARTICLE: True}


def test_main_path_and_metadata(zim):
    assert zim.main_path == "Main_Page"
    assert zim.metadata("Date") == "2026-09-28"
    assert zim.metadata("Nope") is None


def test_get_article_and_asset(zim):
    item = zim.get("Albert_Einstein")
    assert item.title == "Albert Einstein"
    assert item.mimetype.startswith("text/html")
    assert b"relativity" in item.content
    assert zim.get("-/style.css").mimetype == "text/css"


def test_missing_path(zim):
    assert zim.get("Nope") is None
    assert zim.redirect_target("Nope") is None
    assert not zim.has("Nope")


def test_redirect(zim):
    assert zim.redirect_target("Einstein") == "Albert_Einstein"
    assert zim.redirect_target("Albert_Einstein") is None
    assert zim.get("Einstein").path == "Albert_Einstein"


def test_slash_in_path(zim):
    assert zim.get("AC/DC").title == "AC/DC"


def test_search_finds_body_text(zim):
    paths = [h.path for h in zim.search("physicist")]
    assert "Albert_Einstein" in paths
    assert "Ulm" in paths


def test_suggest_collapses_redirect_onto_target(zim):
    paths = [h.path for h in zim.suggest("Einstein")]
    assert paths.count("Albert_Einstein") == 1
    assert "Einstein" not in paths


def test_blank_queries_return_nothing(zim):
    assert zim.search("  ") == []
    assert zim.suggest("") == []


def test_large_article_roundtrips_from_zim(tmp_path):
    content = "<html><body>" + ("substantial article text " * 80_000) + "</body></html>"
    path = tmp_path / "large.zim"
    creator = Creator(path).config_indexing(False, "eng")
    with creator:
        creator.set_mainpath("Large")
        creator.add_item(ContentItem("Large", content))

    item = ZimReader(path).get("Large")
    assert item.content == content.encode()


def test_malformed_zim_fails_at_open(tmp_path):
    path = tmp_path / "malformed.zim"
    path.write_bytes(b"not a ZIM archive")
    with pytest.raises(RuntimeError, match="too small to contain a header"):
        ZimReader(path)
