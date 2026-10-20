"""Fill the research cache from the market feed's history, so a 12-month backtest does not need 12 months of
uninterrupted live uptime (review 9.6). The warm-up only writes the last few sessions the worker happened to read."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from tbo_platform_kit.config.calendar import IST
from tbo_platform_kit.http_client import UpstreamUnavailable
from tbo_platform_kit.logging import get_logger

from tbo_technical_engine.clients.market_feed import MarketFeedClient
from tbo_technical_engine.services.warmup import ADJUSTED_TIMEFRAMES
from tbo_technical_engine.store.research_cache import ResearchCache

log = get_logger(__name__)
DEFAULT_TIMEFRAMES = ("5m", "15m", "1d")


@dataclass
class FillResult:
    """How many candles were fetched, and which stocks failed (the cache keeps what it had for them)."""

    candles: int = 0
    failed: list[str] = field(default_factory=list)


def _midnight(day: date) -> datetime:
    return datetime.combine(day, time(0, 0), tzinfo=IST)


async def fill_cache(
    feed: MarketFeedClient,
    cache: ResearchCache,
    isins: Sequence[str],
    start: date,
    end: date,
    timeframes: Sequence[str] = DEFAULT_TIMEFRAMES,
    *,
    concurrency: int = 4,
) -> FillResult:
    """Page the feed's `/v1/candles` for `start`..`end` (inclusive) and merge it into the cache. Idempotent: the cache
    merges by candle start, latest revision wins. Daily candles are read split-adjusted, as the live warm-up does."""
    gate = asyncio.Semaphore(concurrency)
    result = FillResult()

    async def one(isin: str) -> None:
        async with gate:
            try:
                for tf in timeframes:
                    fetched = [
                        c
                        async for c in feed.candles(
                            isin, tf, _midnight(start), _midnight(end + timedelta(days=1)),
                            adjusted=tf in ADJUSTED_TIMEFRAMES,
                        )
                    ]  # fmt: skip
                    cache.write(isin, tf, fetched)
                    result.candles += len(fetched)
            except UpstreamUnavailable as exc:
                log.warning("cache_fill_failed", isin=isin, error=str(exc)[:200])
                result.failed.append(isin)

    await asyncio.gather(*(one(i) for i in isins))
    return result


async def fill_index_cache(
    feed: MarketFeedClient,
    cache: ResearchCache,
    codes: Sequence[str],
    start: date,
    end: date,
    timeframes: Sequence[str] = ("5m",),
) -> FillResult:
    """Page the feed's `/v1/index-candles` for `start`..`end` (inclusive) into the cache's index folder, so a backtest
    can apply `vwap_pivot`'s index filter (ADR-019). Idempotent like `fill_cache`."""
    result = FillResult()
    for code in codes:
        try:
            for tf in timeframes:
                fetched = [
                    c.as_candle()
                    async for c in feed.index_candles(code, tf, _midnight(start), _midnight(end + timedelta(days=1)))
                ]
                cache.indices.write(code, tf, fetched)
                result.candles += len(fetched)
        except UpstreamUnavailable as exc:
            log.warning("index_cache_fill_failed", index_code=code, error=str(exc)[:200])
            result.failed.append(code)
    return result
