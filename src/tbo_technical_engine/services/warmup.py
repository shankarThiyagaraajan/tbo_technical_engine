"""Warm-up: fill the buffer from the feed's REST history so indicators start from complete, current data.

Run at start, at 09:00 IST (a fresh session), and after the feed was unhealthy. A stock stays silent until each
timeframe it needs has `min_bars` candles. One stock failing never stops the others: it stays cold and the next
warm-up pass retries it.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import date, datetime, time

from tbo_platform_kit.calendar import IST, TradingCalendar
from tbo_platform_kit.http_client import UpstreamUnavailable
from tbo_platform_kit.logging import get_logger

from tbo_technical_engine.clients.market_feed import MarketFeedClient
from tbo_technical_engine.schemas.candle import Candle
from tbo_technical_engine.services.candle_buffer import CandleBuffer
from tbo_technical_engine.store.research_cache import ResearchCache

log = get_logger(__name__)


@dataclass(frozen=True)
class WarmupSpec:
    """How much history each timeframe needs: sessions to read back, and candles required before a stock is warm.

    Why these numbers (starting values, tuned with the strategies): EMA(21), ATR(14) and ADX(14) need about 100
    five-minute candles to settle (Wilder smoothing keeps a memory of the start); Supertrend(10, 3) on 15 minutes
    about 30; the daily candles give ATR(14) for the opening-range rule and the previous day's pivots.
    """

    sessions: dict[str, int] = field(default_factory=lambda: {"5m": 5, "15m": 5, "1d": 30})
    min_bars: dict[str, int] = field(default_factory=lambda: {"5m": 100, "15m": 30, "1d": 15})

    @property
    def timeframes(self) -> list[str]:
        return list(self.sessions)


@dataclass
class WarmupResult:
    warm: set[str] = field(default_factory=set)
    cold: set[str] = field(default_factory=set)


def window_start(calendar: TradingCalendar, today: date, sessions: int) -> datetime:
    """Midnight IST of the trading day `sessions` trading days before `today` (UTC-aware)."""
    day = today
    for _ in range(sessions):
        day = calendar.previous_trading_day(day)
    return datetime.combine(day, time(0, 0), tzinfo=IST)


def is_warm(buffer: CandleBuffer, isin: str, spec: WarmupSpec) -> bool:
    return all(buffer.count(isin, tf) >= need for tf, need in spec.min_bars.items())


async def warm_up(
    client: MarketFeedClient,
    buffer: CandleBuffer,
    isins: list[str],
    now: datetime,
    calendar: TradingCalendar,
    spec: WarmupSpec,
    *,
    cache: ResearchCache | None = None,
    concurrency: int = 8,
) -> WarmupResult:
    """Read history for every stock and merge it into the buffer (live candles already there are kept)."""
    today = now.astimezone(IST).date()
    gate = asyncio.Semaphore(concurrency)
    result = WarmupResult()

    async def one(isin: str) -> None:
        async with gate:
            try:
                for tf, sessions in spec.sessions.items():
                    start = window_start(calendar, today, sessions)
                    fetched: list[Candle] = [c async for c in client.candles(isin, tf, start, now)]
                    buffer.add_many(fetched)
                    if cache is not None:
                        try:
                            cache.write(isin, tf, fetched)
                        except Exception as exc:  # the cache is an optimisation, never a reason to stay cold
                            log.warning(
                                "research_cache_write_failed", isin=isin, timeframe=tf, error=type(exc).__name__
                            )
            except UpstreamUnavailable as exc:
                log.warning("warmup_failed", isin=isin, error=str(exc)[:200])
        (result.warm if is_warm(buffer, isin, spec) else result.cold).add(isin)

    await asyncio.gather(*(one(i) for i in isins))
    log.info("warmup_done", warm=len(result.warm), cold=len(result.cold))
    return result
