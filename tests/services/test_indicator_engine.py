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


def test_a_read_after_several_candles_catches_up_and_equals_a_rebuild() -> None:
    # The evaluator reads only inside entry windows, so candles arrive unread: a catch-up must give a rebuild's values.
    buffer, engine = filled()
    engine.values(ISIN_A, "5m")
    today = session_candles(ISIN_A, "5m", TODAY)
    buffer.add_many(today[:9])  # nine candles, no read in between
    caught_up = engine.values(ISIN_A, "5m")
    assert caught_up is not None and caught_up[0] == today[8]
    assert same(caught_up[1], replay(buffer, "5m"))
    assert [c.start_at for c, _ in engine.recent(ISIN_A, "5m")][-1] == today[8].start_at


def test_a_catch_up_does_not_rebuild_the_state() -> None:
    buffer, engine = filled()
    engine.values(ISIN_A, "5m")
    state = engine._state[(ISIN_A, "5m", "")]
    buffer.add_many(session_candles(ISIN_A, "5m", TODAY)[:5])
    engine.values(ISIN_A, "5m")
    assert engine._state[(ISIN_A, "5m", "")] is state  # the same object, advanced


def test_an_older_candle_filled_in_behind_the_newest_forces_a_rebuild() -> None:
    buffer, engine = filled()
    today = session_candles(ISIN_A, "5m", TODAY)
    buffer.add_many([today[0], today[2]])
    engine.values(ISIN_A, "5m")
    state = engine._state[(ISIN_A, "5m", "")]
    buffer.add(today[1])  # the newest is unchanged, but the series behind it is not what the state consumed
    found = engine.values(ISIN_A, "5m")
    assert found is not None and same(found[1], replay(buffer, "5m"))
    assert engine._state[(ISIN_A, "5m", "")] is not state


def test_the_state_keeps_up_when_the_buffer_is_at_capacity_and_candles_are_skipped() -> None:
    buffer = CandleBuffer()
    engine = IndicatorEngine(buffer)
    base = session_candles(ISIN_A, "5m", date(2026, 10, 1))
    days = [date(2026, 10, d) for d in (1, 2, 5, 6, 7, 8, 9, 12, 13, 14, 15)]
    stream = [c for d in days for c in session_candles(ISIN_A, "5m", d)]
    assert base and len(stream) > 700
    for i, c in enumerate(stream):
        buffer.add(c)
        if i % 37 == 0:  # reads at irregular intervals
            engine.values(ISIN_A, "5m")
    found = engine.values(ISIN_A, "5m")
    assert found is not None and found[0] == stream[-1]
    # The state is the stream's indicator values from its first candle on: a rebuild of the same stream.
    _, expected = default_suites()["5m"].replay(Bars.from_candles(stream))
    assert same(found[1], expected)


def test_prime_builds_every_timeframe_so_the_first_candle_pays_nothing() -> None:
    buffer, engine = filled()
    engine.prime(ISIN_A)
    keys = set(engine._state)
    assert {(ISIN_A, "5m", ""), (ISIN_A, "15m", ""), (ISIN_A, "1d", ""), (ISIN_A, "15m", "asof")} <= keys
    state = engine._state[(ISIN_A, "5m", "")]
    engine.values(ISIN_A, "5m")
    assert engine._state[(ISIN_A, "5m", "")] is state  # a read after priming reuses it
    engine.prime("INE999999999")  # a stock with no candles is skipped quietly
