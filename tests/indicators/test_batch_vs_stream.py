"""Plan exit criterion: batch equals streaming over a full year. Every output, every bar, 1e-9."""

from __future__ import annotations

import math

import numpy as np
import pytest

from tbo_technical_engine import indicators
from tbo_technical_engine.indicators.bars import Bars
from tbo_technical_engine.indicators.base import Indicator
from tests.indicators.data import random_walk

ALL: list[tuple[str, dict[str, float]]] = [
    ("sma", {"period": 20}),
    ("ema", {"period": 9}),
    ("ema", {"period": 21}),
    ("atr", {"period": 14}),
    ("adx", {"period": 14}),
    ("rsi", {"period": 14}),
    ("supertrend", {"period": 10, "multiplier": 3.0}),
    ("supertrend", {"period": 7, "multiplier": 2.5}),
    ("vwap", {}),
    ("pivots", {}),
    ("camarilla", {}),
    ("cpr", {}),
    ("opening_range", {"minutes": 15, "timeframe_minutes": 5}),
    ("opening_range", {"minutes": 30, "timeframe_minutes": 5}),
    ("volume_average", {"period": 20}),
]


@pytest.fixture(scope="module")
def year() -> Bars:
    return random_walk(days=250)


@pytest.mark.parametrize(("name", "params"), ALL, ids=[f"{n}-{i}" for i, (n, _) in enumerate(ALL)])
def test_batch_equals_streaming_for_a_year(year: Bars, name: str, params: dict[str, float]) -> None:
    indicator: Indicator = indicators.get(name, **params)
    batch = indicator.batch(year)
    stream = indicator.stream()
    rows = [stream.update(year.row(i)) for i in range(len(year))]
    assert set(batch) == set(indicator.outputs)
    for out in indicator.outputs:
        streamed = np.array([r[out] for r in rows])
        assert np.array_equal(np.isnan(streamed), np.isnan(batch[out])), f"{out}: nan positions differ"
        ok = ~np.isnan(batch[out])
        assert ok.any(), f"{out} never produced a value"
        assert np.abs(streamed[ok] - batch[out][ok]).max() <= 1e-9, out


def test_a_restarted_stream_equals_one_that_never_stopped(year: Bars) -> None:
    """TE1's exit criterion at indicator level: replaying the buffer rebuilds the same state, so the next candle's
    values are identical."""
    suite = indicators.Suite([indicators.get(n, **p) for n, p in [ALL[0], ALL[1], *ALL[3:13], ALL[14]]])
    steady = suite.stream()
    cut = len(year) - 300  # the engine "restarts" 300 bars before the end, mid-session
    for i in range(cut):
        steady.update(year.row(i))
    restarted, _ = suite.replay(Bars.from_bars([year.row(i) for i in range(cut)]))
    for i in range(cut, len(year)):
        a, b = steady.update(year.row(i)), restarted.update(year.row(i))
        assert a.keys() == b.keys()
        for key in a:
            assert (math.isnan(a[key]) and math.isnan(b[key])) or a[key] == b[key], (i, key)


def test_a_suite_refuses_two_indicators_with_the_same_output() -> None:
    with pytest.raises(ValueError, match="same output"):
        indicators.Suite([indicators.get("ema", period=9), indicators.get("ema", period=9)])
