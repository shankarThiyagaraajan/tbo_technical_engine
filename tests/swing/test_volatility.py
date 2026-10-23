from __future__ import annotations

import numpy as np

from tbo_technical_engine.swing import volatility
from tests.swing.data import PARAMS, bars_from, wave

P = PARAMS.volatility


def test_percentile_counts_ties_as_half() -> None:
    window = np.array([1.0, 2.0, 3.0, 4.0])
    assert volatility.percentile_of(4.0, window) == 88  # (3 below + 0.5) / 4
    assert volatility.percentile_of(1.0, window) == 12
    assert volatility.percentile_of(2.0, np.array([2.0, 2.0])) == 50


def test_bandwidth_is_four_deviations_over_the_mean_for_k_two() -> None:
    close = np.array([10.0, 12.0, 10.0, 12.0])
    width = volatility.bandwidth(close, 4, 2.0)
    assert np.isnan(width[:3]).all()
    assert abs(width[3] - 2 * 2 * 1.0 / 11.0) < 1e-12  # population deviation of 10,12,10,12 is 1; mean 11


def test_the_regime_follows_the_percentile_of_atr_pct() -> None:
    # A quiet year that turns wild at the end: ATR % is at the top of its own year.
    close = wave(300, 100, 0.0, amplitude=0.5)
    high = [c + 0.5 for c in close]
    low = [c - 0.5 for c in close]
    for i in range(-10, 0):
        high[i], low[i] = close[i] + 6, close[i] - 6
    expanding = volatility.assess(bars_from(close, high=high, low=low), P)
    assert expanding.regime == "expanding" and expanding.percentile >= P.expanding_above
    # And a quiet end after a wild year is contracting.
    close = wave(300, 100, 0.0, amplitude=0.5)
    high = [c + 6 for c in close]
    low = [c - 6 for c in close]
    for i in range(-40, 0):
        high[i], low[i] = close[i] + 0.3, close[i] - 0.3
    contracting = volatility.assess(bars_from(close, high=high, low=low), P)
    assert contracting.regime == "contracting" and contracting.percentile <= P.contracting_below


def test_a_squeeze_is_a_bandwidth_in_the_bottom_fifth_of_six_months() -> None:
    close = wave(300, 100, 0.0, amplitude=5.0, period=9.0)
    for i in range(-25, 0):  # the last 25 sessions go almost flat
        close[i] = 100 + 0.1 * np.sin(i)
    assert volatility.assess(bars_from(close), P).is_squeeze
    assert not volatility.assess(bars_from(wave(300, 100, 0.0, amplitude=5.0, period=9.0)), P).is_squeeze


def test_too_little_history_for_a_percentile_gives_the_neutral_fifty() -> None:
    short = volatility.assess(bars_from(wave(30, 100, 0.5)), P)  # 30 bars: ATR exists for 16 sessions, under min_points
    assert short.percentile == 50 and short.regime == "normal"
