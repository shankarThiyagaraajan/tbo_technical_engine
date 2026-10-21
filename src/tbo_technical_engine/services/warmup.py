"""Warm-up: fill the buffer from the feed's REST history so indicators start from complete, current data.

Run at start, at 09:00 IST (a fresh session), and after the feed was unhealthy. A stock stays silent until each
timeframe it needs has `min_bars` candles. One stock failing never stops the others: it stays cold and the next
warm-up pass retries it.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import date, datetime, time
from decimal import Decimal

from tbo_platform_kit.calendar import TradingCalendar
from tbo_platform_kit.config.calendar import IST
from tbo_platform_kit.http_client import UpstreamUnavailable
from tbo_platform_kit.logging import get_logger

from tbo_technical_engine.clients.market_feed import MarketFeedClient
from tbo_technical_engine.schemas.candle import Candle
from tbo_technical_engine.services.adjustment import adjust, rebase_cache, session_factors
from tbo_technical_engine.services.candle_buffer import CandleBuffer
from tbo_technical_engine.store.research_cache import ResearchCache

log = get_logger(__name__)
ADJUSTED_TIMEFRAMES = frozenset({"1d"})


@dataclass(frozen=True)
class WarmupSpec:
    """How much history each timeframe needs: sessions to read back, and candles required before a stock is warm.

    Why these numbers (starting values, tuned with the strategies): EMA(21), ATR(14) and ADX(14) need about 100
    five-minute candles to settle (Wilder smoothing keeps a memory of the start); Supertrend(10, 3) on 15 minutes
    about 30; the daily candles give ATR(14) for the opening-range rule and the previous day's pivots. The daily
    ATR keeps about 30 % of its seed after 30 bars, so 150 sessions (one REST page) make a restarted worker agree with
    one that has run for months, and with the backtest (review 9.8).
    """

    sessions: dict[str, int] = field(default_factory=lambda: {"5m": 5, "15m": 5, "1d": 150})
    min_bars: dict[str, int] = field(default_factory=lambda: {"5m": 100, "15m": 30, "1d": 15})

    @property
    def timeframes(self) -> list[str]:
        return list(self.sessions)


@dataclass
class WarmupResult:
    """Which stocks ended the warm-up with enough history (`warm`) and which did not (`cold`)."""

    warm: set[str] = field(default_factory=set)
    cold: set[str] = field(default_factory=set)


def window_start(calendar: TradingCalendar, today: date, sessions: int) -> datetime:
    """Midnight IST of the trading day `sessions` trading days before `today` (UTC-aware)."""
    day = today
    for _ in range(sessions):
        day = calendar.previous_trading_day(day)
    return datetime.combine(day, time(0, 0), tzinfo=IST)


def is_warm(buffer: CandleBuffer, isin: str, spec: WarmupSpec) -> bool:
    """True when the buffer holds the minimum number of candles of every timeframe the spec lists for `isin`."""
    return all(buffer.count(isin, tf) >= need for tf, need in spec.min_bars.items())


def _rebase(cache: ResearchCache | None, isin: str, adjusted_daily: list[Candle]) -> None:
    """Drop the stock's cache when a split changed its scale (9.23); a cache failure never keeps a stock cold."""
    if cache is None:
        return
    try:
        rebase_cache(cache, isin, adjusted_daily)
    except Exception as exc:  # the cache is an optimisation
        log.warning("research_cache_rebase_failed", isin=isin, error=type(exc).__name__)


def _write(cache: ResearchCache | None, isin: str, timeframe: str, candles: list[Candle]) -> None:
    """Write through to the research cache; a failure is logged, never a reason to stay cold."""
    if cache is None:
        return
    try:
        cache.write(isin, timeframe, candles)
    except Exception as exc:  # the cache is an optimisation, never a reason to stay cold
        log.warning("research_cache_write_failed", isin=isin, timeframe=timeframe, error=type(exc).__name__)


async def _load(
    client: MarketFeedClient,
    buffer: CandleBuffer,
    cache: ResearchCache | None,
    isin: str,
    now: datetime,
    calendar: TradingCalendar,
    spec: WarmupSpec,
) -> None:
    """Read one stock's history for every timeframe and merge it into the buffer."""
    today = now.astimezone(IST).date()
    # Daily first: its adjusted read gives the factors that put the intraday candles on the same scale (9.23).
    order = sorted(spec.sessions, key=lambda tf: tf not in ADJUSTED_TIMEFRAMES)
    intraday = [n for tf, n in spec.sessions.items() if tf not in ADJUSTED_TIMEFRAMES]
    raw_daily: list[Candle] = []
    if intraday and "1d" in spec.sessions:  # the raw daily closes of the intraday window
        since = window_start(calendar, today, max(intraday))
        raw_daily = [c async for c in client.candles(isin, "1d", since, now)]
    factors: dict[date, Decimal] = {}
    for tf in order:
        start = window_start(calendar, today, spec.sessions[tf])
        # Daily candles are read split- and bonus-adjusted, so the daily ATR has no gap in it (9.5), and
        # intraday ones are rescaled to match (9.23): one price scale across timeframes.
        fetched: list[Candle] = [
            c async for c in client.candles(isin, tf, start, now, adjusted=tf in ADJUSTED_TIMEFRAMES)
        ]
        if tf in ADJUSTED_TIMEFRAMES:
            factors = session_factors(raw_daily, fetched)
            _rebase(cache, isin, fetched)
        else:
            fetched = adjust(fetched, factors)
            if factors:  # a split inside the window: older buffered candles are on the old scale
                buffer.forget_before(isin, tf, start)
        buffer.add_many(fetched, replace=True)
        _write(cache, isin, tf, fetched)


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
    gate = asyncio.Semaphore(concurrency)
    result = WarmupResult()

    async def one(isin: str) -> None:
        async with gate:
            try:
                await _load(client, buffer, cache, isin, now, calendar, spec)
            except UpstreamUnavailable as exc:
                log.warning("warmup_failed", isin=isin, error=str(exc)[:200])
        (result.warm if is_warm(buffer, isin, spec) else result.cold).add(isin)

    await asyncio.gather(*(one(i) for i in isins))
    log.info("warmup_done", warm=len(result.warm), cold=len(result.cold))
    return result
