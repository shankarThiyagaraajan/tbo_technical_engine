"""Fill the research cache from the feed's history (review 9.6)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from tbo_technical_engine.services.cache_fill import fill_cache
from tbo_technical_engine.store.research_cache import ResearchCache
from tests.support import ISIN_A, ISIN_B, FakeFeed, history

TODAY = date(2026, 10, 15)


async def test_history_lands_in_the_cache_and_a_repeat_changes_nothing(tmp_path: Path) -> None:
    feed = FakeFeed([c for isin in (ISIN_A, ISIN_B) for tf in history(isin, TODAY).values() for c in tf])
    cache = ResearchCache(tmp_path)
    first = await fill_cache(feed.client(), cache, [ISIN_A, ISIN_B], date(2026, 10, 1), TODAY)
    assert not first.failed and first.candles > 0
    rows = len(cache.read(ISIN_A, "5m"))
    assert rows > 0 and cache.read(ISIN_B, "1d").height > 0
    await fill_cache(feed.client(), cache, [ISIN_A, ISIN_B], date(2026, 10, 1), TODAY)
    assert len(cache.read(ISIN_A, "5m")) == rows  # merged by candle start: idempotent


async def test_daily_candles_are_asked_for_adjusted(tmp_path: Path) -> None:
    feed = FakeFeed([c for tf in history(ISIN_A, TODAY).values() for c in tf])
    await fill_cache(feed.client(), ResearchCache(tmp_path), [ISIN_A], date(2026, 10, 1), TODAY)
    asked = {r.url.params["timeframe"]: r.url.params.get("adjusted") for r in feed.calls}
    assert asked["1d"] == "true" and asked["5m"] is None


async def test_one_failing_stock_is_reported_and_the_rest_are_filled(tmp_path: Path) -> None:
    feed = FakeFeed([c for isin in (ISIN_A, ISIN_B) for tf in history(isin, TODAY).values() for c in tf])
    feed.fail_isins = {ISIN_B}
    cache = ResearchCache(tmp_path)
    result = await fill_cache(feed.client(), cache, [ISIN_A, ISIN_B], date(2026, 10, 1), TODAY)
    assert result.failed == [ISIN_B] and cache.read(ISIN_A, "5m").height > 0
