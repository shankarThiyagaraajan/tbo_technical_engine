"""Test builders: candles, a fake market feed over httpx.MockTransport, and a calendar that needs no data file.

The kit's holiday file ships empty (it is filled from NSE's circular), so tests build their own calendar.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Any

import httpx
from tbo_platform_kit.calendar import IST, TradingCalendar
from tbo_platform_kit.http_client import ServiceClient, StaticTokenProvider

from tbo_technical_engine.clients import fundamental, market_feed
from tbo_technical_engine.schemas.candle import Candle
from tbo_technical_engine.utils.timefmt import z
from tbo_technical_engine.utils.timeframes import length

ISIN_A = "INE002A01018"
ISIN_B = "INE040A01034"
HOLIDAYS = {
    "2025": {"holidays": []},  # synthetic years for the indicator tests: weekends only
    "2027": {"holidays": []},
    "2026": {
        "holidays": [{"date": "2026-10-02", "name": "Gandhi Jayanti"}, {"date": "2026-10-20", "name": "Dussehra"}]
    },
}
CALENDAR = TradingCalendar.from_document({"years": HOLIDAYS})
OPEN = time(9, 15)


def session_start(day: date) -> datetime:
    """09:15 IST of `day` as an aware UTC datetime."""
    return datetime.combine(day, OPEN, tzinfo=IST).astimezone(UTC)


def candle(
    isin: str, timeframe: str, start: datetime, close: str = "100.00", *, revision: int = 1, volume: int = 1000
) -> Candle:
    price = Decimal(close)
    return Candle(
        isin=isin,
        timeframe=timeframe,
        start_at=start,
        open=price,
        high=price + Decimal("1.00"),
        low=price - Decimal("1.00"),
        close=price,
        volume=volume,
        revision=revision,
    )


def session_candles(isin: str, timeframe: str, day: date, *, base: float = 100.0) -> list[Candle]:
    """A full session of candles for `day` (09:15 to 15:30 IST) with a gentle deterministic wave in the price."""
    step = {"5m": 5, "15m": 15, "1d": 0}[timeframe]
    if timeframe == "1d":
        start = datetime.combine(day, time(0, 0), tzinfo=IST).astimezone(UTC)
        return [candle(isin, "1d", start, f"{base:.2f}")]
    out = []
    t = session_start(day)
    end = datetime.combine(day, time(15, 30), tzinfo=IST).astimezone(UTC)
    n = 0
    while t < end:
        out.append(candle(isin, timeframe, t, f"{base + (n % 17) * 0.35 + day.day * 0.01:.2f}", volume=1000 + n))
        t += timedelta(minutes=step)
        n += 1
    return out


def trading_days_back(day: date, count: int) -> list[date]:
    days: list[date] = []
    while len(days) < count:
        day = CALENDAR.previous_trading_day(day)
        days.append(day)
    return sorted(days)


def history(isin: str, today: date, *, sessions: int = 6, base: float = 100.0) -> dict[str, list[Candle]]:
    """History of the last `sessions` sessions before `today` for every timeframe the warm-up reads."""
    days = trading_days_back(today, sessions)
    out: dict[str, list[Candle]] = {"5m": [], "15m": [], "1d": []}
    for tf in out:
        for d in days if tf != "1d" else trading_days_back(today, 30):
            out[tf] += session_candles(isin, tf, d, base=base)
    return out


def candle_json(c: Candle) -> dict[str, Any]:
    return {
        "isin": c.isin,
        "timeframe": c.timeframe,
        "start_at": z(c.start_at),
        "open": str(c.open),
        "high": str(c.high),
        "low": str(c.low),
        "close": str(c.close),
        "volume": c.volume,
        "open_interest": None,
        "revision": c.revision,
        "is_adjusted": False,
        "is_adjustment_pending": False,
    }


class FakeFeed:
    """GET /v1/candles and /v1/feed/status over a dict of candles; pages are `page` items long."""

    def __init__(self, data: Iterable[Candle] = (), *, page: int = 500) -> None:
        self.data: list[Candle] = list(data)
        self.page = page
        self.state = "healthy"
        self.fail_isins: set[str] = set()
        self.calls: list[httpx.Request] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        if request.url.path == "/v1/feed/status":
            return httpx.Response(
                200,
                json={
                    "state": self.state,
                    "subscribed_count": 2,
                    "last_tick_at": None,
                    "reason": None,
                    "changed_at": "2026-10-15T03:00:00Z",
                },
            )
        q = request.url.params
        isin = q["isin"]
        if isin in self.fail_isins:
            return httpx.Response(503)
        lo, hi = q["from"], q["to"]
        rows = sorted(
            (
                c
                for c in self.data
                if c.isin == isin
                and c.timeframe == q["timeframe"]
                and lo <= z(c.start_at)
                and z(c.start_at + length(c.timeframe)) <= hi  # only closed candles are history
            ),
            key=lambda c: c.start_at,
        )
        offset = int(q.get("cursor", "0"))
        chunk = rows[offset : offset + self.page]
        more = offset + self.page < len(rows)
        return httpx.Response(
            200,
            json={"items": [candle_json(c) for c in chunk], "next_cursor": str(offset + self.page) if more else None},
        )

    def client(self) -> market_feed.MarketFeedClient:
        return market_feed.MarketFeedClient(
            make_client(market_feed.AUDIENCE, market_feed.SCOPES, httpx.MockTransport(self.handle))
        )


def make_client(audience: str, scopes: list[str], transport: httpx.MockTransport) -> ServiceClient:
    return ServiceClient(
        "http://upstream",
        audience=audience,
        scopes=scopes,
        tokens=StaticTokenProvider({audience: "t"}),
        transport=transport,
        max_retries=0,
    )


def fundamental_client(handler: Callable[[httpx.Request], httpx.Response]) -> fundamental.FundamentalClient:
    return fundamental.FundamentalClient(
        make_client(fundamental.AUDIENCE, fundamental.SCOPES, httpx.MockTransport(handler))
    )
