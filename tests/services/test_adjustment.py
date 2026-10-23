"""Review 9.23: one price scale across timeframes. Before an ex-date the feed adjusts daily candles, not intraday
ones; the warm-up and the cache fill rescale the intraday candles by the session's factor, and a cache written before
a split is dropped rather than mixed."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx

from tbo_technical_engine.backtest.replay import day_of, replay_stock
from tbo_technical_engine.schemas.candle import Candle
from tbo_technical_engine.services.adjustment import adjust, basis_changed, session_factors, session_of
from tbo_technical_engine.services.cache_fill import fill_cache
from tbo_technical_engine.services.candle_buffer import CandleBuffer
from tbo_technical_engine.services.warmup import WarmupSpec, warm_up
from tbo_technical_engine.store.research_cache import ResearchCache
from tests.strategies.session import CONFIG, History
from tests.support import CALENDAR, ISIN_A, FakeFeed

BASE = History(days=24, seed=12)
EX_DATE = day_of(BASE.sessions[16][0])
FIRST, LAST = day_of(BASE.sessions[12][0]), day_of(BASE.sessions[19][0])
OLDEST, NEWEST = day_of(BASE.sessions[0][0]), day_of(BASE.sessions[-1][0])


def even(c: Candle) -> Candle:
    """The fixture's candle with an even volume, so a 1:2 split's halving is exact."""
    return c.model_copy(update={"volume": c.volume * 2})


def pre_split(c: Candle) -> Candle:
    """The candle as the exchange printed it before a 1:2 split on EX_DATE: twice the price, half the volume."""
    if session_of(c) >= EX_DATE:
        return c
    two = Decimal(2)
    return c.model_copy(
        update={"open": c.open * two, "high": c.high * two, "low": c.low * two, "close": c.close * two,
                "volume": c.volume // 2}
    )  # fmt: skip


FIVE = [even(c) for c in BASE.five]
FIFTEEN = [even(c) for c in BASE.fifteen]
DAILY = [even(c) for c in BASE.daily]
RAW = [pre_split(c) for c in (*FIVE, *FIFTEEN, *DAILY)]


class SplitFeed(FakeFeed):
    """Raw candles, and the daily ones adjusted when asked (`adjusted=true`), as the feed's contract says."""

    def __init__(self, raw: list[Candle], adjusted_daily: list[Candle]) -> None:
        super().__init__(raw)
        self.raw, self.adjusted_daily = raw, adjusted_daily

    def handle(self, request: httpx.Request) -> httpx.Response:
        adjusted = request.url.params.get("adjusted") == "true" and request.url.params.get("timeframe") == "1d"
        self.data = [c for c in self.raw if c.timeframe != "1d"] + self.adjusted_daily if adjusted else self.raw
        return super().handle(request)


def test_the_factor_of_a_session_is_adjusted_over_raw_close_and_absent_without_an_action() -> None:
    factors = session_factors([c for c in RAW if c.timeframe == "1d"], DAILY)
    assert set(factors) == {day_of(s[0]) for s in BASE.sessions[:16]}
    assert set(factors.values()) == {Decimal("0.5")}
    assert session_factors(DAILY, DAILY) == {}
    nearly = [c.model_copy(update={"close": c.close + Decimal("0.01")}) for c in DAILY]
    assert session_factors(DAILY, nearly) == {}  # rounding, not a corporate action


def test_adjusting_gives_back_the_unsplit_candles_and_leaves_the_rest_untouched() -> None:
    raw_five = [c for c in RAW if c.timeframe == "5m"]
    factors = session_factors([c for c in RAW if c.timeframe == "1d"], DAILY)
    assert adjust(raw_five, factors) == FIVE
    after = [c for c in raw_five if session_of(c) >= EX_DATE]
    assert all(a is b for a, b in zip(adjust(after, factors), after, strict=True))
    assert str(adjust(raw_five[:1], factors)[0].close) == str(FIVE[0].close)  # the feed's two decimals


