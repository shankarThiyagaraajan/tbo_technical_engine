"""The market feed's REST API: candle and index candle history (cursor-paginated), the indices it knows and the feed's
health.

A page that fails raises UpstreamUnavailable instead of returning a partial history: indicators computed on a
series with a hole in the middle are wrong in a way that looks right.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

from tbo_platform_kit.http_client import ServiceClient, UpstreamUnavailable

from tbo_technical_engine.schemas.candle import Candle, FeedStatus, IndexCandle, IndexInfo
from tbo_technical_engine.utils.timefmt import z

AUDIENCE = "tbo_market_feed_service"
SCOPES = ["market_feed:read"]
PAGE = 500
MAX_PAGES = 100
"""A runaway cursor (a bug upstream) stops here: 100 x 500 five-minute candles is over 600 sessions."""


class MarketFeedClient:
    """Typed access to the market feed over the platform's authenticated `ServiceClient`."""

    def __init__(self, client: ServiceClient) -> None:
        self.client = client

    async def candles(
        self, isin: str, timeframe: str, start: datetime, end: datetime, *, adjusted: bool = False
    ) -> AsyncIterator[Candle]:
        """Candles of [start, end), oldest first, latest revision of each."""
        params: dict[str, str | int | bool] = {"isin": isin, "timeframe": timeframe, "from": z(start), "to": z(end)}
        if adjusted:
            params["adjusted"] = True
        async for item in self._pages("/v1/candles", params):
            yield Candle.model_validate(item)

    async def index_candles(
        self, index_code: str, timeframe: str, start: datetime, end: datetime
    ) -> AsyncIterator[IndexCandle]:
        """Index (or India VIX) candles of [start, end), oldest first, latest revision of each (ADR-019)."""
        params: dict[str, str | int | bool] = {
            "index_code": index_code,
            "timeframe": timeframe,
            "from": z(start),
            "to": z(end),
        }
        async for item in self._pages("/v1/index-candles", params):
            yield IndexCandle.model_validate(item)

    async def indices(self) -> list[IndexInfo]:
        """The indices the feed knows, tracked or not; raises UpstreamUnavailable on any non-200 answer."""
        response = await self.client.get("/v1/indices")
        if response.status_code != 200:
            raise UpstreamUnavailable(AUDIENCE, f"GET /v1/indices answered {response.status_code}")
        return [IndexInfo.model_validate(i) for i in response.json()["indices"]]

    async def _pages(self, path: str, params: dict[str, str | int | bool]) -> AsyncIterator[dict[str, Any]]:
        """Every item of a cursor-paged list, page after page; a failed page or a cursor that never ends raises."""
        cursor: str | None = None
        for _ in range(MAX_PAGES):
            query = {**params, "limit": PAGE}
            if cursor:
                query["cursor"] = cursor
            response = await self.client.get(path, params=query)
            if response.status_code != 200:
                raise UpstreamUnavailable(AUDIENCE, f"GET {path} answered {response.status_code}")
            body = response.json()
            for item in body["items"]:
                yield item
            cursor = body.get("next_cursor")
            if not cursor:
                return
        raise UpstreamUnavailable(AUDIENCE, "candle paging did not end")

    async def feed_status(self) -> FeedStatus:
        """The feed's current health; raises UpstreamUnavailable on any non-200 answer."""
        response = await self.client.get("/v1/feed/status")
        if response.status_code != 200:
            raise UpstreamUnavailable(AUDIENCE, f"GET /v1/feed/status answered {response.status_code}")
        return FeedStatus.model_validate(response.json())
