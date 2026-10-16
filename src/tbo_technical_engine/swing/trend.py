"""Trend state and score from the alignment of the moving averages and the directional movement.

`bull` is a 0-100 alignment score: the share of the available weight whose bullish condition holds. The state is
`up` at or above `up_at`, `down` at or below `down_at`, else `sideways`. The contract's `trend_score` is the strength
of the call, `|bull - 50| * 2`: 100 means every condition points the same way, 0 means they split evenly. So an
`up` of 71 and a `down` of 71 are equally convincing, and a `sideways` is always a low number.

Averages alone call a range-bound market "up" or "down" half the time, because they are always ordered one way or the
other. So ADX gates the state: below `adx_min` there is no trend and the answer is `sideways`, and below `adx_full`
the score is scaled down in proportion. Without an ADX yet (a young stock) the averages stand alone.

A condition that cannot be evaluated yet (the 200 EMA needs 200 sessions, ADX about 28) drops out and the others
are rescaled: a young stock is not called bearish for being young.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

from tbo_technical_engine import indicators
from tbo_technical_engine.indicators.bars import Bars
from tbo_technical_engine.schemas.swing import TrendParams

TrendState = Literal["up", "down", "sideways"]


@dataclass(frozen=True)
class Trend:
    """Trend state and score for one stock."""

    state: TrendState
    score: int
    bull: float
    ema_fast: float
    ema_mid: float
    ema_slow: float  # nan before 200 sessions
    mid_series_slope_up: bool


def assess(bars: Bars, p: TrendParams) -> Trend:
    """Score the trend from EMAs, slope and ADX."""
    close = float(bars.close[-1])
    fast = float(indicators.get("ema", period=p.fast).batch(bars)[f"ema_{p.fast}"][-1])
    mid_series = indicators.get("ema", period=p.mid).batch(bars)[f"ema_{p.mid}"]
    mid = float(mid_series[-1])
    slow = float(indicators.get("ema", period=p.slow).batch(bars)[f"ema_{p.slow}"][-1])
    adx = indicators.get("adx", period=p.adx_period).batch(bars)
    plus, minus = float(adx[f"plus_di_{p.adx_period}"][-1]), float(adx[f"minus_di_{p.adx_period}"][-1])
    earlier = float(mid_series[-1 - p.slope_bars]) if len(bars) > p.slope_bars else math.nan
    rising = mid > earlier if math.isfinite(earlier) else None

    w = p.weights
    checks: list[tuple[float, bool | None]] = [
        (w.close_above_fast, close > fast if math.isfinite(fast) else None),
        (w.close_above_mid, close > mid if math.isfinite(mid) else None),
        (w.fast_above_mid, fast > mid if math.isfinite(fast) and math.isfinite(mid) else None),
        (w.mid_above_slow, mid > slow if math.isfinite(mid) and math.isfinite(slow) else None),
        (w.mid_rising, rising if math.isfinite(mid) else None),
        (w.di_bullish, plus > minus if math.isfinite(plus) and math.isfinite(minus) else None),
    ]
    available = sum(weight for weight, ok in checks if ok is not None)
    earned = sum(weight for weight, ok in checks if ok)
    bull = 100.0 * earned / available if available > 0 else 50.0
    adx_now = float(adx[f"adx_{p.adx_period}"][-1])
    trending = not math.isfinite(adx_now) or adx_now >= p.adx_min
    state: TrendState = "sideways"
    if trending and bull >= p.up_at:
        state = "up"
    elif trending and bull <= p.down_at:
        state = "down"
    weight = min(1.0, adx_now / p.adx_full) if math.isfinite(adx_now) else 1.0
    score = min(100, max(0, round(abs(bull - 50.0) * 2.0 * weight)))
    return Trend(state, score, bull, fast, mid, slow, bool(rising))
