from __future__ import annotations

from datetime import UTC, datetime

from tbo_technical_engine.schemas.candle import FeedStatus
from tbo_technical_engine.services.readiness import Readiness

NOW = datetime(2026, 10, 15, 4, 0, tzinfo=UTC)


def status(state: str) -> FeedStatus:
    return FeedStatus(state=state, changed_at=NOW)


def test_starts_inactive_and_degraded() -> None:
    r = Readiness()
    assert (r.active, r.data_quality) == (False, "degraded")


def test_needs_a_healthy_feed_and_a_fresh_warm_up() -> None:
    r = Readiness()
    r.on_feed_status(status("healthy"))
    assert r.active is False  # healthy, but never warmed
    assert r.mark_warm(r.generation) is True
    assert (r.active, r.data_quality) == (True, "ok")


def test_any_unhealthy_status_suspends_until_a_new_warm_up() -> None:
    for bad in ("stale", "down", "connecting"):
        r = Readiness()
        r.on_feed_status(status("healthy"))
        r.mark_warm(r.generation)
        r.on_feed_status(status(bad))
        assert r.active is False
        r.on_feed_status(status("healthy"))
        assert r.active is False  # recovery alone is not enough
        assert r.mark_warm(r.generation) and r.active


def test_a_warm_up_that_overlapped_an_outage_does_not_count() -> None:
    r = Readiness()
    r.on_feed_status(status("healthy"))
    started = r.generation
    r.on_feed_status(status("down"))
    r.on_feed_status(status("healthy"))
    assert r.mark_warm(started) is False
    assert r.needs_warmup is True


def test_repeated_healthy_statuses_change_nothing() -> None:
    r = Readiness()
    r.on_feed_status(status("healthy"))
    r.mark_warm(r.generation)
    r.on_feed_status(status("healthy"))
    assert r.active is True


def test_a_new_session_asks_for_a_warm_up() -> None:
    r = Readiness()
    r.on_feed_status(status("healthy"))
    r.mark_warm(r.generation)
    r.request_warmup()
    assert r.active is False
