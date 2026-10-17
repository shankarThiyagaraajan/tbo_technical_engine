"""What every indicator looks like, so strategies and the API use them all the same way.

An indicator has two forms that must agree on every bar (tests check it over a year):

- `batch(bars)` computes the whole series with NumPy; used by backtests and research.
- `stream()` returns a small state machine; `update(bar)` consumes one closed bar and returns that bar's values.
  Used by the live engine, which must not recompute a series on every candle.

Values are floats; `nan` means "not enough history yet" (the indicator is warming up). Output names carry the
parameters (`ema_9`, `atr_14`, `supertrend_10_3`) because that is how the API and the candidates name them.
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from typing import Protocol

import numpy as np

from tbo_technical_engine.indicators.bars import F64, Bar, Bars

NAN = math.nan


class Stream(Protocol):
    """The live form: a small state machine fed one closed bar at a time."""

    def update(self, bar: Bar) -> dict[str, float]:
        """Consume one closed bar and return that bar's value for every output (nan while warming up)."""
        ...


class Indicator(ABC):
    """The contract every indicator meets; `indicators.get(name, **params)` builds one."""

    @property
    @abstractmethod
    def outputs(self) -> tuple[str, ...]:
        """The names of the series this indicator produces."""

    @property
    @abstractmethod
    def lookback(self) -> int:
        """Bars needed before the first valid value (0 means valid from the first bar of a session or series)."""

    @abstractmethod
    def batch(self, bars: Bars) -> dict[str, F64]:
        """The whole series at once from NumPy arrays, one array per output name."""

    @abstractmethod
    def stream(self) -> Stream:
        """A fresh live state machine that gives the same values as `batch` on every bar."""


def full(n: int) -> F64:
    """An array of `n` nans: the starting point of a batch output that is filled from `lookback` on."""
    return np.full(n, np.nan, dtype=np.float64)
