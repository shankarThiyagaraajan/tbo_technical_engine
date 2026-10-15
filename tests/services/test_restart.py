"""TE1 exit criterion: after a restart mid-session the engine resumes with the same data within one candle.

The inputs of every indicator are the buffer's candles, so identical buffers mean identical indicator values (the
indicator-level version of this test is in tests/indicators once TE2 exists).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from tbo_technical_engine.schemas.candle import Candle
from tbo_technical_engine.services.candle_buffer import CandleBuffer
from tbo_technical_engine.services.warmup import WarmupSpec, warm_up
from tests.support import CALENDAR, ISIN_A, FakeFeed, history, session_candles

TODAY = date(2026, 10, 15)


def snapshot(buffer: CandleBuffer) -> dict[str, list[Candle]]:
    return {tf: buffer.series(ISIN_A, tf) for tf in ("5m", "15m", "1d")}


async def test_a_restarted_engine_holds_the_same_candles_as_one_that_never_stopped() -> None:
    past = history(ISIN_A, TODAY, sessions=5)  # exactly the warm-up window: both engines read the same history
    today_5m = session_candles(ISIN_A, "5m", TODAY)
    today_15m = session_candles(ISIN_A, "15m", TODAY)
    feed_data = [c for tf in past.values() for c in tf] + today_5m + today_15m

    # The engine that never stopped: history at 09:00, then every closed candle of today through the stream.
    steady = CandleBuffer()
    steady.add_many(c for tf in past.values() for c in tf)
    for c in today_5m + today_15m:
        if c.start_at + timedelta(minutes=5 if c.timeframe == "5m" else 15) <= datetime(2026, 10, 15, 6, 0, tzinfo=UTC):
            steady.add(c)

    # The engine that crashed at 11:20 IST, was down for 10 minutes, and restarts at 11:30 IST (06:00Z): history up to
    # 11:30 from REST, and the stream's backlog (candles it missed and some it had already seen) after that.
    restarted = CandleBuffer()
    now = datetime(2026, 10, 15, 6, 0, tzinfo=UTC)
    await warm_up(FakeFeed(feed_data).client(), restarted, [ISIN_A], now, CALENDAR, WarmupSpec())
    for c in today_5m[-10:]:  # redelivered stream entries
        if c.start_at + timedelta(minutes=5) <= now:
            restarted.add(c)

    assert snapshot(restarted) == snapshot(steady)


async def test_a_candle_missed_in_an_outage_is_repaired_by_the_next_warm_up() -> None:
    day = session_candles(ISIN_A, "5m", TODAY)
    feed = FakeFeed([c for tf in history(ISIN_A, TODAY).values() for c in tf] + day)
    now = datetime(2026, 10, 15, 6, 0, tzinfo=UTC)
    buffer = CandleBuffer()
    live = [c for c in day if c.start_at + timedelta(minutes=5) <= now]
    buffer.add_many(c for i, c in enumerate(live) if i not in (10, 11, 12))  # three candles lost
    assert buffer.count(ISIN_A, "5m") == len(live) - 3
    await warm_up(feed.client(), buffer, [ISIN_A], now, CALENDAR, WarmupSpec())
    today_only = [c for c in buffer.series(ISIN_A, "5m") if c.start_at >= day[0].start_at]
    assert today_only == live
