from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from tbo_platform_kit.calendar import IST

from tbo_technical_engine.config import latency as cfg
from tbo_technical_engine.schemas.latency import BoundaryLatency
from tbo_technical_engine.services.latency import LatencyTracker, percentile, session_boundaries
from tests.support import CALENDAR

DAY = date(2026, 10, 15)
FIRST = session_boundaries(DAY)[0]  # 09:20 IST
START = FIRST - timedelta(hours=1)


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def tracker(now: datetime = START) -> tuple[LatencyTracker, Clock]:
    clock = Clock(now)
    return LatencyTracker(CALENDAR, clock), clock


def row(**changes: object) -> BoundaryLatency:
    base: dict[str, object] = {
        "boundary_at": FIRST, "trade_date": DAY, "expected": 10, "evaluated": 10, "stale": 0, "cold": 0, "inactive": 0,
        "failed": 0, "absent": 0, "suspended": False, "written_at": FIRST,
    }  # fmt: skip
    return BoundaryLatency.model_validate({**base, **changes})


def test_a_session_has_75_boundaries_from_0920_to_1530_ist() -> None:
    boundaries = session_boundaries(DAY)
    assert len(boundaries) == 75
    assert boundaries[0].astimezone(IST).strftime("%H:%M") == "09:20"
    assert boundaries[-1].astimezone(IST).strftime("%H:%M") == "15:30"
    assert boundaries[0].tzinfo == UTC


def test_the_percentile_is_a_latency_that_happened() -> None:
    values = [float(i) for i in range(1, 101)]
    assert percentile(values, 0.95) == 95.0 and percentile(values, 0.5) == 50.0
    assert percentile([3.0], 0.95) == 3.0


def test_a_boundary_stays_open_through_the_grace_period_and_then_becomes_a_row() -> None:
    t, clock = tracker()
    for i in range(10):
        t.observe(FIRST - timedelta(minutes=5), cfg.EVALUATED, 0.1 * (i + 1))
    clock.now = FIRST + cfg.BOUNDARY_GRACE - timedelta(seconds=1)
    assert t.close_due(10, True) == []
    clock.now = FIRST + cfg.BOUNDARY_GRACE
    (found,) = t.close_due(10, True)
    assert found.boundary_at == FIRST and found.evaluated == 10 and found.missed == 0
    assert found.p50_seconds == 0.5 and found.p95_seconds == 1.0 and found.max_seconds == 1.0
    assert t.close_due(10, True) == []  # written once


def test_stocks_that_sent_no_candle_are_absent_and_every_reason_is_counted() -> None:
    t, clock = tracker()
    at = FIRST - timedelta(minutes=5)
    t.observe(at, cfg.EVALUATED, 0.2)
    t.observe(at, cfg.STALE)
    t.observe(at, cfg.COLD)
    t.observe(at, cfg.INACTIVE)
    t.observe(at, cfg.FAILED)
    clock.now = FIRST + cfg.BOUNDARY_GRACE
    (found,) = t.close_due(8, True)
    assert (found.evaluated, found.stale, found.cold, found.inactive, found.failed, found.absent) == (1, 1, 1, 1, 1, 3)
    assert found.missed == 7


def test_a_boundary_with_the_feed_down_is_suspended_and_misses_nothing() -> None:
    t, clock = tracker()
    clock.now = FIRST + cfg.BOUNDARY_GRACE
    (found,) = t.close_due(10, False)
    assert found.suspended and found.absent == 10 and found.missed == 0


def test_boundaries_before_the_process_started_are_not_its_to_report() -> None:
    t, clock = tracker(FIRST + timedelta(minutes=1))
    clock.now = FIRST + timedelta(minutes=12)
    assert [r.boundary_at for r in t.close_due(10, True)] == [
        FIRST + timedelta(minutes=5),
        FIRST + timedelta(minutes=10),
    ]


def test_nothing_is_written_without_a_universe_or_on_a_holiday() -> None:
    t, clock = tracker()
    clock.now = FIRST + timedelta(hours=1)
    assert t.close_due(0, True) == []
    holiday = datetime(2026, 10, 17, 6, 0, tzinfo=UTC)  # a Saturday
    t2, clock2 = tracker(holiday - timedelta(days=1))
    clock2.now = holiday
    assert t2.close_due(10, True) == []


def test_a_candle_after_its_row_was_written_changes_nothing() -> None:
    t, clock = tracker()
    clock.now = FIRST + cfg.BOUNDARY_GRACE
    t.close_due(10, True)
    t.observe(FIRST - timedelta(minutes=5), cfg.EVALUATED, 1.0)
    clock.now += timedelta(minutes=1)
    assert all(r.boundary_at != FIRST for r in t.close_due(10, True))
