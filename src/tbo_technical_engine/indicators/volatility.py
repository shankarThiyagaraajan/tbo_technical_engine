"""True range, ATR (Wilder, as TA-Lib) and Supertrend.

ATR(n): the first value, at bar index n, is the mean of the true ranges of bars 1..n (bar 0 has no previous
close); later ones are `(prev * (n - 1) + tr) / n`.

Supertrend(period, multiplier) is built on that ATR, with the standard final-band rule: a band only tightens
toward the price while the previous close stayed on its side of it. `direction` is +1 (green, price above the
line) or -1 (red). The first direction is +1 by convention; it corrects itself within a few bars, long before the
warm-up completes.
"""

from __future__ import annotations

import math

from tbo_technical_engine.indicators.bars import F64, Bar, Bars
from tbo_technical_engine.indicators.base import NAN, Indicator, Stream, full


def true_range(high: float, low: float, prev_close: float) -> float:
    """The largest of the bar's own range and its gaps from the previous close (a gap-up still counts as range)."""
    return max(high - low, abs(high - prev_close), abs(low - prev_close))


def true_range_series(bars: Bars) -> F64:
    """True range of every bar; the first bar has no previous close, so it is nan."""
    out = full(len(bars))
    for i in range(1, len(bars)):
        out[i] = true_range(float(bars.high[i]), float(bars.low[i]), float(bars.close[i - 1]))
    return out


def _num(value: float) -> str:
    """A parameter as it appears in an output name: `3.0` -> `3`, `1.5` -> `1.5` (`supertrend_10_3`)."""
    return f"{value:g}"


class Atr(Indicator):
    """Average true range, Wilder-smoothed and seeded with the mean of the first `period` true ranges (TA-Lib)."""

    def __init__(self, period: int = 14) -> None:
        if period < 2:
            raise ValueError("period must be at least 2")
        self.period = period
        self.name = f"atr_{period}"

    @property
    def outputs(self) -> tuple[str, ...]:
        return (self.name,)

    @property
    def lookback(self) -> int:
        return self.period

    def batch(self, bars: Bars) -> dict[str, F64]:
        n = self.period
        out = full(len(bars))
        if len(bars) <= n:
            return {self.name: out}
        tr = true_range_series(bars)
        value = float(sum(tr[1 : n + 1]) / n)
        out[n] = value
        for i in range(n + 1, len(bars)):
            value = (value * (n - 1) + tr[i]) / n
            out[i] = value
        return {self.name: out}

    def stream(self) -> Stream:
        return _AtrStream(self)


class _AtrStream:
    """Live form of Atr."""

    def __init__(self, spec: Atr) -> None:
        self.spec = spec
        self.prev_close = NAN
        self.seed: list[float] = []
        self.value = NAN

    def update(self, bar: Bar) -> dict[str, float]:
        n = self.spec.period
        if not math.isnan(self.prev_close):
            tr = true_range(bar.high, bar.low, self.prev_close)
            if len(self.seed) < n:
                self.seed.append(tr)
                if len(self.seed) == n:
                    self.value = float(sum(self.seed) / n)
            else:
                self.value = (self.value * (n - 1) + tr) / n
        self.prev_close = bar.close
        return {self.spec.name: self.value}


class Supertrend(Indicator):
    """ATR bands that flip direction when the close crosses the opposite band; outputs the line and the direction."""

    def __init__(self, period: int = 10, multiplier: float = 3.0) -> None:
        if period < 2 or multiplier <= 0:
            raise ValueError("period must be at least 2 and multiplier positive")
        self.period = period
        self.multiplier = multiplier
        suffix = f"{period}_{_num(multiplier).replace('.', 'p')}"
        self.line = f"supertrend_{suffix}"
        self.direction = f"supertrend_dir_{suffix}"
        self._atr = Atr(period)

    @property
    def outputs(self) -> tuple[str, ...]:
        return (self.line, self.direction)

    @property
    def lookback(self) -> int:
        return self.period

    def batch(self, bars: Bars) -> dict[str, F64]:
        atr = self._atr.batch(bars)[self._atr.name]
        line, direction = full(len(bars)), full(len(bars))
        state = _SupertrendState(self.multiplier)
        for i in range(len(bars)):
            if math.isnan(atr[i]):
                continue
            line[i], direction[i] = state.step(
                float(bars.high[i]), float(bars.low[i]), float(bars.close[i]), float(atr[i])
            )
        return {self.line: line, self.direction: direction}

    def stream(self) -> Stream:
        return _SupertrendStream(self)


class _SupertrendState:
    """One step of the band recursion, shared by batch and stream so the two cannot disagree on the rule."""

    def __init__(self, multiplier: float) -> None:
        self.m = multiplier
        self.started = False
        self.upper = NAN
        self.lower = NAN
        self.prev_close = NAN
        self.direction = 1.0

    def step(self, high: float, low: float, close: float, atr: float) -> tuple[float, float]:
        mid = (high + low) / 2
        basic_upper, basic_lower = mid + self.m * atr, mid - self.m * atr
        if not self.started:
            self.upper, self.lower, self.direction, self.started = basic_upper, basic_lower, 1.0, True
        else:
            upper = basic_upper if basic_upper < self.upper or self.prev_close > self.upper else self.upper
            lower = basic_lower if basic_lower > self.lower or self.prev_close < self.lower else self.lower
            if self.direction < 0:
                self.direction = 1.0 if close > upper else -1.0
            else:
                self.direction = -1.0 if close < lower else 1.0
            self.upper, self.lower = upper, lower
        self.prev_close = close
        return (self.lower if self.direction > 0 else self.upper), self.direction


class _SupertrendStream:
    """Live form of Supertrend: an ATR stream feeding the shared band state."""

    def __init__(self, spec: Supertrend) -> None:
        self.spec = spec
        self.atr = spec._atr.stream()
        self.state = _SupertrendState(spec.multiplier)

    def update(self, bar: Bar) -> dict[str, float]:
        atr = self.atr.update(bar)[self.spec._atr.name]
        if math.isnan(atr):
            return {self.spec.line: NAN, self.spec.direction: NAN}
        line, direction = self.state.step(bar.high, bar.low, bar.close, atr)
        return {self.spec.line: line, self.spec.direction: direction}
