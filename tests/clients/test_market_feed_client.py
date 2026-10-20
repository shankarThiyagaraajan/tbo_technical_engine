from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest
from tbo_platform_kit.http_client import UpstreamUnavailable

from tbo_technical_engine.clients import market_feed
from tests.support import ISIN_A, FakeFeed, candle, make_client

T0 = datetime(2026, 10, 15, 3, 45, tzinfo=UTC)
ROWS = [candle(ISIN_A, "5m", T0 + timedelta(minutes=5 * i)) for i in range(7)]


async def test_follows_the_cursor_and_returns_oldest_first() -> None:
    feed = FakeFeed(reversed(ROWS), page=3)
    got = [c async for c in feed.client().candles(ISIN_A, "5m", T0, T0 + timedelta(hours=1))]
    assert [c.start_at for c in got] == [r.start_at for r in ROWS]
    assert len(feed.calls) == 3


async def test_sends_the_window_as_contract_timestamps_and_the_token() -> None:
    feed = FakeFeed(ROWS)
    [c async for c in feed.client().candles(ISIN_A, "5m", T0, T0 + timedelta(hours=1))]
    request = feed.calls[0]
    assert request.url.params["from"] == "2026-10-15T03:45:00Z"
    assert request.url.params["to"] == "2026-10-15T04:45:00Z"
    assert request.url.params["timeframe"] == "5m"
    assert request.headers["authorization"] == "Bearer t"


async def test_a_failed_page_raises_instead_of_returning_a_partial_history() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("cursor"):
            return httpx.Response(503)
        return httpx.Response(200, json={"items": [], "next_cursor": "2"})

    client = market_feed.MarketFeedClient(
        make_client(market_feed.AUDIENCE, market_feed.SCOPES, httpx.MockTransport(handle))
    )
    with pytest.raises(UpstreamUnavailable):
        [c async for c in client.candles(ISIN_A, "5m", T0, T0 + timedelta(hours=1))]


async def test_a_cursor_that_never_ends_stops() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"items": [], "next_cursor": "again"})

    client = market_feed.MarketFeedClient(
        make_client(market_feed.AUDIENCE, market_feed.SCOPES, httpx.MockTransport(handle))
    )
    with pytest.raises(UpstreamUnavailable, match="did not end"):
        [c async for c in client.candles(ISIN_A, "5m", T0, T0 + timedelta(hours=1))]


async def test_extra_fields_from_a_newer_feed_are_ignored() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        item = {
            "isin": ISIN_A,
            "timeframe": "5m",
            "start_at": "2026-10-15T03:45:00Z",
            "open": "1.00",
            "high": "2.00",
            "low": "0.50",
            "close": "1.50",
            "volume": 5,
            "revision": 1,
            "new_field": 1,
        }
        return httpx.Response(200, json={"items": [item], "next_cursor": None})

    client = market_feed.MarketFeedClient(
        make_client(market_feed.AUDIENCE, market_feed.SCOPES, httpx.MockTransport(handle))
    )
    assert len([c async for c in client.candles(ISIN_A, "5m", T0, T0)]) == 1


async def test_feed_status() -> None:
    feed = FakeFeed()
    feed.state = "stale"
    assert (await feed.client().feed_status()).state == "stale"
    feed.state = "healthy"
    assert (await feed.client().feed_status()).state == "healthy"


async def test_feed_status_error_raises() -> None:
    client = market_feed.MarketFeedClient(
        make_client(market_feed.AUDIENCE, market_feed.SCOPES, httpx.MockTransport(lambda r: httpx.Response(500)))
    )
    with pytest.raises(UpstreamUnavailable):
        await client.feed_status()


async def test_index_candles_and_the_index_list() -> None:
    calls: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path == "/v1/indices":
            row = {"index_code": "india_vix", "name": "INDIA VIX", "kind": "volatility", "is_tracked": True}
            return httpx.Response(200, json={"indices": [row]})
        item = {
            "index_code": "nifty_50",
            "timeframe": "5m",
            "start_at": "2026-10-15T03:45:00Z",
            "open": "24000.00",
            "high": "24020.00",
            "low": "23990.00",
            "close": "24010.00",
            "session_vwap": "24006.67",
            "revision": 1,
            "source": "quote",
        }
        page = request.url.params.get("cursor")
        return httpx.Response(200, json={"items": [item], "next_cursor": None if page else "1"})

    client = market_feed.MarketFeedClient(
        make_client(market_feed.AUDIENCE, market_feed.SCOPES, httpx.MockTransport(handle))
    )
    got = [c async for c in client.index_candles("nifty_50", "5m", T0, T0 + timedelta(hours=1))]
    assert [(c.index_code, c.session_vwap, c.source) for c in got] == [("nifty_50", Decimal("24006.67"), "quote")] * 2
    assert calls[0].url.path == "/v1/index-candles"
    assert (calls[0].url.params["index_code"], calls[0].url.params["from"]) == ("nifty_50", "2026-10-15T03:45:00Z")
    assert got[0].as_candle().volume == 0
    [info] = await client.indices()
    assert (info.index_code, info.kind, info.is_tracked, info.last_candle_at) == ("india_vix", "volatility", True, None)


async def test_a_failed_index_list_raises() -> None:
    client = market_feed.MarketFeedClient(
        make_client(market_feed.AUDIENCE, market_feed.SCOPES, httpx.MockTransport(lambda r: httpx.Response(500)))
    )
    with pytest.raises(UpstreamUnavailable, match="/v1/indices answered 500"):
        await client.indices()
