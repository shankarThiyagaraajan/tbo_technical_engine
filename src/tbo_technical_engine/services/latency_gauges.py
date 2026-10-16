"""Prometheus gauges for the evaluation record, kept by the API process.

The signal worker has no HTTP port, so Prometheus cannot scrape it. It writes its record to `evaluation_boundary`; the
API process (which Prometheus does scrape) re-reads today's rows every `GAUGE_REFRESH_SECONDS` and sets these gauges.
The alert rules and the dashboard panels (`docs/monitoring.md`) read them.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tbo_platform_kit.calendar import IST, TradingCalendar
from tbo_platform_kit.logging import get_logger
from tbo_platform_kit.metrics import Metrics

from tbo_technical_engine.config import latency as cfg
from tbo_technical_engine.schemas.latency import BoundaryLatency
from tbo_technical_engine.store import latency as store

log = get_logger(__name__)
NO_RECORD = -1.0
"""Age reported while today has no row yet; an alert rule reads a negative age as 'nothing written', during hours."""


class LatencyGauges:
    """Today's evaluation record as gauges."""

    def __init__(self, metrics: Metrics) -> None:
        self.p95 = metrics.gauge(
            "evaluation_latency_p95", "seconds", "p95 of candle close to candidate at the latest boundary."
        )
        self.worst = metrics.gauge(
            "evaluation_latency_max", "seconds", "Slowest evaluation at the latest boundary, candle close to candidate."
        )
        self.boundaries = metrics.gauge("evaluation_boundaries_today", "count", "Boundaries recorded today.")
        self.missed = metrics.gauge(
            "evaluation_missed_today", "count", "Evaluations missed today (not counting boundaries with the feed down)."
        )
        self.slow = metrics.gauge(
            "evaluation_slow_boundaries_today", "count", "Boundaries today whose p95 was over the 2 s target."
        )
        self.session_day = metrics.gauge(
            "evaluation_session_day", "count", "1 when today is a trading day (the alert rules only watch those)."
        )
        self.refreshed = metrics.gauge(
            "evaluation_gauges_refreshed", "seconds", "Unix time these gauges were last refreshed from the database."
        )
        self.age = metrics.gauge(
            "evaluation_last_record_age",
            "seconds",
            "Seconds since the newest boundary row was written (-1: none today).",
        )

    def update(self, rows: list[BoundaryLatency], now: datetime, *, trading_day: bool = True) -> None:
        """Set every gauge from today's rows (oldest first)."""
        self.session_day.set(1 if trading_day else 0)
        self.refreshed.set(now.timestamp())
        self.boundaries.set(len(rows))
        self.missed.set(sum(r.missed for r in rows))
        self.slow.set(sum(1 for r in rows if r.p95_seconds is not None and r.p95_seconds > cfg.TARGET_P95_SECONDS))
        timed = [r for r in rows if r.p95_seconds is not None and r.max_seconds is not None]
        if timed:
            self.p95.set(timed[-1].p95_seconds or 0.0)
            self.worst.set(timed[-1].max_seconds or 0.0)
        self.age.set((now - max(r.written_at for r in rows)).total_seconds() if rows else NO_RECORD)


async def refresh_forever(
    gauges: LatencyGauges,
    sessions: async_sessionmaker[AsyncSession],
    clock: Callable[[], datetime],
    calendar: TradingCalendar,
    stop: asyncio.Event,
    interval: float = cfg.GAUGE_REFRESH_SECONDS,
) -> None:
    """Refresh until `stop`; a failed read keeps the old values (and the age gauge keeps growing in the rule's eyes only
    through Prometheus' own `absent`/staleness handling) and is logged."""
    while not stop.is_set():
        try:
            now = clock()
            async with sessions() as session:
                rows = await store.for_day(session, now.astimezone(IST).date())
            gauges.update(rows, now, trading_day=calendar.is_trading_day(now.astimezone(IST).date()))
        except Exception as exc:
            log.error("evaluation_gauges_refresh_failed", error=type(exc).__name__)
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), interval)
