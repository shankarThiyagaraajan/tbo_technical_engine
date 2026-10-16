"""Hand-computed checks for the indicators TA-Lib does not have, and the edges of the ones it does."""

from __future__ import annotations

import math

import numpy as np
import pytest

from tbo_technical_engine import indicators
from tbo_technical_engine.indicators.base import Indicator
from tbo_technical_engine.indicators.format import decimal_text
from tbo_technical_engine.indicators.levels import camarilla_pivots, classic_pivots, cpr
from tests.indicators.data import from_ohlcv

NEEDS_PERIOD = {"sma": {"period": 20}, "ema": {"period": 9}}


def make(name: str) -> Indicator:
    return indicators.get(name, **NEEDS_PERIOD.get(name, {}))


def flat(n: int, price: float = 100.0, volume: float = 1000.0) -> list[tuple[float, float, float, float, float]]:
    return [(price, price, price, price, volume)] * n


# --- pivots, Camarilla, CPR: textbook numbers for H=110, L=90, C=100 ---------------------------------------------


def test_classic_pivots() -> None:
    p = classic_pivots(110, 90, 100)
    assert p == pytest.approx(
        {
            "pivot_p": 100,
            "pivot_r1": 110,
            "pivot_s1": 90,
            "pivot_r2": 120,
            "pivot_s2": 80,
            "pivot_r3": 130,
            "pivot_s3": 70,
        }
    )


def test_camarilla_pivots() -> None:
    c = camarilla_pivots(110, 90, 100)  # range 20
    assert c["cam_r1"] == pytest.approx(100 + 20 * 1.1 / 12)
    assert c["cam_r4"] == pytest.approx(100 + 20 * 1.1 / 2)
    assert c["cam_s3"] == pytest.approx(100 - 20 * 1.1 / 4)


def test_cpr_width_is_a_percentage_of_the_pivot() -> None:
    c = cpr(110, 90, 100)
    assert (c["cpr_pivot"], c["cpr_bc"], c["cpr_tc"], c["cpr_width_pct"]) == pytest.approx((100, 100, 100, 0))
    wide = cpr(120, 90, 90)  # P = 100, BC = 105, TC = 95
    assert wide["cpr_top"] == pytest.approx(105) and wide["cpr_bottom"] == pytest.approx(95)
    assert wide["cpr_width_pct"] == pytest.approx(10.0)


def test_pivots_come_from_the_previous_session_only() -> None:
    day1 = [(100, 110, 90, 100, 10)] * 3  # session 1: H 110, L 90, C 100
    day2 = [(105, 106, 104, 105, 10)] * 3
    bars = from_ohlcv(day1 + day2, per_session=3)
    p = indicators.get("pivots").batch(bars)["pivot_p"]
    assert np.isnan(p[:3]).all()  # nothing before the second session
    assert p[3:] == pytest.approx([100.0] * 3)  # and today's own prices change nothing
    stream = indicators.get("pivots").stream()
    streamed = [stream.update(bars.row(i))["pivot_p"] for i in range(6)]
    assert all(math.isnan(v) for v in streamed[:3]) and streamed[3:] == pytest.approx([100.0] * 3)


# --- VWAP --------------------------------------------------------------------------------------------------------


def test_vwap_is_volume_weighted_and_resets_each_session() -> None:
    rows = [(10, 10, 10, 10, 100), (20, 20, 20, 20, 300), (50, 50, 50, 50, 10), (60, 60, 60, 60, 10)]
    out = indicators.get("vwap").batch(from_ohlcv(rows, per_session=2))
    assert out["vwap"] == pytest.approx([10, (10 * 100 + 20 * 300) / 400, 50, 55])


