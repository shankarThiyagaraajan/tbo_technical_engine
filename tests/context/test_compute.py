"""The context functions on numbers that can be checked by hand."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest

from tbo_technical_engine.context import compute
from tbo_technical_engine.context.index_source import IndexSeries, VixReading
from tbo_technical_engine.schemas.candle import Candle
from tbo_technical_engine.services.candle_buffer import CandleBuffer
from tests.strategies.builders import DAY
from tests.support import ISIN_A, ISIN_B, candle, session_start, trading_days_back

YESTERDAY = trading_days_back(DAY, 1)[0]
THIRD = "INE001A01036"
FOURTH = "INE009A01021"
FIFTH = "INE090A01021"


def at(day: date, i: int) -> datetime:
    return session_start(day) + timedelta(minutes=5 * i)


def test_breadth_counts_stocks_against_yesterdays_close() -> None:
    buffer = CandleBuffer()
    for isin, y, t in ((ISIN_A, "100.00", "101.00"), (ISIN_B, "100.00", "99.00"), (THIRD, "100.00", "100.00")):
        buffer.add(candle(isin, "5m", at(YESTERDAY, 74), y))
        buffer.add(candle(isin, "5m", at(DAY, 0), "100.50"))  # an earlier candle today...
        buffer.add(candle(isin, "5m", at(DAY, 3), t))  # ...the latest one decides
    buffer.add(candle(FOURTH, "5m", at(DAY, 3), "105.00"))  # no previous session: not counted
    buffer.add(candle(FIFTH, "5m", at(YESTERDAY, 74), "50.00"))  # nothing today: not counted
    got = compute.breadth(buffer, [ISIN_A, ISIN_B, THIRD, FOURTH, FIFTH, "INE0NOTHING1"], DAY)
    assert (got.advancing, got.declining, got.unchanged) == (1, 1, 1)


def bars(day: date, closes: list[float], volume: int = 1000) -> list[Candle]:
    return [candle("nifty_50", "5m", at(day, i), f"{c:.2f}", volume=volume) for i, c in enumerate(closes)]


def fifteen(closes: list[float]) -> list[Candle]:
    day = trading_days_back(DAY, 6)[0]
    out = []
    for i, c in enumerate(closes):
        out.append(candle("nifty_50", "15m", session_start(day) + timedelta(minutes=15 * i), f"{c:.2f}"))
    return out


def series(today: list[Candle], trend: list[Candle]) -> IndexSeries:
    return IndexSeries("nifty_50", today, trend, candle("nifty_50", "1d", session_start(YESTERDAY), "100.00"))


RISING = [100 + 0.8 * i for i in range(60)]
FALLING = [150 - 0.8 * i for i in range(60)]
FLAT = [100.0] * 60


def test_an_index_above_its_vwap_in_an_uptrend() -> None:
    state = compute.index_state(series(bars(DAY, [100, 101, 102, 103]), fifteen(RISING)), DAY)
    assert state is not None
    assert state.index_code == "nifty_50" and state.last_price == "103.00"
    # each bar's VWAP price is (H+L+C)/3 = close here (the helper's +-1 band is symmetric): the running mean of 100..103
    assert state.vwap == "101.50" and state.is_above_vwap is True
    assert state.trend_15m == "up"


def test_an_index_below_its_vwap_in_a_downtrend() -> None:
    state = compute.index_state(series(bars(DAY, [103, 102, 101, 100]), fifteen(FALLING)), DAY)
    assert state is not None and state.vwap == "101.50" and state.is_above_vwap is False and state.trend_15m == "down"


def test_an_index_without_volume_has_no_vwap_and_is_not_above_it() -> None:
    state = compute.index_state(series(bars(DAY, [100, 101, 102], volume=0), fifteen(FLAT)), DAY)
    assert state is not None and state.vwap is None and state.is_above_vwap is False and state.trend_15m == "sideways"


def test_an_index_with_no_candle_today_has_no_state() -> None:
    assert compute.index_state(series(bars(YESTERDAY, [100, 101]), fifteen(RISING)), DAY) is None


@pytest.mark.parametrize("closes", [[], RISING[:5]])
def test_trend_is_sideways_while_there_is_too_little_data(closes: list[float]) -> None:
    assert compute.trend_15m(fifteen(closes)) == "sideways"


def test_vix_change() -> None:
    got = compute.vix_state(VixReading(12.85, 13.12))
    assert got is not None and (got.level, got.change_pct) == ("12.85", "-2.06")
    assert compute.vix_state(None) is None
    assert compute.vix_state(VixReading(12.0, 0.0)) is None
    assert compute.vix_state(VixReading(float("nan"), 12.0)) is None


def hlc(h: str, low: str, c: str) -> Candle:
    return Candle(
        isin="nifty_50",
        timeframe="1d",
        start_at=session_start(YESTERDAY),
        open=Decimal(c),
        high=Decimal(h),
        low=Decimal(low),
        close=Decimal(c),
        volume=0,
    )


@pytest.mark.parametrize(
    ("h", "low", "c", "expected"),
    [
        ("105", "95", "100", "narrow"),  # P = BC = TC: width 0
        ("101", "99", "99.3", "normal"),  # width 0.47 %
        ("101", "99", "99.0", "wide"),  # width 0.67 %
        ("110", "90", "95", "wide"),
    ],
)
def test_cpr_class(h: str, low: str, c: str, expected: str) -> None:
    assert compute.cpr_class(hlc(h, low, c)) == expected


def test_cpr_class_limits_are_exclusive_and_configurable() -> None:
    candle_ = hlc("101", "99", "99.3")  # width about 0.468 %
    assert compute.cpr_class(candle_, narrow=0.5, wide=0.9) == "narrow"
    assert compute.cpr_class(candle_, narrow=0.1, wide=0.4) == "wide"
    assert compute.cpr_class(None) is None
