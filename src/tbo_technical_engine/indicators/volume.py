"""Volume against its recent average.

`vol_avg_N` is the mean volume of the N bars *before* the current one, and `vol_ratio_N` is the current bar's volume
divided by it. The current bar is left out so a spike cannot lift its own yardstick ("volume >= 1.5x the 20-bar
average" means 1.5x what was normal just before). The window runs across sessions without a reset: the first bars of a
day are compared with the last bars of the day before. Volumes are whole numbers, so the sums are exact.
"""

from __future__ import annotations

from collections import deque

import numpy as np

from tbo_technical_engine.indicators.bars import F64, Bar, Bars
from tbo_technical_engine.indicators.base import NAN, Indicator, Stream, full


class VolumeAverage(Indicator):
    def __init__(self, period: int = 20) -> None:
        if period < 2:
            raise ValueError("period must be at least 2")
        self.period = period
        self.avg = f"vol_avg_{period}"
        self.ratio = f"vol_ratio_{period}"

    @property
    def outputs(self) -> tuple[str, ...]:
        return (self.avg, self.ratio)

    @property
    def lookback(self) -> int:
        return self.period

    def batch(self, bars: Bars) -> dict[str, F64]:
        n, p = len(bars), self.period
        avg, ratio = full(n), full(n)
        if n > p:
            windows = np.lib.stride_tricks.sliding_window_view(bars.volume, p).sum(axis=1)  # window ending at j
            avg[p:] = windows[: n - p] / p
            with np.errstate(divide="ignore", invalid="ignore"):
                ratio[p:] = np.where(avg[p:] > 0, bars.volume[p:] / avg[p:], np.nan)
        return {self.avg: avg, self.ratio: ratio}

    def stream(self) -> Stream:
        return _VolStream(self)


class _VolStream:
    def __init__(self, spec: VolumeAverage) -> None:
        self.spec = spec
        self.window: deque[float] = deque(maxlen=spec.period)

    def update(self, bar: Bar) -> dict[str, float]:
        avg = ratio = NAN
        if len(self.window) == self.spec.period:
            avg = sum(self.window) / self.spec.period
            ratio = bar.volume / avg if avg > 0 else NAN
        self.window.append(bar.volume)
        return {self.spec.avg: avg, self.spec.ratio: ratio}
