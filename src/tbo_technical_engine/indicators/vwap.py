"""Session VWAP with volume-weighted standard-deviation bands.

VWAP resets at every session. Per bar the typical price is `(high + low + close) / 3`, weighted by volume:

    vwap  = sum(v * tp) / sum(v)
    sigma = sqrt(max(0, sum(v * tp^2) / sum(v) - vwap^2))
    upper_k = vwap + k * sigma,   lower_k = vwap - k * sigma

The bands use the same cumulative sums as VWAP itself, so they cost nothing extra. Both forms add the bars in the same
order, so batch and streaming agree to the last bit. A session with no volume yet has no VWAP (nan).
"""

from __future__ import annotations

import math

import numpy as np

from tbo_technical_engine.indicators.bars import F64, Bar, Bars
from tbo_technical_engine.indicators.base import NAN, Indicator, Stream, full


def _k(multiplier: float) -> str:
    """A band multiplier as it appears in an output name: `1.5` -> `1p5` (a dot would not survive as a field name)."""
    return f"{multiplier:g}".replace(".", "p")


class Vwap(Indicator):
    """Session VWAP (resets each IST trading day) with volume-weighted standard-deviation bands."""

    def __init__(self, band_multipliers: tuple[float, ...] = (1.0, 2.0)) -> None:
        self.multipliers = band_multipliers
        self.vwap = "vwap"
        self.upper = {m: f"vwap_upper_{_k(m)}" for m in band_multipliers}
        self.lower = {m: f"vwap_lower_{_k(m)}" for m in band_multipliers}

    @property
    def outputs(self) -> tuple[str, ...]:
        return (self.vwap, *self.upper.values(), *self.lower.values())

    @property
    def lookback(self) -> int:
        return 0

    def batch(self, bars: Bars) -> dict[str, F64]:
        n = len(bars)
        out = {name: full(n) for name in self.outputs}
        if n == 0:
            return out
        tp = (bars.high + bars.low + bars.close) / 3.0
        v = bars.volume
        starts = np.flatnonzero(np.r_[True, bars.session[1:] != bars.session[:-1]])
        ends = np.r_[starts[1:], n]
        for a, b in zip(starts, ends, strict=True):
            cum_v = np.cumsum(v[a:b])
            cum_pv = np.cumsum(v[a:b] * tp[a:b])
            cum_pv2 = np.cumsum(v[a:b] * tp[a:b] * tp[a:b])
            with np.errstate(divide="ignore", invalid="ignore"):
                vwap = np.where(cum_v > 0, cum_pv / cum_v, np.nan)
                var = np.where(cum_v > 0, cum_pv2 / cum_v - vwap * vwap, np.nan)
            sigma = np.sqrt(np.maximum(var, 0.0))
            out[self.vwap][a:b] = vwap
            for m in self.multipliers:
                out[self.upper[m]][a:b] = vwap + m * sigma
                out[self.lower[m]][a:b] = vwap - m * sigma
        return out

    def stream(self) -> Stream:
        return _VwapStream(self)


class _VwapStream:
    """Live form of Vwap: running sums reset when the session changes."""

    def __init__(self, spec: Vwap) -> None:
        self.spec = spec
        self.session = -1
        self.cum_v = self.cum_pv = self.cum_pv2 = 0.0

    def update(self, bar: Bar) -> dict[str, float]:
        if bar.session != self.session:
            self.session = bar.session
            self.cum_v = self.cum_pv = self.cum_pv2 = 0.0
        tp = (bar.high + bar.low + bar.close) / 3.0
        self.cum_v += bar.volume
        self.cum_pv += bar.volume * tp
        self.cum_pv2 += bar.volume * tp * tp
        out = {name: NAN for name in self.spec.outputs}
        if self.cum_v > 0:
            vwap = self.cum_pv / self.cum_v
            sigma = math.sqrt(max(self.cum_pv2 / self.cum_v - vwap * vwap, 0.0))
            out[self.spec.vwap] = vwap
            for m in self.spec.multipliers:
                out[self.spec.upper[m]] = vwap + m * sigma
                out[self.spec.lower[m]] = vwap - m * sigma
        return out