def test_vwap_bands_are_the_weighted_standard_deviation() -> None:
    rows = [(10, 10, 10, 10, 100), (20, 20, 20, 20, 300)]
    out = indicators.get("vwap", band_multipliers=(1.0, 2.0)).batch(from_ohlcv(rows))
    vwap = 17.5
    sigma = math.sqrt((100 * 100 + 300 * 400) / 400 - vwap**2)
    assert out["vwap_upper_1"][1] == pytest.approx(vwap + sigma)
    assert out["vwap_lower_2"][1] == pytest.approx(vwap - 2 * sigma)
    assert out["vwap_upper_1"][0] == pytest.approx(10.0)  # one bar has no spread


def test_vwap_has_no_value_until_there_is_volume() -> None:
    out = indicators.get("vwap").batch(from_ohlcv([(10, 10, 10, 10, 0), (12, 12, 12, 12, 5)]))
    assert math.isnan(out["vwap"][0]) and out["vwap"][1] == pytest.approx(12.0)


# --- opening range -----------------------------------------------------------------------------------------------


def or_rows() -> list[tuple[float, float, float, float, float]]:
    return [(100, 102, 99, 101, 1), (101, 105, 100, 104, 1), (104, 106, 103, 105, 1), (105, 120, 80, 100, 1)]


def test_opening_range_exists_only_once_the_window_has_closed() -> None:
    out = indicators.get("opening_range", minutes=15).batch(from_ohlcv(or_rows(), per_session=4))
    # 09:15, 09:20, 09:25 are the range; the 09:25 bar closes at 09:30, so the range is visible from index 2.
    assert np.isnan(out["or_high"][:2]).all()
    assert out["or_high"][2:] == pytest.approx([106, 106]) and out["or_low"][2:] == pytest.approx([99, 99])
    assert out["or_mid"][3] == pytest.approx(102.5) and out["or_height"][3] == pytest.approx(
        7
    )  # the wild 09:30 bar is outside


def test_a_session_that_starts_late_has_no_opening_range() -> None:
    bars = from_ohlcv(or_rows(), per_session=4)
    late = type(bars)(
        ts=bars.ts[1:],
        open=bars.open[1:],
        high=bars.high[1:],
        low=bars.low[1:],
        close=bars.close[1:],
        volume=bars.volume[1:],
        session=bars.session[1:],
        tod=bars.tod[1:],
    )
    assert np.isnan(indicators.get("opening_range").batch(late)["or_high"]).all()


# --- volume ------------------------------------------------------------------------------------------------------


def test_volume_average_leaves_the_current_bar_out() -> None:
    vols = [100.0] * 20 + [300.0, 100.0]
    bars = from_ohlcv([(1, 1, 1, 1, v) for v in vols])
    out = indicators.get("volume_average", period=20).batch(bars)
    assert math.isnan(out["vol_avg_20"][19])
    assert out["vol_avg_20"][20] == pytest.approx(100) and out["vol_ratio_20"][20] == pytest.approx(3.0)
    assert out["vol_avg_20"][21] == pytest.approx(110)  # the spike now counts, one bar later


# --- supertrend --------------------------------------------------------------------------------------------------


def test_supertrend_follows_a_rising_market_below_the_price_and_flips_when_it_breaks() -> None:
    up = [(p, p + 1, p - 1, p + 0.5, 100.0) for p in map(float, np.arange(100.0, 130.0, 1.0))]
    down = [(p, p + 1, p - 1, p - 0.5, 100.0) for p in map(float, np.arange(129.0, 90.0, -3.0))]
    bars = from_ohlcv(up + down)
    out = indicators.get("supertrend", period=10, multiplier=3.0).batch(bars)
    line, direction = out["supertrend_10_3"], out["supertrend_dir_10_3"]
    assert (direction[10 : len(up)] == 1).all()  # green all the way up
    assert (line[10 : len(up)] < bars.close[10 : len(up)]).all()  # support sits below the price
    assert direction[-1] == -1 and line[-1] > bars.close[-1]  # red at the end, resistance above
    flips = np.flatnonzero(np.diff(direction[10:]) != 0)
    assert len(flips) == 1  # one clean reversal, no whipsaw


