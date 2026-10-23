"""Write sessions in the market feed's recorded-tick Parquet format (`ticks-<date>-<seq>.parquet`), so the feed's own
`ReplaySource` can replay them and this service's drills run on the same files a real recording would give.

Four ticks per minute (open, low, high, close, in that order) with the day's cumulative volume reproduce the minute
exactly through the feed's candle builder: the open and close are the first and last tick, the extremes are in
between. The schema is the feed's `ingest/recorder.py` SCHEMA; the writer here is Polars (no PyArrow dependency), and
Polars stores strings as Arrow `large_string`, so a file reads with the feed's schema after a cast (checked once
against the feed's recorder SCHEMA with PyArrow: same columns, same order, 3000 rows per stock-session pair read
back), and `tests/synthetic` checks the column names and Polars types.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import polars as pl

from tbo_technical_engine.config import synthetic as cfg
from tbo_technical_engine.schemas.synthetic import SyntheticSpec, isin_of
from tbo_technical_engine.synthetic.generate import Minutes, session_open_utc, stock_sessions

TICK_SCHEMA: dict[str, pl.DataType | type[pl.DataType]] = {
    "isin": pl.String,
    "price": pl.String,
    "cum_volume": pl.Int64,
    "exchange_at": pl.Datetime("us", "UTC"),
    "oi": pl.Int64,
    "day_open": pl.String,
    "day_high": pl.String,
    "day_low": pl.String,
}


def session_ticks(isin: str, session: Minutes) -> pl.DataFrame:
    """The ticks of one stock-session, in time order (so the feed's candle builder sees the order it expects)."""
    n = cfg.SESSION_MINUTES
    # Within a minute: open, then the low, then the high, then the close. A bar whose open or close is its extreme just
    # repeats that price, which changes nothing.
    prices = np.stack([session.open, session.low, session.high, session.close], axis=1).reshape(-1)
    start = session_open_utc(session.day)
    offsets = np.tile(np.array([0, 15, 30, 45]), n) + np.repeat(np.arange(n) * 60, cfg.TICKS_PER_MINUTE)
    times = [start + timedelta(seconds=int(s)) for s in offsets]
    per_tick_volume = np.zeros(n * cfg.TICKS_PER_MINUTE, dtype=np.int64)
    per_tick_volume[cfg.TICKS_PER_MINUTE - 1 :: cfg.TICKS_PER_MINUTE] = session.volume  # all of it on the last tick
    cumulative = np.cumsum(per_tick_volume)
    day_high = np.maximum.accumulate(prices)
    day_low = np.minimum.accumulate(prices)
    count = prices.shape[0]
    return pl.DataFrame(
        {
            "isin": [isin] * count,
            "price": [f"{p:.2f}" for p in prices],
            "cum_volume": cumulative,
            "exchange_at": times,
            "oi": [None] * count,
            "day_open": [f"{session.open[0]:.2f}"] * count,
            "day_high": [f"{p:.2f}" for p in day_high],
            "day_low": [f"{p:.2f}" for p in day_low],
        },
        schema=TICK_SCHEMA,
    )


def write_ticks(spec: SyntheticSpec, directory: Path) -> list[Path]:
    """One part file per session day holding every stock's ticks, named like the feed's recorder names them."""
    per_day: dict[datetime, list[pl.DataFrame]] = {}
    for index in range(spec.stocks):
        isin = isin_of(index)
        for session in stock_sessions(spec, index):
            per_day.setdefault(session_open_utc(session.day), []).append(session_ticks(isin, session))
    directory.mkdir(parents=True, exist_ok=True)
    written = []
    for opened in sorted(per_day):
        frame = pl.concat(per_day[opened]).sort("exchange_at", maintain_order=True)
        day = opened.date()  # 03:45 UTC is 09:15 IST: the same calendar date
        final = directory / f"ticks-{day}-0001.parquet"
        tmp = final.with_suffix(".tmp")
        frame.write_parquet(tmp, compression="zstd")
        tmp.replace(final)
        written.append(final)
    return written
