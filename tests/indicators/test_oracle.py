"""Oracle tests: the indicators TA-Lib has must equal TA-Lib to 1e-6 over a full year of 5-minute bars.

TA-Lib is a dev dependency used only here; the service never imports it.
"""

from __future__ import annotations

import numpy as np
import pytest
import talib

from tbo_technical_engine import indicators
from tbo_technical_engine.indicators.bars import Bars
from tests.indicators.data import random_walk

TOLERANCE = 1e-6


@pytest.fixture(scope="module")
def bars() -> Bars:
    return random_walk(days=250)


def same(ours: np.ndarray, oracle: np.ndarray) -> None:
    assert ours.shape == oracle.shape
    assert np.array_equal(np.isnan(ours), np.isnan(oracle)), "the warm-up (nan) region differs"
    ok = ~np.isnan(oracle)
    assert np.abs(ours[ok] - oracle[ok]).max() <= TOLERANCE
    assert ok.sum() > 1000  # the comparison really covered the year


@pytest.mark.parametrize("period", [2, 5, 20, 50])
def test_sma(bars: Bars, period: int) -> None:
    same(indicators.get("sma", period=period).batch(bars)[f"sma_{period}"], talib.SMA(bars.close, period))


@pytest.mark.parametrize("period", [2, 9, 21, 50, 200])
def test_ema(bars: Bars, period: int) -> None:
    same(indicators.get("ema", period=period).batch(bars)[f"ema_{period}"], talib.EMA(bars.close, period))


@pytest.mark.parametrize("period", [2, 7, 14, 50])
def test_atr(bars: Bars, period: int) -> None:
    ours = indicators.get("atr", period=period).batch(bars)[f"atr_{period}"]
    same(ours, talib.ATR(bars.high, bars.low, bars.close, period))


@pytest.mark.parametrize("period", [2, 7, 14, 28])
def test_adx_and_directional_indicators(bars: Bars, period: int) -> None:
    out = indicators.get("adx", period=period).batch(bars)
    same(out[f"adx_{period}"], talib.ADX(bars.high, bars.low, bars.close, period))
    same(out[f"plus_di_{period}"], talib.PLUS_DI(bars.high, bars.low, bars.close, period))
    same(out[f"minus_di_{period}"], talib.MINUS_DI(bars.high, bars.low, bars.close, period))


@pytest.mark.parametrize("period", [2, 7, 14, 21])
def test_rsi(bars: Bars, period: int) -> None:
    same(indicators.get("rsi", period=period).batch(bars)[f"rsi_{period}"], talib.RSI(bars.close, period))


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_holds_on_other_random_series(seed: int) -> None:
    other = random_walk(days=20, seed=seed, price=85.0)
    same_loose = lambda a, b: np.testing.assert_allclose(a[~np.isnan(b)], b[~np.isnan(b)], atol=TOLERANCE)  # noqa: E731
    same_loose(
        indicators.get("adx", period=14).batch(other)["adx_14"], talib.ADX(other.high, other.low, other.close, 14)
    )
    same_loose(indicators.get("rsi", period=14).batch(other)["rsi_14"], talib.RSI(other.close, 14))
    same_loose(indicators.get("ema", period=21).batch(other)["ema_21"], talib.EMA(other.close, 21))
