"""RSI (Wilder), as TA-Lib: the first average gain and loss are plain means of the first n changes (so the first
value is at bar n); later ones are `(prev * (n - 1) + x) / n`.
"""

from __future__ import annotations

import math

from tbo_technical_engine.indicators.bars import F64, Bar, Bars
from tbo_technical_engine.indicators.base import NAN, Indicator, Stream, full


def _rsi(avg_gain: float, avg_loss: float) -> float:
    total = avg_gain + avg_loss
    return 100.0 * (avg_gain / total) if total != 0 else 0.0


class Rsi(Indicator):
    def __init__(self, period: int = 14) -> None:
        if period < 2:
            raise ValueError("period must be at least 2")
        self.period = period
        self.name = f"rsi_{period}"

    @property
    def outputs(self) -> tuple[str, ...]:
        return (self.name,)

    @property
    def lookback(self) -> int:
        return self.period

    def batch(self, bars: Bars) -> dict[str, F64]:
        n, x = self.period, bars.close
        out = full(len(bars))
        if len(x) <= n:
            return {self.name: out}
        gains = [max(float(x[i] - x[i - 1]), 0.0) for i in range(1, len(x))]
        losses = [max(float(x[i - 1] - x[i]), 0.0) for i in range(1, len(x))]
        gain, loss = sum(gains[:n]) / n, sum(losses[:n]) / n
        out[n] = _rsi(gain, loss)
        for i in range(n, len(gains)):
            gain = (gain * (n - 1) + gains[i]) / n
            loss = (loss * (n - 1) + losses[i]) / n
            out[i + 1] = _rsi(gain, loss)
        return {self.name: out}

    def stream(self) -> Stream:
        return _RsiStream(self)


class _RsiStream:
    def __init__(self, spec: Rsi) -> None:
        self.spec = spec
        self.prev = NAN
        self.gains: list[float] = []
        self.losses: list[float] = []
        self.gain = self.loss = NAN
        self.value = NAN

    def update(self, bar: Bar) -> dict[str, float]:
        n = self.spec.period
        if not math.isnan(self.prev):
            change = bar.close - self.prev
            g, l_ = max(change, 0.0), max(-change, 0.0)
            if len(self.gains) < n:
                self.gains.append(g)
                self.losses.append(l_)
                if len(self.gains) == n:
                    self.gain, self.loss = sum(self.gains) / n, sum(self.losses) / n
                    self.value = _rsi(self.gain, self.loss)
            else:
                self.gain = (self.gain * (n - 1) + g) / n
                self.loss = (self.loss * (n - 1) + l_) / n
                self.value = _rsi(self.gain, self.loss)
        self.prev = bar.close
        return {self.spec.name: self.value}
