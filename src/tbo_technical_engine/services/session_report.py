"""Roll boundary rows up into a session report, and count consecutive clean sessions (the TE8 exit criterion).

A session is clean when every boundary of it has a row, none of those rows is slow (p95 over the target) and none has
a missed evaluation. A boundary with no row at all means the worker was not running, which is the worst kind of miss, so
it makes the session not clean. Boundaries while the feed was down (`suspended`) are not misses: the plan says the
engine stops with its input.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import date

from tbo_technical_engine.config import latency as cfg
from tbo_technical_engine.schemas.latency import BoundaryLatency, SessionReport
from tbo_technical_engine.services.latency import trading_day_boundaries


def report(day: date, rows: Iterable[BoundaryLatency], expected: int | None = None) -> SessionReport:
    """The report of `day` from its rows (rows of other days are ignored). `expected` is the session's boundary count
    (`trading_day_boundaries` of the calendar's window: fewer on a day with a delayed open); the usual 75 by default."""
    mine = [r for r in rows if r.trade_date == day]
    expected = trading_day_boundaries(day) if expected is None else expected
    slow = [r for r in mine if r.p95_seconds is not None and r.p95_seconds > cfg.TARGET_P95_SECONDS]
    missed = sum(r.missed for r in mine)
    missing = max(expected - len(mine), 0)
    p95s = [r.p95_seconds for r in mine if r.p95_seconds is not None]
    return SessionReport(
        trade_date=day,
        boundaries_expected=expected,
        boundaries_recorded=len(mine),
        boundaries_missing=missing,
        suspended=sum(1 for r in mine if r.suspended),
        missed_evaluations=missed,
        slow_boundaries=len(slow),
        worst_p95_seconds=max(p95s) if p95s else None,
        clean=bool(mine) and missing == 0 and missed == 0 and not slow,
    )


def consecutive_clean(reports: Sequence[SessionReport]) -> int:
    """How many of the newest sessions in a row are clean (`reports` oldest first)."""
    count = 0
    for r in reversed(reports):
        if not r.clean:
            break
        count += 1
    return count


def render(reports: Sequence[SessionReport]) -> str:
    """A plain-text table, newest last, and the streak against the required ten."""
    lines = ["date        boundaries  missing  suspended  missed  slow  worst p95 (s)  clean"]
    for r in reports:
        worst = "-" if r.worst_p95_seconds is None else f"{r.worst_p95_seconds:.2f}"
        lines.append(
            f"{r.trade_date}  {r.boundaries_recorded:>3}/{r.boundaries_expected:<3}     {r.boundaries_missing:>5}"
            f"  {r.suspended:>9}  {r.missed_evaluations:>6}  {r.slow_boundaries:>4}  {worst:>13}  "
            f"{'yes' if r.clean else 'NO'}"
        )
    streak = consecutive_clean(reports)
    lines.append(f"consecutive clean sessions: {streak} of {cfg.CLEAN_SESSIONS_REQUIRED} required")
    return "\n".join(lines) + "\n"
