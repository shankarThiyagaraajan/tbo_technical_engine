"""Review 9.26: a candle late enough to need the grace but young enough to be evaluated is counted, not `absent`."""

from __future__ import annotations

from datetime import timedelta

from tbo_technical_engine.apps.signal_worker import boundary_grace
from tbo_technical_engine.config import latency as cfg
from tbo_technical_engine.config.settings import Settings
from tbo_technical_engine.services.latency import LatencyTracker, session_boundaries
from tests.services.test_latency import DAY, START, Clock
from tests.support import CALENDAR


def test_the_grace_is_never_shorter_than_the_evaluated_candle_age() -> None:
    assert boundary_grace(Settings.model_fields["max_candle_age_seconds"].default) == timedelta(seconds=120)
    assert boundary_grace(10) == cfg.BOUNDARY_GRACE


def test_a_candle_100_s_late_is_evaluated_and_counted_against_its_boundary() -> None:
    first = session_boundaries(DAY)[0]
    clock = Clock(START)
    tracker = LatencyTracker(CALENDAR, clock, grace=boundary_grace(120))
    clock.now = first + timedelta(seconds=60)
    assert tracker.close_due(2, True) == []  # the old 30 s grace wrote the row here, with both stocks absent
    tracker.observe(first - timedelta(minutes=5), cfg.EVALUATED, 1.0)
    clock.now = first + timedelta(seconds=100)
    tracker.observe(first - timedelta(minutes=5), cfg.EVALUATED, 100.0)
    clock.now = first + timedelta(seconds=120)
    (row,) = tracker.close_due(2, True)
    assert (row.evaluated, row.absent) == (2, 0)
