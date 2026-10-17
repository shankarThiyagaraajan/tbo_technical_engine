"""Trend state and score on series whose answer is obvious, and the rescaling for a young stock."""

from __future__ import annotations

from tbo_technical_engine.swing import trend
from tests.swing.data import PARAMS, bars_from, mean_reverting, wave

P = PARAMS.trend


def test_a_steady_climb_is_up_with_every_condition_true() -> None:
    result = trend.assess(bars_from(wave(300, 100, 0.5)), P)
    assert (result.state, result.bull, result.score) == ("up", 100.0, 100)


def test_a_steady_fall_is_down_and_as_convincing_as_the_climb() -> None:
    result = trend.assess(bars_from(wave(300, 400, -0.5)), P)
    assert (result.state, result.bull, result.score) == ("down", 0.0, 100)


def test_a_flat_market_is_sideways_with_a_low_score() -> None:
    result = trend.assess(bars_from(mean_reverting(300, seed=3)), P)
    assert result.state == "sideways"
    assert result.score < 40


def test_aligned_averages_without_adx_are_still_sideways() -> None:
    # Seed 4 wanders around 100 with every average ordered bullishly (bull 100), yet ADX is under 20: no trend.
    result = trend.assess(bars_from(mean_reverting(300, seed=4)), P)
    assert result.bull == 100.0 and result.state == "sideways"
    assert result.score < 100  # and the score is scaled down with the weak ADX


def test_score_is_the_distance_from_an_even_split_doubled() -> None:
    # 65 bull points is the lowest "up": |65 - 50| * 2 = 30.
    result = trend.assess(bars_from(wave(300, 100, 0.5)), P)
    assert round(abs(result.bull - 50) * 2) == result.score


def test_a_young_stock_is_judged_on_the_conditions_it_can_show() -> None:
    # 80 sessions: the 200 EMA does not exist. Its 20 points drop out and the rest are rescaled, so a clean climb is
    # still 100 and not 80.
    result = trend.assess(bars_from(wave(80, 100, 0.5)), P)
    assert result.bull == 100.0 and result.state == "up"


def test_a_shallow_dip_inside_an_uptrend_loses_points_but_keeps_the_state() -> None:
    closes = wave(300, 100, 0.5)
    for i in range(
        1, 5
    ):  # four sessions sliding up to 4.5 points off the climb: under the 20 EMA, above the 50 and 200
        closes[-i] -= 3.0 * (5 - i) / 2
    result = trend.assess(bars_from(closes), P)
    assert result.state == "up" and 65 <= result.bull < 100


def test_a_sharp_drop_ends_the_call() -> None:
    closes = wave(300, 100, 0.5)
    for i in range(1, 8):
        closes[-i] = closes[-8] - 3.0 * (8 - i) / 2
    assert trend.assess(bars_from(closes), P).state != "up"
