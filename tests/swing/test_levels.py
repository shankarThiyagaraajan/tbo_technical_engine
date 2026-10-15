"""Swing pivots, clustering, the prior month, and the order and kind of the zones."""

from __future__ import annotations

from datetime import date

import numpy as np

from tbo_technical_engine.swing import levels
from tests.swing.data import PARAMS, bars_from, weekdays_ending

P = PARAMS.levels


def test_a_swing_high_is_the_extreme_of_its_wings_and_the_last_sessions_cannot_qualify() -> None:
    highs = np.array([1, 2, 3, 9, 3, 2, 1, 2, 3, 4, 5, 6], dtype=np.float64)
    assert levels.swing_pivots(highs, 3, highs=True) == [9.0]  # the 6 at the end is rising, not a pivot yet


def test_a_swing_low_mirrors_it() -> None:
    lows = np.array([9, 8, 7, 1, 7, 8, 9, 8, 7, 6, 5, 4], dtype=np.float64)
    assert levels.swing_pivots(lows, 3, highs=False) == [1.0]


def test_cluster_groups_from_the_lowest_price_up_and_drops_lone_pivots() -> None:
    zones = levels.cluster([100.0, 100.4, 100.9, 110.0, 120.0, 120.2], tolerance=1.0, min_touches=2)
    assert zones == [(100.0, 100.9), (120.0, 120.2)]  # 110 stands alone: not a zone


def test_prior_month_is_the_calendar_month_before_the_session() -> None:
    days = weekdays_ending(date(2026, 10, 15), 60)
    close = [100.0 + i for i in range(60)]
    bars = bars_from(close, end=date(2026, 10, 15))
    september = [i for i, d in enumerate(days) if d.month == 9]
    high, low = levels.prior_month_range(bars, date(2026, 10, 15)) or (0, 0)
    assert high == max(bars.high[september]) and low == min(bars.low[september])


def test_prior_month_across_a_year_end() -> None:
    bars = bars_from([100.0] * 30, end=date(2026, 1, 9))
    assert levels.prior_month_range(bars, date(2026, 1, 9)) is not None  # December 2025


def test_no_session_in_the_prior_month_is_none() -> None:
    assert levels.prior_month_range(bars_from([100.0] * 5, end=date(2026, 10, 15)), date(2026, 10, 15)) is None


def zigzag(n: int = 200) -> list[float]:
    """Swings between about 95 and 105, so pivots cluster near both."""
    return [100 + 5 * np.sin(2 * np.pi * i / 20) for i in range(n)]


def test_zones_are_nearest_first_and_the_kind_follows_the_close() -> None:
    bars = bars_from(zigzag())
    found = levels.build(bars, 1.5, date(2026, 10, 15), P)
    close = float(bars.close[-1])
    gaps = [0.0 if z.low <= close <= z.high else min(abs(z.low - close), abs(z.high - close)) for _, z in found]
    assert gaps == sorted(gaps)
    for kind, zone in found:
        assert (kind == "resistance") == ((zone.low + zone.high) / 2 > close)
    assert {z.source for _, z in found} >= {"swing_cluster", "week_52_high", "week_52_low"}


def test_clusters_need_two_touches_and_at_most_max_levels_come_back() -> None:
    found = levels.build(bars_from(zigzag()), 1.5, date(2026, 10, 15), P)
    assert len(found) <= P.max_levels
    swing = [z for _, z in found if z.source == "swing_cluster"]
    assert swing and all(z.low <= z.high for z in swing)


def test_the_52_week_zones_sit_on_the_inner_side_of_the_extreme() -> None:
    bars = bars_from(zigzag())
    found = {z.source: z for _, z in levels.build(bars, 2.0, date(2026, 10, 15), P)}
    top, bottom = float(bars.high.max()), float(bars.low.min())
    assert found["week_52_high"].high == top and found["week_52_high"].low == top - P.band_atr * 2.0
    assert found["week_52_low"].low == bottom and found["week_52_low"].high == bottom + P.band_atr * 2.0