def test_a_split_in_the_middle_of_the_cache_replays_like_no_split_at_all(tmp_path: Path) -> None:
    """The vector: a cache filled across a 1:2 split gives the same candidates as the unsplit history."""
    feed = SplitFeed(RAW, DAILY)
    cache = ResearchCache(tmp_path)
    result = asyncio_run(fill_cache(feed.client(), cache, [ISIN_A], OLDEST, NEWEST))
    assert not result.failed
    data = cache.stock(ISIN_A)
    assert data is not None
    assert [c.close for c in data.five] == [c.close for c in FIVE]
    assert [c.volume for c in data.fifteen] == [c.volume for c in FIFTEEN]

    def key(c: Any) -> tuple[Any, ...]:
        return tuple(v for k, v in c.model_dump().items() if k != "created_at")

    split = [key(c) for c in replay_stock(CONFIG, data.five, data.fifteen, data.daily, FIRST, LAST)]
    unsplit = [key(c) for c in replay_stock(CONFIG, FIVE, FIFTEEN, DAILY, FIRST, LAST)]
    assert split  # the strategies fire
    assert split == unsplit  # and identically


def test_a_cache_written_before_the_split_is_dropped_not_mixed(tmp_path: Path) -> None:
    cache = ResearchCache(tmp_path)
    for tf in ("5m", "15m", "1d"):
        cache.write(ISIN_A, tf, [c for c in RAW if c.timeframe == tf])  # written before the action was known
    older = [c for c in RAW if c.timeframe == "5m" and session_of(c) < day_of(BASE.sessions[4][0])]
    assert basis_changed(cache.daily_closes(ISIN_A), DAILY)
    asyncio_run(fill_cache(SplitFeed(RAW, DAILY).client(), cache, [ISIN_A], day_of(BASE.sessions[4][0]), LAST))
    kept = cache.candles(ISIN_A, "5m")
    assert {c.start_at for c in older}.isdisjoint({c.start_at for c in kept})  # old-scale rows are gone
    assert [c.close for c in kept] == [c.close for c in FIVE if day_of(BASE.sessions[4][0]) <= session_of(c) <= LAST]


async def test_the_warm_up_rescales_the_buffer_of_a_worker_that_was_running_through_the_split() -> None:
    now = datetime.combine(day_of(BASE.sessions[17][0]), datetime.min.time(), tzinfo=UTC) + timedelta(hours=3)
    buffer = CandleBuffer()
    buffer.add_many(c for c in RAW if session_of(c) < day_of(BASE.sessions[17][0]))  # what the stream had sent
    feed = SplitFeed(RAW, DAILY)
    await warm_up(feed.client(), buffer, [ISIN_A], now, CALENDAR, WarmupSpec())
    held = {c.start_at: c for c in buffer.series(ISIN_A, "5m")}
    unsplit = {c.start_at: c for c in FIVE if c.start_at in held}
    assert held == unsplit  # same revision, other prices: the warm-up's copy wins (replace)
    # What the warm-up did not read again (older than its 5 sessions) was on the old scale, so it is gone.
    assert min(held) == min(c.start_at for c in FIVE if session_of(c) >= day_of(BASE.sessions[12][0]))
    daily = {c.start_at: c.close for c in buffer.series(ISIN_A, "1d")}
    assert all(daily[c.start_at] == c.close for c in DAILY if c.start_at in daily)
    asked = [
        (r.url.params["timeframe"], r.url.params.get("adjusted")) for r in feed.calls if r.url.path == "/v1/candles"
    ]
    assert asked[:2] == [("1d", None), ("1d", "true")]  # raw closes of the intraday window, then adjusted history


def test_a_newer_revision_is_never_replaced_by_an_older_one() -> None:
    buffer = CandleBuffer()
    newer = FIVE[0].model_copy(update={"revision": 2, "close": FIVE[0].close + 1})
    buffer.add(newer)
    assert buffer.add(FIVE[0], replace=True) is False
    assert buffer.last(ISIN_A, "5m") == newer
    assert buffer.add(newer, replace=True) is False  # the same copy changes nothing
    assert buffer.add(newer.model_copy(update={"close": newer.close + 1}), replace=True) is True
    assert buffer.add(newer, replace=False) is False


def asyncio_run(coro: Any) -> Any:
    import asyncio

    return asyncio.run(coro)
