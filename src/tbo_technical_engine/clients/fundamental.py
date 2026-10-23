"""The fundamental engine's tradable universe (`GET /v1/universe`)."""

from __future__ import annotations

from datetime import date
from typing import Any

from tbo_platform_kit.http_client import ServiceClient, UpstreamUnavailable

from tbo_technical_engine.schemas.candle import UniverseSnapshot

AUDIENCE = "tbo_fundamental_engine"
SCOPES = ["fundamental:read"]
PAGE = 500
MAX_PAGES = 20
"""20 x 500 is over ten times the 800-stock ceiling of the `universe.published` event."""


class FundamentalClient:
    """Typed access to the fundamental engine; only the universe is needed here."""

    def __init__(self, client: ServiceClient) -> None:
        self.client = client

    async def _page(self, path: str, params: dict[str, str | int]) -> dict[str, Any] | None:
        """One page of a paged list; None on 404 (not published), UpstreamUnavailable on any other non-200."""
        response = await self.client.get(path, params=params)
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            raise UpstreamUnavailable(AUDIENCE, f"GET {path} answered {response.status_code}")
        body: dict[str, Any] = response.json()
        return body

    async def universe(self, trade_date: date | None = None) -> UniverseSnapshot | None:
        """The universe of `trade_date` (default: today, IST), or None when it is not published yet (404)."""
        isins: list[str] = []
        version = 0
        cursor: str | None = None
        for _ in range(MAX_PAGES):
            params: dict[str, str | int] = {"limit": PAGE}
            if trade_date:
                params["date"] = trade_date.isoformat()
            if cursor:
                params["cursor"] = cursor
            body = await self._page("/v1/universe", params)
            if body is None:
                return None
            for item in body["items"]:
                isins.append(item["isin"])
                version = max(version, int(item["universe_version"]))
                day = item["trade_date"]
            cursor = body.get("next_cursor")
            if not cursor:
                return UniverseSnapshot(trade_date=day, universe_version=version, isins=tuple(isins)) if isins else None
        raise UpstreamUnavailable(AUDIENCE, "universe paging did not end")

    async def gate(self, trade_date: date) -> dict[str, str] | None:
        """The gate status of every stock in that day's universe (`GET /v1/gate?date=`), or None when that day's run
        has not finished or never happened (404). Past dates never change, which is what makes a backtest repeatable."""
        statuses: dict[str, str] = {}
        cursor: str | None = None
        for _ in range(MAX_PAGES):
            params: dict[str, str | int] = {"limit": PAGE, "date": trade_date.isoformat()}
            if cursor:
                params["cursor"] = cursor
            body = await self._page("/v1/gate", params)
            if body is None:
                return None
            for item in body["items"]:
                statuses[item["isin"]] = item["status"]
            cursor = body.get("next_cursor")
            if not cursor:
                return statuses or None
        raise UpstreamUnavailable(AUDIENCE, "gate paging did not end")
