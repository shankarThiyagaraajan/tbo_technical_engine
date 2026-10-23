"""ADX with +DI and -DI (Wilder), implemented the way TA-Lib does so the oracle test can demand 1e-6.

With n = period: bars 1..n-1 only accumulate directional movement and true range; from bar n those sums are
smoothed (`s = s - s / n + x`) and +DI, -DI and DX exist; the first ADX is the mean of the first n DX values (at bar
2n-1) and later ones are `(adx * (n - 1) + dx) / n`.
"""

from __future__ import annotations

from tbo_technical_engine.indicators.bars import F64, Bar, Bars
from tbo_technical_engine.indicators.base import NAN, Indicator, Stream, full
from tbo_technical_engine.indicators.volatility import true_range


class _AdxState:
    """Wilder's smoothing for +DM, -DM and TR, then for DX; shared by batch and stream so they cannot disagree."""

    def __init__(self, period: int) -> None:
        self.n = period
        self.count = 0  # bars seen
        self.prev_high = self.prev_low = self.prev_close = NAN
        self.plus_dm = self.minus_dm = self.tr = 0.0
        self.dx_sum = 0.0
        self.dx_count = 0
        self.adx = NAN

    def step(self, high: float, low: float, close: float) -> tuple[float, float, float]:
        """Consume one bar; returns (adx, plus_di, minus_di), nan while warming up."""
        plus_di = minus_di = NAN
        if self.count > 0:
            up, down = high - self.prev_high, self.prev_low - low
            plus = up if (up > 0 and up > down) else 0.0
            minus = down if (down > 0 and up < down) else 0.0
            tr = true_range(high, low, self.prev_close)
            if self.count < self.n:  # bars 1..n-1: plain sums
                self.plus_dm += plus
                self.minus_dm += minus
                self.tr += tr
            else:  # bar n onwards: Wilder smoothing
                plus_di, minus_di = self._smoothed(plus, minus, tr)
                self._add_dx(plus_di, minus_di)
        self.count += 1
        self.prev_high, self.prev_low, self.prev_close = high, low, close
        return self.adx, plus_di, minus_di

    def _smoothed(self, plus: float, minus: float, tr: float) -> tuple[float, float]:
        """Wilder-smooth the directional movements and the true range; returns (+DI, -DI)."""
        n = self.n
        self.plus_dm = self.plus_dm - self.plus_dm / n + plus
        self.minus_dm = self.minus_dm - self.minus_dm / n + minus
        self.tr = self.tr - self.tr / n + tr
        if self.tr == 0:
            return 0.0, 0.0
        return 100.0 * (self.plus_dm / self.tr), 100.0 * (self.minus_dm / self.tr)

    def _add_dx(self, plus_di: float, minus_di: float) -> None:
        """The bar's DX: the first n are averaged into the first ADX, later ones Wilder-smoothed into it."""
        n = self.n
        total = plus_di + minus_di
        dx = 100.0 * (abs(minus_di - plus_di) / total) if total != 0 else 0.0
        if self.dx_count < n:
            self.dx_sum += dx
            self.dx_count += 1
            if self.dx_count == n:
                self.adx = self.dx_sum / n
        else:
            self.adx = (self.adx * (n - 1) + dx) / n


class Adx(Indicator):
    """ADX with +DI and -DI (outputs `adx_N`, `plus_di_N`, `minus_di_N`); matches TA-Lib."""

    def __init__(self, period: int = 14) -> None:
        if period < 2:
            raise ValueError("period must be at least 2")
        self.period = period
        self.adx = f"adx_{period}"
        self.plus = f"plus_di_{period}"
        self.minus = f"minus_di_{period}"

    @property
    def outputs(self) -> tuple[str, ...]:
        return (self.adx, self.plus, self.minus)

    @property
    def lookback(self) -> int:
        return 2 * self.period - 1

    def batch(self, bars: Bars) -> dict[str, F64]:
        adx, plus, minus = full(len(bars)), full(len(bars)), full(len(bars))
        state = _AdxState(self.period)
        for i in range(len(bars)):
            adx[i], plus[i], minus[i] = state.step(float(bars.high[i]), float(bars.low[i]), float(bars.close[i]))
        return {self.adx: adx, self.plus: plus, self.minus: minus}

    def stream(self) -> Stream:
        return _AdxStream(self)


class _AdxStream:
    """Live form of Adx: feeds the shared state one bar at a time."""

    def __init__(self, spec: Adx) -> None:
        self.spec = spec
        self.state = _AdxState(spec.period)

    def update(self, bar: Bar) -> dict[str, float]:
        adx, plus, minus = self.state.step(bar.high, bar.low, bar.close)
        return {self.spec.adx: adx, self.spec.plus: plus, self.spec.minus: minus}
