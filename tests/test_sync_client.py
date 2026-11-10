from datetime import UTC, datetime

import httpx
import pytest
import respx

import weki.sync.client as sync_client
from weki.sync.client import (
    Offline,
    RateLimited,
    WikiClient,
    WikiError,
    parse_retry_after,
    rest_url_to_title,
)

BASE = "https://simple.wikipedia.org"
API = f"{BASE}/w/api.php"
UA = "WekI/test (https://example.org; bot)"
OK = {"query": {"allrevisions": []}}
PAGE = {
    "key": "Ulm",
    "title": "Ulm",
    "latest": {"id": 7, "timestamp": "2026-11-01T00:00:00Z"},
    "html": "<p>x</p>",
}


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def client():
    sleeps = []

    async def sleep(seconds):
        sleeps.append(seconds)

    wiki = WikiClient("simple.wikipedia.org", UA, delay=0, sleep=sleep, max_retries=3)
    wiki.sleeps = sleeps
    yield wiki
    await wiki.aclose()


def waits(client):
    """Drop the zero politeness delays, leaving the backoff waits."""
    return [s for s in client.sleeps if s]


@pytest.mark.anyio
@respx.mock
async def test_action_sends_ua_maxlag_and_format(client):
    route = respx.get(API).mock(return_value=httpx.Response(200, json=OK))
    await client.action({"action": "query"})
    request = route.calls[0].request
    assert request.headers["user-agent"] == UA
    assert "gzip" in request.headers["accept-encoding"]
    params = request.url.params
    assert (params["maxlag"], params["format"], params["formatversion"]) == ("5", "json", "2")


@pytest.mark.anyio
@respx.mock
async def test_429_honors_retry_after(client):
    route = respx.get(API).mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "7"}),
            httpx.Response(200, json=OK),
        ]
    )
    assert await client.action({}) == OK
    assert route.call_count == 2
    assert waits(client) == [7]


@pytest.mark.anyio
@respx.mock
async def test_maxlag_error_arrives_as_200_and_waits_at_least_five_seconds(client):
    lagged = httpx.Response(200, json={"error": {"code": "maxlag"}}, headers={"Retry-After": "1"})
    respx.get(API).mock(side_effect=[lagged, httpx.Response(200, json=OK)])
    assert await client.action({}) == OK
    assert waits(client) == [5]


@pytest.mark.anyio
@respx.mock
async def test_503_uses_exponential_backoff_with_jitter(client, monkeypatch):
    monkeypatch.setattr(sync_client.random, "uniform", lambda low, high: high / 2)
    route = respx.get(API).mock(return_value=httpx.Response(503))
    with pytest.raises(WikiError):
        await client.action({})
    assert route.call_count == 4
    assert waits(client) == [0.5, 1, 2]


@pytest.mark.anyio
@respx.mock
async def test_retry_jitter_is_bounded_by_exponential_backoff(client, monkeypatch):
    waits_seen = []

    def jitter(low, high):
        waits_seen.append((low, high))
        return low

    monkeypatch.setattr(sync_client.random, "uniform", jitter)
    respx.get(API).mock(return_value=httpx.Response(503))
    with pytest.raises(WikiError):
        await client.action({})
    assert waits_seen == [(0.0, 1), (0.0, 2), (0.0, 4)]


@pytest.mark.anyio
@respx.mock
async def test_persistent_429_raises_rate_limited(client):
    respx.get(API).mock(return_value=httpx.Response(429))
    with pytest.raises(RateLimited):
        await client.action({})


@pytest.mark.anyio
@respx.mock
async def test_huge_retry_after_stops_immediately(client):
    route = respx.get(API).mock(return_value=httpx.Response(429, headers={"Retry-After": "3600"}))
    with pytest.raises(RateLimited):
        await client.action({})
    assert route.call_count == 1


@pytest.mark.anyio
@respx.mock
async def test_transport_error_retries_then_raises_offline(client):
    route = respx.get(API).mock(side_effect=httpx.ConnectError("down"))
    with pytest.raises(Offline):
        await client.action({})
    assert route.call_count == 4


@pytest.mark.anyio
@respx.mock
async def test_other_action_errors_are_not_retried(client):
    route = respx.get(API).mock(
        return_value=httpx.Response(200, json={"error": {"code": "badvalue"}})
    )
    with pytest.raises(WikiError):
        await client.action({})
    assert route.call_count == 1


