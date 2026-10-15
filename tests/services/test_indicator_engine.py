"""The live indicator state equals a replay of the buffer, however the buffer got there."""

from __future__ import annotations

import math
from datetime import date, timedelta

from tbo_technical_engine.indicators.bars import Bars
from tbo_technical_engine.services.candle_buffer import CandleBuffer
from tbo_technical_engine.services.indicator_engine import IndicatorEngine, default_suites
from tests.support import ISIN_A, candle, history, session_candles

TODAY = date(2026, 10, 15)


def filled() -> tuple[CandleBuffer, IndicatorEngine]:
    buffer = CandleBuffer()
    buffer.add_many(c for tf in history(ISIN_A, TODAY, sessions=5).values() for c in tf)
    return buffer, IndicatorEngine(buffer)


def same(a: dict[str, float], b: dict[str, float]) -> bool:
    return a.keys() == b.keys() and all((math.isnan(a[k]) and math.isnan(b[k])) or abs(a[k] - b[k]) <= 1e-9 for k in a)


def replay(buffer: CandleBuffer, timeframe: str) -> dict[str, float]:
    _, values = default_suites()[timeframe].replay(Bars.from_candles(buffer.series(ISIN_A, timeframe)))
    return values


def test_first_read_replays_the_buffer() -> None:
    buffer, engine = filled()
    found = engine.values(ISIN_A, "5m")
    assert found is not None and found[0] == buffer.last(ISIN_A, "5m")
    assert same(found[1], replay(buffer, "5m"))


def test_each_new_candle_advances_the_state_by_one_step() -> None:
    buffer, engine = filled()
    engine.values(ISIN_A, "5m")
    for c in session_candles(ISIN_A, "5m", TODAY)[:12]:
        buffer.add(c)
        found = engine.values(ISIN_A, "5m")
        assert found is not None and same(found[1], replay(buffer, "5m"))


def test_a_gap_filled_by_the_warm_up_forces_a_rebuild() -> None:
    buffer, engine = filled()
    engine.values(ISIN_A, "5m")
    today = session_candles(ISIN_A, "5m", TODAY)
    buffer.add(today[0])
    engine.values(ISIN_A, "5m")
    buffer.add(today[3])  # candles 1 and 2 were missed
    gappy = engine.values(ISIN_A, "5m")
    buffer.add_many(today[1:3])  # the warm-up repairs the hole
    repaired = engine.values(ISIN_A, "5m")
    assert gappy is not None and repaired is not None
    assert same(repaired[1], replay(buffer, "5m"))


def test_a_corrected_candle_forces_a_rebuild() -> None:
    buffer, engine = filled()
    engine.values(ISIN_A, "5m")
    last = buffer.last(ISIN_A, "5m")
    assert last is not None
    buffer.add(candle(ISIN_A, "5m", last.start_at, "250.00", revision=2))
    found = engine.values(ISIN_A, "5m")
    assert found is not None and found[0].revision == 2 and same(found[1], replay(buffer, "5m"))


def test_a_restarted_engine_matches_one_that_kept_running() -> None:
    buffer, steady = filled()
    steady.values(ISIN_A, "5m")
    for c in session_candles(ISIN_A, "5m", TODAY)[:20]:
        buffer.add(c)
        steady.values(ISIN_A, "5m")
    restarted = IndicatorEngine(buffer)  # a new process over the same history
    a, b = steady.values(ISIN_A, "5m"), restarted.values(ISIN_A, "5m")
    assert a is not None and b is not None and same(a[1], b[1])
    nxt = session_candles(ISIN_A, "5m", TODAY)[20]
    buffer.add(nxt)
    a, b = steady.values(ISIN_A, "5m"), restarted.values(ISIN_A, "5m")
    assert a is not None and b is not None and same(a[1], b[1])


def test_unknown_timeframes_and_empty_buffers_give_nothing() -> None:
    buffer, engine = filled()
    assert engine.values(ISIN_A, "60m") is None
    assert IndicatorEngine(CandleBuffer()).values(ISIN_A, "5m") is None


def test_the_fifteen_minute_and_daily_suites_work_too() -> None:
    buffer, engine = filled()
    for tf in ("15m", "1d"):
        found = engine.values(ISIN_A, tf)
        assert found is not None and same(found[1], replay(buffer, tf))
    assert timedelta(0) == timedelta(0)
