from __future__ import annotations

import math

import numpy as np

from tbo_technical_engine.swing import liquidity, rank


def test_turnover_is_the_mean_of_close_times_volume() -> None:
    close = np.array([10.0, 20.0, 30.0])
    volume = np.array([100.0, 100.0, 200.0])
    assert liquidity.turnover(close, volume, 2) == (20 * 100 + 30 * 200) / 2
    assert liquidity.turnover(close, volume, 20) == (1000 + 2000 + 6000) / 3  # fewer sessions than asked: all of them


def corwin_schultz(h1: float, l1: float, h2: float, l2: float) -> float:
    """The paper's formula written out for one pair, independent of the vectorised code."""
    beta = math.log(h1 / l1) ** 2 + math.log(h2 / l2) ** 2
    gamma = math.log(max(h1, h2) / min(l1, l2)) ** 2
    k = 3 - 2 * math.sqrt(2)
    alpha = (math.sqrt(2 * beta) - math.sqrt(beta)) / k - math.sqrt(gamma / k)
    return 2 * (math.exp(alpha) - 1) / (1 + math.exp(alpha))


def test_a_pair_matches_the_formula() -> None:
    high, low = np.array([110.0, 110.0]), np.array([100.0, 100.0])
    expected = corwin_schultz(110, 100, 110, 100)
    assert abs(liquidity.pair_spreads(high, low)[0] - expected) < 1e-12
    assert 0.09 < expected < 0.1  # worked by hand: alpha = ln(1.1) = 0.0953, S = 2(e^a - 1)/(1 + e^a)


def test_the_spread_is_the_mean_of_non_negative_pair_estimates_in_percent() -> None:
    high = np.array([102.0, 103.0, 102.5, 104.0])
    low = np.array([100.0, 101.0, 100.5, 101.5])
    pairs = [
        corwin_schultz(102, 100, 103, 101),
        corwin_schultz(103, 101, 102.5, 100.5),
        corwin_schultz(102.5, 100.5, 104, 101.5),
    ]
    expected = float(np.mean([max(p, 0.0) for p in pairs])) * 100
    assert abs((liquidity.spread_pct(high, low, 20, 3) or 0) - expected) < 1e-9


def test_an_estimate_below_zero_counts_as_zero() -> None:
    # Identical bars with a tiny range: gamma exceeds what beta explains, and the raw estimate dips below zero.
    high, low = np.full(5, 100.1), np.full(5, 100.0)
    assert (liquidity.spread_pct(high, low, 20, 3) or 0) >= 0


def test_too_few_usable_pairs_is_none_and_a_bad_price_is_skipped() -> None:
    assert liquidity.spread_pct(np.array([101.0, 102.0]), np.array([100.0, 101.0]), 20, 3) is None
    high, low = np.array([101.0, 0.0, 102.0, 103.0]), np.array([100.0, 0.0, 101.0, 102.0])
    assert np.isnan(liquidity.pair_spreads(high, low)[:2]).all()


def test_ranks_use_half_for_ties_and_a_lone_stock_is_fifty() -> None:
    assert rank.percentile_ranks({"a": 5.0}) == {"a": 50}
    ranks = rank.percentile_ranks({"a": 1.0, "b": 2.0, "c": 3.0, "d": 4.0})
    assert ranks == {"a": 12, "b": 38, "c": 62, "d": 88}
    assert rank.percentile_ranks({"a": 1.0, "b": 1.0}) == {"a": 50, "b": 50}
    assert rank.percentile_ranks({}) == {}


def test_blend_is_the_weighted_mean_for_stocks_with_both_ranks() -> None:
    assert rank.blend({"a": 100, "b": 0}, {"a": 50, "c": 10}, 0.5, 0.5) == {"a": 75}
    assert rank.blend({"a": 100}, {"a": 0}, 0.25, 0.75) == {"a": 25}
