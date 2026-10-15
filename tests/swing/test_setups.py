"""The three detectors and the shared pricing, on series worked out by hand."""

from __future__ import annotations

from decimal import Decimal

import pytest

from tbo_technical_engine.indicators.bars import Bars
from tbo_technical_engine.swing import analysis, setups
from tbo_technical_engine.swing.trend import Trend
from tests.swing.data import (
    PARAMS,
    as_bars,
    bars_from,
    base_breakout_series,
    pullback_series,
    week_52_series,
)

SP = PARAMS.setups
ISIN = "INE002A01018"


def trend_of(state: str) -> Trend:
    return Trend(state, 80, 90.0, 0.0, 0.0, 0.0, True)  # type: ignore[arg-type]


def detect(bars: Bars) -> setups.Draft | None:
    measured = analysis.measure(ISIN, bars, PARAMS)
    assert measured is not None
    return analysis.find_setup(measured, bars, PARAMS)


def pullback_bars(**kwargs: object) -> Bars:
    s = pullback_series(**kwargs)  # type: ignore[arg-type]
    return bars_from(s["close"], high=s["high"], low=s["low"], open_=s["open"], volume=s["volume"])


# --- finish: pricing shared by every detector --------------------------------------------------------------------


def test_prices_round_the_safe_way_and_targets_are_multiples_of_the_risk() -> None:
    draft = setups.finish("base_breakout", 100.02, 95.03, 2.0, 1.5, 3.0, SP, {})
    assert draft is not None
    # entry up to 100.05; risk 100.05 - 95.03 = 5.02 (stop floors to 95.00); T1 = 100.05 + 7.53, T2 = 100.05 + 15.06
    assert (draft.entry, draft.stop) == (Decimal("100.05"), Decimal("95.00"))
    assert (draft.target_1, draft.target_2) == (Decimal("107.55"), Decimal("115.10"))


def test_a_stop_further_than_the_cap_is_pulled_in_to_it() -> None:
    draft = setups.finish("base_breakout", 100.0, 80.0, 2.0, 1.5, 3.0, SP, {})
    assert draft is not None and draft.stop == Decimal("92.00")  # 100 - 4 ATR


def test_a_stop_inside_the_noise_gives_no_setup() -> None:
    assert setups.finish("ema_pullback", 100.0, 99.5, 2.0, 1.5, 2.5, SP, {}) is None  # 0.25 ATR away, minimum 0.5


def test_a_buy_stop_entry_is_one_tick_above_the_rounded_high() -> None:
    draft = setups.finish("ema_pullback", 100.01, 96.0, 2.0, 1.5, 2.5, SP, {}, entry_up_tick=True)
    assert draft is not None and draft.entry == Decimal("100.10")  # 100.01 up to 100.05, plus a tick


# --- base breakout -----------------------------------------------------------------------------------------------


def test_base_breakout_on_rising_volume_prices_from_the_base() -> None:
    bars = as_bars(base_breakout_series())
    measured = analysis.measure(ISIN, bars, PARAMS)
    assert measured is not None
    draft = detect(bars)
    assert draft is not None and draft.setup_type == "base_breakout"
    base_low = draft.facts["base_low"]
    assert abs(base_low - 98.134) < 0.001 and abs(draft.facts["base_high"] - 101.866) < 0.001
    stop = base_low - 0.1 * measured.atr  # one tenth of an ATR under the base
    risk = 105.0 - stop
    assert draft.entry == Decimal("105.00")
    assert draft.stop == Decimal(str(int(stop * 20) / 20)).quantize(Decimal("0.01"))
    assert draft.target_1 == Decimal(str(int((105 + 1.5 * risk) * 20) / 20)).quantize(Decimal("0.01"))
    assert draft.target_2 == Decimal(str(int((105 + 3 * risk) * 20) / 20)).quantize(Decimal("0.01"))


def test_volume_must_reach_one_and_a_half_times_the_base() -> None:
    assert detect(as_bars(base_breakout_series(breakout_volume=150_000))) is not None  # exactly 1.5x passes
    assert detect(as_bars(base_breakout_series(breakout_volume=149_000))) is None


def test_a_base_deeper_than_twelve_percent_is_not_a_base() -> None:
    assert detect(as_bars(base_breakout_series(base_amplitude=9.0, breakout_close=115.0))) is None


def test_the_close_must_clear_the_base_high_by_a_tenth_of_a_percent() -> None:
    assert detect(as_bars(base_breakout_series(breakout_close=101.9))) is None  # base high 101.866 * 1.001 = 101.968
    assert detect(as_bars(base_breakout_series(breakout_close=102.1))) is not None


def test_only_the_first_close_above_the_base_is_reported() -> None:
    series = base_breakout_series()
    series["close"].append(108.0)  # the next session closes higher still, above the breakout day's high: a continuation
    series["high"].append(109.0)
    series["low"].append(106.0)
    series["volume"].append(300_000.0)
    assert detect(as_bars(series)) is None  # the breakout was yesterday; it is not announced twice


def test_a_downtrend_blocks_a_base_breakout() -> None:
    bars = as_bars(base_breakout_series())
    measured = analysis.measure(ISIN, bars, PARAMS)
    assert measured is not None
    assert setups.base_breakout(bars, measured.atr, trend_of("down"), SP) is None
    assert setups.base_breakout(bars, measured.atr, trend_of("sideways"), SP) is not None


