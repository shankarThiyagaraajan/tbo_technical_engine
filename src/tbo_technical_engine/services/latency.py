"""Evaluation latency and missed evaluations, per 5-minute boundary (TE8).

`CandleIngest` tells the tracker what became of each 5-minute candle of a universe stock: evaluated (with the time from
the candle's close until its evaluators finished), or why it was not (`stale`, `cold`, `inactive`, `failed`). The
supervise loop calls `close_due` every 15 s; a boundary that has been closed for `BOUNDARY_GRACE` becomes one
`BoundaryLatency` row, in which the stocks that never sent a candle count as `absent`. Nothing here touches the
database: `LatencyRecorder.flush` writes the rows, so the candle path pays nothing for the record.

Latency is measured on the worker's own clock against the candle's close time, so it includes the feed's time to build
and publish the candle, the stream and the queue behind the consumer: the figure the plan's "candle close to
candidate" means, and the one a real session measures.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from math import ceil

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tbo_platform_kit.calendar import IST, TradingCalendar
from tbo_platform_kit.logging import get_logger

from tbo_technical_engine.config import latency as cfg
from tbo_technical_engine.schemas.latency import BoundaryLatency
from tbo_technical_engine.store import latency as store

log = get_logger(__name__)
MAX_ROWS_WAITING = 2_000
"""Rows kept while the database is unreachable (about 25 sessions of boundaries); the oldest go first."""


def session_boundaries(day: date) -> list[datetime]:
    """The 75 closes of the day's 5-minute candles, UTC, oldest first (a weekday calendar; callers check holidays)."""
    first = datetime.combine(day, cfg.FIRST_CLOSE, tzinfo=IST)
    last = datetime.combine(day, cfg.LAST_CLOSE, tzinfo=IST)
    step = timedelta(minutes=cfg.BOUNDARY_MINUTES)
    out: list[datetime] = []
    at = first
    while at <= last:
        out.append(at.astimezone(UTC))
        at += step
    return out


def percentile(values: list[float], q: float) -> float:
    """Nearest-rank percentile (`q` in 0..1) of a non-empty list: the smallest value that at least `q` of them do not
    exceed. Chosen over interpolation because with a few hundred stocks it is a latency that really happened."""
    ordered = sorted(values)
    rank = max(1, ceil(q * len(ordered)))
    return ordered[rank - 1]


@dataclass
class _Boundary:
    counts: dict[str, int] = field(default_factory=dict)
    seconds: list[float] = field(default_factory=list)

    def add(self, outcome: str, seconds: float | None) -> None:
        self.counts[outcome] = self.counts.get(outcome, 0) + 1
        if outcome == cfg.EVALUATED and seconds is not None:
            self.seconds.append(max(seconds, 0.0))

    @property
    def total(self) -> int:
        return sum(self.counts.values())


class LatencyTracker:
    """Collects outcomes in memory and turns closed boundaries into rows."""

    def __init__(self, calendar: TradingCalendar, clock: Callable[[], datetime]) -> None:
        self.calendar = calendar
        self.clock = clock
        self._open: dict[datetime, _Boundary] = {}
        self._written: set[datetime] = set()
        self.since = clock()
        """Boundaries that closed before this process started are not its to report: they belong to whatever ran then
        (or, if nothing did, to the session report, which counts a boundary with no row as missing)."""

    def observe(self, candle_start_at: datetime, outcome: str, seconds: float | None = None) -> None:
        """One 5-minute candle's outcome. `candle_start_at` is the candle's start; its boundary is start + 5 minutes."""
        boundary = candle_start_at + timedelta(minutes=cfg.BOUNDARY_MINUTES)
        if boundary in self._written:
            return  # the row exists; a very late candle cannot change it
        self._open.setdefault(boundary, _Boundary()).add(outcome, seconds)

    def open_count(self, boundary: datetime) -> int:
        """How many candles of a boundary (its close time) are waiting to be written as a row."""
        found = self._open.get(boundary)
        return 0 if found is None else found.total

    def close_due(self, expected: int, feed_healthy: bool) -> list[BoundaryLatency]:
        """Rows for every boundary of today that closed more than the grace period ago and has none yet.

        `expected` is the universe size now and `feed_healthy` the feed's state now; a universe of 0 writes nothing (no
        stocks, nothing to miss)."""
        now = self.clock()
        today = now.astimezone(IST).date()
        if expected == 0 or not self.calendar.is_trading_day(today):
            return []
        rows: list[BoundaryLatency] = []
        for boundary in session_boundaries(today):
            if boundary in self._written or boundary + cfg.BOUNDARY_GRACE > now or boundary <= self.since:
                continue
            seen = self._open.pop(boundary, _Boundary())
            rows.append(self._row(boundary, seen, expected, feed_healthy, now))
            self._written.add(boundary)
        stale = [b for b in self._open if b + cfg.BOUNDARY_GRACE <= now]
        for boundary in stale:  # a boundary of another day or outside the session: never written, so do not keep it
            del self._open[boundary]
        return rows

    @staticmethod
    def _row(boundary: datetime, seen: _Boundary, expected: int, healthy: bool, now: datetime) -> BoundaryLatency:
        counts = seen.counts
        absent = max(expected - seen.total, 0)
        return BoundaryLatency(
            boundary_at=boundary,
            trade_date=boundary.astimezone(IST).date(),
            expected=expected,
            evaluated=counts.get(cfg.EVALUATED, 0),
            stale=counts.get(cfg.STALE, 0),
            cold=counts.get(cfg.COLD, 0),
            inactive=counts.get(cfg.INACTIVE, 0),
            failed=counts.get(cfg.FAILED, 0),
            absent=absent,
            suspended=not healthy,
            p50_seconds=percentile(seen.seconds, 0.5) if seen.seconds else None,
            p95_seconds=percentile(seen.seconds, 0.95) if seen.seconds else None,
            max_seconds=max(seen.seconds) if seen.seconds else None,
            written_at=now,
        )


class LatencyRecorder:
    """Holds finished rows until `flush` writes them; a failed write keeps them for the next flush."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self.sessions = sessions
        self._waiting: list[BoundaryLatency] = []

    def add(self, rows: Iterable[BoundaryLatency]) -> None:
        """Queue rows for the next flush, dropping the oldest beyond `MAX_ROWS_WAITING`."""
        self._waiting.extend(rows)
        if len(self._waiting) > MAX_ROWS_WAITING:
            del self._waiting[: len(self._waiting) - MAX_ROWS_WAITING]
            log.warning("evaluation_boundary_dropped", reason="database unreachable for too long")

    async def flush(self) -> int:
        """Write the waiting rows in one transaction; returns how many. On a failure they stay queued."""
        if not self._waiting:
            return 0
        batch, self._waiting = self._waiting, []
        try:
            async with self.sessions() as session, session.begin():
                return await store.save_many(session, batch)
        except Exception as exc:
            log.error("evaluation_boundary_write_failed", error=type(exc).__name__, waiting=len(batch))
            self._waiting = batch + self._waiting
            return 0


def trading_day_boundaries(day: date) -> int:
    """How many boundaries a full session has (75): the denominator of a session report."""
    return len(session_boundaries(day))


__all__ = [
    "LatencyRecorder",
    "LatencyTracker",
    "percentile",
    "session_boundaries",
    "trading_day_boundaries",
]
