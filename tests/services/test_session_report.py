from __future__ import annotations

from datetime import date, timedelta

from tbo_technical_engine.schemas.latency import BoundaryLatency
from tbo_technical_engine.services.latency import session_boundaries
from tbo_technical_engine.services.session_report import consecutive_clean, render, report

DAY = date(2026, 10, 15)


def rows(day: date = DAY, *, count: int = 75, **changes: object) -> list[BoundaryLatency]:
    base: dict[str, object] = {
        "trade_date": day, "expected": 10, "evaluated": 10, "stale": 0, "cold": 0, "inactive": 0, "failed": 0,
        "absent": 0, "suspended": False, "p95_seconds": 0.5, "p50_seconds": 0.2, "max_seconds": 0.9,
    }  # fmt: skip
    return [
        BoundaryLatency.model_validate({**base, **changes, "boundary_at": b, "written_at": b})
        for b in session_boundaries(day)[:count]
    ]


def test_a_full_session_with_nothing_missed_and_nothing_slow_is_clean() -> None:
    r = report(DAY, rows())
    assert r.clean and r.boundaries_recorded == 75 and r.worst_p95_seconds == 0.5


def test_a_missing_boundary_makes_the_session_not_clean() -> None:
    r = report(DAY, rows(count=74))
    assert not r.clean and r.boundaries_missing == 1


def test_a_missed_evaluation_or_a_slow_boundary_makes_it_not_clean() -> None:
    assert not report(DAY, rows(evaluated=9, absent=1)).clean
    slow = report(DAY, rows(p95_seconds=2.5))
    assert not slow.clean and slow.slow_boundaries == 75 and slow.worst_p95_seconds == 2.5


def test_boundaries_with_the_feed_down_are_not_misses() -> None:
    r = report(DAY, rows(evaluated=0, absent=10, suspended=True, p95_seconds=None, p50_seconds=None, max_seconds=None))
    assert r.clean and r.suspended == 75 and r.missed_evaluations == 0


def test_a_day_without_rows_is_not_clean_and_other_days_rows_are_ignored() -> None:
    other = rows(DAY - timedelta(days=1))
    assert not report(DAY, other).clean and report(DAY, other).boundaries_recorded == 0


def test_the_streak_counts_the_newest_clean_sessions_in_a_row() -> None:
    clean, dirty = report(DAY, rows()), report(DAY, rows(count=1))
    assert consecutive_clean([dirty, clean, clean]) == 2
    assert consecutive_clean([clean, dirty]) == 0
    assert consecutive_clean([]) == 0


def test_the_table_says_how_far_the_streak_is_from_ten() -> None:
    text = render([report(DAY, rows())])
    assert "2026-10-15" in text and "consecutive clean sessions: 1 of 10 required" in text
