"""The local research cache: candles by stock and timeframe as Parquet files, so backtests do not re-read the API.

One file per (isin, timeframe): `<dir>/<timeframe>/<isin>.parquet`. Prices are stored as float64: every indicator is
computed in float64 from the same two-decimal prices, so a backtest reading the cache sees exactly the numbers the
live engine computed from. A write merges with what is there (latest revision of each candle wins) and replaces the
file atomically, so a crash never leaves half a file. The cache is an optimisation: callers treat any failure as a
miss, never as a reason to stop.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import polars as pl
from tbo_platform_kit.calendar import IST

from tbo_technical_engine.schemas.backtest import StockData
from tbo_technical_engine.schemas.candle import Candle

COLUMNS: dict[str, pl.DataType | type[pl.DataType]] = {
    "start_at": pl.Datetime("us", "UTC"),
    "open": pl.Float64,
    "high": pl.Float64,
    "low": pl.Float64,
    "close": pl.Float64,
    "volume": pl.Int64,
    "revision": pl.Int32,
}


class ResearchCache:
    """Parquet files of candles, one per (timeframe, stock), for research and backtests. The service never reads it to
    decide a live signal."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def path(self, isin: str, timeframe: str) -> Path:
        """Where the file for a series lives (it may not exist yet)."""
        return self.root / timeframe / f"{isin}.parquet"

    def read(self, isin: str, timeframe: str) -> pl.DataFrame:
        """All cached candles, oldest first; empty (with the columns) when there are none."""
        file = self.path(isin, timeframe)
        if not file.exists():
            return pl.DataFrame(schema=COLUMNS)
        return pl.read_parquet(file)

    def stock(self, isin: str) -> StockData | None:
        """A stock's candles for a replay, or None when it has no 5-minute candles cached."""
        five = self.candles(isin, "5m")
        if not five:
            return None
        return StockData(five, self.candles(isin, "15m"), self.candles(isin, "1d"))

    def session_dates(self, isin: str, timeframe: str) -> set[date]:
        """The IST dates that have at least one cached candle; reads the one column only (coverage reports)."""
        file = self.path(isin, timeframe)
        if not file.exists():
            return set()
        starts = pl.read_parquet(file, columns=["start_at"])["start_at"]
        return {s.astimezone(IST).date() for s in starts.unique().to_list()}

    def last_start(self, isin: str, timeframe: str) -> datetime | None:
        """Start time of the newest cached candle, or None when nothing is cached."""
        frame = self.read(isin, timeframe)
        if frame.is_empty():
            return None
        value = frame["start_at"].max()
        return value if isinstance(value, datetime) else None

    def write(self, isin: str, timeframe: str, candles: Iterable[Candle]) -> int:
        """Merge `candles` into the cache; returns the number of rows now cached."""
        rows = [
            {
                "start_at": c.start_at.astimezone(UTC),
                "open": float(c.open),
                "high": float(c.high),
                "low": float(c.low),
                "close": float(c.close),
                "volume": c.volume,
                "revision": c.revision,
            }
            for c in candles
        ]
        if not rows:
            return self.read(isin, timeframe).height
        merged = (
            pl.concat([self.read(isin, timeframe), pl.DataFrame(rows, schema=COLUMNS)])
            .sort(["start_at", "revision"])
            .unique(subset=["start_at"], keep="last", maintain_order=True)
            .sort("start_at")
        )
        file = self.path(isin, timeframe)
        file.parent.mkdir(parents=True, exist_ok=True)
        tmp = file.with_suffix(".parquet.tmp")
        merged.write_parquet(tmp)
        os.replace(tmp, file)
        return merged.height

    def candles(self, isin: str, timeframe: str) -> list[Candle]:
        """The cached candles as Candle objects (prices back to two decimals), for replays and backtests."""
        return [
            Candle(
                isin=isin,
                timeframe=timeframe,
                start_at=row["start_at"],
                open=Decimal(f"{row['open']:.2f}"),
                high=Decimal(f"{row['high']:.2f}"),
                low=Decimal(f"{row['low']:.2f}"),
                close=Decimal(f"{row['close']:.2f}"),
                volume=row["volume"],
                revision=row["revision"],
            )
            for row in self.read(isin, timeframe).iter_rows(named=True)
        ]
