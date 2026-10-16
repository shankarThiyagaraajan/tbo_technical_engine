"""Property-based tests: random and pathological bars (flat prices, zero volume, huge gaps, tiny sessions).

What must hold whatever the market does: no exceptions, bounded oscillators, no look-ahead (changing a later bar never
changes an earlier value), and batch equal to streaming.
"""

from __future__ import annotations

import math

import numpy as np
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from tbo_technical_engine import indicators
from tbo_technical_engine.indicators.bars import F64, Bars
from tests.indicators.data import from_ohlcv

BarRows = list[tuple[float, float, float, float, float]]

price = st.floats(min_value=0.05, max_value=100_000, allow_nan=False, allow_infinity=False)
volume = st.one_of(st.just(0.0), st.integers(1, 5_000_000).map(float))


@st.composite
def bar_rows(draw: st.DrawFn) -> BarRows:
    n = draw(st.integers(1, 160))
    rows = []
    for _ in range(n):
        a, b, c = draw(price), draw(price), draw(price)
        high, low = max(a, b, c), min(a, b, c)
        rows.append((a, high, low, c, draw(volume)))
    return rows


FAST = settings(max_examples=60, deadline=None, suppress_health_check=list(HealthCheck))
SUITE = [
    indicators.get("ema", period=9),
    indicators.get("atr"),
    indicators.get("adx"),
    indicators.get("rsi"),
    indicators.get("supertrend"),
    indicators.get("vwap"),
    indicators.get("pivots"),
    indicators.get("cpr"),
    indicators.get("opening_range"),
    indicators.get("volume_average"),
]


def finite(values: F64) -> F64:
    return np.asarray(values[~np.isnan(values)], dtype=np.float64)


@FAST
@given(rows=bar_rows(), per_session=st.integers(1, 80))
def test_oscillators_stay_in_range_and_nothing_blows_up(rows: BarRows, per_session: int) -> None:
    bars = from_ohlcv(rows, per_session=per_session)
    rsi = finite(indicators.get("rsi").batch(bars)["rsi_14"])
    adx = indicators.get("adx").batch(bars)
    atr = finite(indicators.get("atr").batch(bars)["atr_14"])
    assert ((rsi >= 0) & (rsi <= 100)).all()
    for key in ("adx_14", "plus_di_14", "minus_di_14"):
        v = finite(adx[key])
        assert ((v >= -1e-9) & (v <= 100 + 1e-9)).all(), key
    assert (atr >= 0).all()
    for ind in SUITE:
        for values in ind.batch(bars).values():
            assert not np.isinf(values).any()


@FAST
@given(rows=bar_rows(), per_session=st.integers(1, 80))
def test_vwap_lies_between_the_session_low_and_high(rows: BarRows, per_session: int) -> None:
    bars = from_ohlcv(rows, per_session=per_session)
    vwap = indicators.get("vwap").batch(bars)["vwap"]
    for i in range(len(bars)):
        if math.isnan(vwap[i]):
            continue
        same = bars.session == bars.session[i]
        upto = same & (np.arange(len(bars)) <= i)
        typical = (bars.high[upto] + bars.low[upto] + bars.close[upto]) / 3
        assert typical.min() - 1e-6 <= vwap[i] <= typical.max() + 1e-6


@FAST
@given(rows=bar_rows(), per_session=st.integers(1, 80), data=st.data())
def test_no_look_ahead_changing_a_later_bar_never_changes_an_earlier_value(
    rows: BarRows, per_session: int, data: st.DataObject
) -> None:
    if len(rows) < 2:
        return
    cut = data.draw(st.integers(1, len(rows) - 1))
    tampered = rows[:cut] + [(r[0] * 2, r[1] * 3, r[2], r[3] * 2, r[4] + 7) for r in rows[cut:]]
    a, b = from_ohlcv(rows, per_session=per_session), from_ohlcv(tampered, per_session=per_session)
    for ind in SUITE:
        left, right = ind.batch(a), ind.batch(b)
        for name in ind.outputs:
            x, y = left[name][:cut], right[name][:cut]
            assert np.array_equal(np.isnan(x), np.isnan(y)), name
            assert np.array_equal(finite(x), finite(y)), name


@FAST
@given(rows=bar_rows(), per_session=st.integers(1, 80))
def test_batch_equals_streaming_on_random_bars(rows: BarRows, per_session: int) -> None:
    bars: Bars = from_ohlcv(rows, per_session=per_session)
    for ind in SUITE:
        batch, stream = ind.batch(bars), ind.stream()
        got = [stream.update(bars.row(i)) for i in range(len(bars))]
        for name in ind.outputs:
            s = np.array([g[name] for g in got])
            assert np.array_equal(np.isnan(s), np.isnan(batch[name])), name
            ok = ~np.isnan(s)
            tol = 1e-7 * max(1.0, float(np.abs(batch[name][ok]).max())) if ok.any() else 0
            assert np.abs(s[ok] - batch[name][ok]).max(initial=0) <= tol, name
