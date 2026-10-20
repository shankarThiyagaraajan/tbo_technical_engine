"""The TE8 exit-criterion report: for each of the last N trading days, were all evaluations made in time.

    python -m tbo_technical_engine.apps.session_report --days 10
    python -m tbo_technical_engine.apps.session_report --days 10 --date 2026-11-02     # N days ending on this date

Reads `technical.evaluation_boundary` (the signal worker writes a row per 5-minute boundary) and prints a table plus the
streak of consecutive clean sessions against the ten the plan requires. Exit code 0 when the streak reaches the
requirement, 1 when it does not (so a cron or a person can gate on it). Needs TBO_TECHNICAL_DATABASE_URL. In the image
(review 9.21): `docker compose exec technical_worker python -m tbo_technical_engine.apps.session_report --days 10`;
from a checkout, `uv run python -m tbo_technical_engine.apps.session_report`.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import date, timedelta

from tbo_platform_kit.calendar import TradingCalendar, now_utc
from tbo_platform_kit.config.calendar import IST
from tbo_platform_kit.db import create_engine, session_factory

from tbo_technical_engine.config import latency as cfg
from tbo_technical_engine.config.settings import Settings
from tbo_technical_engine.schemas.latency import SessionReport
from tbo_technical_engine.services import session_report
from tbo_technical_engine.services.latency import regular_window, trading_day_boundaries
from tbo_technical_engine.services.market_calendar import build_calendar
from tbo_technical_engine.store import latency as store


def trading_days_ending(calendar: TradingCalendar, end: date, count: int) -> list[date]:
    """The `count` days with a regular session up to and including `end`, oldest first. A special session (a Muhurat
    hour) is skipped: it has no boundary rows, so it neither breaks nor extends the streak (review 9.22)."""
    days: list[date] = []
    day = end
    while len(days) < count and (end - day).days < count * 4 + 10:
        if regular_window(calendar, day) is not None:
            days.append(day)
        day -= timedelta(days=1)
    return days[::-1]


async def build(settings: Settings, days: list[date], calendar: TradingCalendar) -> list[SessionReport]:
    """One report per day from the database, each against its own session's boundaries (a delayed open has fewer)."""
    engine = create_engine(settings)
    try:
        sessions = session_factory(engine)
        out: list[SessionReport] = []
        for day in days:
            expected = trading_day_boundaries(day, regular_window(calendar, day))
            async with sessions() as session:
                out.append(session_report.report(day, await store.for_day(session, day), expected))
        return out
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> int:
    """Print the report; returns the exit code."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, default=cfg.CLEAN_SESSIONS_REQUIRED)
    ap.add_argument("--date", type=date.fromisoformat, help="the last day (default: today, IST)")
    args = ap.parse_args(argv)
    settings = Settings()
    settings.require("database_url")
    end = args.date or now_utc().astimezone(IST).date()
    calendar = build_calendar(settings)
    asyncio.run(calendar.refresh())
    days = trading_days_ending(calendar, end, args.days)
    reports = asyncio.run(build(settings, days, calendar))
    sys.stdout.write(session_report.render(reports))
    return 0 if session_report.consecutive_clean(reports) >= cfg.CLEAN_SESSIONS_REQUIRED else 1


if __name__ == "__main__":
    raise SystemExit(main())
