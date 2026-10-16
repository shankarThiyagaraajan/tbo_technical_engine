from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from tbo_technical_engine.schemas.candle import Candle
from tbo_technical_engine.store.research_cache import ResearchCache
from tests.support import ISIN_A, candle

T0 = datetime(2026, 10, 15, 3, 45, tzinfo=UTC)


def rows(n: int, close: str = "100.00") -> list[Candle]:
    return [candle(ISIN_A, "5m", T0 + timedelta(minutes=5 * i), close) for i in range(n)]


def test_empty_when_nothing_cached(tmp_path: Path) -> None:
    cache = ResearchCache(tmp_path)
    assert (
        cache.read(ISIN_A, "5m").is_empty()
        and cache.last_start(ISIN_A, "5m") is None
        and cache.candles(ISIN_A, "5m") == []
    )


def test_round_trips_prices_to_two_decimals(tmp_path: Path) -> None:
    cache = ResearchCache(tmp_path)
    original = [candle(ISIN_A, "5m", T0, "1247.35")]
    cache.write(ISIN_A, "5m", original)
    (back,) = cache.candles(ISIN_A, "5m")
    assert (back.open, back.high, back.low, back.close, back.volume) == (
        original[0].open,
        original[0].high,
        original[0].low,
        original[0].close,
        original[0].volume,
    )
    assert back.start_at == T0


def test_a_second_write_merges_and_the_latest_revision_wins(tmp_path: Path) -> None:
    cache = ResearchCache(tmp_path)
    assert cache.write(ISIN_A, "5m", rows(3)) == 3
    corrected = [candle(ISIN_A, "5m", T0, "105.00", revision=2)]
    assert cache.write(ISIN_A, "5m", corrected + rows(5)[3:]) == 5
    got = {c.start_at: c for c in cache.candles(ISIN_A, "5m")}
    assert got[T0].revision == 2 and str(got[T0].close) == "105.00"
    assert cache.last_start(ISIN_A, "5m") == T0 + timedelta(minutes=20)


def test_writing_nothing_changes_nothing(tmp_path: Path) -> None:
    cache = ResearchCache(tmp_path)
    cache.write(ISIN_A, "5m", rows(2))
    assert cache.write(ISIN_A, "5m", []) == 2


def test_no_temp_file_is_left_behind(tmp_path: Path) -> None:
    cache = ResearchCache(tmp_path)
    cache.write(ISIN_A, "5m", rows(2))
    assert [p.name for p in (tmp_path / "5m").iterdir()] == [f"{ISIN_A}.parquet"]
