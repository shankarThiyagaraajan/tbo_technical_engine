"""Support and resistance zones: clustered swing highs and lows, the 52-week high and low, the prior month's high
and low.

A swing high (low) is a session whose high (low) is the extreme of `pivot_wing` sessions on each side, so a pivot is
only known `pivot_wing` sessions after it happened (the last sessions can not be pivots yet: no look-ahead).
Pivot prices are sorted and grouped from the lowest up: a group starts at its lowest price and takes every price
within `cluster_atr` ATR of it. A group with at least `min_touches` pivots is a zone, drawn from its lowest to its
highest price. Single prices (52-week and prior-month extremes) become a thin zone of `band_atr` ATR on the inner side.

A zone is `resistance` when its middle is above the close and `support` when not. The list is nearest first.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal

from tbo_technical_engine.indicators.bars import F64, Bars
from tbo_technical_engine.schemas.swing import LevelParams

Source = Literal["swing_cluster", "week_52_high", "week_52_low", "prior_month_high", "prior_month_low"]
TRADING_DAYS_52W = 252


@dataclass(frozen=True)
class Zone:
    low: float
    high: float
    source: Source


def swing_pivots(values: F64, wing: int, *, highs: bool) -> list[float]:
    """Prices of the swing highs (or lows) in `values`; the last `wing` sessions can not qualify yet."""
    out: list[float] = []
    for i in range(wing, len(values) - wing):
        left, right, v = values[i - wing : i], values[i + 1 : i + 1 + wing], values[i]
        if highs and v > left.max() and v >= right.max():
            out.append(float(v))
        if not highs and v < left.min() and v <= right.min():
            out.append(float(v))
    return out


def cluster(prices: list[float], tolerance: float, min_touches: int) -> list[tuple[float, float]]:
    """Zones as (low, high) from sorted prices, grouped from the lowest up within `tolerance` of the group's start."""
    zones: list[tuple[float, float]] = []
    group: list[float] = []
    for price in sorted(prices):
        if group and price - group[0] > tolerance:
            if len(group) >= min_touches:
                zones.append((group[0], group[-1]))
            group = []
        group.append(price)
    if len(group) >= min_touches:
        zones.append((group[0], group[-1]))
    return zones


def prior_month_range(bars: Bars, as_of: date) -> tuple[float, float] | None:
    """High and low of the calendar month before `as_of`'s month (IST dates); None if no session fell in it."""
    year, month = (as_of.year, as_of.month - 1) if as_of.month > 1 else (as_of.year - 1, 12)
    first, last = date(year, month, 1), date(as_of.year, as_of.month, 1)
    mask = (bars.session >= first.toordinal()) & (bars.session < last.toordinal())
    if not mask.any():
        return None
    return float(bars.high[mask].max()), float(bars.low[mask].min())


def build(bars: Bars, atr: float, as_of: date, p: LevelParams) -> list[tuple[Literal["support", "resistance"], Zone]]:
    """All zones for the last bar, nearest first, at most `max_levels`."""
    close = float(bars.close[-1])
    highs, lows = bars.high[-p.lookback :], bars.low[-p.lookback :]
    pivots = swing_pivots(highs, p.pivot_wing, highs=True) + swing_pivots(lows, p.pivot_wing, highs=False)
    zones = [Zone(lo, hi, "swing_cluster") for lo, hi in cluster(pivots, p.cluster_atr * atr, p.min_touches)]
    band = p.band_atr * atr
    year = bars.high[-TRADING_DAYS_52W:], bars.low[-TRADING_DAYS_52W:]
    top, bottom = float(year[0].max()), float(year[1].min())
    zones.append(Zone(top - band, top, "week_52_high"))
    zones.append(Zone(bottom, bottom + band, "week_52_low"))
    month = prior_month_range(bars, as_of)
    if month is not None:
        zones.append(Zone(month[0] - band, month[0], "prior_month_high"))
        zones.append(Zone(month[1], month[1] + band, "prior_month_low"))
    ranked: list[tuple[Literal["support", "resistance"], Zone]] = [
        ("resistance" if (z.low + z.high) / 2.0 > close else "support", z) for z in zones
    ]

    def distance(item: tuple[Literal["support", "resistance"], Zone]) -> tuple[float, float]:
        z = item[1]
        gap = 0.0 if z.low <= close <= z.high else min(abs(z.low - close), abs(z.high - close))
        return (gap, z.low)

    return sorted(ranked, key=distance)[: p.max_levels]
