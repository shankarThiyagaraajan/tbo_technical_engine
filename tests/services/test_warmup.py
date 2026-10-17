from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

from tbo_platform_kit.config.calendar import IST

from tbo_technical_engine.services.candle_buffer import CandleBuffer
from tbo_technical_engine.services.warmup import WarmupSpec, is_warm, warm_up, window_start
from tbo_technical_engine.store.research_cache import ResearchCache
from tests.support import CALENDAR, ISIN_A, ISIN_B, FakeFeed, history, session_candles

TODAY = date(2026, 10, 15)  # a Thursday
NOW = datetime(2026, 10, 15, 4, 30, tzinfo=UTC)  # 10:00 IST


def test_the_window_starts_at_midnight_ist_n_trading_days_back() -> None:
    # 5 trading days before Thu 15 Oct: Wed 14, Tue 13, Mon 12, Fri 9, Thu 8.
    assert window_start(CALENDAR, TODAY, 5) == datetime(2026, 10, 8, 0, 0, tzinfo=IST)


def test_a_holiday_is_not_a_trading_day_in_the_window() -> None:
    # Fri 2 Oct is a holiday: from Mon 5 Oct, two sessions back is Thu 1 Oct, not Fri 2 Oct.
    assert window_start(CALENDAR, date(2026, 10, 5), 2) == datetime(2026, 9, 30, 0, 0, tzinfo=IST)


async def test_history_fills_the_buffer_and_stocks_become_warm() -> None:
    feed = FakeFeed([c for tf in history(ISIN_A, TODAY).values() for c in tf])
    buffer = CandleBuffer()
    result = await warm_up(feed.client(), buffer, [ISIN_A], NOW, CALENDAR, WarmupSpec())
    assert result.warm == {ISIN_A} and not result.cold
    assert buffer.count(ISIN_A, "5m") >= 100 and is_warm(buffer, ISIN_A, WarmupSpec())


async def test_a_stock_with_too_little_history_stays_cold() -> None:
    thin = list(session_candles(ISIN_A, "5m", date(2026, 10, 14)))
    result = await warm_up(FakeFeed(thin).client(), CandleBuffer(), [ISIN_A], NOW, CALENDAR, WarmupSpec())
    assert result.cold == {ISIN_A} and not result.warm


async def test_one_failing_stock_does_not_stop_the_others() -> None:
    data = [c for isin in (ISIN_A, ISIN_B) for tf in history(isin, TODAY).values() for c in tf]
    feed = FakeFeed(data)
    feed.fail_isins = {ISIN_B}
    result = await warm_up(feed.client(), CandleBuffer(), [ISIN_A, ISIN_B], NOW, CALENDAR, WarmupSpec())
    assert result.warm == {ISIN_A} and result.cold == {ISIN_B}


async def test_only_closed_candles_are_read() -> None:
    today = session_candles(ISIN_A, "5m", TODAY)
    feed = FakeFeed([c for tf in history(ISIN_A, TODAY).values() for c in tf] + today)
    buffer = CandleBuffer()
    await warm_up(feed.client(), buffer, [ISIN_A], NOW, CALENDAR, WarmupSpec())
    last = buffer.last(ISIN_A, "5m")
    assert last is not None and last.start_at == datetime(
        2026, 10, 15, 4, 25, tzinfo=UTC
    )  # 09:55 IST, closed at 10:00; the 10:00 candle is still forming


async def test_live_candles_already_in_the_buffer_are_kept() -> None:
    feed = FakeFeed([c for tf in history(ISIN_A, TODAY).values() for c in tf])
    buffer = CandleBuffer()
    live = session_candles(ISIN_A, "5m", TODAY)[0]
    buffer.add(live)
    await warm_up(feed.client(), buffer, [ISIN_A], NOW, CALENDAR, WarmupSpec())
    assert live in buffer.series(ISIN_A, "5m")


async def test_history_is_written_through_to_the_research_cache(tmp_path: Path) -> None:
    feed = FakeFeed([c for tf in history(ISIN_A, TODAY).values() for c in tf])
    cache = ResearchCache(tmp_path)
    buffer = CandleBuffer()
    await warm_up(feed.client(), buffer, [ISIN_A], NOW, CALENDAR, WarmupSpec(), cache=cache)
    assert len(cache.candles(ISIN_A, "5m")) == buffer.count(ISIN_A, "5m")


async def test_a_broken_cache_never_keeps_a_stock_cold(tmp_path: Path) -> None:
    blocker = tmp_path / "cache"
    blocker.write_text("a file where the directory should be")
    feed = FakeFeed([c for tf in history(ISIN_A, TODAY).values() for c in tf])
    result = await warm_up(
        feed.client(), CandleBuffer(), [ISIN_A], NOW, CALENDAR, WarmupSpec(), cache=ResearchCache(blocker)
    )
    assert result.warm == {ISIN_A}
