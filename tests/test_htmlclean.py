import pytest

from weki.htmlclean import clean, rewrite_href


def body(inner: str) -> str:
    return f'<html><head><base href="//x/"><title>T</title></head><body>{inner}</body></html>'


@pytest.mark.parametrize(
    ("href", "expected"),
    [
        ("./Ulm", "/wiki/Ulm"),
        ("./AC/DC", "/wiki/AC/DC"),
        ("./Caf%C3%A9#History", "/wiki/Caf%C3%A9#History"),
        ("./Einstein#cite_note-1", "/wiki/Einstein#cite_note-1"),
        ("./Nope?action=edit&redlink=1", None),
        ("#top", "#top"),
        ("//en.wikipedia.org/wiki/Ulm", "https://en.wikipedia.org/wiki/Ulm"),
        ("https://example.org/a", "https://example.org/a"),
        ("mailto:a@example.org", "mailto:a@example.org"),
        ("javascript:alert(1)", None),
        (" JaVaScRiPt:alert(1)", None),
        ("data:text/html,<script>alert(1)</script>", None),
        ("vbscript:x", None),
    ],
)
def test_rewrite_href(href, expected):
    assert rewrite_href(href) == expected


def test_rewrites_wiki_links_and_drops_redlinks():
    page = clean(
        body(
            '<a rel="mw:WikiLink" href="./Ulm">Ulm</a>'
            '<a class="new" href="./Nope?action=edit&amp;redlink=1">Nope</a>'
        )
    )
    assert 'href="/wiki/Ulm"' in page.html
    assert "redlink" not in page.html
    assert ">Nope</a>" in page.html


@pytest.mark.parametrize(
    "dirty",
    [
        "<script>alert(1)</script>",
        '<base href="//evil/">',
        '<iframe src="https://evil"></iframe>',
        '<object data="x"></object>',
        '<embed src="x">',
        '<form action="https://evil"><input></form>',
        '<link rel="stylesheet" href="https://evil/a.css">',
        '<meta http-equiv="refresh" content="0;url=https://evil">',
    ],
)
def test_strips_active_content(dirty):
    assert clean(body(f"<p>safe</p>{dirty}")).html == "<p>safe</p>"


def test_strips_event_handlers_and_script_urls():
    html = clean(
        body(
            '<p onclick="x()" ONMOUSEOVER="y()">a</p>'
            '<a href="javascript:x()">b</a>'
            '<svg><a xlink:href="javascript:x()">c</a></svg>'
        )
    ).html
    assert "onclick" not in html.lower()
    assert "onmouseover" not in html.lower()
    assert "javascript" not in html.lower()


def test_nopic_removes_media_but_keeps_text():
    page = clean(
        body(
            '<figure typeof="mw:File/Thumb"><img src="//upload/x.png"><figcaption>cap'
            '</figcaption></figure><span typeof="mw:File"><img src="//upload/y.png"></span>'
            "<p>text</p><video src='v'></video><audio src='a'></audio>"
        )
    )
    assert page.html == "<p>text</p>"


def test_keeps_images_when_not_nopic():
    assert "<img" in clean(body('<img src="//upload/x.png">'), nopic=False).html


def test_keeps_math_and_templatestyles():
    html = clean(body("<style>.a{color:red}</style><math><mi>x</mi></math>")).html
    assert "<style>" in html
    assert "<math>" in html


def test_drops_parsoid_metadata():
    html = clean(body('<p about="#mwt1" typeof="x" data-mw="{}" id="keep">a</p>')).html
    assert html == '<p id="keep">a</p>'


def test_text_skips_css_references_and_navboxes():
    page = clean(
        body(
            "<style>.a{}</style><p>Some   text<sup class='reference'>[1]</sup></p>"
            "<div class='navbox'>nav</div><div class='mw-references-wrap'>refs</div>"
        )
    )
    assert page.text == "Some text"


def test_empty_document():
    assert clean("").text == ""
