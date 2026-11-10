import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from weki.config import Config
from weki.server import create_app, inject, wiki_url


@pytest.fixture
def config(tmp_path, zim_file):
    return Config(zim_path=zim_file, overlay_path=tmp_path / "overlay.db")


@pytest.fixture
def client(config):
    app = create_app(config)
    with TestClient(app, follow_redirects=False) as test_client:
        test_client.overlay = app.state.overlay
        yield test_client


def add(client, path="Ulm", title="Ulm", html="<p>fresh <a href='/wiki/Bern'>Bern</a></p>"):
    client.overlay.upsert_article(path, title, html, "fresh text", 2, "2026-11-01T00:00:00Z")


def test_home_shows_search_and_snapshot(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "2026-09-28" in resp.text
    assert "data-suggest" in resp.text
    assert 'class="wk-home-logo" src="/assets/wiki-logo.svg"' in resp.text
    assert 'src="/assets/wiki-logo.svg"' in resp.text


def test_logo_asset_is_served(client):
    response = client.get("/assets/wiki-logo.svg")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/svg+xml"
    assert "<svg" in response.text


@pytest.mark.parametrize("url", ["/", "/wiki/Ulm", "/search?q=x", "/status"])
def test_every_page_loads_theme_before_styles_and_has_toggle(client, url):
    html = client.get(url).text
    assert html.index("/static/theme.js") < html.index("/static/style.css")
    assert "data-theme-toggle" in html


def test_home_without_zim_says_how_to_get_one(tmp_path):
    config = Config(zim_path=None, overlay_path=tmp_path / "o.db")
    with TestClient(create_app(config)) as client:
        resp = client.get("/")
        assert "download_zim.py" in resp.text
        assert client.get("/wiki/Ulm").status_code == 404
        assert client.get("/status").status_code == 200


def test_zim_article_gets_chrome_and_footer(client):
    resp = client.get("/wiki/Ulm")
    assert resp.status_code == 200
    assert "A city in Germany" in resp.text
    assert 'class="wk-bar"' in resp.text
    assert "/static/style.css" in resp.text
    assert "CC BY-SA 4.0" in resp.text
    assert "https://simple.wikipedia.org/wiki/Ulm" in resp.text
    assert "Content-Security-Policy" not in resp.headers


def test_zim_asset_served_raw_and_not_recorded(client):
    resp = client.get("/wiki/-/style.css")
    assert resp.headers["content-type"].startswith("text/css")
    assert "wk-bar" not in resp.text
    assert client.overlay.recent_history() == []


def test_opening_article_records_history(client):
    client.get("/wiki/Ulm")
    assert client.overlay.recent_history() == [("Ulm", "Ulm")]
    assert "Pick up where you left off" in client.get("/").text


def test_slash_title(client):
    assert client.get("/wiki/AC/DC").status_code == 200


def test_redirect_goes_to_canonical_path(client):
    resp = client.get("/wiki/Einstein")
    assert resp.status_code == 302
    assert resp.headers["location"] == "/wiki/Albert_Einstein"


def test_spaces_in_url_serve_without_redirect_loop(client):
    resp = client.get("/wiki/Albert Einstein")
    assert resp.status_code == 200


def test_overlay_article_shows_badge_and_strict_csp(client):
    add(client)
    resp = client.get("/wiki/Ulm")
    assert "Updated 2026-11-01" in resp.text
    assert "wk-fresh" in resp.text
    assert "<a href='/wiki/Bern'>Bern</a>" in resp.text
    csp = resp.headers["content-security-policy"]
    assert "script-src 'self'" in csp
    assert "default-src 'none'" in csp


def test_overlay_revision_older_than_snapshot_gets_no_ribbon(client):
    client.overlay.upsert_article("Ulm", "Ulm", "<p>x</p>", "x", 2, "2026-09-20T00:00:00Z")
    resp = client.get("/wiki/Ulm")
    assert "Last edited 2026-09-20" in resp.text
    assert "wk-fresh" not in resp.text


def test_removed_page(client):
    client.overlay.mark_deleted("Ulm", "Ulm", "2026-11-02T00:00:00Z")
    resp = client.get("/wiki/Ulm")
    assert resp.status_code == 410
    assert "removed from Wikipedia on 2026-11-02" in resp.text


def test_missing_page(client):
    resp = client.get("/wiki/Nope")
    assert resp.status_code == 404
    assert "on this disk" in resp.text


def test_search_page(client):
    add(client, "Ulm", "Ulm")
    resp = client.get("/search", params={"q": "physicist"})
    assert 'href="/wiki/Albert_Einstein"' in resp.text


def test_search_escapes_query(client):
    resp = client.get("/search", params={"q": "<script>alert(1)</script>"})
    assert "<script>alert(1)</script>" not in resp.text


def test_empty_search(client):
    assert client.get("/search").status_code == 200


def test_suggest_json(client):
    data = client.get("/suggest", params={"q": "Einstein"}).json()
    assert data == [{"path": "Albert_Einstein", "title": "Albert Einstein"}]


def test_status_page_and_json(client):
    assert "Not synced yet" in client.get("/status").text
    data = client.get("/status.json").json()
    assert data["zim_articles"] >= 1
    assert data["zim_date"] == "2026-09-28"
    assert data["articles"] == 0


def test_rejects_foreign_host_header(client):
    assert client.get("/", headers={"Host": "evil.example"}).status_code == 400


def test_sync_refuses_cross_origin_post(client):
    resp = client.post("/sync", headers={"Origin": "https://evil.example"})
    assert resp.status_code == 403


@respx.mock
def test_sync_runs_in_background_and_reports_offline(client):
    respx.get("https://simple.wikipedia.org/w/api.php").mock(side_effect=httpx.ConnectError("down"))
    resp = client.post("/sync", headers={"Origin": "http://testserver"})
    assert resp.status_code == 202
    assert resp.json() == {"started": True}
    for _ in range(100):
        data = client.get("/status.json").json()
        if not data["running"] and data["last_result"]:
            break
    assert data["last_result"] == "offline"


def test_inject_keeps_content():
    page = b"<html><head><title>x</title></head><body class='a'><p>y</p></body></html>"
    out = inject(page, head="H", top="T", bottom="B")
    assert out == b"<html><head><title>x</title>H</head><body class='a'>T<p>y</p>B</body></html>"


def test_wiki_url_encodes_reserved_characters():
    assert wiki_url("AC/DC") == "/wiki/AC/DC"
    assert wiki_url("What?") == "/wiki/What%3F"
    assert wiki_url("Café") == "/wiki/Caf%C3%A9"
