"""Review 9.22: boundary records, market context and the warm-up follow the calendar's session, not a fixed
09:15 to 15:30. A special (Muhurat) session is not a TE8 session and is not a day of absent evaluations; a delayed open
moves the first boundary."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any, cast

from tbo_platform_kit.calendar import TradingCalendar
from tbo_platform_kit.config.calendar import IST

from tbo_technical_engine.apps.session_report import trading_days_ending
from tbo_technical_engine.config import latency as cfg
from tbo_technical_engine.services.candle_buffer import CandleBuffer
from tbo_technical_engine.services.context_service import ContextService
from tbo_technical_engine.services.latency import (
    LatencyTracker,
    regular_window,
    session_boundaries,
    trading_day_boundaries,
)
from tbo_technical_engine.services.readiness import Readiness
from tbo_technical_engine.services.session_report import report
from tbo_technical_engine.services.supervisor import WarmupSupervisor
from tbo_technical_engine.services.universe import UniverseHolder
from tbo_technical_engine.services.warmup import WarmupSpec
from tests.support import HOLIDAYS

MUHURAT = date(2026, 11, 7)  # a Saturday
DELAYED = date(2026, 10, 22)  # a Thursday that opens at 10:15 after an outage
USUAL = date(2026, 10, 15)
CALENDAR = TradingCalendar.from_document(
    {
        "years": {
            **HOLIDAYS,
            "2026": {
                "holidays": [
                    {"date": "2026-10-02", "name": "Gandhi Jayanti"},
                    {"date": "2026-10-20", "name": "Dussehra"},
                ],
                "special_sessions": [
                    {"date": MUHURAT.isoformat(), "name": "Muhurat", "open": "18:00", "close": "19:00"}
                ],
                "timing_changes": [{"date": DELAYED.isoformat(), "open": "10:15", "close": "15:30"}],
            },
        }
    }
)


def ist(day: date, hh: int, mm: int, ss: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hh, mm, ss, tzinfo=IST).astimezone(UTC)


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def test_the_window_is_the_calendars_and_a_special_session_has_none() -> None:
    assert regular_window(CALENDAR, USUAL) == (ist(USUAL, 9, 15), ist(USUAL, 15, 30))
    assert regular_window(CALENDAR, DELAYED) == (ist(DELAYED, 10, 15), ist(DELAYED, 15, 30))
    assert CALENDAR.is_trading_day(MUHURAT) and regular_window(CALENDAR, MUHURAT) is None
    assert regular_window(CALENDAR, date(2026, 10, 17)) is None  # an ordinary Saturday


def test_a_delayed_open_moves_the_first_boundary() -> None:
    boundaries = session_boundaries(DELAYED, regular_window(CALENDAR, DELAYED))
    assert boundaries[0] == ist(DELAYED, 10, 20) and boundaries[-1] == ist(DELAYED, 15, 30)
    assert len(boundaries) == 63 == trading_day_boundaries(DELAYED, regular_window(CALENDAR, DELAYED))
    assert session_boundaries(USUAL, regular_window(CALENDAR, USUAL)) == session_boundaries(USUAL)


def test_a_special_session_day_writes_no_absent_rows() -> None:
    clock = Clock(ist(MUHURAT, 8, 0))
    tracker = LatencyTracker(CALENDAR, clock)
    clock.now = ist(MUHURAT, 23, 0)
    assert tracker.close_due(expected=500, feed_healthy=True) == []


def test_a_delayed_open_day_expects_no_boundary_before_the_open() -> None:
    clock = Clock(ist(DELAYED, 8, 0))
    tracker = LatencyTracker(CALENDAR, clock)
    clock.now = ist(DELAYED, 10, 15)
    assert tracker.close_due(expected=500, feed_healthy=True) == []  # was 11 rows of 500 absent each
    clock.now = ist(DELAYED, 10, 20) + cfg.BOUNDARY_GRACE
    (row,) = tracker.close_due(expected=500, feed_healthy=True)
    assert row.boundary_at == ist(DELAYED, 10, 20)


def test_the_session_report_counts_a_delayed_session_against_its_own_boundaries() -> None:
    assert (
        report(DELAYED, [], trading_day_boundaries(DELAYED, regular_window(CALENDAR, DELAYED))).boundaries_expected
        == 63
    )
    assert report(USUAL, []).boundaries_expected == 75


def test_the_clean_sessions_streak_skips_a_special_session() -> None:
    days = trading_days_ending(CALENDAR, date(2026, 11, 9), 3)
    assert MUHURAT not in days and days == [date(2026, 11, 5), date(2026, 11, 6), date(2026, 11, 9)]


def supervisor(now: datetime) -> WarmupSupervisor:
    return WarmupSupervisor(
        cast(Any, None), CandleBuffer(), Readiness(), UniverseHolder(), WarmupSpec(), CALENDAR, lambda: now
    )


def test_the_fresh_warm_up_is_due_before_the_calendars_open() -> None:
    s = supervisor(ist(USUAL, 8, 0))
    assert s._warmup_due(USUAL) == ist(USUAL, 9, 0)
    assert s._warmup_due(DELAYED) == ist(DELAYED, 10, 0)
    assert s._warmup_due(MUHURAT) == ist(MUHURAT, 17, 45)  # was 09:00, eight hours before the session


async def test_no_market_context_on_a_special_session_or_before_a_delayed_open() -> None:
    clock = Clock(ist(MUHURAT, 18, 30, 20))
    readiness = Readiness()
    readiness.feed_state, readiness.needs_warmup = "healthy", False
    svc = ContextService(CandleBuffer(), UniverseHolder(), readiness, cast(Any, None), cast(Any, None), CALENDAR, clock)
    assert await svc.tick() is False  # no database touched: it returns before building
    clock.now = ist(DELAYED, 9, 30, 20)
    assert await svc.tick() is False
