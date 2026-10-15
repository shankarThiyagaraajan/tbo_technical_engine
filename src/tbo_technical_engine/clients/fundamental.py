"""The fundamental engine's tradable universe (`GET /v1/universe`)."""

from __future__ import annotations

from datetime import date

from tbo_platform_kit.http_client import ServiceClient, UpstreamUnavailable

from tbo_technical_engine.schemas.candle import UniverseSnapshot

AUDIENCE = "tbo_fundamental_engine"
SCOPES = ["fundamental:read"]
PAGE = 500
MAX_PAGES = 20
"""20 x 500 is over ten times the 800-stock ceiling of the `universe.published` event."""


class FundamentalClient:
    def __init__(self, client: ServiceClient) -> None:
        self.client = client

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
            response = await self.client.get("/v1/universe", params=params)
            if response.status_code == 404:
                return None
            if response.status_code != 200:
                raise UpstreamUnavailable(AUDIENCE, f"GET /v1/universe answered {response.status_code}")
            body = response.json()
            for item in body["items"]:
                isins.append(item["isin"])
                version = max(version, int(item["universe_version"]))
                day = item["trade_date"]
            cursor = body.get("next_cursor")
            if not cursor:
                if not isins:
                    return None
                return UniverseSnapshot(trade_date=day, universe_version=version, isins=tuple(isins))
        raise UpstreamUnavailable(AUDIENCE, "universe paging did not end")
