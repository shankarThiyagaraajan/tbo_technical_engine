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


def test_a_view_reads_like_the_series_without_copying() -> None:
    buffer = CandleBuffer()
    for i in range(6):
        buffer.add(candle(ISIN_A, "5m", T0 + timedelta(minutes=5 * i)))
    view = buffer.view(ISIN_A, "5m")
    series = buffer.series(ISIN_A, "5m")
    assert len(view) == 6 and list(view) == series and list(reversed(view)) == series[::-1]
    assert view[0] == series[0] and view[-1] == series[-1] and view[-2] == series[-2] and view[1:4] == series[1:4]
    assert view.count_through(T0 + timedelta(minutes=10)) == 3
    assert len(buffer.view("INE000000000", "5m")) == 0
    try:
        view[6]
    except IndexError:
        pass
    else:  # pragma: no cover
        raise AssertionError("an index past the end must raise")


def test_view_before_is_before_without_a_scan() -> None:
    buffer = CandleBuffer()
    for i in range(8):
        buffer.add(candle(ISIN_A, "15m", T0 + timedelta(minutes=15 * i)))
    for minutes in (0, 14, 15, 29, 30, 44, 45, 200):
        cutoff = T0 + timedelta(minutes=minutes)
        assert list(buffer.view_before(ISIN_A, "15m", cutoff)) == buffer.before(ISIN_A, "15m", cutoff)
        assert buffer.before(ISIN_A, "15m", cutoff) == [
            c for c in buffer.series(ISIN_A, "15m") if c.start_at + timedelta(minutes=15) <= cutoff
        ]


def test_the_sorted_starts_survive_out_of_order_arrivals_trimming_and_forgetting() -> None:
    buffer = CandleBuffer()
    for minutes in (30, 0, 15, 45, 5):  # arrival order is not time order
        buffer.add(candle(ISIN_A, "5m", T0 + timedelta(minutes=minutes)))
    assert [int((c.start_at - T0).total_seconds() // 60) for c in buffer.series(ISIN_A, "5m")] == [0, 5, 15, 30, 45]
    for i in range(CAPACITY["15m"] + 5):
        buffer.add(candle(ISIN_B, "15m", T0 + timedelta(minutes=15 * i)))
    held = buffer.series(ISIN_B, "15m")
    assert len(held) == CAPACITY["15m"] == len(buffer.view(ISIN_B, "15m"))
    assert buffer.last(ISIN_B, "15m") == held[-1]
    buffer.forget({ISIN_A})
    assert buffer.series(ISIN_A, "5m") == [] and buffer.last(ISIN_A, "5m") is None