# --- EMA pullback ------------------------------------------------------------------------------------------------


def test_pullback_to_the_20_ema_enters_above_the_days_high() -> None:
    bars = pullback_bars()
    measured = analysis.measure(ISIN, bars, PARAMS)
    assert measured is not None and measured.trend.state == "up"
    draft = detect(bars)
    assert draft is not None and draft.setup_type == "ema_pullback" and draft.facts["ema_period"] == 20
    high = float(bars.high[-1])
    assert Decimal(str(round(high, 2))) < draft.entry <= Decimal(str(round(high + 0.1, 2)))  # a tick over the high
    assert draft.stop < Decimal(str(round(draft.facts["ema"], 2)))  # under the EMA by a quarter ATR


def test_a_low_that_did_not_come_near_the_ema_is_no_pullback() -> None:
    assert detect(pullback_bars(low_above_ema_pct=1.5)) is None  # 1.5% above the EMA; the band is 0.5%


def test_a_red_day_is_no_bounce() -> None:
    assert detect(pullback_bars(bullish=False)) is None


def test_a_shallow_dip_is_not_a_pullback() -> None:
    assert detect(pullback_bars(dip=3.0)) is None  # under 2% off the recent high


def test_a_pullback_needs_an_uptrend() -> None:
    bars = pullback_bars()
    measured = analysis.measure(ISIN, bars, PARAMS)
    assert measured is not None
    emas = {20: measured.trend.ema_fast, 50: measured.trend.ema_mid}
    assert setups.ema_pullback(bars, measured.atr, trend_of("sideways"), emas, SP) is None


# --- 52-week high breakout ---------------------------------------------------------------------------------------


def test_52_week_breakout_prices_from_the_old_high() -> None:
    bars = as_bars(week_52_series())
    measured = analysis.measure(ISIN, bars, PARAMS)
    assert measured is not None
    draft = detect(bars)
    assert draft is not None and draft.setup_type == "week_52_high_breakout"
    prior = draft.facts["prior_high"]
    assert abs(prior - 184.28) < 0.01
    assert draft.entry == Decimal("185.80")  # the close 185.78 rounded up
    assert draft.stop == Decimal("181.10")  # 184.28 - 1.5 ATR(2.12) = 181.10
    assert draft.target_1 > draft.entry and draft.target_2 > draft.target_1


def week_52(**kwargs: float) -> setups.Draft | None:
    bars = as_bars(week_52_series(**kwargs))
    measured = analysis.measure(ISIN, bars, PARAMS)
    assert measured is not None
    return setups.week_52_high_breakout(bars, measured.atr, SP)


def test_52_week_breakout_needs_volume_and_a_strong_close() -> None:
    assert week_52() is not None
    assert week_52(volume=110_000) is None  # 1.1x the month's volume, under 1.2x
    assert week_52(last_high_gap=6.0) is None  # a long upper wick: it closes low in the day's range


def test_52_week_volume_of_exactly_the_minimum_passes() -> None:
    assert week_52(volume=120_000) is not None  # 1.2x the mean of the 20 sessions before


def test_a_second_session_above_the_old_high_is_not_a_new_breakout() -> None:
    series = week_52_series()
    last = series["close"][-1]
    series["close"].append(last + 3.0)
    series["high"].append(last + 3.5)
    series["low"].append(last + 1.0)
    series["volume"].append(200_000.0)
    bars = as_bars(series)
    measured = analysis.measure(ISIN, bars, PARAMS)
    assert measured is not None and setups.week_52_high_breakout(bars, measured.atr, SP) is None


def test_a_close_that_does_not_clear_the_old_high_is_no_breakout() -> None:
    assert week_52(close_above=-0.5) is None


def test_a_young_stock_has_no_52_week_high() -> None:
    series = week_52_series()
    short = {k: v[-150:] for k, v in series.items()}
    measured = analysis.measure(ISIN, as_bars(short), PARAMS)
    assert measured is not None and setups.week_52_high_breakout(as_bars(short), measured.atr, SP) is None


# --- precedence --------------------------------------------------------------------------------------------------


def test_when_two_detectors_fire_the_first_in_precedence_wins() -> None:
    # A breakout from a flat base that is also a 52-week high: both rules fire, and the 52-week one is listed first.
    close = [100.0] * 270
    close = [100.0 + 0.4 * (i % 5) for i in range(270)] + [106.0]
    high, low = [c + 1.0 for c in close], [c - 1.0 for c in close]
    volume = [100_000.0] * 270 + [300_000.0]
    bars = bars_from(close, high=high, low=low, volume=volume)
    measured = analysis.measure(ISIN, bars, PARAMS)
    assert measured is not None
    both = setups.detect(bars, measured.atr, trend_of("up"), {20: 0.0, 50: 0.0}, SP)
    assert both is not None and both.setup_type == "week_52_high_breakout"
    assert setups.base_breakout(bars, measured.atr, trend_of("up"), SP) is not None


@pytest.mark.parametrize("bad", [["base_breakout"], ["base_breakout", "base_breakout", "ema_pullback"]])
def test_precedence_must_name_each_type_once(bad: list[str]) -> None:
    from pydantic import ValidationError

    from tbo_technical_engine.schemas.swing import SwingParams

    document = PARAMS.model_dump()
    document["setups"]["precedence"] = bad
    with pytest.raises(ValidationError):
        SwingParams.model_validate(document)
