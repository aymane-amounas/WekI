"""Polite Wikimedia client: bot UA, low concurrency, maxlag, backoff that honors Retry After."""

from __future__ import annotations

import asyncio
import random
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from urllib.parse import quote, unquote

import httpx

MAX_RETRY_AFTER = 300.0
MAX_BACKOFF = 60.0
MIN_MAXLAG_WAIT = 5.0
RETRY_STATUS = {429, 503}
REDIRECT_STATUS = {301, 302, 303, 307, 308}
REST_PAGE = re.compile(r"/v1/page/([^/?#]+)")


class SyncError(Exception):
    pass


class Offline(SyncError):
    pass


class RateLimited(SyncError):
    pass


class WikiError(SyncError):
    pass


@dataclass(frozen=True)
class PageData:
    title: str
    rev_id: int = 0
    rev_ts: str = ""
    html: str = ""
    redirect_to: str | None = None


def parse_retry_after(value: str | None, now: datetime | None = None) -> float | None:
    if not value:
        return None
    try:
        return max(float(value), 0.0)
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    return max((when - (now or datetime.now(UTC))).total_seconds(), 0.0)


def rest_url_to_title(url: str) -> str | None:
    match = REST_PAGE.search(url)
    return unquote(match.group(1)).split("#")[0] if match else None


class WikiClient:
    def __init__(
        self,
        domain: str,
        user_agent: str,
        *,
        concurrency: int = 2,
        delay: float = 0.5,
        max_retries: int = 5,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.base_url = f"https://{domain}"
        self._semaphore = asyncio.Semaphore(concurrency)
        self._delay = delay
        self._max_retries = max_retries
        self._sleep = sleep
        self._http = httpx.AsyncClient(
            base_url=self.base_url,
            headers={"User-Agent": user_agent, "Accept-Encoding": "gzip"},
            timeout=httpx.Timeout(30),
            follow_redirects=False,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def is_online(self) -> bool:
        try:
            resp = await self._http.get(
                "/w/api.php",
                params={"action": "query", "meta": "siteinfo", "format": "json"},
                timeout=5,
            )
        except httpx.TransportError:
            return False
        return resp.status_code < 500

    async def _get(self, url: str, params: dict) -> httpx.Response:
        for attempt in range(self._max_retries + 1):
            async with self._semaphore:
                try:
                    resp = await self._http.get(url, params=params)
                except httpx.TransportError as exc:
                    resp, error = None, exc
                else:
                    error = None
                await self._sleep(self._delay)

            if resp is not None and not self._is_retryable(resp):
                return resp
            if attempt == self._max_retries:
                break
            await self._sleep(self._wait_seconds(resp, attempt))

        if resp is None:
            raise Offline(str(error))
        if resp.status_code == 429:
            raise RateLimited("still rate limited after retries")
        raise WikiError(f"gave up after {self._max_retries} retries: HTTP {resp.status_code}")

    @staticmethod
    def _is_retryable(resp: httpx.Response) -> bool:
        return resp.status_code in RETRY_STATUS or _is_maxlag(resp)

    @staticmethod
    def _wait_seconds(resp: httpx.Response | None, attempt: int) -> float:
        backoff = min(2.0**attempt, MAX_BACKOFF)
        if resp is None:
            return random.uniform(0.0, backoff)
        retry_after = parse_retry_after(resp.headers.get("retry-after"))
        if retry_after is not None and retry_after > MAX_RETRY_AFTER:
            raise RateLimited(f"server asked to wait {retry_after:.0f}s")
        wait = random.uniform(0.0, backoff) if retry_after is None else retry_after
        return max(wait, MIN_MAXLAG_WAIT) if _is_maxlag(resp) else wait

    async def action(self, params: dict) -> dict:
        full = {"format": "json", "formatversion": 2, "maxlag": 5, **params}
        resp = await self._get("/w/api.php", full)
        if resp.status_code != 200:
            raise WikiError(f"action API: HTTP {resp.status_code}")
        try:
            data = resp.json()
        except ValueError as exc:
            raise WikiError("action API returned invalid JSON") from exc
        if not isinstance(data, dict):
            raise WikiError("action API returned an unexpected response")
        if "error" in data:
            raise WikiError(f"action API: {data['error'].get('code')}")
        return data

    async def paged(self, params: dict, list_key: str) -> AsyncIterator[dict]:
        """Yield every entry of a list query, following `continue` to the end."""
        continuation: dict = {}
        while True:
            data = await self.action({"action": "query", **params, **continuation})
            for entry in data.get("query", {}).get(list_key, []):
                yield entry
            if "continue" not in data:
                return
            continuation = data["continue"]

    async def page(self, title: str) -> PageData | None:

        key = quote(title.replace(" ", "_"), safe="")
        resp = await self._get(f"/w/rest.php/v1/page/{key}/with_html", {"redirect": "no"})
        if resp.status_code == 404:
            return None
        if resp.status_code in REDIRECT_STATUS:
            target = rest_url_to_title(resp.headers.get("location", ""))
            if target is None:
                raise WikiError(f"unreadable redirect for {title}")
            return PageData(title, redirect_to=target)
        if resp.status_code != 200:
            raise WikiError(f"page {title}: HTTP {resp.status_code}")

        try:
            data = resp.json()
        except ValueError as exc:
            raise WikiError(f"page {title}: invalid JSON response") from exc
        if not isinstance(data, dict):
            raise WikiError(f"page {title}: unexpected REST response")
        if target := data.get("redirect_target"):
            redirect_to = rest_url_to_title(target)
            if redirect_to is None:
                raise WikiError(f"unreadable redirect_target for {title}: {target}")
            return PageData(data["title"], redirect_to=redirect_to)
        latest = data.get("latest")
        if (
            not isinstance(data.get("title"), str)
            or not isinstance(latest, dict)
            or not isinstance(latest.get("id"), int)
            or not isinstance(latest.get("timestamp"), str)
            or not isinstance(data.get("html"), str)
        ):
            raise WikiError(f"page {title}: malformed REST response")
        return PageData(data["title"], latest["id"], latest["timestamp"], data["html"])


def _is_maxlag(resp: httpx.Response) -> bool:
    if "x-database-lag" in resp.headers:
        return True
    if resp.status_code != 200 or not resp.url.path.endswith("/api.php"):
        return False
    try:
        return resp.json().get("error", {}).get("code") == "maxlag"
    except ValueError:
        return False
