"""SMA and EMA, defined as TA-Lib defines them (so the oracle test is exact): the first EMA value is the SMA of the
first `period` bars, and every later one is `prev + k * (x - prev)` with `k = 2 / (period + 1)`.
"""

from __future__ import annotations

from collections import deque
from typing import Literal

import numpy as np

from tbo_technical_engine.indicators.bars import F64, Bar, Bars
from tbo_technical_engine.indicators.base import NAN, Indicator, Stream, full

Source = Literal["close", "open", "high", "low", "volume"]


def _x(bars: Bars, source: Source) -> F64:
    return getattr(bars, source)  # type: ignore[no-any-return]


def _check(period: int) -> int:
    if period < 2:
        raise ValueError("period must be at least 2")
    return period


class Sma(Indicator):
    def __init__(self, period: int, source: Source = "close") -> None:
        self.period = _check(period)
        self.source: Source = source
        self.name = f"sma_{period}" if source == "close" else f"sma_{source}_{period}"

    @property
    def outputs(self) -> tuple[str, ...]:
        return (self.name,)

    @property
    def lookback(self) -> int:
        return self.period - 1

    def batch(self, bars: Bars) -> dict[str, F64]:
        x, n = _x(bars, self.source), self.period
        out = full(len(bars))
        if len(x) >= n:
            windows = np.lib.stride_tricks.sliding_window_view(x, n)
            out[n - 1 :] = windows.sum(axis=1) / n
        return {self.name: out}

    def stream(self) -> Stream:
        return _SmaStream(self)


class _SmaStream:
    def __init__(self, spec: Sma) -> None:
        self.spec = spec
        self.window: deque[float] = deque(maxlen=spec.period)

    def update(self, bar: Bar) -> dict[str, float]:
        self.window.append(getattr(bar, self.spec.source))
        full_ = len(self.window) == self.spec.period
        return {self.spec.name: sum(self.window) / self.spec.period if full_ else NAN}


class Ema(Indicator):
    def __init__(self, period: int, source: Source = "close") -> None:
        self.period = _check(period)
        self.source: Source = source
        self.name = f"ema_{period}" if source == "close" else f"ema_{source}_{period}"

    @property
    def outputs(self) -> tuple[str, ...]:
        return (self.name,)

    @property
    def lookback(self) -> int:
        return self.period - 1

    def batch(self, bars: Bars) -> dict[str, F64]:
        x, n = _x(bars, self.source), self.period
        out = full(len(bars))
        if len(x) < n:
            return {self.name: out}
        k = 2.0 / (n + 1)
        value = float(sum(x[:n]) / n)
        out[n - 1] = value
        for i in range(n, len(x)):
            value += k * (x[i] - value)
            out[i] = value
        return {self.name: out}

    def stream(self) -> Stream:
        return _EmaStream(self)


class _EmaStream:
    def __init__(self, spec: Ema) -> None:
        self.spec = spec
        self.k = 2.0 / (spec.period + 1)
        self.seed: list[float] = []
        self.value = NAN

    def update(self, bar: Bar) -> dict[str, float]:
        x = getattr(bar, self.spec.source)
        if len(self.seed) < self.spec.period:
            self.seed.append(x)
            if len(self.seed) == self.spec.period:
                self.value = float(sum(self.seed) / self.spec.period)
        else:
            self.value += self.k * (x - self.value)
        return {self.spec.name: self.value}
