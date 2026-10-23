"""One stock, one session: every number of its snapshot except the universe-wide rank and the traded value.

`measure` is pure (daily bars in, a `Measured` out), so the same stock gives the same answer in the daily job, in a
test and in a backtest. The universe rank needs every stock, so the service ranks the `Measured` of all of them and
then calls `snapshot`.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

import numpy as np
from numpy.typing import NDArray

from tbo_technical_engine.indicators.bars import Bars
from tbo_technical_engine.schemas.swing import Level, Snapshot, SwingParams
from tbo_technical_engine.swing import levels, liquidity, setups, trend, volatility
from tbo_technical_engine.swing.levels import Zone


def decimal(value: float, places: int = 2) -> str:
    """A float as the contract's Decimal text, rounded half up (`6.405` -> `6.41`, as a person would)."""
    step = Decimal(1).scaleb(-places)
    return format(Decimal(repr(value)).quantize(step, rounding=ROUND_HALF_UP), "f")


@dataclass(frozen=True)
class Measured:
    """Everything measured on one stock's bars."""

    isin: str
    as_of: date
    close: float
    atr: float
    trend: trend.Trend
    return_20d: float
    return_60d: float
    return_12m: float | None
    zones: list[tuple[Literal["support", "resistance"], Zone]]
    volatility: volatility.Volatility
    spread_pct: float | None


RETURN_12M_SESSIONS = 250
"""Sessions back for the 12-month return: the close this many sessions before the last one (about a year)."""


def return_12m(close: Sequence[float] | NDArray[np.float64]) -> float | None:
    """Percent change of the last close against the close 250 sessions earlier; None below 251 closes, never 0."""
    if len(close) <= RETURN_12M_SESSIONS:
        return None
    base = float(close[-1 - RETURN_12M_SESSIONS])
    if not (math.isfinite(base) and base > 0):
        return None
    return (float(close[-1]) / base - 1.0) * 100.0


def measure(isin: str, bars: Bars, p: SwingParams) -> Measured | None:
    """None when the stock has fewer than `min_bars` sessions or its last bar has no usable ATR."""
    if len(bars) < p.min_bars:
        return None
    as_of = date.fromordinal(int(bars.session[-1]))
    vol = volatility.assess(bars, p.volatility)
    if not (math.isfinite(vol.atr) and vol.atr > 0):
        return None
    close = bars.close
    return Measured(
        isin=isin,
        as_of=as_of,
        close=float(close[-1]),
        atr=vol.atr,
        trend=trend.assess(bars, p.trend),
        return_20d=float(close[-1] / close[-21] - 1.0) * 100.0,
        return_60d=float(close[-1] / close[-61] - 1.0) * 100.0,
        return_12m=return_12m(close),
        zones=list(levels.build(bars, vol.atr, as_of, p.levels)),
        volatility=vol,
        spread_pct=liquidity.spread_pct(bars.high, bars.low, p.liquidity.spread_days, p.liquidity.spread_min_pairs),
    )


def snapshot(m: Measured, rs_percentile: int, avg_turnover: float, computed_at: str) -> Snapshot:
    """Build the snapshot record from the measurements."""
    return Snapshot(
        isin=m.isin,
        as_of_date=m.as_of.isoformat(),
        trend_state=m.trend.state,
        trend_score=m.trend.score,
        rs_percentile=rs_percentile,
        return_20d_pct=decimal(m.return_20d),
        return_60d_pct=decimal(m.return_60d),
        return_12m_pct=None if m.return_12m is None else decimal(m.return_12m),
        levels=[Level(kind=kind, low=decimal(z.low), high=decimal(z.high), source=z.source) for kind, z in m.zones],
        atr_pct=decimal(m.volatility.atr_pct),
        atr_pct_percentile=m.volatility.percentile,
        vol_regime=m.volatility.regime,
        is_squeeze=m.volatility.is_squeeze,
        avg_turnover_20d=decimal(avg_turnover),
        spread_est_pct=None if m.spread_pct is None else decimal(m.spread_pct, 3),
        computed_at=computed_at,
    )


def find_setup(m: Measured, bars: Bars, p: SwingParams) -> setups.Draft | None:
    """The first setup the detectors find, in precedence order, or None."""
    emas = {p.trend.fast: m.trend.ema_fast, p.trend.mid: m.trend.ema_mid}
    return setups.detect(bars, m.atr, m.trend, emas, p.setups)
