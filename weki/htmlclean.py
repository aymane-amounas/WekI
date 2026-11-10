"""Turn Parsoid HTML into local, safe overlay HTML plus plain text for FTS"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from selectolax.lexbor import LexborHTMLParser

ALWAYS_REMOVE = "script, noscript, base, meta, link, iframe, object, embed, applet, form, title"
MEDIA_REMOVE = (
    'img, picture, video, audio, source, track, figure, [typeof*="mw:File"], '
    '[typeof*="mw:Audio"], [typeof*="mw:Video"]'
)
NOT_FOR_INDEX = "style, sup.reference, .mw-references-wrap, .navbox, .mw-editsection"
DROP_ATTRS = {
    "about",
    "data-mw",
    "data-mw-i18n",
    "data-parsoid",
    "formaction",
    "ping",
    "srcdoc",
    "typeof",
}
SAFE_SCHEMES = {"http", "https", "mailto"}


@dataclass(frozen=True)
class CleanPage:
    html: str
    text: str


def rewrite_href(href: str) -> str | None:
    """Local URL, safe external URL, or None when the link must lose its target"""
    href = href.strip()
    if href.startswith("#"):
        return href
    if href.startswith("./"):
        return None if "redlink=1" in href else "/wiki/" + href[2:]
    if href.startswith("//"):
        return "https:" + href
    try:
        scheme = urlsplit(href).scheme.lower()
    except ValueError:
        return None
    return href if scheme in SAFE_SCHEMES else None


def clean(html: str, *, nopic: bool = True) -> CleanPage:
    tree = LexborHTMLParser(html)
    for node in tree.css(ALWAYS_REMOVE):
        node.decompose()
    if nopic:
        for node in tree.css(MEDIA_REMOVE):
            node.decompose()

    body = tree.body
    if body is None:
        return CleanPage("", "")

    for node in body.traverse():
        if node.tag == "-text" or not node.attributes:
            continue
        attrs = node.attrs
        for name in list(node.attributes):
            lowered = name.lower()
            if lowered.startswith("on") or lowered in DROP_ATTRS or _unsafe_url_attr(node, lowered):
                del attrs[name]
        if node.tag == "a" and "href" in node.attributes:
            href = rewrite_href(node.attributes["href"] or "")
            if href is None:
                del attrs["href"]
            else:
                attrs["href"] = href

    cleaned = body.inner_html or ""
    for node in body.css(NOT_FOR_INDEX):
        node.decompose()
    text = re.sub(r"\s+", " ", body.text(separator=" ")).strip()
    return CleanPage(cleaned, text)


def _unsafe_url_attr(node, name: str) -> bool:
    if name not in ("src", "srcset", "action", "xlink:href", "poster", "background"):
        return False
    return name != "src" or rewrite_href(node.attributes.get(name) or "") is None
