"""ATR % and where it sits in its own last year, the volatility regime, and the Bollinger squeeze."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

import numpy as np

from tbo_technical_engine import indicators
from tbo_technical_engine.indicators.bars import F64, Bars
from tbo_technical_engine.schemas.swing import VolatilityParams

Regime = Literal["contracting", "normal", "expanding"]


@dataclass(frozen=True)
class Volatility:
    atr: float
    atr_pct: float
    percentile: int
    regime: Regime
    is_squeeze: bool


def percentile_of(latest: float, window: F64) -> int:
    """Where `latest` sits among `window` (which includes it), 0-100: ties count half, so the lowest of 100 distinct
    values is 0.5 -> 1 and the highest 99.5 -> 100 rather than both ends being unreachable."""
    below = float(np.sum(window < latest))
    equal = float(np.sum(window == latest))
    return min(100, max(0, round(100.0 * (below + equal / 2.0) / len(window))))


def bandwidth(close: F64, period: int, k: float) -> F64:
    """(upper - lower) / middle of the Bollinger bands, nan until `period` closes exist. Population deviation, as
    charting platforms plot it."""
    out = np.full(len(close), np.nan)
    if len(close) < period:
        return out
    windows = np.lib.stride_tricks.sliding_window_view(close, period)
    mean = windows.mean(axis=1)
    std = windows.std(axis=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        out[period - 1 :] = 2.0 * k * std / mean
    return out


def assess(bars: Bars, p: VolatilityParams) -> Volatility:
    atr_series = indicators.get("atr", period=p.atr_period).batch(bars)[f"atr_{p.atr_period}"]
    with np.errstate(divide="ignore", invalid="ignore"):
        pct_series = atr_series / bars.close * 100.0
    pct_series = pct_series[np.isfinite(pct_series)]
    atr = float(atr_series[-1])
    atr_pct = float(pct_series[-1]) if len(pct_series) else math.nan
    window = pct_series[-p.percentile_window :]
    percentile = percentile_of(atr_pct, window) if len(window) >= p.min_points else 50
    regime: Regime = "normal"
    if percentile <= p.contracting_below:
        regime = "contracting"
    elif percentile >= p.expanding_above:
        regime = "expanding"
    width = bandwidth(bars.close, p.bollinger_period, p.bollinger_k)
    width = width[np.isfinite(width)][-p.squeeze_window :]
    squeeze = bool(len(width) >= p.squeeze_min_points and width[-1] <= np.percentile(width, p.squeeze_percentile))
    return Volatility(atr, atr_pct, percentile, regime, squeeze)
