import hashlib

import httpx
import pytest
import respx

from scripts.download_zim import (
    ChecksumError,
    download,
    fastest_mirror,
    parse_mirrors,
    pick_latest,
)

URL = "https://mirror.test/x.zim"
DATA = b"0123456789" * 100
SHA = hashlib.sha256(DATA).hexdigest()


@pytest.fixture
def client():
    with httpx.Client() as c:
        yield c


def test_pick_latest_ignores_other_flavours():
    html = """
    wikipedia_en-simple_all_nopic_2026-06.zim wikipedia_en-simple_all_nopic_2026-09.zim
    wikipedia_en-simple_all_maxi_2026-10.zim wikipedia_en_all_nopic_2026-10.zim
    """
    assert pick_latest(html, "en-simple", "nopic") == "wikipedia_en-simple_all_nopic_2026-09.zim"


def test_pick_latest_exits_when_nothing_matches():
    with pytest.raises(SystemExit):
        pick_latest("nothing here", "en-simple", "nopic")


@respx.mock
def test_fresh_download(client, tmp_path):
    respx.get(URL).mock(return_value=httpx.Response(200, content=DATA))
    dest = download(client, URL, tmp_path / "x.zim", SHA)
    assert dest.read_bytes() == DATA
    assert not (tmp_path / "x.zim.part").exists()


@respx.mock
def test_resume_sends_range_and_appends(client, tmp_path):
    (tmp_path / "x.zim.part").write_bytes(DATA[:100])
    route = respx.get(URL).mock(return_value=httpx.Response(206, content=DATA[100:]))
    dest = download(client, URL, tmp_path / "x.zim", SHA)
    assert route.calls[0].request.headers["range"] == "bytes=100-"
    assert dest.read_bytes() == DATA


@respx.mock
def test_server_ignoring_range_restarts_the_file(client, tmp_path):
    (tmp_path / "x.zim.part").write_bytes(b"stale")
    respx.get(URL).mock(return_value=httpx.Response(200, content=DATA))
    assert download(client, URL, tmp_path / "x.zim", SHA).read_bytes() == DATA


@respx.mock
def test_416_means_part_is_already_complete(client, tmp_path):
    (tmp_path / "x.zim.part").write_bytes(DATA)
    respx.get(URL).mock(return_value=httpx.Response(416))
    assert download(client, URL, tmp_path / "x.zim", SHA).read_bytes() == DATA


@respx.mock
def test_checksum_mismatch_discards_the_file(client, tmp_path):
    respx.get(URL).mock(return_value=httpx.Response(200, content=b"corrupt"))
    with pytest.raises(ChecksumError):
        download(client, URL, tmp_path / "x.zim", SHA)
    assert not (tmp_path / "x.zim").exists()
    assert not (tmp_path / "x.zim.part").exists()


def test_parse_mirrors_skips_non_zim_urls():
    meta4 = """
    <url>https://kiwix.org</url>
    <url location="nl" priority="1">https://a.test/x.zim</url>
    <url priority="2">https://b.test/x.zim</url>
    """
    assert parse_mirrors(meta4) == ["https://a.test/x.zim", "https://b.test/x.zim"]


@respx.mock
def test_fastest_mirror_skips_failing_ones(client):
    meta4 = "<url>https://bad.test/x.zim</url><url>https://good.test/x.zim</url>"
    respx.get(f"{URL}.meta4").mock(return_value=httpx.Response(200, text=meta4))
    respx.get("https://bad.test/x.zim").mock(return_value=httpx.Response(500))
    respx.get("https://good.test/x.zim").mock(return_value=httpx.Response(206, content=DATA))
    assert fastest_mirror(client, URL) == "https://good.test/x.zim"


@respx.mock
def test_fastest_mirror_falls_back_without_meta4(client):
    respx.get(f"{URL}.meta4").mock(return_value=httpx.Response(404))
    assert fastest_mirror(client, URL) == URL
