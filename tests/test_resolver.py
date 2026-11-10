import pytest

from weki.resolver import Resolver, normalize_path, overlay_snippet, zim_snippet


@pytest.fixture
def resolver(zim, store):
    return Resolver(zim, store)


def add(store, path, title=None, text="fresh text", rev=2):
    store.upsert_article(path, title or path, f"<p>{text}</p>", text, rev, "2026-11-01T00:00:00Z")


def paths(hits):
    return [hit.path for hit in hits]


def test_falls_through_to_zim(resolver):
    found = resolver.resolve("Ulm")
    assert (found.kind, found.title) == ("zim", "Ulm")


def test_overlay_beats_zim(resolver, store):
    add(store, "Ulm", text="updated")
    found = resolver.resolve("Ulm")
    assert found.kind == "overlay"
    assert "updated" in found.article.html


def test_deleted_beats_zim(resolver, store):
    store.mark_deleted("Ulm", "Ulm", "2026-11-01T00:00:00Z")
    found = resolver.resolve("Ulm")
    assert found.kind == "deleted"
    assert found.title == "Ulm"


def test_restored_page_comes_back(resolver, store):
    store.mark_deleted("Ulm", "Ulm", "2026-11-01T00:00:00Z")
    add(store, "Ulm")
    assert resolver.resolve("Ulm").kind == "overlay"


def test_overlay_redirect_beats_zim_and_follows_to_zim(resolver, store):
    store.set_redirect("Ulm", "Albert_Einstein")
    found = resolver.resolve("Ulm")
    assert (found.kind, found.path) == ("zim", "Albert_Einstein")


def test_zim_redirect_lands_on_overlay_version_of_target(resolver, store):
    add(store, "Albert_Einstein", "Albert Einstein", "newer")
    found = resolver.resolve("Einstein")
    assert (found.kind, found.path) == ("overlay", "Albert_Einstein")


def test_zim_redirect_to_deleted_target(resolver, store):
    store.mark_deleted("Albert_Einstein", "Albert Einstein", "2026-11-01T00:00:00Z")
    assert resolver.resolve("Einstein").kind == "deleted"


def test_redirect_loop_is_missing(resolver, store):
    store.set_redirect("A", "B")
    store.set_redirect("B", "A")
    assert resolver.resolve("A").kind == "missing"


def test_missing(resolver):
    found = resolver.resolve("Nope_Nope")
    assert found.kind == "missing"
    assert found.title == "Nope Nope"


def test_lowercase_first_letter_and_spaces(resolver):
    assert resolver.resolve("albert Einstein").path == "Albert_Einstein"


def test_normalize_path():
    assert normalize_path(" Albert Einstein ") == "Albert_Einstein"


def test_works_without_zim(store):
    resolver = Resolver(None, store)
    assert resolver.resolve("Ulm").kind == "missing"
    assert resolver.search("Ulm") == []
    assert resolver.suggest("Ulm") == []
    add(store, "Ulm")
    assert resolver.resolve("Ulm").kind == "overlay"


def test_open_records_history_only_for_real_pages(resolver, store):
    resolver.open("Einstein")
    resolver.open("Nope")
    store.mark_deleted("Ulm", "Ulm", "2026-11-01T00:00:00Z")
    resolver.open("Ulm")
    assert store.recent_history() == [("Albert_Einstein", "Albert Einstein")]


def test_search_filters_deleted_and_flags_overlay(resolver, store):
    store.mark_deleted("Removed_Page", "Removed Page", "2026-11-01T00:00:00Z")
    add(store, "Ulm", "Ulm", "another physicist")
    hits = resolver.search("physicist")
    assert "Removed_Page" not in paths(hits)
    assert paths(hits).count("Ulm") == 1
    assert next(h for h in hits if h.path == "Ulm").updated
    assert not next(h for h in hits if h.path == "Albert_Einstein").updated


def test_search_keeps_zim_best_match_above_body_only_overlay_hits(resolver, store):
    add(store, "New_Page", "New Page", "mentions a physicist in passing")
    assert paths(resolver.search("physicist"))[-1] == "New_Page"


def test_search_puts_overlay_title_match_first(resolver, store):
    add(store, "Physicist", "Physicist", "a scientist")
    assert paths(resolver.search("physicist"))[0] == "Physicist"


def test_suggest_merges_and_filters(resolver, store):
    add(store, "Ulmus", "Ulmus", "a tree")
    store.mark_deleted("Albert_Einstein", "Albert Einstein", "2026-11-01T00:00:00Z")
    assert ("Ulmus", "Ulmus") in resolver.suggest("Ulm")
    assert "Ulm" in dict(resolver.suggest("Ulm"))
    assert "Albert_Einstein" not in dict(resolver.suggest("Einstein"))


def test_suggest_treats_like_wildcards_literally(store):
    add(store, "Ulm", "Ulm")
    assert store.suggest("%") == []
    assert store.suggest("U_m") == []


def test_snippets_escape_html():
    assert overlay_snippet("<i>\x02x\x03") == "&lt;i&gt;<mark>x</mark>"
    assert zim_snippet("<i><b>x</b>") == "&lt;i&gt;<mark>x</mark>"