@pytest.mark.anyio
@respx.mock
async def test_invalid_action_json_has_clear_error(client):
    respx.get(API).mock(return_value=httpx.Response(200, text="not json"))
    with pytest.raises(WikiError, match="action API returned invalid JSON"):
        await client.action({})


@pytest.mark.anyio
@respx.mock
async def test_malformed_page_response_has_clear_error(client):
    respx.get(f"{BASE}/w/rest.php/v1/page/Ulm/with_html").mock(
        return_value=httpx.Response(200, json={"title": "Ulm", "latest": {}})
    )
    with pytest.raises(WikiError, match="page Ulm: malformed REST response"):
        await client.page("Ulm")


@pytest.mark.anyio
@respx.mock
async def test_paged_follows_continue(client):
    route = respx.get(API).mock(
        side_effect=[
            httpx.Response(
                200,
                json={
                    "continue": {"arvcontinue": "x|1", "continue": "-||"},
                    "query": {"allrevisions": [{"n": 1}]},
                },
            ),
            httpx.Response(200, json={"query": {"allrevisions": [{"n": 2}]}}),
        ]
    )
    entries = [e async for e in client.paged({"list": "allrevisions"}, "allrevisions")]
    assert entries == [{"n": 1}, {"n": 2}]
    second = route.calls[1].request.url.params
    assert (second["arvcontinue"], second["continue"]) == ("x|1", "-||")


@pytest.mark.anyio
@respx.mock
async def test_page_returns_content(client):
    route = respx.get(f"{BASE}/w/rest.php/v1/page/Ulm/with_html").mock(
        return_value=httpx.Response(200, json=PAGE)
    )
    page = await client.page("Ulm")
    assert (page.title, page.rev_id, page.html) == ("Ulm", 7, "<p>x</p>")
    assert route.calls[0].request.url.params["redirect"] == "no"


@pytest.mark.anyio
@respx.mock
async def test_page_encodes_slash_in_title(client):
    route = respx.get(url__regex=r".*/page/AC%2FDC/with_html.*").mock(
        return_value=httpx.Response(200, json=PAGE)
    )
    await client.page("AC/DC")
    assert route.called
    assert b"AC%2FDC" in route.calls[0].request.url.raw_path


@pytest.mark.anyio
@respx.mock
async def test_page_404_is_none(client):
    respx.get(f"{BASE}/w/rest.php/v1/page/Gone/with_html").mock(
        return_value=httpx.Response(404, json={"errorKey": "rest-nonexistent-title"})
    )
    assert await client.page("Gone") is None


@pytest.mark.anyio
@respx.mock
async def test_page_redirect_target_is_parsed_from_rest_url(client):
    body = {
        **PAGE,
        "title": "Einstein",
        "redirect_target": "/w/rest.php/v1/page/Albert_Einstein/with_html?redirect=no",
    }
    respx.get(f"{BASE}/w/rest.php/v1/page/Einstein/with_html").mock(
        return_value=httpx.Response(200, json=body)
    )
    assert (await client.page("Einstein")).redirect_to == "Albert_Einstein"


@pytest.mark.anyio
@respx.mock
async def test_is_online(client):
    route = respx.get(API).mock(return_value=httpx.Response(200, json={}))
    assert await client.is_online()
    route.mock(side_effect=httpx.ConnectError("down"))
    assert not await client.is_online()


@pytest.mark.parametrize(
    ("url", "title"),
    [
        ("/w/rest.php/v1/page/Albert_Einstein/with_html?redirect=no", "Albert_Einstein"),
        ("/w/rest.php/v1/page/AC%2FDC/with_html?redirect=no", "AC/DC"),
        ("https://x/w/rest.php/v1/page/Caf%C3%A9/with_html", "Café"),
        ("/nonsense", None),
    ],
)
def test_rest_url_to_title(url, title):
    assert rest_url_to_title(url) == title


def test_parse_retry_after():
    now = datetime(2026, 11, 7, 12, 0, 0, tzinfo=UTC)
    assert parse_retry_after("12") == 12
    assert parse_retry_after("Sat, 07 Nov 2026 12:00:30 GMT", now) == 30
    assert parse_retry_after("Sat, 07 Nov 2026 11:00:00 GMT", now) == 0
    assert parse_retry_after("soon") is None
    assert parse_retry_after(None) is None
