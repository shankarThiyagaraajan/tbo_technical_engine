"""The synthetic market is reproducible, shaped like the feed's candles, and busy enough to fire every strategy."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import polars as pl
from tbo_platform_kit.config.calendar import IST

from tbo_technical_engine.backtest.replay import replay_stock
from tbo_technical_engine.config import synthetic as cfg
from tbo_technical_engine.schemas.synthetic import SyntheticSpec, isin_of
from tbo_technical_engine.services.strategy_loader import load_strategies
from tbo_technical_engine.store.research_cache import ResearchCache
from tbo_technical_engine.synthetic.cache import fill_cache
from tbo_technical_engine.synthetic.generate import (
    fifteen_minute,
    five_minute,
    minute_candles,
    stock_candles,
    stock_sessions,
    weekdays_ending,
)
from tbo_technical_engine.synthetic.ticks import TICK_SCHEMA, session_ticks, write_ticks

END = date(2026, 10, 15)
ROOT = Path(__file__).resolve().parents[2]


def spec(stocks: int = 2, sessions: int = 4, seed: int = 7) -> SyntheticSpec:
    return SyntheticSpec(stocks=stocks, sessions=sessions, end=END, seed=seed)


def test_the_same_spec_gives_the_same_candles_and_a_stock_does_not_depend_on_the_others() -> None:
    a = stock_candles(spec(stocks=2), 1)
    b = stock_candles(spec(stocks=50), 1)  # the stock count does not enter the stock's own random stream
    assert a == b and a != stock_candles(spec(seed=8), 1)


def test_isins_are_well_formed_and_distinct() -> None:
    isins = [isin_of(i) for i in range(300)]
    assert len(set(isins)) == 300 and all(re.fullmatch(r"IN[A-Z0-9]{9}[0-9]", i) for i in isins)


def test_weekdays_skip_weekends_and_end_on_the_end_date() -> None:
    days = weekdays_ending(END, 7)
    assert days[-1] == END and all(d.weekday() < 5 for d in days) and days == sorted(days) and len(set(days)) == 7


def test_candles_aggregate_the_minutes_the_way_the_feed_does() -> None:
    session = stock_sessions(spec(), 0)[-1]
    isin = isin_of(0)
    minutes, five, fifteen = minute_candles(isin, session), five_minute(isin, session), fifteen_minute(isin, session)
    assert (len(minutes), len(five), len(fifteen)) == (375, 75, 25)
    first = five[0]
    assert first.open == minutes[0].open and first.close == minutes[4].close
    assert first.high == max(m.high for m in minutes[:5]) and first.low == min(m.low for m in minutes[:5])
    assert first.volume == sum(m.volume for m in minutes[:5])
    assert sum(c.volume for c in fifteen) == sum(c.volume for c in five) == sum(m.volume for m in minutes)
    assert five[0].start_at.astimezone(IST).strftime("%H:%M") == "09:15"
    assert five[-1].start_at.astimezone(IST).strftime("%H:%M") == "15:25"


def test_every_candle_is_internally_consistent() -> None:
    data = stock_candles(spec(stocks=1, sessions=6), 0)
    for c in (*data.five, *data.fifteen, *data.daily):
        assert c.low <= min(c.open, c.close) and c.high >= max(c.open, c.close) and c.low > 0 and c.volume > 0
    assert [c.start_at for c in data.five] == sorted({c.start_at for c in data.five})


def test_the_ticks_use_the_feeds_recorder_schema_and_rebuild_the_minute_candles(tmp_path: Path) -> None:
    session = stock_sessions(spec(), 0)[0]
    frame = session_ticks(isin_of(0), session)
    assert dict(frame.schema) == TICK_SCHEMA and frame.height == 375 * cfg.TICKS_PER_MINUTE
    assert frame["exchange_at"].is_sorted() and frame["cum_volume"].is_sorted()
    # Group the ticks by minute the way the feed's candle builder does: open = first, close = last, volume = delta of
    # the day's cumulative volume.
    by_minute = (
        frame.with_columns(pl.col("price").cast(pl.Float64), pl.col("exchange_at").dt.truncate("1m").alias("minute"))
        .group_by("minute", maintain_order=True)
        .agg(
            pl.col("price").first().alias("open"),
            pl.col("price").max().alias("high"),
            pl.col("price").min().alias("low"),
            pl.col("price").last().alias("close"),
            pl.col("cum_volume").last().alias("cum"),
        )
    )
    minutes = minute_candles(isin_of(0), session)
    assert by_minute.height == len(minutes)
    rows = by_minute.to_dicts()
    previous = 0
    for row, m in zip(rows, minutes, strict=True):
        assert (row["open"], row["high"], row["low"], row["close"]) == (
            float(m.open),
            float(m.high),
            float(m.low),
            float(m.close),
        )
        assert row["cum"] - previous == m.volume
        previous = row["cum"]


def test_write_ticks_makes_one_part_file_per_day_named_like_the_recorders(tmp_path: Path) -> None:
    files = write_ticks(spec(stocks=2, sessions=3), tmp_path)
    assert [f.name for f in files] == sorted(f.name for f in files) and len(files) == 3
    assert all(re.fullmatch(r"ticks-\d{4}-\d{2}-\d{2}-0001\.parquet", f.name) for f in files)
    frame = pl.read_parquet(files[0])
    assert frame["isin"].n_unique() == 2 and dict(frame.schema) == TICK_SCHEMA


def test_fill_cache_writes_the_files_the_backtests_read(tmp_path: Path) -> None:
    isins = fill_cache(spec(stocks=2, sessions=3), tmp_path)
    cache = ResearchCache(tmp_path)
    data = cache.stock(isins[0])
    assert data is not None and len(data.five) == 3 * 75 and len(data.fifteen) == 3 * 25 and len(data.daily) == 3
    assert data.five == stock_candles(spec(stocks=2, sessions=3), 0).five


def test_the_synthetic_market_fires_every_strategy() -> None:
    file = load_strategies(ROOT / "config" / "strategies.yaml")
    big = spec(stocks=4, sessions=40)
    days = weekdays_ending(END, 40)
    fired: set[str] = set()
    for i in range(4):
        data = stock_candles(big, i)
        fired |= {c.strategy for c in replay_stock(file, data.five, data.fifteen, data.daily, days[12], days[-1])}
    assert fired == {s for s, spec_ in file.strategies.items() if spec_.is_active}