def test_the_supertrend_band_only_tightens_while_the_trend_holds() -> None:
    rows = [(p, p + 1, p - 1, p + 0.5, 100.0) for p in map(float, np.arange(100.0, 160.0, 1.0))]
    line = indicators.get("supertrend").batch(from_ohlcv(rows))["supertrend_10_3"]
    valid = line[~np.isnan(line)]
    assert (np.diff(valid) >= 0).all()


# --- flat prices and tiny inputs ---------------------------------------------------------------------------------


def test_flat_prices_give_neutral_values_not_errors() -> None:
    bars = from_ohlcv(flat(80))
    assert indicators.get("rsi").batch(bars)["rsi_14"][-1] == 0.0
    assert indicators.get("adx").batch(bars)["adx_14"][-1] == 0.0
    assert indicators.get("atr").batch(bars)["atr_14"][-1] == 0.0
    assert indicators.get("ema", period=9).batch(bars)["ema_9"][-1] == pytest.approx(100.0)


@pytest.mark.parametrize("name", sorted(indicators.CATALOGUE))
def test_series_shorter_than_the_lookback_are_all_nan_and_do_not_fail(name: str) -> None:
    bars = from_ohlcv(flat(3))
    indicator = make(name)
    batch = indicator.batch(bars)
    stream = indicator.stream()
    for i in range(len(bars)):
        stream.update(bars.row(i))
    if indicator.lookback > 3:
        assert all(np.isnan(v).all() for v in batch.values())


@pytest.mark.parametrize("name", sorted(indicators.CATALOGUE))
def test_an_empty_series_gives_empty_outputs(name: str) -> None:
    bars = from_ohlcv([])
    assert all(len(v) == 0 for v in make(name).batch(bars).values())


# --- relative strength -------------------------------------------------------------------------------------------


def test_relative_strength_is_the_return_gap_in_percentage_points() -> None:
    rs = indicators.RelativeStrength(period=2)
    stock = np.array([100.0, 101.0, 110.0, 99.0])
    bench = np.array([100.0, 100.0, 105.0, 100.0])
    out = rs.batch(stock, bench)
    assert np.isnan(out[:2]).all()
    assert out[2] == pytest.approx(10.0 - 5.0) and out[3] == pytest.approx((99 / 101 - 1) * 100 - 0.0)
    stream = rs.stream()
    assert [stream.update(float(s), float(b)) for s, b in zip(stock, bench, strict=True)][2:] == pytest.approx(
        list(out[2:])
    )


def test_relative_strength_needs_the_same_bars() -> None:
    with pytest.raises(ValueError, match="same bars"):
        indicators.RelativeStrength().batch(np.zeros(3), np.zeros(4))


# --- the facade and the contract's number format ----------------------------------------------------------------


def test_get_rejects_unknown_names_and_bad_parameters() -> None:
    with pytest.raises(ValueError, match="unknown indicator"):
        indicators.get("macd")
    with pytest.raises(ValueError, match="period"):
        indicators.get("ema", period=1)
    with pytest.raises(TypeError):
        indicators.get("ema", periods=9)


def test_output_names_carry_their_parameters() -> None:
    assert indicators.get("ema", period=21).outputs == ("ema_21",)
    assert indicators.get("supertrend", period=10, multiplier=3).outputs == ("supertrend_10_3", "supertrend_dir_10_3")
    assert indicators.get("supertrend", period=7, multiplier=2.5).outputs[0] == "supertrend_7_2p5"
    assert indicators.get("adx").outputs == ("adx_14", "plus_di_14", "minus_di_14")


@pytest.mark.parametrize(
    ("value", "text"),
    [(24.6, "24.6"), (1247.2, "1247.2"), (0.0, "0"), (-0.0, "0"), (-1e-9, "0"), (3.0, "3"), (0.1234567891, "0.123457")],
)
def test_values_are_written_as_contract_decimals(value: float, text: str) -> None:
    assert decimal_text(value) == text


def test_warming_up_values_are_null() -> None:
    assert decimal_text(math.nan) is None and decimal_text(math.inf) is None
