from __future__ import annotations

from datetime import UTC, datetime, timedelta

from tbo_technical_engine.services.candle_buffer import CAPACITY, CandleBuffer
from tests.support import ISIN_A, ISIN_B, candle

T0 = datetime(2026, 10, 15, 3, 45, tzinfo=UTC)


def test_a_candle_seen_twice_is_stored_once() -> None:
    buffer = CandleBuffer()
    assert buffer.add(candle(ISIN_A, "5m", T0)) is True
    assert buffer.add(candle(ISIN_A, "5m", T0)) is False
    assert buffer.count(ISIN_A, "5m") == 1


def test_a_higher_revision_replaces_and_a_lower_one_is_ignored() -> None:
    buffer = CandleBuffer()
    buffer.add(candle(ISIN_A, "5m", T0, "100.00", revision=2))
    assert buffer.add(candle(ISIN_A, "5m", T0, "99.00", revision=1)) is False
    assert buffer.add(candle(ISIN_A, "5m", T0, "101.00", revision=3)) is True
    only = buffer.series(ISIN_A, "5m")
    assert [(str(c.close), c.revision) for c in only] == [("101.00", 3)]


def test_series_is_oldest_first_whatever_the_arrival_order() -> None:
    buffer = CandleBuffer()
    for minutes in (10, 0, 5):
        buffer.add(candle(ISIN_A, "5m", T0 + timedelta(minutes=minutes)))
    starts = [c.start_at for c in buffer.series(ISIN_A, "5m")]
    assert starts == sorted(starts) and len(starts) == 3


def test_stocks_and_timeframes_are_separate() -> None:
    buffer = CandleBuffer()
    buffer.add(candle(ISIN_A, "5m", T0))
    buffer.add(candle(ISIN_A, "15m", T0))
    buffer.add(candle(ISIN_B, "5m", T0))
    assert (buffer.count(ISIN_A, "5m"), buffer.count(ISIN_A, "15m"), buffer.count(ISIN_B, "5m")) == (1, 1, 1)
    assert buffer.last(ISIN_B, "15m") is None


def test_the_oldest_candles_go_first_when_full() -> None:
    buffer = CandleBuffer()
    for i in range(CAPACITY["15m"] + 3):
        buffer.add(candle(ISIN_A, "15m", T0 + timedelta(minutes=15 * i)))
    series = buffer.series(ISIN_A, "15m")
    assert len(series) == CAPACITY["15m"]
    assert series[0].start_at == T0 + timedelta(minutes=15 * 3)


def test_before_returns_only_candles_closed_by_the_cutoff() -> None:
    buffer = CandleBuffer()
    for i in range(4):
        buffer.add(candle(ISIN_A, "15m", T0 + timedelta(minutes=15 * i)))
    # The candle starting at T0+30 closes at T0+45: not closed by T0+30 or T0+44, closed by T0+45.
    assert len(buffer.before(ISIN_A, "15m", T0 + timedelta(minutes=30))) == 2
    assert len(buffer.before(ISIN_A, "15m", T0 + timedelta(minutes=44))) == 2
    assert len(buffer.before(ISIN_A, "15m", T0 + timedelta(minutes=45))) == 3


def test_forget_drops_only_the_given_stocks() -> None:
    buffer = CandleBuffer()
    buffer.add(candle(ISIN_A, "5m", T0))
    buffer.add(candle(ISIN_B, "5m", T0))
    buffer.forget([ISIN_A])
    assert buffer.count(ISIN_A, "5m") == 0 and buffer.count(ISIN_B, "5m") == 1
