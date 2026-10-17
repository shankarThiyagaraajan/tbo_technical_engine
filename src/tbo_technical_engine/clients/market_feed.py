"""The market feed's REST API: candle history (cursor-paginated), the latest candle and the feed's health.

A page that fails raises UpstreamUnavailable instead of returning a partial history: indicators computed on a
series with a hole in the middle are wrong in a way that looks right.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime

from tbo_platform_kit.http_client import ServiceClient, UpstreamUnavailable

from tbo_technical_engine.schemas.candle import Candle, FeedStatus
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
        cursor: str | None = None
        for _ in range(MAX_PAGES):
            params: dict[str, str | int | bool] = {
                "isin": isin,
                "timeframe": timeframe,
                "from": z(start),
                "to": z(end),
                "limit": PAGE,
            }
            if adjusted:
                params["adjusted"] = True
            if cursor:
                params["cursor"] = cursor
            response = await self.client.get("/v1/candles", params=params)
            if response.status_code != 200:
                raise UpstreamUnavailable(AUDIENCE, f"GET /v1/candles answered {response.status_code}")
            body = response.json()
            for item in body["items"]:
                yield Candle.model_validate(item)
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
