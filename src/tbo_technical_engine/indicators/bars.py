"""The input of every indicator: bars as float64 arrays (batch) or one scalar `Bar` (streaming).

Prices are float64 here (the feed's two-decimal prices are exact enough, and every number a candidate carries is
rounded back to two decimals at the edge). Times are kept in three forms so no indicator parses a timestamp:
`ts` (epoch seconds of the candle start), `session` (the IST trading date as a date ordinal) and `tod` (minutes
after midnight IST of the candle start, so 09:15 is 555).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import NamedTuple

import numpy as np
from numpy.typing import NDArray
from tbo_platform_kit.calendar import IST

from tbo_technical_engine.schemas.candle import Candle

F64 = NDArray[np.float64]
I64 = NDArray[np.int64]
SESSION_OPEN_TOD = 9 * 60 + 15


class Bar(NamedTuple):
    """One closed candle as scalars, the unit the streaming indicators consume."""

    ts: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    session: int
    tod: int


def session_and_tod(start: datetime) -> tuple[int, int]:
    """The IST trading date (as an ordinal) and minutes after midnight IST of a candle start: `04:00Z` is
    09:30 IST, so `(date ordinal, 570)`. Session logic (VWAP reset, opening range) works on these, never on
    timestamps."""
    local = start.astimezone(IST)
    return local.date().toordinal(), local.hour * 60 + local.minute


def bar_of(candle: Candle) -> Bar:
    """A `Candle` (Decimal prices) as a float `Bar` with its session and time of day."""
    session, tod = session_and_tod(candle.start_at)
    return Bar(
        ts=int(candle.start_at.timestamp()),
        open=float(candle.open),
        high=float(candle.high),
        low=float(candle.low),
        close=float(candle.close),
        volume=float(candle.volume),
        session=session,
        tod=tod,
    )


@dataclass(frozen=True)
class Bars:
    """The same candles as parallel NumPy arrays: the unit the batch indicators consume."""

    ts: I64
    open: F64
    high: F64
    low: F64
    close: F64
    volume: F64
    session: I64
    tod: I64

    def __len__(self) -> int:
        return int(self.ts.shape[0])

    @classmethod
    def from_candles(cls, candles: Sequence[Candle]) -> Bars:
        """Oldest first; the caller guarantees one timeframe of one stock."""
        bars = [bar_of(c) for c in candles]
        return cls.from_bars(bars)

    @classmethod
    def from_bars(cls, bars: Sequence[Bar]) -> Bars:
        """Stack scalar bars into arrays (the inverse of `row`)."""
        return cls(
            ts=np.array([b.ts for b in bars], dtype=np.int64),
            open=np.array([b.open for b in bars], dtype=np.float64),
            high=np.array([b.high for b in bars], dtype=np.float64),
            low=np.array([b.low for b in bars], dtype=np.float64),
            close=np.array([b.close for b in bars], dtype=np.float64),
            volume=np.array([b.volume for b in bars], dtype=np.float64),
            session=np.array([b.session for b in bars], dtype=np.int64),
            tod=np.array([b.tod for b in bars], dtype=np.int64),
        )

    def row(self, i: int) -> Bar:
        """Bar `i` as a scalar `Bar`, for feeding a streaming indicator."""
        return Bar(
            int(self.ts[i]),
            float(self.open[i]),
            float(self.high[i]),
            float(self.low[i]),
            float(self.close[i]),
            float(self.volume[i]),
            int(self.session[i]),
            int(self.tod[i]),
        )
