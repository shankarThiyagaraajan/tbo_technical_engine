"""The three swing setup detectors, long only: base breakout, pullback to the 20 or 50 EMA, 52-week high breakout.

Each looks at the last bar (the session just closed) and the sessions before it, and answers with a draft: entry,
stop and two targets as floats, or None. Prices are put on the 0.05 tick by `finish`, the safe way for a buyer: the
entry rounds up, the stop and the targets down, so rounding never makes a setup look better than it is.

Common rules:
  * a breakout is the FIRST close above the level (yesterday's close was not), so a stock that keeps climbing is
    reported once, not every day;
  * the stop is clamped to at most `risk_atr_max` ATR from the entry and the setup is dropped if it is closer than
    `risk_atr_min` ATR (a stop inside the noise);
  * targets are multiples of the risk (R) from the entry;
  * when more than one detector fires on a stock the first in `precedence` wins: one setup per stock per session.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal

import numpy as np

from tbo_technical_engine.indicators.bars import Bars
from tbo_technical_engine.schemas.swing import SetupParams, SetupType
from tbo_technical_engine.strategies.pricing import TICK, to_tick
from tbo_technical_engine.swing.trend import Trend

VOLUME_LOOKBACK = 20


@dataclass(frozen=True)
class Draft:
    """A setup before its ID and dates are set."""

    setup_type: SetupType
    entry: Decimal
    stop: Decimal
    target_1: Decimal
    target_2: Decimal
    facts: dict[str, float]
    """The numbers the rule used (base high, volume ratio, ...), for the review sheet and the logs. Not published."""


def finish(
    kind: SetupType,
    entry: float,
    stop: float,
    atr: float,
    t1_r: float,
    t2_r: float,
    p: SetupParams,
    facts: dict[str, float],
    *,
    entry_up_tick: bool = False,
) -> Draft | None:
    """Round the entry first, then derive stop and targets as exact R multiples of it."""
    entry_p = to_tick(entry, up=True) + (TICK if entry_up_tick else Decimal(0))
    entry = float(entry_p)  # the risk is measured from the price the trade is really entered at
    stop = max(stop, entry - p.risk_atr_max * atr)
    risk = entry - stop
    if not (math.isfinite(risk) and risk >= p.risk_atr_min * atr > 0):
        return None
    stop_p = to_tick(stop, up=False)
    t1, t2 = to_tick(entry + t1_r * risk, up=False), to_tick(entry + t2_r * risk, up=False)
    if not (0 < stop_p < entry_p < t1 < t2):
        return None
    return Draft(kind, entry_p, stop_p, t1, t2, facts)


def volume_ratio(bars: Bars, window: int) -> float:
    """Today's volume against the mean of the `window` sessions before it (nan without that history or volume)."""
    if len(bars) <= window:
        return math.nan
    mean = float(np.mean(bars.volume[-1 - window : -1]))
    return float(bars.volume[-1]) / mean if mean > 0 else math.nan


def base_breakout(bars: Bars, atr: float, trend: Trend, p: SetupParams) -> Draft | None:
    """Close above a tight base's high on high volume."""
    q = p.base_breakout
    n = len(bars)
    if n < q.base_bars + 2 or trend.state == "down":
        return None
    high, low, close = bars.high, bars.low, bars.close
    base_high = float(high[n - 1 - q.base_bars : n - 1].max())
    base_low = float(low[n - 1 - q.base_bars : n - 1].min())
    depth = (base_high - base_low) / base_high * 100.0
    yesterday_base_high = float(high[n - 2 - q.base_bars : n - 2].max())
    ratio = volume_ratio(bars, q.base_bars)
    today = float(close[-1])
    if not (
        depth <= q.max_depth_pct
        and today > base_high * (1.0 + q.close_above_pct / 100.0)
        and float(close[-2]) <= yesterday_base_high
        and ratio >= q.volume_ratio_min
    ):
        return None
    return finish(
        "base_breakout",
        today,
        base_low - q.stop_buffer_atr * atr,
        atr,
        q.target_1_r,
        q.target_2_r,
        p,
        {"base_high": base_high, "base_low": base_low, "base_depth_pct": depth, "volume_ratio": ratio},
    )


def ema_pullback(bars: Bars, atr: float, trend: Trend, emas: dict[int, float], p: SetupParams) -> Draft | None:
    """A dip to the 20/50 EMA inside an uptrend, with a bounce."""
    q = p.ema_pullback
    n = len(bars)
    if trend.state != "up" or n < q.pullback_bars + 2:
        return None
    low, close = float(bars.low[-1]), float(bars.close[-1])
    touched = next(
        (
            (period, emas[period])
            for period in q.ema
            if period in emas
            and math.isfinite(emas[period])
            and emas[period] * (1.0 - q.touch_pct / 100.0) <= low <= emas[period] * (1.0 + q.touch_pct / 100.0)
            and close >= emas[period]
        ),
        None,
    )
    if touched is None:
        return None
    recent_high = float(bars.high[n - 1 - q.pullback_bars : n - 1].max())
    depth = (recent_high - close) / recent_high * 100.0
    if depth < q.min_pullback_pct or not close > float(bars.open[-1]):
        return None
    period, level = touched
    return finish(
        "ema_pullback",
        float(bars.high[-1]),  # a buy stop one tick above the day's high: it only triggers if the bounce continues
        min(low, level) - q.stop_buffer_atr * atr,
        atr,
        q.target_1_r,
        q.target_2_r,
        p,
        {"ema_period": float(period), "ema": level, "pullback_pct": depth, "recent_high": recent_high},
        entry_up_tick=True,
    )


def week_52_high_breakout(bars: Bars, atr: float, p: SetupParams) -> Draft | None:
    """First close above the 52-week high on high volume."""
    q = p.week_52_high_breakout
    n = len(bars)
    if n < q.min_bars:
        return None
    high, low, close = bars.high, bars.low, bars.close
    start = max(0, n - 1 - q.lookback)
    prior_high = float(high[start : n - 1].max())
    yesterday_prior_high = float(high[max(0, start - 1) : n - 2].max())
    ratio = volume_ratio(bars, VOLUME_LOOKBACK)
    day_range = float(high[-1] - low[-1])
    position = (float(close[-1]) - float(low[-1])) / day_range if day_range > 0 else 0.0
    if not (
        float(close[-1]) > prior_high
        and float(close[-2]) <= yesterday_prior_high
        and ratio >= q.volume_ratio_min
        and position >= q.close_in_range_min
    ):
        return None
    return finish(
        "week_52_high_breakout",
        float(close[-1]),
        prior_high - q.stop_atr * atr,
        atr,
        q.target_1_r,
        q.target_2_r,
        p,
        {"prior_high": prior_high, "volume_ratio": ratio, "close_in_range": position},
    )


def detect(bars: Bars, atr: float, trend: Trend, emas: dict[int, float], p: SetupParams) -> Draft | None:
    """The first detector in `precedence` that fires."""
    for kind in p.precedence:
        draft = (
            week_52_high_breakout(bars, atr, p)
            if kind == "week_52_high_breakout"
            else base_breakout(bars, atr, trend, p)
            if kind == "base_breakout"
            else ema_pullback(bars, atr, trend, emas, p)
        )
        if draft is not None:
            return draft
    return None
