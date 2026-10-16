"""Relative strength against a benchmark: the stock's N-bar return minus the benchmark's, in percentage points.

`rel_strength_N = (close / close[N bars ago] - 1) * 100 - (bench / bench[N bars ago] - 1) * 100`

Needs a second series (the benchmark's closes on the same bars), so it is not part of the single-series facade
`indicators.get`. The universe-wide percentile rank is a daily-snapshot job (TE6), not computed here.
"""

from __future__ import annotations

from collections import deque

from tbo_technical_engine.indicators.bars import F64
from tbo_technical_engine.indicators.base import NAN, full


class RelativeStrength:
    """Stock return minus benchmark return over `period` bars, in percentage points."""

    def __init__(self, period: int = 20) -> None:
        if period < 1:
            raise ValueError("period must be at least 1")
        self.period = period
        self.name = f"rel_strength_{period}"

    def batch(self, close: F64, benchmark: F64) -> F64:
        """The series for two aligned arrays of closes; ValueError when their lengths differ."""
        if close.shape != benchmark.shape:
            raise ValueError("the stock and the benchmark need the same bars")
        n, p = len(close), self.period
        out = full(n)
        for i in range(p, n):
            out[i] = (close[i] / close[i - p] - 1.0) * 100.0 - (benchmark[i] / benchmark[i - p] - 1.0) * 100.0
        return out

    def stream(self) -> RelativeStrengthStream:
        return RelativeStrengthStream(self)


class RelativeStrengthStream:
    """Live form: keeps the last period + 1 pairs of closes and compares the newest with the oldest."""

    def __init__(self, spec: RelativeStrength) -> None:
        self.spec = spec
        self.window: deque[tuple[float, float]] = deque(maxlen=spec.period + 1)

    def update(self, close: float, benchmark: float) -> float:
        self.window.append((close, benchmark))
        if len(self.window) <= self.spec.period:
            return NAN
        c0, b0 = self.window[0]
        return (close / c0 - 1.0) * 100.0 - (benchmark / b0 - 1.0) * 100.0
